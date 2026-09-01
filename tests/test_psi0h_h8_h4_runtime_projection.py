import json
import sqlite3
from pathlib import Path

from src.evidence.contracts.psi0h_h8_to_h4_runtime_projection import (
    SCHEMA_VERSION,
    Psi0hH8ToH4RuntimeProjectionError,
    project_h8_to_h4_runtime,
    verify_projection_manifest,
)


def _h8_payload() -> dict:
    return {
        "schema_version": "psi0h-h8.bounded-historical-backfill-execution.v1",
        "status": "PASS",
        "artifact_digest": "a" * 64,
        "execution": {
            "evidence_rows": [
                {
                    "evidence_id": "e1",
                    "operation_id": "op-A",
                    "event_time": 100,
                    "payload": {
                        "operation_id": "op-A",
                        "mechanism": "LAUNCH_LINKAGE",
                        "event_types": ["migration_launch"],
                        "wallet": "w1",
                        "wallets": ["w1", "w2"],
                        "roles": {"creator": "w1"},
                        "window": {"start": 100, "end": 200},
                    },
                },
            ],
            "primitive_rows": [
                {
                    "primitive_id": "p1",
                    "operation_id": "op-A",
                    "event_time": 101,
                    "payload": {
                        "operation_id": "op-A",
                        "mechanism": "LAUNCH_LINKAGE",
                        "event_types": ["migration_launch"],
                        "wallet": "w1",
                        "destination": "w3",
                        "roles": {"recipient": "w3"},
                    },
                }
            ],
        },
    }


def _run_projection(tmp_path: Path) -> dict:
    h8_payload = _h8_payload()
    manifest_path = tmp_path / "projection_manifest.json"
    runtime_db_path = tmp_path / "runtime.db"
    manifest, result = project_h8_to_h4_runtime(
        h8_artifact=h8_payload,
        runtime_db_path=runtime_db_path,
        manifest_path=manifest_path,
    )
    verify_projection_manifest(manifest)
    assert manifest_path.exists()
    assert runtime_db_path.exists()
    assert result["runtime_db_path"] == str(runtime_db_path)
    return manifest


def test_projection_schema_and_determinism(tmp_path: Path):
    first = _run_projection(tmp_path / "run1")
    second = _run_projection(tmp_path / "run2")

    assert first["schema_version"] == second["schema_version"] == "1.0.0"
    assert first["run_id"] == second["run_id"]
    assert first["manifest_digest"] == second["manifest_digest"]


def test_projection_preserves_lineage_and_subjects(tmp_path: Path):
    manifest = _run_projection(tmp_path)
    runtime_path = Path(manifest["files"]["operation-runtime.db"]["path"])
    conn = sqlite3.connect(f"file:{runtime_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        counts = {
            "behaviour": conn.execute("SELECT COUNT(*) FROM behaviour_observations").fetchone()[0],
            "detector_inputs": conn.execute("SELECT COUNT(*) FROM detector_inputs").fetchone()[0],
            "references": conn.execute("SELECT COUNT(*) FROM operation_runtime_references").fetchone()[0],
        }
        assert counts["behaviour"] == 1
        assert counts["detector_inputs"] == 1
        assert counts["references"] >= 2

        behaviour = json.loads(conn.execute("SELECT payload_json FROM behaviour_observations ORDER BY output_id").fetchone()[0])
        assert behaviour["local_role"] in {"w1", ""}
        assert behaviour["subjects"] == ["w1", "w2"]
        detector = json.loads(conn.execute("SELECT payload_json FROM detector_inputs ORDER BY output_id").fetchone()[0])
        assert detector["operation_id"] == "op-A"
        assert "e1" in detector["evidence_refs"]
        assert detector["evidence_refs"]  # continuity references should be present for deterministic replay
    finally:
        conn.close()


