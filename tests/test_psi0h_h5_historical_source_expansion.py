import json
import sqlite3
from pathlib import Path

import pytest

from src.evidence.contracts.psi0h_h5_historical_source_expansion import (
    Psi0hH5HistoricalSourceExpansionError,
    qualify_historical_source_expansion,
    verify_historical_source_expansion,
)


def _write_h4_artifact(tmp_path: Path) -> Path:
    h4 = {
        "schema_version": "psi0h-h4.historical-operation-census.v1",
        "milestone": "PSI0H-H4",
        "status": "PASS",
        "operation_count": 2,
        "discovered_populations": [
            {
                "operation_id": "watchtower",
                "source_path": "a",
                "source_identity": {"device": 1, "inode": 2, "size_bytes": 3, "mtime_ns": 4},
                "identity_guarded": True,
                "same_operation_claim": False,
                "same_human_claim": False,
            },
            {
                "operation_id": "three_sw2",
                "source_path": "b",
                "source_identity": {"device": 5, "inode": 6, "size_bytes": 7, "mtime_ns": 8},
                "identity_guarded": True,
                "same_operation_claim": False,
                "same_human_claim": False,
            },
        ],
    }
    from src.evidence.contracts.psi0h_h4_historical_operation_census import _digest

    digest_payload = dict(h4)
    # H4 verification hashes the record after all fields except artifact_digest.
    h4["artifact_digest"] = _digest(digest_payload)
    p = tmp_path / "h4_artifact.json"
    p.write_text(json.dumps(h4, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return p


def _make_evidence_db(tmp_path: Path, *, include_operations: bool = False) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "evidence.db"
    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE normalized_evidence_records ("
            "evidence_id INTEGER PRIMARY KEY,"
            "logical_fact_id TEXT,"
            "fact_family TEXT,"
            "fact_schema_version TEXT,"
            "chain TEXT,"
            "network TEXT,"
            "natural_key TEXT,"
            "payload_json TEXT,"
            "payload_digest TEXT,"
            "raw_artifact_digest TEXT,"
            "observed_at INTEGER,"
            "acquired_at INTEGER)"
        )
        cur.execute(
            "CREATE TABLE primitive_observations ("
            "primitive_id INTEGER PRIMARY KEY,"
            "primitive_type TEXT,"
            "primitive_version TEXT,"
            "subjects_json TEXT,"
            "parameters_json TEXT,"
            "window_start INTEGER,"
            "window_end INTEGER,"
            "output_payload_json TEXT,"
            "output_digest TEXT,"
            "quality_state TEXT,"
            "missing_inputs_json TEXT,"
            "failure_state TEXT)"
        )
        cur.execute(
            "CREATE TABLE normalized_evidence_provenance("
            "evidence_id INTEGER,"
            "provider_request_id TEXT,"
            "endpoint_method TEXT)"
        )
        if include_operations:
            payload = json.dumps({"operation_id": "historical-op-1", "source": "x", "wallet": "abc", "window": {"start": 1, "end": 2}})
            primitive = json.dumps({"operation_id": "historical-op-1", "event_types": ["SYSTEM_TRANSFER"], "window": {"start": 1, "end": 2}})
        else:
            payload = json.dumps({"wallet": "abc", "signature": "sig"})
            primitive = json.dumps({"wallet": "abc", "destination": "def", "event_types": ["SYSTEM_TRANSFER"], "window": {"start": 1, "end": 2}})
        cur.execute(
            "INSERT INTO normalized_evidence_records (evidence_id, payload_json, observed_at) VALUES (1, ?, 1)",
            (payload,),
        )
        cur.execute("INSERT INTO primitive_observations (primitive_id, primitive_type, output_payload_json) VALUES (1, 'DIRECT_COUNTERPARTY', ?)", (primitive,))
        cur.execute("INSERT INTO normalized_evidence_provenance (evidence_id, provider_request_id) VALUES (1, 'prov-1')")
        conn.commit()
    finally:
        conn.close()
    return db


def test_h5_detects_additional_candidate_only_when_operation_fields_present(tmp_path):
    h4 = json.loads(_write_h4_artifact(tmp_path).read_text(encoding="utf-8"))
    db = _make_evidence_db(tmp_path / "op", include_operations=True)
    _make_evidence_db(tmp_path / "motif", include_operations=False)

    result = qualify_historical_source_expansion(h4_artifact=h4, evidence_root=str(tmp_path))
    assert result["status"] == "PASS"
    assert result["reconstructed_additional_operation_population_count"] >= 1
    assert any(row["operation_id"] == "historical-op-1" for row in result["expanded_populations"])
    verify_historical_source_expansion(result)


def test_h5_requires_h4_artifact_schema():
    with pytest.raises(Psi0hH5HistoricalSourceExpansionError, match="PSI0H_H5_H4_BINDING_INVALID"):
        qualify_historical_source_expansion(h4_artifact={"schema_version": "wrong", "discovered_populations": []}, evidence_root="/tmp")


def test_h5_runner_writes_artifact(tmp_path):
    h4_path = _write_h4_artifact(tmp_path)
    h4 = json.loads(h4_path.read_text(encoding="utf-8"))
    _make_evidence_db(tmp_path)

    from scripts.run_psi0h_h5_historical_source_expansion import run

    out = run(
        h4_artifact=str(h4_path),
        evidence_root=str(tmp_path),
        output=str(tmp_path / "h5.json"),
    )
    artifact = json.loads((tmp_path / "h5.json").read_text(encoding="utf-8"))
    assert out["artifact"] == str(tmp_path / "h5.json")
    assert artifact["artifact_digest"] == out["artifact_digest"]
    assert artifact["schema_version"] == "psi0h-h5.historical-source-expansion.v1"
    verify_historical_source_expansion(artifact)
