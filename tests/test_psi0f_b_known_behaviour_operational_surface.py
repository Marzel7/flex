from dataclasses import replace
import json

import pytest

from src.evidence.contracts.production_shadow_assessment import QUERY_IDS
from src.evidence.contracts.known_behaviour_operational_surface import (
    KnownBehaviourOperationalSurfaceError,
    PSI0E_BUNDLE_DIGEST,
    PSI0E_ENVELOPE_DIGEST,
    PSI0E_H_DIGEST,
    PSI0F_A_DIGEST,
    build_known_behaviour_operational_surface_contract,
    project_fixture_known_behaviour_operational_surface,
    verify_known_behaviour_operational_surface,
    verify_known_behaviour_operational_surface_contract,
)


def lineage():
    return {
        "psi0f_a_digest": PSI0F_A_DIGEST, "psi0e_h_digest": PSI0E_H_DIGEST,
        "psi0e_bundle_digest": PSI0E_BUNDLE_DIGEST, "psi0e_envelope_digest": PSI0E_ENVELOPE_DIGEST,
    }


def authority():
    return {k: False for k in ("policy", "ranking", "attribution", "integration", "deployment", "activation")}


def surface(cohort=10, present=10, rows=10, unmatched=0):
    unique = present + unmatched
    return {
        "coverage_numerator": present, "coverage_denominator": cohort,
        "row_count": rows, "unique_mint_count": unique,
        "duplicate_row_count": rows - unique, "unmatched_row_count": unmatched,
        "missingness_semantics": "ABSENT_NOT_NEGATIVE",
    }


def psi0e(cohort=10):
    return {
        "schema_version": "psi0f-b.synthetic-psi0e-summary.v1", "lineage": lineage(),
        "fixture_only": True, "default_off": True, "consumer_enabled": False,
        "cohort_count": cohort, "surfaces": {q: surface(cohort) for q in QUERY_IDS},
        "unresolved_conflict_count": 0, "orphan_unmatched_count": 0,
        "reason_codes": ["PSI0C_B_ABSENCE_IS_NOT_NEGATIVE"], "authority": authority(),
    }


def nomination(role="listener", state="SUPPORTED"):
    return {
        "primary_role": role, "nomination_state": state,
        "member_operation_ids": ["op-b", "op-a"], "supporting_fact_ids": ["fact-b", "fact-a"],
        "shared_edge_features": ["writes_to_queue"],
        "shared_mechanism_features": ["bounded_batch"],
        "shared_temporal_features": ["periodic_heartbeat"],
        "supporting_sources": ["runtime:v2", "contract:v1"],
        "quality_state": "OBSERVED", "completeness_state": "COMPLETE",
        "conflict_count": 0, "operator_identity_asserted": False,
    }


def families(items=None):
    return {
        "schema_version": "psi0f-b.synthetic-operational-families.v1", "lineage": lineage(),
        "fixture_only": True, "identity_basis": "PLATFORM_OPERATION_ID",
        "allowed_roles": ["worker", "listener"],
        "nominations": [nomination()] if items is None else items, "authority": authority(),
    }


def project(p=None, f=None):
    return project_fixture_known_behaviour_operational_surface(
        build_known_behaviour_operational_surface_contract(),
        psi0e_summary=psi0e() if p is None else p,
        operational_families=families() if f is None else f,
    )


def doc(result=None):
    return json.loads((project() if result is None else result).canonical_surface)


def test_contract_is_pure_fixture_only_default_off_and_non_authoritative():
    contract = build_known_behaviour_operational_surface_contract()
    assert verify_known_behaviour_operational_surface_contract(contract)
    assert contract.fixture_only and contract.default_off and not contract.performs_io
    assert not any((contract.cross_layer_join_allowed, contract.thresholds_allowed,
                    contract.negative_inference_allowed, contract.duplicate_collapse_allowed,
                    contract.conflict_resolution_allowed, contract.operator_identity_allowed,
                    contract.grants_policy_authority, contract.grants_ranking_authority,
                    contract.grants_attribution_authority, contract.grants_integration_authority,
                    contract.grants_deployment_authority, contract.grants_activation_authority))


def test_complete_surface_is_descriptive_separate_and_replayable():
    contract = build_known_behaviour_operational_surface_contract()
    p, f = psi0e(), families()
    result = project_fixture_known_behaviour_operational_surface(contract, psi0e_summary=p, operational_families=f)
    assert verify_known_behaviour_operational_surface(contract, psi0e_summary=p, operational_families=f, surface=result)
    value = doc(result)
    assert value["cross_layer_join_performed"] is False
    assert value["operational_roles"]["listener"]["supported_count"] == 1
    assert value["global_evidence_availability_context"]["cohort_count"] == 10
    assert not any(value["authority"].values()) and not any(value["interpretation"].values())


def test_empty_and_partial_coverage_preserve_absence_without_linkage():
    p = psi0e()
    p["surfaces"] = {q: surface(present=0, rows=0) for q in QUERY_IDS}
    value = doc(project(p, families([])))
    assert all(x["coverage_numerator"] == 0 for x in value["global_evidence_availability_context"]["surfaces"].values())
    assert value["operational_roles"]["listener"]["nomination_count"] == 0
    p = psi0e(); p["surfaces"]["snapshot_selected_cohort"] = surface(present=4, rows=6)
    value = doc(project(p, families([nomination(state="PROPOSED")])))
    assert value["global_evidence_availability_context"]["surfaces"]["snapshot_selected_cohort"]["duplicate_row_count"] == 2
    assert not value["interpretation"]["operation_specific_coverage_inferred"]