def test_projection_preserves_missingness_without_inventing_rows(tmp_path: Path):
    payload = _h8_payload()
    payload["execution"]["evidence_rows"].append(
        {
            "evidence_id": "e2",
            "event_time": 1,
            "payload": {"wallet": "w9"},
        }
    )
    manifest_path = tmp_path / "manifest.json"
    runtime_db_path = tmp_path / "runtime.db"

    manifest, _ = project_h8_to_h4_runtime(
        h8_artifact=payload,
        runtime_db_path=runtime_db_path,
        manifest_path=manifest_path,
    )
    verify_projection_manifest(manifest)

    run = manifest["run"]
    assert run["dropped_rows"]["missing_contract_id"] == 1
    assert run["kept_evidence_rows"] == 1
    assert run["kept_primitive_rows"] == 1
    assert manifest["projection_schema"] == SCHEMA_VERSION


def test_projection_writes_continuity_fingerprint_refs(tmp_path: Path):
    payload = _h8_payload()
    payload["execution"]["primitive_rows"][0]["event_time"] = 0
    manifest, _ = project_h8_to_h4_runtime(
        h8_artifact=payload,
        runtime_db_path=tmp_path / "runtime.db",
        manifest_path=tmp_path / "manifest.json",
    )
    verify_projection_manifest(manifest)

    runtime_path = Path(manifest["files"]["operation-runtime.db"]["path"])
    conn = sqlite3.connect(f"file:{runtime_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        behaviour = json.loads(conn.execute("SELECT payload_json FROM behaviour_observations").fetchone()[0])
        detector = json.loads(conn.execute("SELECT payload_json FROM detector_inputs").fetchone()[0])
        assert any("ps0h-h8.continuity." in ref for ref in behaviour["evidence_refs"])
        assert any("ps0h-h8.continuity." in ref for ref in detector["evidence_refs"])
    finally:
        conn.close()


def test_projection_continuity_ids_are_replay_deterministic(tmp_path: Path):
    manifest_one = _run_projection(tmp_path / "run1")
    manifest_two = _run_projection(tmp_path / "run2")

    assert manifest_one["run"]["kept_primitive_rows"] == manifest_two["run"]["kept_primitive_rows"] == 1
    assert manifest_one["run"]["kept_evidence_rows"] == manifest_two["run"]["kept_evidence_rows"] == 1

    with sqlite3.connect(f"file:{manifest_one['files']['operation-runtime.db']['path']}?mode=ro", uri=True) as one_conn:
        one_conn.row_factory = sqlite3.Row
        one_refs = [
            json.loads(r["payload_json"])["evidence_refs"]
            for r in one_conn.execute("SELECT payload_json FROM behaviour_observations UNION ALL SELECT payload_json FROM detector_inputs")
        ]
    with sqlite3.connect(f"file:{manifest_two['files']['operation-runtime.db']['path']}?mode=ro", uri=True) as two_conn:
        two_conn.row_factory = sqlite3.Row
        two_refs = [
            json.loads(r["payload_json"])["evidence_refs"]
            for r in two_conn.execute("SELECT payload_json FROM behaviour_observations UNION ALL SELECT payload_json FROM detector_inputs")
        ]
    assert sorted(one_refs) == sorted(two_refs)


def test_projection_detects_tampered_manifest_digest(tmp_path: Path):
    manifest = _run_projection(tmp_path)
    tampered = dict(manifest)
    tampered["source"] = {"path": "tampered"}
    try:
        verify_projection_manifest(tampered)
    except Exception as exc:
        assert "MANIFEST_DIGEST_MISMATCH" in str(exc)
    else:
        raise AssertionError("tampered manifest should fail digest check")


def test_projection_rejects_invalid_h8_artifact():
    try:
        project_h8_to_h4_runtime(h8_artifact={}, runtime_db_path="tmp.db")
    except Psi0hH8ToH4RuntimeProjectionError as exc:
        assert "H8_SCHEMA_MISMATCH" in str(exc)
    else:
        raise AssertionError("invalid H8 artifact should fail")
