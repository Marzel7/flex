from dataclasses import replace
from hashlib import sha256
import json
import sqlite3

import pytest

import src.evidence.contracts.known_behaviour_operational_surface_adapters as adapters
import src.evidence.contracts.production_shadow_integration_envelope_publisher as publisher
from src.evidence.contracts.production_shadow_assessment import QUERY_IDS
from src.evidence.contracts.known_behaviour_operational_surface import (
    build_known_behaviour_operational_surface_contract,
)
from src.evidence.contracts.known_behaviour_operational_surface_adapters import (
    KnownBehaviourOperationalSurfaceAdapterError,
    adapt_eb0_4_bundle_bytes,
    adapt_psi0e_bundle_bytes,
    build_known_behaviour_operational_surface_adapter_contract,
    project_immutable_summary_bytes,
    verify_known_behaviour_operational_surface_adapter_contract,
)
from src.evidence.contracts.operational_family_bundle import write_operational_family_bundle
from src.evidence.contracts.operational_family_extractor import extract_operational_families
from src.evidence.contracts.production_shadow_integration_envelope import (
    _project_envelope,
    build_integration_envelope_contract,
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def psi0e_files(monkeypatch, cohort=10):
    envelope_contract = build_integration_envelope_contract()
    projection = {
        "cohort_count": cohort,
        "surfaces": {
            query_id: {
                "coverage_numerator": cohort, "coverage_denominator": cohort,
                "row_count": cohort, "unique_mint_count": cohort,
                "duplicate_row_count": 0, "unmatched_row_count": 0,
                "missingness_semantics": "ABSENT_NOT_NEGATIVE",
            }
            for query_id in QUERY_IDS
        },
        "unresolved_conflict_count": 0, "orphan_unmatched_count": 0,
        "reason_codes": ["PSI0C_B_ABSENCE_IS_NOT_NEGATIVE"],
    }
    result = _project_envelope(envelope_contract, projection, input_digest=publisher.PSI0E_C_INPUT_DIGEST)
    monkeypatch.setattr(publisher, "EXPECTED_ENVELOPE_DIGEST", result.envelope_digest)
    publication = publisher.build_integration_envelope_publication_contract()
    payloads = {
        "contract.json": canonical(publisher._manifest(publication)),
        "envelope.json": result.canonical_envelope,
    }
    digests = {name: sha256(payload).hexdigest() for name, payload in payloads.items()}
    bundle_digest = sha256(json.dumps(digests, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    payloads["hashes.json"] = canonical({"file_digests": digests, "bundle_digest": bundle_digest})
    monkeypatch.setattr(adapters, "PSI0E_BUNDLE_DIGEST", bundle_digest)
    return payloads, bundle_digest


def eb0_4_files(tmp_path):
    path = tmp_path / "source.db"
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE operation_cohort(position INTEGER,operation_id TEXT); CREATE TABLE normalized_operation_runtime(schema_version TEXT,identity_basis TEXT,operation_id TEXT,primary_role TEXT,contract_id TEXT,contract_version TEXT,module_id TEXT,module_version TEXT,topology_revision_id TEXT,behaviour_observation_id TEXT,input_digest TEXT,edge_features_json TEXT,mechanism_features_json TEXT,temporal_features_json TEXT,quality_state TEXT,completeness_state TEXT,conflict_group_id TEXT); CREATE TABLE nomination_candidates(group_id TEXT,position INTEGER,operation_id TEXT,nomination_state TEXT);")
    for position, operation in enumerate(("operation-alpha", "operation-beta")):
        db.execute("INSERT INTO operation_cohort VALUES (?,?)", (position, operation))
        db.execute("INSERT INTO normalized_operation_runtime VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "eb0.4c.normalized-runtime.v1", "PLATFORM_OPERATION_ID", operation, "LISTENER",
            "contract", "1", f"module-{position}", "1", f"topology-{position}",
            f"observation-{position}", f"input-{position}", json.dumps(["writes_to_queue"]),
            json.dumps(["bounded_batch"]), json.dumps(["periodic_heartbeat"]),
            "OBSERVED", "COMPLETE", None,
        ))
        db.execute("INSERT INTO nomination_candidates VALUES (?,?,?,?)", ("group", position, operation, "SUPPORTED"))
    db.commit(); db.close()
    result = extract_operational_families(path)
    output = tmp_path / "bundle"
    bundle = write_operational_family_bundle(result, output, run_id="fixture", engineering_revision="3013be47017a000c1db3c70fbf323102d0fc40d3")
    return {item.name: item.read_bytes() for item in output.iterdir()}, bundle.bundle_digest


def setup(monkeypatch, tmp_path):
    psi_files, psi_digest = psi0e_files(monkeypatch)
    eb_files, eb_digest = eb0_4_files(tmp_path)
    return build_known_behaviour_operational_surface_adapter_contract(), psi_files, psi_digest, eb_files, eb_digest


def test_contract_is_pure_fixture_only_no_join_and_non_authoritative(monkeypatch):
    psi0e_files(monkeypatch)
    contract = build_known_behaviour_operational_surface_adapter_contract()
    assert verify_known_behaviour_operational_surface_adapter_contract(contract)
    assert contract.fixture_only and not contract.performs_io and not contract.retries_allowed
    assert not contract.retains_source_values and not contract.cross_layer_join_allowed
    assert not any((contract.grants_policy_authority, contract.grants_ranking_authority,
                    contract.grants_attribution_authority, contract.grants_integration_authority,
                    contract.grants_deployment_authority, contract.grants_activation_authority))


def test_valid_injected_bundles_project_separate_layers(monkeypatch, tmp_path):
    contract, psi_files, psi_digest, eb_files, eb_digest = setup(monkeypatch, tmp_path)
    result = project_immutable_summary_bytes(
        contract, build_known_behaviour_operational_surface_contract(),
        psi0e_files=psi_files, eb0_4_files=eb_files, expected_eb0_4_bundle_digest=eb_digest,
    )
    document = json.loads(result.surface.canonical_surface)
    assert result.psi0e_bundle_digest == psi_digest and result.eb0_4_bundle_digest == eb_digest
    assert document["cross_layer_join_performed"] is False
    assert document["operational_roles"]["LISTENER"]["supported_count"] == 1
    assert document["global_evidence_availability_context"]["cohort_count"] == 10
    assert result.psi0e_source_provenance == adapters.PSI0E_PROVENANCE
    assert result.eb0_4_source_provenance == adapters.EB0_4_PROVENANCE


def test_adapters_are_input_order_independent(monkeypatch, tmp_path):
    contract, psi_files, _, eb_files, eb_digest = setup(monkeypatch, tmp_path)
    first = project_immutable_summary_bytes(contract, build_known_behaviour_operational_surface_contract(), psi0e_files=psi_files, eb0_4_files=eb_files, expected_eb0_4_bundle_digest=eb_digest)
    second = project_immutable_summary_bytes(contract, build_known_behaviour_operational_surface_contract(), psi0e_files=dict(reversed(list(psi_files.items()))), eb0_4_files=dict(reversed(list(eb_files.items()))), expected_eb0_4_bundle_digest=eb_digest)
    assert first == second


@pytest.mark.parametrize("target,mutation,match", [
    ("psi", lambda x: x.pop("contract.json"), "PSI0E_FILE_SET"),
    ("psi", lambda x: x.update(extra=b"{}\n"), "PSI0E_FILE_SET"),
    ("psi", lambda x: x.update({"envelope.json": b"{}"}), "PSI0E_NONCANONICAL"),
    ("psi", lambda x: x.update({"envelope.json": b"not-json"}), "PSI0E_INVALID_JSON"),
    ("eb", lambda x: x.pop("corpora.json"), "EB0_4_FILE_SET"),
    ("eb", lambda x: x.update(extra=b"{}\n"), "EB0_4_FILE_SET"),
    ("eb", lambda x: x.update({"manifests.json": b"{}"}), "EB0_4_NONCANONICAL"),
    ("eb", lambda x: x.update({"manifests.json": b"bad"}), "EB0_4_INVALID_JSON"),
])
def test_file_set_malformed_and_noncanonical_fail(monkeypatch, tmp_path, target, mutation, match):
    contract, psi_files, _, eb_files, eb_digest = setup(monkeypatch, tmp_path)
    changed = dict(psi_files if target == "psi" else eb_files); mutation(changed)
    with pytest.raises(KnownBehaviourOperationalSurfaceAdapterError, match=match):
        if target == "psi": adapt_psi0e_bundle_bytes(contract, changed)
        else: adapt_eb0_4_bundle_bytes(contract, changed, expected_bundle_digest=eb_digest)


def test_hash_digest_and_contract_drift_fail(monkeypatch, tmp_path):
    contract, psi_files, _, eb_files, eb_digest = setup(monkeypatch, tmp_path)
    changed = dict(psi_files); hashes = json.loads(changed["hashes.json"]); hashes["bundle_digest"] = "0" * 64; changed["hashes.json"] = canonical(hashes)
    with pytest.raises(KnownBehaviourOperationalSurfaceAdapterError, match="PSI0E_HASH_REPLAY"):
        adapt_psi0e_bundle_bytes(contract, changed)
    changed = dict(eb_files); hashes = json.loads(changed["hashes.json"]); hashes["bundle_digest"] = "0" * 64; changed["hashes.json"] = canonical(hashes)
    with pytest.raises(KnownBehaviourOperationalSurfaceAdapterError, match="EB0_4_HASH_REPLAY"):
        adapt_eb0_4_bundle_bytes(contract, changed, expected_bundle_digest=eb_digest)
    with pytest.raises(KnownBehaviourOperationalSurfaceAdapterError, match="SURFACE_CONTRACT_DRIFT"):
        project_immutable_summary_bytes(contract, replace(build_known_behaviour_operational_surface_contract(), contract_digest="0" * 64), psi0e_files=psi_files, eb0_4_files=eb_files, expected_eb0_4_bundle_digest=eb_digest)


def test_eb0_4_content_replay_and_expected_identity_fail(monkeypatch, tmp_path):
    contract, _, _, eb_files, eb_digest = setup(monkeypatch, tmp_path)
    with pytest.raises(KnownBehaviourOperationalSurfaceAdapterError, match="BUNDLE_IDENTITY_DRIFT"):
        adapt_eb0_4_bundle_bytes(contract, eb_files, expected_bundle_digest="0" * 64)
    changed = dict(eb_files); corpora = json.loads(changed["corpora.json"]); corpora["corpora"][0]["primary_role"] = "ALTERED"; changed["corpora.json"] = canonical(corpora)
    digests = {n: sha256(changed[n]).hexdigest() for n in ("run.json", "accounting.json", "manifests.json", "corpora.json")}
    digest = sha256(canonical(digests)).hexdigest(); changed["hashes.json"] = canonical({"bundle_schema_version": "eb0.4h.v1", "files": digests, "bundle_digest": digest})
    with pytest.raises(KnownBehaviourOperationalSurfaceAdapterError, match="CONTENT_REPLAY_FAILED"):
        adapt_eb0_4_bundle_bytes(contract, changed, expected_bundle_digest=digest)


def test_contract_authority_and_replay_tamper_fail(monkeypatch):
    psi0e_files(monkeypatch); contract = build_known_behaviour_operational_surface_adapter_contract()
    with pytest.raises(KnownBehaviourOperationalSurfaceAdapterError, match="CONTRACT_REPLAY"):
        verify_known_behaviour_operational_surface_adapter_contract(replace(contract, cross_layer_join_allowed=True))
    with pytest.raises(KnownBehaviourOperationalSurfaceAdapterError, match="CONTRACT_REPLAY"):
        verify_known_behaviour_operational_surface_adapter_contract(replace(contract, grants_attribution_authority=True))


def test_adapter_execution_performs_zero_file_database_network_service_or_configuration_io(monkeypatch, tmp_path):
    contract, psi_files, _, eb_files, eb_digest = setup(monkeypatch, tmp_path)
    def forbidden(*args, **kwargs): raise AssertionError("I/O attempted")
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("sqlite3.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    result = project_immutable_summary_bytes(contract, build_known_behaviour_operational_surface_contract(), psi0e_files=psi_files, eb0_4_files=eb_files, expected_eb0_4_bundle_digest=eb_digest)
    assert result.surface.canonical_surface
