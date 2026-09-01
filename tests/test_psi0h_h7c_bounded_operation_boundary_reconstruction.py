import json
import sqlite3
from pathlib import Path

import pytest

from src.evidence.contracts.psi0h_h7c_bounded_operation_boundary_reconstruction import (
    H7R_SCHEMA_VERSION,
    OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE,
    OUTCOME_RECONSTRUCTABLE_OPERATION_BOUNDARY,
    OUTCOME_REQUIRES_BOUNDED_OP_BOUNDARY_BACKFILL,
    Psi0hH7COperationBoundaryReconstructionError,
    verify_h7c_operation_boundary_reconstruction,
    qualify_h7c_operation_boundary_reconstruction,
)


def _write_source_db(path: Path, *, include_operation: bool) -> None:
    conn = sqlite3.connect(path)
    c = conn.cursor()
    try:
        c.execute(
            """
            CREATE TABLE normalized_evidence_records(
                evidence_id TEXT PRIMARY KEY,
                payload_json TEXT,
                observed_at INTEGER
            )
            """
        )
        c.execute(
            """
            CREATE TABLE primitive_observations(
                primitive_id TEXT PRIMARY KEY,
                primitive_type TEXT,
                output_payload_json TEXT,
                window_start INTEGER,
                window_end INTEGER,
                generated_at INTEGER,
                missing_inputs_json TEXT
            )
            """
        )

        evidence_payload = {
            "wallet": "w1",
            "funder": "w2",
            "recipient": "w3",
            "source": "pumpportal",
            "destination": "treasury",
            "roles": {"funder": "w2", "recipient": "w3"},
            "mechanism": "system-transfer",
            "event_types": ["SYSTEM_TRANSFER"],
            "window": {"start": 101, "end": 102},
            "event_time": 101,
        }
        if include_operation:
            evidence_payload["operation_id"] = "op-boundary"

        primitive_payload = {
            "wallet": "w1",
            "funder": "w2",
            "recipient": "w3",
            "source": "pumpportal",
            "destination": "treasury",
            "roles": {"funder": "w2", "recipient": "w3"},
            "mechanism": "system-transfer",
            "event_types": ["SYSTEM_TRANSFER"],
            "window": {"start": 101, "end": 102},
            "window_start": 101,
            "window_end": 102,
            "event_time": 101,
        }
        if include_operation:
            primitive_payload["operation_id"] = "op-boundary"

        c.execute(
            "INSERT INTO normalized_evidence_records(evidence_id,payload_json,observed_at) VALUES (?,?,?)",
            ("e1", json.dumps(evidence_payload), 101),
        )
        c.execute(
            "INSERT INTO primitive_observations(primitive_id,primitive_type,output_payload_json,window_start,window_end,generated_at,missing_inputs_json)"
            " VALUES (?,?,?,?,?,?,?)",
            ("p1", "BEHAVIOURAL_TIMING", json.dumps(primitive_payload), 101, 102, 110, json.dumps({"missing": []})),
        )
        conn.commit()
    finally:
        conn.close()


def _write_h7r_artifact(
    path: Path,
    source_path: Path,
    *,
    reconstructable: bool,
    missing_fields: list[str] | None = None,
) -> Path:
    diag = {
        "source_path": str(source_path),
        "source_identity": {
            "device": source_path.stat().st_dev,
            "inode": source_path.stat().st_ino,
            "mtime_ns": source_path.stat().st_mtime_ns,
            "size_bytes": source_path.stat().st_size,
        },
        "row_reconstruction_ceiling": 200,
        "outcome": "REQUIRES_BOUNDED_HISTORICAL_BACKFILL" if not reconstructable else "RECONSTRUCTABLE_OPERATION_SOURCE",
        "missing_required_fields": missing_fields or ["operation_id"] if not reconstructable else [],
        "blocking_reasons": missing_fields or [],
        "evidence_rows": 1,
        "primitive_rows": 1,
        "reconstructable": bool(reconstructable),
        "source_class": "LEGACY_CANDIDATE_ONLY",
        "source_scan_metrics": {
            "has_mechanism_fields": True,
            "has_topology_fields": True,
            "has_role_fields": True,
            "has_temporal_window": True,
            "has_event_type_field": True,
            "sampled_evidence_rows": 1,
            "sampled_primitive_rows": 1,
        },
    }

    artifact = {
        "schema_version": H7R_SCHEMA_VERSION,
        "status": "PASS",
        "verdict": "PSI0H_H7R_HOLD_LEGACY_RECONCILIATION",
        "artifact_digest": "1" * 64,
        "diagnostics": [diag],
    }
    path.write_text(json.dumps(artifact, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def test_h7c_classifies_reconstructable_with_existing_boundary_fields(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    _write_source_db(source, include_operation=True)
    h7r = _write_h7r_artifact(tmp_path / "h7r.json", source, reconstructable=True)
    monkeypatch.setenv("PSI0H_H7C_OPERATION_BOUNDARY_AUTHORIZED", "1")

    result = qualify_h7c_operation_boundary_reconstruction(
        h7r_artifact=json.loads(h7r.read_text(encoding="utf-8")),
        destination=tmp_path / "out",
    )

    verify_h7c_operation_boundary_reconstruction(result)
    assert result["status"] == "PASS"
    assert result["reconstructable_source_count"] == 1
    assert result["reconstruction"]["classifications"][OUTCOME_RECONSTRUCTABLE_OPERATION_BOUNDARY] == 1


def test_h7c_holds_when_missing_rows(tmp_path, monkeypatch):
    source = tmp_path / "missing.db"
    source.write_bytes(b"")
    h7r = _write_h7r_artifact(
        tmp_path / "h7r.json",
        source,
        reconstructable=False,
        missing_fields=["operation_id", "event_window"],
    )
    monkeypatch.setenv("PSI0H_H7C_OPERATION_BOUNDARY_AUTHORIZED", "1")

    result = qualify_h7c_operation_boundary_reconstruction(
        h7r_artifact=json.loads(h7r.read_text(encoding="utf-8")),
        destination=tmp_path / "out",
    )

    assert result["status"] == "HOLD"
    assert result["reconstruction"]["classifications"][OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE] == 1


def test_h7c_requires_authorization(tmp_path):
    source = tmp_path / "source.db"
    _write_source_db(source, include_operation=True)
    h7r = _write_h7r_artifact(tmp_path / "h7r.json", source, reconstructable=True)
    with pytest.raises(Psi0hH7COperationBoundaryReconstructionError, match="H7C_AUTHORIZATION_MISSING"):
        qualify_h7c_operation_boundary_reconstruction(
            h7r_artifact=json.loads(h7r.read_text(encoding="utf-8")),
            destination=tmp_path / "out",
        )


def test_h7c_runner_writes_artifact(tmp_path, monkeypatch):
    from scripts.run_psi0h_h7c_bounded_operation_boundary_reconstruction import run

    source = tmp_path / "source.db"
    _write_source_db(source, include_operation=True)
    h7r = _write_h7r_artifact(tmp_path / "h7r.json", source, reconstructable=True)
    monkeypatch.setenv("PSI0H_H7C_OPERATION_BOUNDARY_AUTHORIZED", "1")

    output = tmp_path / "out" / "h7c.json"
    payload = run(h7r_artifact=str(h7r), output=str(output))
    artifact = json.loads(output.read_text(encoding="utf-8"))

    assert payload["artifact"] == str(output)
    assert artifact["schema_version"] == "psi0h-h7c.operation-boundary-reconstruction.v1"
    verify_h7c_operation_boundary_reconstruction(artifact)
