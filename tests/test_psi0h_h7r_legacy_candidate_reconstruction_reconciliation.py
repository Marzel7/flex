import json
import sqlite3
from pathlib import Path

import pytest

from src.evidence.contracts.psi0h_h7r_legacy_candidate_reconstruction_reconciliation import (
    H7_SCHEMA_VERSION,
    OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE,
    OUTCOME_RECONSTRUCTABLE_AFTER_LOCAL_REBIND,
    OUTCOME_REQUIRES_BOUNDED_HISTORICAL_BACKFILL,
    Psi0hH7RLegacyCandidateReconstructionReconciliationError,
    qualify_legacy_candidate_reconstruction_reconciliation,
    verify_h7r_reconciliation,
)


def _write_source_db(
    path: Path,
    *,
    include_operation_fields: bool,
) -> None:
    conn = sqlite3.connect(path)
    try:
        c = conn.cursor()
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
                subjects_json TEXT,
                parameters_json TEXT,
                window_start INTEGER,
                window_end INTEGER,
                output_payload_json TEXT
            )
            """
        )
        c.execute(
            "INSERT INTO normalized_evidence_records(evidence_id,payload_json,observed_at) VALUES (?,?,?)",
            (
                "e1",
                json.dumps(
                    {
                        "wallet": "w1",
                        "funder": "w2",
                        "creator": "w3",
                        "recipient": "w4",
                        "source": "S",
                        "destination": "D",
                        "mechanism": "m",
                        "roles": {"funder": "w2"},
                        "window_start": 101,
                        "window_end": 101,
                        "event_time": 101,
                        **({"operation_id": "op-1"} if include_operation_fields else {}),
                    }
                ),
                101,
            ),
        )
        c.execute(
            "INSERT INTO primitive_observations(primitive_id,primitive_type,subjects_json,parameters_json,window_start,window_end,output_payload_json) VALUES (?,?,?,?,?,?,?)",
            (
                "p1",
                "BEHAVIOURAL_TIMING",
                json.dumps(["w1", "w2"]),
                json.dumps({"mechanism": "m"}),
                101,
                101,
                json.dumps(
                    {
                        **({"operation_id": "op-1"} if include_operation_fields else {}),
                        "event_types": ["SYSTEM_TRANSFER"],
                        "window": {"start": 101, "end": 101},
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _write_h7_artifact(path: Path, rows: list[dict]) -> Path:
    artifact = {
        "schema_version": H7_SCHEMA_VERSION,
        "status": "PASS",
        "verdict": "READY_H7_BOUND_PLAN",
        "artifact_digest": "f" * 64,
        "source_plan": {"candidate_sources": [], "legacy_candidate_sources": rows},
    }
    path.write_text(json.dumps(artifact, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def test_h7r_classifies_reconstructable_after_rebind(tmp_path):
    source = tmp_path / "op.db"
    _write_source_db(source, include_operation_fields=True)

    h7 = _write_h7_artifact(
        tmp_path / "h7.json",
        [
            {
                "source_path": str(source),
                "source_identity": {"size_bytes": source.stat().st_size},
                "evidence_rows": 1,
                "primitive_rows": 1,
                "row_reconstruction_ceiling": 10,
                "blocking_reasons": [],
                "source_class": "LEGACY_CANDIDATE_ONLY",
                "reconstructable": False,
            }
        ],
    )

    result = qualify_legacy_candidate_reconstruction_reconciliation(h7_artifact=json.loads(h7.read_text(encoding="utf-8")))
    assert result["status"] == "PASS"
    assert result["classifications"][OUTCOME_RECONSTRUCTABLE_AFTER_LOCAL_REBIND] == 1
    assert result["classifications"][OUTCOME_REQUIRES_BOUNDED_HISTORICAL_BACKFILL] == 0
    assert result["classifications"][OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE] == 0
    assert result["diagnostics"][0]["outcome"] == OUTCOME_RECONSTRUCTABLE_AFTER_LOCAL_REBIND
    verify_h7r_reconciliation(result)


def test_h7r_classifies_requires_backfill_and_not_constructable(tmp_path):
    source = tmp_path / "op.db"
    _write_source_db(source, include_operation_fields=False)

    h7 = _write_h7_artifact(
        tmp_path / "h7.json",
        [
            {
                "source_path": str(source),
                "source_identity": {"size_bytes": source.stat().st_size},
                "evidence_rows": 1,
                "primitive_rows": 0,
                "row_reconstruction_ceiling": 10,
                "blocking_reasons": ["ADDRESS_LEVEL_MOTIFS_ONLY"],
                "source_class": "LEGACY_CANDIDATE_ONLY",
                "reconstructable": False,
            }
        ],
    )

    result = qualify_legacy_candidate_reconstruction_reconciliation(h7_artifact=json.loads(h7.read_text(encoding="utf-8")))
    assert result["classifications"][OUTCOME_REQUIRES_BOUNDED_HISTORICAL_BACKFILL] == 1
    assert result["classifications"][OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE] == 0
    assert result["diagnostics"][0]["outcome"] == OUTCOME_REQUIRES_BOUNDED_HISTORICAL_BACKFILL
    verify_h7r_reconciliation(result)


def test_h7r_classifies_candidate_only_for_missing_data(tmp_path):
    h7 = _write_h7_artifact(
        tmp_path / "h7.json",
        [
            {
                "source_path": str(tmp_path / "missing.db"),
                "source_identity": {"size_bytes": 4},
                "evidence_rows": 0,
                "primitive_rows": 0,
                "row_reconstruction_ceiling": 0,
                "blocking_reasons": ["NO_STABLE_OPERATION_BOUNDARY"],
                "source_class": "LEGACY_CANDIDATE_ONLY",
                "reconstructable": False,
            }
        ],
    )
    result = qualify_legacy_candidate_reconstruction_reconciliation(h7_artifact=json.loads(h7.read_text(encoding="utf-8")))
    assert result["status"] == "HOLD"
    assert result["classifications"][OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE] == 1
    assert result["diagnostics"][0]["outcome"] == OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE
    verify_h7r_reconciliation(result)


def test_h7r_runner_writes_artifact(tmp_path):
    from scripts.run_psi0h_h7r_legacy_candidate_reconstruction_reconciliation import run

    source = tmp_path / "op.db"
    _write_source_db(source, include_operation_fields=False)
    h7_path = _write_h7_artifact(
        tmp_path / "h7.json",
        [
            {
                "source_path": str(source),
                "source_identity": {"size_bytes": source.stat().st_size},
                "evidence_rows": 1,
                "primitive_rows": 0,
                "row_reconstruction_ceiling": 10,
                "blocking_reasons": ["ADDRESS_LEVEL_MOTIFS_ONLY"],
                "source_class": "LEGACY_CANDIDATE_ONLY",
                "reconstructable": False,
            }
        ],
    )
    output = tmp_path / "out.json"
    payload = run(h7_artifact=str(h7_path), output=str(output))

    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["artifact_digest"] == payload["artifact_digest"]
    assert artifact["schema_version"] == "psi0h-h7r.legacy-candidate-reconstruction-reconciliation.v1"
    verify_h7r_reconciliation(artifact)


def test_h7r_requires_valid_h7_schema():
    with pytest.raises(Psi0hH7RLegacyCandidateReconstructionReconciliationError, match="PSI0H_H7R_H7_SCHEMA_MISMATCH"):
        qualify_legacy_candidate_reconstruction_reconciliation(
            h7_artifact={"schema_version": "bad", "status": "PASS", "artifact_digest": "x" * 64, "source_plan": {"legacy_candidate_sources": []}}
        )
