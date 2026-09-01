import json
import sqlite3

import pytest

from src.evidence.contracts.psi0h_h9_historical_backfill_blocker_reconciliation import (
    BLOCKER_H7_SELECTED_NON_RECONSTRUCTABLE,
    BLOCKER_ROW_BUDGET_PREVENTED_PRIMITIVE_DERIVATION,
    H7_SCHEMA_VERSION,
    H8_SCHEMA_VERSION,
    Psi0hH9HistoricalBackfillBlockerReconciliationError,
    qualify_h9_backfill_blocker_reconciliation,
    verify_h9_blocker_reconciliation,
)


def _base_h7() -> dict:
    return {
        "schema_version": H7_SCHEMA_VERSION,
        "status": "PASS",
        "verdict": "READY_H7_BOUND_PLAN",
        "artifact_digest": "f" * 64,
        "boundaries": {
            "max_sources": 10,
            "max_rows_per_source": 200,
            "max_total_rows": 1400,
            "max_reconstruction_windows": 7,
            "max_event_gap_seconds": 86400,
        },
        "destination": {
            "destination_root": "/tmp/x",
            "isolation_required": True,
            "candidate_paths_are_frozen": True,
        },
        "selection": {
            "h6_artifact_digest": "a" * 64,
            "h6_status": "PASS",
            "h6_verdict": "READY_BOUNDED_BACKFILL",
        },
        "source_plan": {
            "candidate_sources": [
                {
                    "source_path": "/tmp/fake-a.db",
                    "source_identity": {},
                    "row_reconstruction_ceiling": 2,
                    "reconstructable": False,
                    "blocking_reasons": ["NO_STABLE_OPERATION_BOUNDARY", "ADDRESS_LEVEL_MOTIFS_ONLY"],
                }
            ]
        },
    }


def _base_h8() -> dict:
    return {
        "schema_version": H8_SCHEMA_VERSION,
        "milestone": "PSI0H-H8",
        "status": "PASS",
        "execution_status": "COMPLETED",
        "execution": {
            "evidence_rows": [
                {"source_path": "/tmp/fake-a.db", "evidence_id": "e1", "event_time": 1},
                {"source_path": "/tmp/fake-a.db", "evidence_id": "e2", "event_time": 2},
            ],
            "primitive_rows": [],
            "primitive_count": 0,
            "evidence_count": 2,
            "selection": [],
            "source_snapshots": [],
            "blockers": [],
            "source_identity_drifts": [],
        },
        "h7_artifact": "docs/x/h7.json",
        "h7_binding": {
            "h7_artifact_digest": "a" * 64,
        },
    }


def _write_db(path: str) -> None:
    conn = sqlite3.connect(path)
    try:
        c = conn.cursor()
        c.execute(
            "CREATE TABLE normalized_evidence_records(evidence_id INTEGER PRIMARY KEY, payload_json TEXT, observed_at INTEGER)"
        )
        c.execute("CREATE TABLE primitive_observations(primitive_id INTEGER PRIMARY KEY, output_payload_json TEXT)")
        c.executemany(
            "INSERT INTO normalized_evidence_records(evidence_id, payload_json, observed_at) VALUES (?, ?, ?)",
            [
                (1, '{"wallet": "w1"}', 1),
                (2, '{"wallet": "w2"}', 2),
            ],
        )
        c.execute(
            "INSERT INTO primitive_observations(primitive_id, output_payload_json) VALUES (?, ?)",
            (1, json.dumps({"operation_id": "op1", "wallets": ["w1"]})),
        )
        conn.commit()
    finally:
        conn.close()


def test_h9_holds_and_classifies_row_budget_blocker(tmp_path):
    fake = tmp_path / "db.sqlite"
    _write_db(str(fake))

    h7 = _base_h7()
    h7["source_plan"]["candidate_sources"][0]["source_path"] = str(fake)

    h8 = _base_h8()
    h8["execution"]["primitive_rows"] = []
    h8["execution"]["evidence_rows"] = [
        {"source_path": str(fake), "evidence_id": "e1", "event_time": 1},
        {"source_path": str(fake), "evidence_id": "e2", "event_time": 2},
    ]

    result = qualify_h9_backfill_blocker_reconciliation(h7_artifact=h7, h8_artifact=h8)
    assert result["status"] == "HOLD"
    assert result["verdict"] == "PSI0H_H9_BLOCKERS_IDENTIFIED"
    assert BLOCKER_ROW_BUDGET_PREVENTED_PRIMITIVE_DERIVATION in result["blockers"]
    assert BLOCKER_H7_SELECTED_NON_RECONSTRUCTABLE in result["blockers"]
    verify_h9_blocker_reconciliation(result)


def test_h9_accepts_non_empty_primitive_pool(tmp_path):
    fake = tmp_path / "db.sqlite"
    _write_db(str(fake))

    h7 = _base_h7()
    h7["source_plan"]["candidate_sources"][0]["source_path"] = str(fake)

    h8 = _base_h8()
    h8["execution"]["primitive_rows"] = [
        {
            "source_path": str(fake),
            "primitive_id": "p-1",
            "primitive_type": "SYSTEM_TRANSFER",
            "window_start": 1,
            "window_end": 1,
            "generated_at": 2,
        }
    ]
    h8["execution"]["primitive_count"] = 1

    result = qualify_h9_backfill_blocker_reconciliation(h7_artifact=h7, h8_artifact=h8)
    assert result["status"] == "PASS"
    assert result["verdict"] == "PSI0H_H9_NO_ACTION_NEEDED"
    assert result["h8_primitive_rows"] == 1
    verify_h9_blocker_reconciliation(result)


def test_h9_requires_valid_artifact_versions():
    with pytest.raises(Psi0hH9HistoricalBackfillBlockerReconciliationError, match="PSI0H_H9_H7_SCHEMA_MISMATCH"):
        qualify_h9_backfill_blocker_reconciliation(
            h7_artifact={"schema_version": "bad"},
            h8_artifact={"schema_version": H8_SCHEMA_VERSION, "status": "PASS", "execution": {"evidence_rows": [], "primitive_rows": []}},
        )


def test_h9_runner_writes_artifact(tmp_path):
    from scripts.run_psi0h_h9_historical_backfill_blocker_reconciliation import run

    h7_path = tmp_path / "h7.json"
    h8_path = tmp_path / "h8.json"
    out_path = tmp_path / "out.json"
    h7 = _base_h7()
    h8 = _base_h8()

    h7_path.write_text(json.dumps(h7), encoding="utf-8")
    h8_path.write_text(json.dumps(h8), encoding="utf-8")

    result = run(h7_artifact=str(h7_path), h8_artifact=str(h8_path), output=str(out_path))
    artifact = json.loads(out_path.read_text(encoding="utf-8"))
    assert artifact["artifact_digest"] == result["artifact_digest"]
    assert artifact["status"] == result["status"]
