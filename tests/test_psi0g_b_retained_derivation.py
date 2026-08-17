from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from scripts.run_psi0g_b_retained_derivation import (
    ROOT, derive_corpus, load_primitives_query_only, sha256_file,
)
from src.evidence.operation_contracts.watchtower_v1 import register_watchtower_v1
from src.evidence.primitives.contracts import ObservationWindow, PrimitiveObservation, PrimitiveQuality, PrimitiveType


def primitive(kind, subjects, payload, index):
    return PrimitiveObservation.create(primitive_type=kind, primitive_version="1",
        evidence_ids=(),
        subjects=subjects, parameters={},
        observation_window=ObservationWindow(index, index), output_payload=payload,
        quality_state=PrimitiveQuality.PROVEN, generated_at=index)


def source_db(path):
    connection = sqlite3.connect(path)
    connection.executescript("""
    CREATE TABLE primitive_observations(
      primitive_id TEXT PRIMARY KEY, primitive_type TEXT, primitive_version TEXT,
      subjects_json TEXT, parameters_json TEXT, window_start INTEGER, window_end INTEGER,
      output_payload_json TEXT, output_digest TEXT, quality_state TEXT,
      missing_inputs_json TEXT, failure_state TEXT, generated_at INTEGER);
    CREATE TABLE primitive_evidence_inputs(primitive_id TEXT,evidence_id TEXT);
    CREATE TABLE normalized_evidence_records(
      evidence_id TEXT,logical_fact_id TEXT,fact_family TEXT,fact_schema_version TEXT,
      chain TEXT,network TEXT,natural_key TEXT,payload_json TEXT,payload_digest TEXT,
      raw_artifact_digest TEXT,observed_at INTEGER,acquired_at INTEGER,source_id TEXT,
      source_version TEXT,provider TEXT,provider_request_id TEXT,parser_id TEXT,
      parser_version TEXT,replay_version TEXT,verification_state TEXT,provenance_quality TEXT,
      corrects_evidence_id TEXT,created_at INTEGER);
    CREATE TABLE normalized_evidence_provenance(
      evidence_id TEXT,provider_request_id TEXT,endpoint_method TEXT,
      request_parameters_digest TEXT,upstream_dependency TEXT,acquisition_path TEXT,
      cache_source TEXT,dependency_group TEXT,parent_evidence_ids_json TEXT);
    """)
    values = (
        primitive(PrimitiveType.SYSTEM_TRANSFER, ("a", "b"),
            {"source": "a", "destination": "b", "amount": 1, "signature": "s"}, 1),
        primitive(PrimitiveType.LAUNCH_SIGNER, ("b", "mint"),
            {"wallet": "b", "mint": "mint", "signer": True, "launch_signature": "l"}, 2),
    )
    for item in values:
        connection.execute("INSERT INTO primitive_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            item.primitive_id, item.primitive_type, item.primitive_version,
            json.dumps(item.subjects), json.dumps(item.parameters),
            item.observation_window.start, item.observation_window.end,
            json.dumps(item.output_payload), item.output_digest, item.quality_state,
            json.dumps(item.missing_inputs), item.failure_state, item.generated_at))
    connection.commit()
    connection.close()


def test_query_only_loader_preserves_source_bytes(tmp_path):
    path = tmp_path / "source.db"
    source_db(path)
    before = sha256_file(path)
    loaded = load_primitives_query_only(path)
    assert len(loaded) == 2
    assert sha256_file(path) == before


def test_derivation_retains_complete_runtime_and_candidate_payloads(tmp_path):
    source = tmp_path / "source.db"
    source_db(source)
    descriptor = derive_corpus(operation_key="watchtower", source=source,
        contract_path=ROOT / "src/evidence/operation_contracts/contracts/watchtower_v1.json",
        register=register_watchtower_v1, runtime_path=tmp_path / "runtime.db",
        discovery_path=tmp_path / "discovery.db")
    assert descriptor["snapshot_digest"]
    assert descriptor["detector_result_id"]
    assert len(descriptor["behaviour_observation_ids"]) == 6
    runtime = sqlite3.connect(tmp_path / "runtime.db")
    assert runtime.execute("SELECT COUNT(*) FROM detector_results").fetchone()[0] == 1
    assert runtime.execute("SELECT COUNT(*) FROM topology_revisions").fetchone()[0] == 1
    runtime.close()
    discovery = sqlite3.connect(tmp_path / "discovery.db")
    payloads = discovery.execute("SELECT payload_json FROM discovery_candidates").fetchall()
    assert payloads and all(json.loads(row[0])["lifecycle"] == "RECURRING_PATTERN" for row in payloads)
    assert discovery.execute("SELECT COUNT(*) FROM discovery_lifecycle_events").fetchone()[0] == 0
    discovery.close()


def test_existing_output_fails_closed_without_mutation(tmp_path, monkeypatch):
    from scripts import run_psi0g_b_retained_derivation as runner
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "marker"
    marker.write_text("preserve")
    with pytest.raises(FileExistsError, match="PSI0G_B_OUTPUT_EXISTS"):
        runner.run(output)
    assert marker.read_text() == "preserve"


def test_real_shape_multi_role_entity_derives_node_scoped_roles(tmp_path):
    source = tmp_path / "source.db"
    source_db(source)
    item = primitive(PrimitiveType.SYSTEM_TRANSFER, ("b", "c"),
        {"source": "b", "destination": "c", "amount": 1, "signature": "s2"}, 3)
    connection = sqlite3.connect(source)
    connection.execute("INSERT INTO primitive_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        item.primitive_id, item.primitive_type, item.primitive_version,
        json.dumps(item.subjects), json.dumps(item.parameters),
        item.observation_window.start, item.observation_window.end,
        json.dumps(item.output_payload), item.output_digest, item.quality_state,
        json.dumps(item.missing_inputs), item.failure_state, item.generated_at))
    connection.commit()
    connection.close()
    descriptor = derive_corpus(operation_key="watchtower", source=source,
        contract_path=ROOT / "src/evidence/operation_contracts/contracts/watchtower_v1.json",
        register=register_watchtower_v1, runtime_path=tmp_path / "runtime.db",
        discovery_path=tmp_path / "discovery.db")
    assert descriptor["topology_revision_id"]
    runtime = sqlite3.connect(tmp_path / "runtime.db")
    payload = json.loads(runtime.execute("SELECT payload_json FROM topology_revisions").fetchone()[0])
    runtime.close()
    assert sorted(node["local_role"] for node in payload["nodes"] if node["entity_ref"] == "b") == [
        "funded_wallet", "funding_source",
    ]
