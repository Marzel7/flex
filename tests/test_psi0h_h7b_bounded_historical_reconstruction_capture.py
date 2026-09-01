import json
import sqlite3
from pathlib import Path

import pytest

from src.evidence.contracts.psi0h_h7b_bounded_historical_reconstruction_capture import (
    H7R_SCHEMA_VERSION,
    OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE,
    OUTCOME_RECONSTRUCTABLE_OPERATION_SOURCE,
    OUTCOME_REQUIRES_BOUNDED_HISTORICAL_BACKFILL,
    Psi0hH7BBoundedHistoricalReconstructionCaptureError,
    qualify_h7b_reconstruction_capture,
    verify_h7b_reconstruction_capture,
)


def _write_source_db(path: Path, *, include_operation: bool) -> None:
    conn = sqlite3.connect(path)
    c = conn.cursor()
    try:
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
                window_start INTEGER,
                window_end INTEGER,
                generated_at INTEGER,
                missing_inputs_json TEXT
            )
            """
        )
        payload = {
            "wallet": "w1",
            "source": "w2",
            "destination": "w3",
            "funder": "w4",
            "recipient": "w5",
            "roles": {"funder": "w4", "recipient": "w5"},
            "mechanism": "system-transfer",
            "event_types": ["SYSTEM_TRANSFER"],
            "window": {"start": 11, "end": 22},
        }
        if include_operation:
            payload["operation_id"] = "op-include"
        c.execute(
            "INSERT INTO normalized_evidence_records(evidence_id, payload_json, observed_at) VALUES (?,?,?)",
            (1, json.dumps(payload), 11),
        )
        prim = {
            "wallets": ["w1", "w2"],
            "source": "w2",
            "destination": "w3",
            "roles": {"funder": "w4", "recipient": "w5"},
            "mechanism": "system-transfer",
            "event_types": ["SYSTEM_TRANSFER"],
            "window": {"start": 11, "end": 22},
        }
        if include_operation:
            prim["operation_id"] = "op-include"
        c.execute(
            "INSERT INTO primitive_observations(primitive_id, primitive_type, output_payload_json, window_start, window_end, generated_at, missing_inputs_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (1, "BEHAVIOURAL_TIMING", json.dumps(prim), 11, 22, 33, json.dumps({"a": 1})),
        )
        conn.commit()
    finally:
        conn.close()


def _write_h7r_artifact(path: Path, source_path: Path, *, reconstructable: bool) -> Path:
    diag = {
        "source_path": str(source_path),
        "source_identity": {"size_bytes": source_path.stat().st_size, "inode": source_path.stat().st_ino, "device": 1, "mtime_ns": source_path.stat().st_mtime_ns},
        "row_reconstruction_ceiling": 100,
        "outcome": "REQUIRES_BOUNDED_HISTORICAL_BACKFILL",
        "missing_required_fields": ["operation_id"] if not reconstructable else [],
    }
    artifact = {
        "schema_version": H7R_SCHEMA_VERSION,
        "status": "PASS",
        "verdict": "PSI0H_H7R_HOLD_LEGACY_RECONCILIATION",
        "artifact_digest": "f" * 64,
        "diagnostics": [diag],
    }
    path.write_text(json.dumps(artifact, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def test_h7b_reconstructs_operation_boundary_and_writes_store(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    _write_source_db(source, include_operation=False)
    h7r = _write_h7r_artifact(tmp_path / "h7r.json", source, reconstructable=False)
    monkeypatch.setenv("PSI0H_H7B_RECONSTRUCTION_AUTHORIZED", "1")

    result = qualify_h7b_reconstruction_capture(
        h7r_artifact=json.loads(h7r.read_text(encoding="utf-8")),
        destination=tmp_path / "out",
    )
    verify_h7b_reconstruction_capture(result)
    assert result["reconstructable_source_count"] == 1
    assert result["reconstruction"]["classifications"][OUTCOME_RECONSTRUCTABLE_OPERATION_SOURCE] == 1
    assert result["source_plan"]["reconstructable_source_count"] == 1


def test_h7b_requires_authorization(tmp_path):
    source = tmp_path / "source.db"
    _write_source_db(source, include_operation=True)
    h7r = _write_h7r_artifact(tmp_path / "h7r.json", source, reconstructable=True)
    with pytest.raises(Psi0hH7BBoundedHistoricalReconstructionCaptureError, match="H7B_AUTHORIZATION_MISSING"):
        qualify_h7b_reconstruction_capture(
            h7r_artifact=json.loads(h7r.read_text(encoding="utf-8")),
            destination=tmp_path / "out",
        )


def test_h7b_holds_when_no_rows(tmp_path, monkeypatch):
    import sqlite3
    empty = tmp_path / "missing.db"
    conn = sqlite3.connect(empty)
    conn.close()
    h7r = _write_h7r_artifact(tmp_path / "h7r.json", empty, reconstructable=False)
    monkeypatch.setenv("PSI0H_H7B_RECONSTRUCTION_AUTHORIZED", "1")
    result = qualify_h7b_reconstruction_capture(
        h7r_artifact=json.loads(h7r.read_text(encoding="utf-8")),
        destination=tmp_path / "out",
    )
    assert result["status"] == "HOLD"
    assert result["reconstruction"]["classifications"][OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE] == 1


def test_h7b_runner_writes_artifact(tmp_path, monkeypatch):
    from scripts.run_psi0h_h7b_bounded_historical_reconstruction_capture import run

    source = tmp_path / "source.db"
    _write_source_db(source, include_operation=False)
    h7r = _write_h7r_artifact(tmp_path / "h7r.json", source, reconstructable=False)
    monkeypatch.setenv("PSI0H_H7B_RECONSTRUCTION_AUTHORIZED", "1")

    out = tmp_path / "out" / "capture.json"
    payload = run(h7r_artifact=str(h7r), output=str(out))
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert payload["artifact"] == str(out)
    assert artifact["schema_version"] == "psi0h-h7b.bounded-historical-reconstruction-capture.v1"
    verify_h7b_reconstruction_capture(artifact)
