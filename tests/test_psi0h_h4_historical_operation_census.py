import json
import sqlite3
from pathlib import Path

import pytest

from src.evidence.contracts.psi0h_h4_historical_operation_census import (
    Psi0hH4HistoricalOperationCensusError,
    qualify_historical_operation_census,
    verify_historical_operation_census,
)


def _make_manifest(path: Path) -> Path:
    payload = {
        "schema_version": "1.0.0",
        "milestone": "PSI0G-B",
        "status": "PASS",
        "run_id": "psi0h-h4-test-run",
        "files": {},
        "operations": [
            {
                "operation_key": "watchtower",
                "source": {"path": "unused"},
            },
            {
                "operation_key": "three_sw2",
                "source": {"path": "unused"},
            },
        ],
    }
    manifest_path = path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    return manifest_path


def _make_runtime_db(path: Path) -> Path:
    db_path = path / "operation-runtime.db"
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE behaviour_observations ("
            "output_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, contract_version TEXT, "
            "producer_version TEXT, input_digest TEXT, payload_json TEXT, payload_digest TEXT, generated_at INTEGER)"
        )
        cur.execute(
            "CREATE TABLE detector_inputs ("
            "output_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, contract_version TEXT, "
            "producer_version TEXT, input_digest TEXT, payload_json TEXT, payload_digest TEXT, generated_at INTEGER)"
        )
        cur.execute(
            "CREATE TABLE detector_results ("
            "output_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, contract_version TEXT, "
            "producer_version TEXT, input_digest TEXT, payload_json TEXT, payload_digest TEXT, generated_at INTEGER)"
        )
        cur.execute(
            "CREATE TABLE topology_revisions ("
            "output_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, contract_version TEXT, "
            "producer_version TEXT, input_digest TEXT, payload_json TEXT, payload_digest TEXT, generated_at INTEGER)"
        )
        cur.execute(
            "CREATE TABLE operation_contract_versions ("
            "contract_id TEXT NOT NULL, contract_version TEXT, contract_digest TEXT, payload_json TEXT, registered_at INTEGER)"
        )
        cur.execute(
            "CREATE TABLE operation_runtime_references ("
            "output_type TEXT, output_id TEXT, reference_type TEXT, reference_id TEXT)"
        )

        cur.executemany(
            "INSERT INTO behaviour_observations VALUES (?,?,?,?,?,?,?,?)",
            [
                ("bo-watchtower", "watchtower", "1.0.0", "test", "ip", json.dumps({"subjects": ["a", "b"]}), "pd", 10),
                ("bo-three", "three_sw2", "1.0.0", "test", "ip", json.dumps({"subjects": ["c"]}), "pd", 20),
            ],
        )
        cur.executemany(
            "INSERT INTO detector_inputs VALUES (?,?,?,?,?,?,?,?)",
            [
                ("di-watchtower", "watchtower", "1.0.0", "test", "ip", json.dumps({}), "pd", 11),
            ],
        )
        cur.executemany(
            "INSERT INTO detector_results VALUES (?,?,?,?,?,?,?,?)",
            [
                ("dr-watchtower", "watchtower", "1.0.0", "test", "ip", json.dumps({}), "pd", 12),
            ],
        )
        cur.executemany(
            "INSERT INTO topology_revisions VALUES (?,?,?,?,?,?,?,?)",
            [
                ("top-watchtower", "watchtower", "1.0.0", "test", "ip", json.dumps({"topology_revision_id":"tr1"}), "pd", 13),
                ("top-three", "three_sw2", "1.0.0", "test", "ip", json.dumps({"topology_revision_id":"tr2"}), "pd", 14),
            ],
        )
        cur.executemany(
            "INSERT INTO operation_contract_versions VALUES (?,?,?,?,?)",
            [
                ("watchtower", "1.0.0", "wcon", json.dumps({"subject_count": 2, "contract_version": "1.0.0"}), 30),
                ("three_sw2", "1.0.0", "tcon", json.dumps({"subject_count": 1, "contract_version": "1.0.0"}), 40),
            ],
        )
        cur.executemany(
            "INSERT INTO operation_runtime_references VALUES (?,?,?,?)",
            [
                ("BehaviourObservation", "bo-watchtower", "evidence_refs", "ev1"),
                ("DetectorInput", "di-watchtower", "primitive_refs", "pr1"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    return db_path


def _fixture_h1_artifact(manifest_path: Path) -> Path:
    payload = {
        "artifact_digest": "a" * 64,
        "manifest_source_path": str(manifest_path),
        "manifest_digest": "b" * 64,
        "eligible_operations": [],
        "status": "PASS",
    }
    path = manifest_path.parent / "h1_artifact.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_h4_discovers_populations(tmp_path):
    manifest_path = _make_manifest(tmp_path)
    _make_runtime_db(tmp_path)
    h1_path = _fixture_h1_artifact(manifest_path)
    h1_payload = json.loads(h1_path.read_text(encoding="utf-8"))
    h1_payload["eligible_operations"] = [{"operation_id": "watchtower"}, {"operation_id": "three_sw2"}]
    h1_path.write_text(json.dumps(h1_payload), encoding="utf-8")

    result = qualify_historical_operation_census(h1_artifact=h1_payload, manifest_path=str(manifest_path))
    assert result["status"] == "PASS"
    assert result["operation_count"] == 2
    assert result["discovered_populations"][0]["same_operation_claim"] is False
    assert result["discovered_populations"][0]["same_human_claim"] is False
    verify_historical_operation_census(result)


def test_h4_rejects_missing_h1_binding():
    with pytest.raises(Psi0hH4HistoricalOperationCensusError, match="PSI0H_H4_MANIFEST_PATH_MISSING"):
        qualify_historical_operation_census(h1_artifact={})


def test_h4_runner_writes_artifact(tmp_path):
    manifest_path = _make_manifest(tmp_path)
    _make_runtime_db(tmp_path)
    h1_path = _fixture_h1_artifact(manifest_path)
    payload = json.loads(h1_path.read_text(encoding="utf-8"))
    payload["eligible_operations"] = [{"operation_id": "watchtower"}]
    h1_path.write_text(json.dumps(payload), encoding="utf-8")

    from scripts.run_psi0h_h4_historical_operation_census import run

    out = run(h1_artifact=str(h1_path), manifest=str(manifest_path), output=str(tmp_path / "h4.json"))
    output = json.loads((tmp_path / "h4.json").read_text(encoding="utf-8"))
    assert out["artifact"] == str(tmp_path / "h4.json")
    assert output["artifact_digest"] == out["artifact_digest"]
    verify_historical_operation_census(output)