def test_role_edge_mechanism_temporal_and_states_are_preserved():
    proposed = nomination(role="worker", state="PROPOSED")
    proposed["shared_temporal_features"] = []
    value = doc(project(psi0e(), families([nomination(), proposed])))
    assert value["operational_roles"]["listener"]["nominations"][0]["shared_edge_features"] == ["writes_to_queue"]
    assert value["operational_roles"]["worker"]["proposed_count"] == 1
    assert value["operational_roles"]["worker"]["nominations"][0]["shared_mechanism_features"] == ["bounded_batch"]


def test_duplicates_conflicts_and_unmatched_are_preserved():
    p = psi0e(); p["surfaces"]["ops_selected_cohort"] = surface(present=7, rows=10, unmatched=1)
    p["unresolved_conflict_count"] = 2; p["orphan_unmatched_count"] = 1
    p["reason_codes"] += ["PSI0F_B_CONFLICT_PRESERVED_UNRESOLVED", "PSI0F_B_UNMATCHED_PRESERVED"]
    n = nomination(state="PROPOSED"); n["quality_state"] = "CONFLICTING"; n["completeness_state"] = "PARTIAL"; n["conflict_count"] = 2
    value = doc(project(p, families([n])))
    assert value["global_evidence_availability_context"]["unresolved_conflict_count"] == 2
    assert value["operational_roles"]["listener"]["unresolved_conflict_count"] == 2
    assert not value["interpretation"]["duplicates_collapsed"] and not value["interpretation"]["conflicts_resolved"]


def test_input_order_independence():
    p1, p2 = psi0e(), psi0e()
    p1["surfaces"] = dict(reversed(list(p1["surfaces"].items())))
    f1 = families([nomination(role="worker", state="PROPOSED"), nomination()])
    f2 = families(list(reversed(f1["nominations"])))
    f1["allowed_roles"] = list(reversed(f1["allowed_roles"]))
    assert project(p1, f1) == project(p2, f2)


def test_topology_only_and_invalid_supported_nomination_fail():
    n = nomination(state="PROPOSED"); n["shared_mechanism_features"] = []; n["shared_temporal_features"] = []
    with pytest.raises(KnownBehaviourOperationalSurfaceError, match="TOPOLOGY_ONLY"):
        project(psi0e(), families([n]))
    n = nomination(); n["supporting_sources"] = ["one:v1"]
    with pytest.raises(KnownBehaviourOperationalSurfaceError, match="UNSUPPORTED_SUPPORTED"):
        project(psi0e(), families([n]))


@pytest.mark.parametrize("mutation,match", [
    (lambda p, f: p.update(extra=True), "UNKNOWN_PSI0E_SCHEMA"),
    (lambda p, f: p["lineage"].update(psi0e_h_digest="0" * 64), "PSI0E_LINEAGE"),
    (lambda p, f: p.update(fixture_only=False), "PSI0E_LINEAGE"),
    (lambda p, f: p["authority"].update(ranking=True), "AUTHORITY_DRIFT"),
    (lambda p, f: p["surfaces"].pop("ops_selected_cohort"), "SURFACE_IDENTITY"),
    (lambda p, f: p["surfaces"]["ops_selected_cohort"].update(score=1), "UNKNOWN_SURFACE"),
    (lambda p, f: p["surfaces"]["ops_selected_cohort"].update(missingness_semantics="NEGATIVE"), "INCONSISTENT_ACCOUNTING"),
    (lambda p, f: f.update(identity_basis="WALLET"), "FAMILY_LINEAGE_OR_IDENTITY"),
    (lambda p, f: f["nominations"][0].update(primary_role="unknown"), "ROLE_OR_NOMINATION"),
    (lambda p, f: f["nominations"][0].update(operator_identity_asserted=True), "OPERATOR_IDENTITY"),
    (lambda p, f: f["nominations"][0].update(confidence=1), "UNKNOWN_NOMINATION_SCHEMA"),
    (lambda p, f: f["nominations"][0].update(shared_mechanism_features=["rank_score"]), "PROHIBITED_SEMANTICS"),
])
def test_schema_lineage_authority_and_prohibited_semantics_fail_closed(mutation, match):
    p, f = psi0e(), families(); mutation(p, f)
    with pytest.raises(KnownBehaviourOperationalSurfaceError, match=match):
        project(p, f)


def test_contract_and_surface_tamper_fail_replay():
    contract = build_known_behaviour_operational_surface_contract()
    with pytest.raises(KnownBehaviourOperationalSurfaceError, match="CONTRACT_REPLAY"):
        verify_known_behaviour_operational_surface_contract(replace(contract, cross_layer_join_allowed=True))
    p, f = psi0e(), families()
    result = project_fixture_known_behaviour_operational_surface(contract, psi0e_summary=p, operational_families=f)
    with pytest.raises(KnownBehaviourOperationalSurfaceError, match="SURFACE_REPLAY"):
        verify_known_behaviour_operational_surface(contract, psi0e_summary=p, operational_families=f,
                                                   surface=replace(result, surface_digest="0" * 64))


def test_projection_performs_zero_file_database_network_service_or_configuration_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("I/O attempted")
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("sqlite3.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    result = project()
    assert result.canonical_surface and result.surface_digest
