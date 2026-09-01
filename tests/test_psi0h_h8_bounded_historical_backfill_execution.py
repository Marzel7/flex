import json
import sqlite3
from pathlib import Path

import pytest

from src.evidence.contracts.psi0h_h8_bounded_historical_backfill_execution import (
    H7_SCHEMA_VERSION,
    H7_VERDICT,
    Psi0hH8BoundedHistoricalBackfillExecutionError,
    qualify_h8_backfill_execution,
    verify_h8_backfill_execution,
)


def _write_sqlite_source(path: Path, *, include_evidence: bool = True, include_primitive: bool = True) -> None:
    conn = sqlite3.connect(path)
    try:
        c = conn.cursor()
        if include_evidence:
            c.execute(
                """
                CREATE TABLE normalized_evidence_records(
                    evidence_id INTEGER PRIMARY KEY,
                    observed_at INTEGER,
                    payload_json TEXT
                )
                """
            )
            c.execute(
                "INSERT INTO normalized_evidence_records (evidence_id, observed_at, payload_json) VALUES (?, ?, ?)",
                (1, 100, json.dumps({"operation_id": "op-1", "fact_family": "funding", "wallet": "w1"})),
            )
        if include_primitive:
            c.execute(
                """
                CREATE TABLE primitive_observations(
                    primitive_id INTEGER PRIMARY KEY,
                    primitive_type TEXT,
                    window_start INTEGER,
                    window_end INTEGER,
                    generated_at INTEGER,
                    missing_inputs_json TEXT,
                    output_payload_json TEXT
                )
                """
            )
            c.execute(
                "INSERT INTO primitive_observations (primitive_id, primitive_type, window_start, window_end, generated_at, missing_inputs_json, output_payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    1,
                    "SYSTEM_TRANSFER",
                    100,
                    120,
                    130,
                    json.dumps({"f": 1}),
                    json.dumps({"operation_id": "op-1", "wallets": ["w1", "w2"]}),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _write_h7_artifact(tmp_path: Path, *, source_path: str, max_rows: int = 500, with_source: bool = True) -> Path:
    artifact = {
        "schema_version": H7_SCHEMA_VERSION,
        "status": "PASS",
        "verdict": H7_VERDICT,
        "artifact_digest": "f" * 64,
        "boundaries": {
            "max_sources": 2,
            "max_rows_per_source": max_rows,
            "max_total_rows": max_rows,
            "max_reconstruction_windows": 2,
        },
        "destination": {
            "destination_root": str(tmp_path / "psi0h_h8"),
            "isolation_required": True,
            "candidate_paths_are_frozen": True,
        },
        "selection": {},
        "source_plan": {
            "candidate_sources": []
        },
    }

    if with_source:
        artifact["source_plan"]["candidate_sources"] = [
            {
                "source_path": source_path,
                "source_identity": {},
                "row_reconstruction_ceiling": 10,
                "reconstructable": True,
            }
        ]

    destination = Path(tmp_path / "psi0h_h8")
    destination.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "h7.json"
    path.write_text(json.dumps(artifact, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def test_h8_holds_empty_when_no_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("PSI0H_H8_REAL_BACKFILL_AUTHORIZED", "1")
    path = _write_h7_artifact(tmp_path, source_path=str(tmp_path / "missing.db"), with_source=False)
    output_path = tmp_path / "psi0h_h8" / "out.json"

    result = qualify_h8_backfill_execution(
        h7_artifact_path=path, output_artifact_path=output_path
    )
    assert result["execution_status"] == "HALT_NO_CANDIDATES"
    assert result["status"] == "PASS"
    assert result["execution"]["blockers"] == ["H7_SOURCE_PLAN_EMPTY"]


def test_h8_collects_rows_and_verifies(tmp_path, monkeypatch):
    monkeypatch.setenv("PSI0H_H8_REAL_BACKFILL_AUTHORIZED", "1")
    db_path = tmp_path / "hist.db"
    _write_sqlite_source(db_path)
    h7_path = _write_h7_artifact(tmp_path, source_path=str(db_path), max_rows=10)

    out = tmp_path / "psi0h_h8" / "h8.json"
    result = qualify_h8_backfill_execution(h7_artifact_path=h7_path, output_artifact_path=out)
    verify_h8_backfill_execution(result)

    assert result["status"] == "PASS"
    assert result["execution"]["evidence_count"] == 1
    assert result["execution"]["primitive_count"] == 1
    assert result["execution"]["selected_source_count"] == 1
    assert out == Path(result["output_digest_bindings"]["artifact_path"])


def test_h8_rejects_without_auth(tmp_path):
    db_path = tmp_path / "hist.db"
    _write_sqlite_source(db_path)
    h7_path = _write_h7_artifact(tmp_path, source_path=str(db_path))

    with pytest.raises(Psi0hH8BoundedHistoricalBackfillExecutionError, match="PSI0H_H8_AUTHORIZATION_MISSING"):
        qualify_h8_backfill_execution(h7_artifact_path=h7_path, output_artifact_path=tmp_path / "h8.json")


def test_h8_runner_writes_artifact(tmp_path, monkeypatch):
    from scripts.run_psi0h_h8_bounded_historical_backfill_execution import run

    db_path = tmp_path / "hist.db"
    _write_sqlite_source(db_path)
    h7_path = _write_h7_artifact(tmp_path, source_path=str(db_path))
    monkeypatch.setenv("PSI0H_H8_REAL_BACKFILL_AUTHORIZED", "1")

    output_path = tmp_path / "psi0h_h8" / "h8_out.json"
    payload = run(h7_artifact=str(h7_path), output=str(output_path))
    artifact = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == str(output_path)
    assert artifact["artifact_digest"] == payload["artifact_digest"]
    assert artifact["schema_version"] == "psi0h-h8.bounded-historical-backfill-execution.v1"
    verify_h8_backfill_execution(artifact)
