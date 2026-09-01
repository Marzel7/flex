import json
import sqlite3
from pathlib import Path

import pytest

from src.evidence.contracts.psi0h_h6_historical_source_retention_availability import (
    VERDICT_BLOCKED_SOURCE_ABSENT,
    VERDICT_READY_BOUNDED_BACKFILL,
    VERDICT_READY_LOCAL_EXPANSION,
    Psi0hH6HistoricalSourceRetentionAvailabilityError,
    qualify_historical_source_retention_availability,
    verify_historical_source_retention_availability,
)


def _write_h5_artifact(tmp_path: Path, *, include_source_inventory_rows=False, source_path: str | None = None) -> Path:
    artifact = {
        "schema_version": "psi0h-h5.historical-source-expansion.v1",
        "status": "PASS",
        "verdict": "H5_SOURCE_EXPANSION_RECONCILIATION_PASS",
        "artifact_digest": "000000000000000000000000000000000000000000000000000000000000000000",
        "candidate_source_paths_scanned": 1,
        "reconstructed_additional_operation_population_count": 0,
        "expanded_populations": [],
        "missing_reason_counts": {"SOURCE_WAS_NEVER_RETAINED": 1},
        "source_inventory_rows": [],
        "source": {
            "known_operation_count": 2,
            "artifact_source_path": "PSI0H-H4",
            "h4_manifest_path": "/tmp/manfest",
        },
    }
    if include_source_inventory_rows and source_path:
        artifact["source_inventory_rows"] = [
            {
                "source_path": source_path,
                "source_identity": {"device": 1, "inode": 2, "size_bytes": 3, "mtime_ns": 4},
                "evidence_rows": 0,
                "primitive_rows": 0,
                "blocking_reasons": ["SOURCE_WAS_NEVER_RETAINED"],
            }
        ]
    path = tmp_path / "h5.json"
    path.write_text(json.dumps(artifact, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def _write_db_with_reconstructable_operation(path: Path, *, ready_local: bool = True) -> None:
    conn = sqlite3.connect(path)
    try:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE normalized_evidence_records(
                evidence_id INTEGER PRIMARY KEY,
                payload_json TEXT,
                observed_at INTEGER
            )
            """
        )
        c.execute(
            """
            CREATE TABLE primitive_observations(
                primitive_id INTEGER PRIMARY KEY,
                primitive_type TEXT,
                output_payload_json TEXT,
                subjects_json TEXT,
                parameters_json TEXT,
                window_start INTEGER,
                window_end INTEGER
            )
            """
        )
        c.execute(
            """
            CREATE TABLE evidence_provenance(
                evidence_id INTEGER,
                provider_request_id TEXT,
                endpoint_method TEXT
            )
            """
        )
        evidence_payload = json.dumps(
            {"operation_id": "op-alpha", "wallet": "w1", "roles": {"funder": "w1"}, "window": {"start": 100, "end": 200}}
        )
        primitive_payload = json.dumps(
            {
                "operation_id": "op-alpha",
                "primitive_type": "SYSTEM_TRANSFER",
                "wallets": ["w1"],
                "window": {"start": 100, "end": 200},
                "roles": {"recipient": "w2"},
            }
        )
        c.execute(
            "INSERT INTO normalized_evidence_records (evidence_id, payload_json, observed_at) VALUES (1, ?, 100)",
            (evidence_payload,),
        )
        c.execute(
            "INSERT INTO primitive_observations (primitive_id, primitive_type, output_payload_json, subjects_json, parameters_json, window_start, window_end)"
            " VALUES (1, 'SYSTEM_TRANSFER', ?, NULL, NULL, 100, 200)",
            (primitive_payload,),
        )
        if ready_local:
            c.execute(
                "INSERT INTO evidence_provenance (evidence_id, provider_request_id, endpoint_method) VALUES (1, 'req-1', 'getTransaction')"
            )
        conn.commit()
    finally:
        conn.close()


def _write_db_with_partial_operation(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        c = conn.cursor()
        c.execute("CREATE TABLE normalized_evidence_records(evidence_id INTEGER PRIMARY KEY, payload_json TEXT, observed_at INTEGER)")
        c.execute(
            "CREATE TABLE primitive_observations(primitive_id INTEGER PRIMARY KEY, primitive_type TEXT, output_payload_json TEXT, subjects_json TEXT, parameters_json TEXT, window_start INTEGER, window_end INTEGER)"
        )
        # no explicit operation_id / no topology fields; only wallet motifs
        evidence_payload = json.dumps({"wallet": "w1", "signature": "sig"})
        primitive_payload = json.dumps({"wallets": ["w1", "w2"]})
        c.execute("INSERT INTO normalized_evidence_records (evidence_id, payload_json, observed_at) VALUES (1, ?, 100)", (evidence_payload,))
        c.execute(
            "INSERT INTO primitive_observations (primitive_id, primitive_type, output_payload_json, subjects_json, parameters_json, window_start, window_end)"
            " VALUES (1, 'UNKNOWN', ?, NULL, NULL, 100, 200)",
            (primitive_payload,),
        )
        conn.commit()
    finally:
        conn.close()


def test_h6_ready_local_expansion(tmp_path):
    h5 = json.loads(_write_h5_artifact(tmp_path, include_source_inventory_rows=True, source_path=str(tmp_path / "seed.db")).read_text())
    db_path = tmp_path / "seed.db"
    _write_db_with_reconstructable_operation(db_path)
    result = qualify_historical_source_retention_availability(h5_artifact=h5, evidence_root=str(tmp_path), maximum_sources=20)
    assert result["verdict"] == VERDICT_READY_LOCAL_EXPANSION
    assert result["ready_local_expansion_operation_count"] >= 1
    verify_historical_source_retention_availability(result)


def test_h6_ready_bounded_backfill(tmp_path):
    h5 = json.loads(_write_h5_artifact(tmp_path).read_text())
    db_path = tmp_path / "partial.db"
    _write_db_with_partial_operation(db_path)
    result = qualify_historical_source_retention_availability(h5_artifact=h5, evidence_root=str(tmp_path), maximum_sources=20)
    assert result["verdict"] == VERDICT_READY_BOUNDED_BACKFILL
    assert result["next_action"]["decision"].startswith("RUN_")
    verify_historical_source_retention_availability(result)


def test_h6_blocked_source_absent_when_no_rows(tmp_path):
    h5 = json.loads(_write_h5_artifact(tmp_path).read_text())
    result = qualify_historical_source_retention_availability(h5_artifact=h5, evidence_root=str(tmp_path), maximum_sources=20)
    assert result["verdict"] == VERDICT_BLOCKED_SOURCE_ABSENT
    verify_historical_source_retention_availability(result)


def test_h6_runner_writes_artifact(tmp_path):
    h5_path = _write_h5_artifact(tmp_path)
    out_path = tmp_path / "h6.json"
    from scripts.run_psi0h_h6_historical_source_retention_availability import run

    payload = run(h5_artifact=str(h5_path), evidence_root=str(tmp_path), output=str(out_path), maximum_sources=20)
    artifact = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == str(out_path)
    assert artifact["artifact_digest"] == payload["artifact_digest"]
    assert artifact["schema_version"] == "psi0h-h6.historical-source-retention-availability.v1"


def test_h6_requires_valid_h5_artifact():
    with pytest.raises(Psi0hH6HistoricalSourceRetentionAvailabilityError, match="PSI0H_H6_H5_BOUNDING_INVALID"):
        qualify_historical_source_retention_availability(h5_artifact={"schema_version": "wrong"})
