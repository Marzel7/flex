from dataclasses import replace
import json

import pytest

from src.evidence.contracts.production_shadow_assessment import QUERY_IDS
from src.evidence.contracts.production_shadow_assessment_summary_consumer import (
    AssessmentSummaryConsumerError,
    AssessmentSummaryProjection,
    PSI0C_C_ASSESSMENT_IDENTITY,
    PSI0C_C_BUNDLE_IDENTITY,
    PSI0C_B_DIGEST,
    PSI0C_D_DIGEST,
    PSI0D_A_DIGEST,
    PROVENANCE_CLASS,
    build_assessment_summary_consumer_contract,
    project_fixture_assessment_summary,
    verify_assessment_summary_consumer_contract,
    verify_assessment_summary_projection,
)


def lineage():
    return {
        "psi0d_a_digest": PSI0D_A_DIGEST,
        "psi0c_d_digest": PSI0C_D_DIGEST,
        "psi0c_c_assessment_identity": PSI0C_C_ASSESSMENT_IDENTITY,
        "psi0c_c_bundle_identity": PSI0C_C_BUNDLE_IDENTITY,
        "psi0c_b_digest": PSI0C_B_DIGEST,
    }


def member(cohort=10, present=10, rows=10, unmatched=0):
    unique = present + unmatched
    return {
        "row_count": rows,
        "unique_mint_count": unique,
        "cohort_present_count": present,
        "cohort_denominator": cohort,
        "coverage_numerator": present,
        "coverage_denominator": cohort,
        "duplicate_row_count": rows - unique,
        "unmatched_row_count": unmatched,
    }


def summary(cohort=10):
    return {
        "schema_version": "psi0d-b.synthetic-summary.v1",
        "input_lineage": lineage(),
        "fixture_only": True,
        "provenance_class": PROVENANCE_CLASS,
        "cohort_count": cohort,
        "membership": {query_id: member(cohort) for query_id in QUERY_IDS},
        "unresolved_conflict_count": 0,
        "orphan_unmatched_count": 0,
        "reason_codes": ["PSI0C_B_ABSENCE_IS_NOT_NEGATIVE"],
        "authority": {"policy": False, "ranking": False, "integration": False, "activation": False},
    }


def project(value=None):
    contract = build_assessment_summary_consumer_contract()
    return project_fixture_assessment_summary(contract, summary() if value is None else value)


def document(projection):
    return json.loads(projection.canonical_projection)


def test_contract_is_pure_default_off_fixture_only_and_non_authoritative():
    contract = build_assessment_summary_consumer_contract()
    assert verify_assessment_summary_consumer_contract(contract)
    assert contract.fixture_only and contract.default_off and not contract.performs_io
    assert not any((
        contract.thresholds_allowed, contract.negative_inference_allowed,
        contract.duplicate_collapse_allowed, contract.conflict_resolution_allowed,
        contract.grants_policy_authority, contract.grants_ranking_authority,
        contract.grants_integration_authority, contract.grants_deployment_authority,
        contract.grants_activation_authority,
    ))


def test_complete_coverage_is_descriptive_and_exactly_replayable():
    contract = build_assessment_summary_consumer_contract()
    value = summary()
    result = project_fixture_assessment_summary(contract, value)
    assert verify_assessment_summary_projection(contract, value, result)
    doc = document(result)
    assert all(item["coverage_numerator"] == item["coverage_denominator"] == 10 for item in doc["surfaces"].values())
    assert not any(doc["authority"].values())
    assert not any(doc["interpretation"].values())


def test_empty_and_partial_coverage_preserve_absence_not_negative():
    empty = summary()
    empty["membership"] = {query_id: member(present=0, rows=0) for query_id in QUERY_IDS}
    doc = document(project(empty))
    assert all(item["coverage_numerator"] == 0 for item in doc["surfaces"].values())
    assert all(item["missingness_semantics"] == "ABSENT_NOT_NEGATIVE" for item in doc["surfaces"].values())
    partial = summary()
    partial["membership"]["snapshot_selected_cohort"] = member(present=4, rows=6)
    doc = document(project(partial))
    assert doc["surfaces"]["snapshot_selected_cohort"]["coverage_numerator"] == 4
    assert doc["surfaces"]["snapshot_selected_cohort"]["duplicate_row_count"] == 2
    assert not doc["interpretation"]["negative_outcome_inferred"]


def test_duplicates_conflicts_and_unmatched_are_preserved_without_resolution():
    value = summary()
    value["membership"]["ops_selected_cohort"] = member(present=2, rows=5, unmatched=1)
    value["unresolved_conflict_count"] = 3
    value["orphan_unmatched_count"] = 1
    value["reason_codes"] += [
        "PSI0C_B_CONFLICT_PRESERVED_UNRESOLVED",
        "PSI0C_B_UNMATCHED_KEY_RECORDED",
    ]
    doc = document(project(value))
    ops = doc["surfaces"]["ops_selected_cohort"]
    assert ops["duplicate_row_count"] == 2 and ops["unmatched_row_count"] == 1
    assert doc["unresolved_conflict_count"] == 3 and doc["orphan_unmatched_count"] == 1
    assert not doc["interpretation"]["duplicates_collapsed"]
    assert not doc["interpretation"]["conflicts_resolved"]


def test_input_mapping_and_reason_order_do_not_change_projection():
    first = summary()
    first["membership"] = dict(reversed(list(first["membership"].items())))
    first["reason_codes"] = ["PSI0C_B_UNMATCHED_KEY_RECORDED", "PSI0C_B_ABSENCE_IS_NOT_NEGATIVE"]
    first["membership"]["ops_selected_cohort"] = member(present=9, rows=10, unmatched=1)
    first["orphan_unmatched_count"] = 1
    second = summary()
    second["membership"]["ops_selected_cohort"] = member(present=9, rows=10, unmatched=1)
    second["orphan_unmatched_count"] = 1
    second["reason_codes"] = list(reversed(first["reason_codes"]))
    assert project(first) == project(second)


@pytest.mark.parametrize("mutation,match", [
    (lambda x: x.update(extra=True), "UNKNOWN_SUMMARY_SCHEMA"),
    (lambda x: x["input_lineage"].update(psi0c_d_digest="0" * 64), "STALE_OR_ALTERED_LINEAGE"),
    (lambda x: x.update(fixture_only=False), "NON_FIXTURE_PROVENANCE"),
    (lambda x: x["authority"].update(integration=True), "AUTHORITY_DRIFT"),
    (lambda x: x["membership"].pop("ops_selected_cohort"), "QUERY_IDENTITY_DRIFT"),
    (lambda x: x["membership"]["ops_selected_cohort"].update(score=1), "UNKNOWN_MEMBERSHIP_SCHEMA"),
    (lambda x: x["membership"]["ops_selected_cohort"].update(row_count=-1), "INVALID_ACCOUNTING"),
    (lambda x: x["membership"]["ops_selected_cohort"].update(coverage_denominator=9), "INCONSISTENT_ACCOUNTING"),
    (lambda x: x["reason_codes"].append("RANK_AND_SELECT"), "REASON_CODE_DRIFT"),
])
def test_schema_lineage_accounting_authority_and_policy_drift_fail_closed(mutation, match):
    value = summary()
    mutation(value)
    with pytest.raises(AssessmentSummaryConsumerError, match=match):
        project(value)


def test_conflict_and_unmatched_reason_codes_are_required():
    value = summary(); value["unresolved_conflict_count"] = 1
    with pytest.raises(AssessmentSummaryConsumerError, match="CONFLICT_REASON_MISSING"):
        project(value)
    value = summary()
    value["membership"]["ops_selected_cohort"] = member(present=9, rows=10, unmatched=1)
    value["orphan_unmatched_count"] = 1
    with pytest.raises(AssessmentSummaryConsumerError, match="UNMATCHED_REASON_MISSING"):
        project(value)


def test_contract_and_projection_tamper_fail_replay():
    contract = build_assessment_summary_consumer_contract()
    with pytest.raises(AssessmentSummaryConsumerError, match="CONTRACT_REPLAY_MISMATCH"):
        verify_assessment_summary_consumer_contract(replace(contract, grants_integration_authority=True))
    value = summary(); result = project_fixture_assessment_summary(contract, value)
    tampered = replace(result, projection_digest="0" * 64)
    with pytest.raises(AssessmentSummaryConsumerError, match="PROJECTION_REPLAY_MISMATCH"):
        verify_assessment_summary_projection(contract, value, tampered)


def test_execution_path_performs_no_file_database_network_or_service_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("I/O attempted")

    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("sqlite3.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    result = project()
    assert result.canonical_projection and result.projection_digest
