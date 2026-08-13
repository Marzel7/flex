import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.evidence.contracts.operational_family_nomination import (
    AUTHORITY_CLASS,
    OperationalFamilyNominationError,
    nominate_operational_family,
    nomination_digest,
    project_operation_behaviour_facts,
)


FIXTURE = Path(__file__).parent / "fixtures" / "eb0_4a_operational_family_nominations.json"


def _records():
    return json.loads(FIXTURE.read_text())["records"]


def test_facts_and_supported_nomination_are_deterministic_and_role_primary():
    facts = project_operation_behaviour_facts(_records())
    replay = project_operation_behaviour_facts(reversed(_records()))
    assert facts == replay
    nomination = nominate_operational_family(facts, nomination_state="SUPPORTED")
    assert nomination.authority_class == AUTHORITY_CLASS
    assert nomination.primary_role == "PROVISIONING_OPERATION"
    assert nomination.member_operation_ids == ("operation-alpha", "operation-beta")
    assert nomination.shared_mechanism_features == ("WSOL_WRAP_CLOSE",)
    assert nomination.shared_temporal_features == ("BURST_THEN_DORMANT",)
    assert nomination.operator_identity_asserted is False
    assert nomination_digest([nomination]) == nomination_digest([nomination])


def test_topology_is_relationship_metadata_and_cannot_support_alone():
    records = [dict(item, mechanism_features=[], temporal_features=[]) for item in _records()]
    with pytest.raises(OperationalFamilyNominationError, match="BEHAVIOUR_FEATURE_REQUIRED"):
        project_operation_behaviour_facts(records)
    records = [dict(item, mechanism_features=[], temporal_features=["SAME_CADENCE"]) for item in _records()]
    nomination = nominate_operational_family(project_operation_behaviour_facts(records), nomination_state="PROPOSED")
    assert nomination.shared_edge_features
    with pytest.raises(OperationalFamilyNominationError, match="MECHANISM_AND_TEMPORAL"):
        nominate_operational_family(project_operation_behaviour_facts(records), nomination_state="SUPPORTED")


def test_supported_requires_two_sources_and_complete_nonconflicting_evidence():
    same_source = [dict(item, source="one") for item in _records()]
    with pytest.raises(OperationalFamilyNominationError, match="TWO_SOURCES"):
        nominate_operational_family(project_operation_behaviour_facts(same_source), nomination_state="SUPPORTED")
    partial = _records(); partial[1] = dict(partial[1], completeness_state="PARTIAL")
    with pytest.raises(OperationalFamilyNominationError, match="COMPLETE_NONCONFLICTING"):
        nominate_operational_family(project_operation_behaviour_facts(partial), nomination_state="SUPPORTED")


def test_conflicting_facts_remain_separate_and_only_propose():
    records = _records()
    records[1] = dict(records[1], quality_state="CONFLICTING", conflict_group_id="conflict-a")
    facts = project_operation_behaviour_facts(records)
    nomination = nominate_operational_family(facts, nomination_state="PROPOSED")
    assert nomination.quality_state == "CONFLICTING"
    assert nomination.conflict_group_ids == ("conflict-a",)
    with pytest.raises(OperationalFamilyNominationError, match="COMPLETE_NONCONFLICTING"):
        nominate_operational_family(facts, nomination_state="SUPPORTED")


def test_role_mismatch_and_single_operation_fail_closed():
    records = _records(); records[1] = dict(records[1], role="CREATOR_OPERATION")
    with pytest.raises(OperationalFamilyNominationError, match="ROLE_MISMATCH"):
        nominate_operational_family(project_operation_behaviour_facts(records), nomination_state="PROPOSED")
    facts = project_operation_behaviour_facts(_records()[:1])
    with pytest.raises(OperationalFamilyNominationError, match="MULTI_OPERATION"):
        nominate_operational_family(facts, nomination_state="PROPOSED")


@pytest.mark.parametrize("field", ["operator_identity", "owner", "confidence_score", "ranking", "policy"])
def test_identity_attribution_scoring_and_policy_fields_are_rejected(field):
    record = dict(_records()[0], **{field: "forbidden"})
    with pytest.raises(OperationalFamilyNominationError, match="FORBIDDEN_FIELD"):
        project_operation_behaviour_facts([record])


def test_authority_promotion_and_fact_tampering_fail_closed():
    facts = project_operation_behaviour_facts(_records())
    with pytest.raises(OperationalFamilyNominationError, match="AUTHORITY_PROMOTION_REJECTED"):
        nominate_operational_family(facts, nomination_state="CONFIRMED")
    tampered = replace(facts[0], mechanism_features=("ALTERED",))
    with pytest.raises(OperationalFamilyNominationError, match="NONCANONICAL_FACT"):
        nominate_operational_family((tampered, facts[1]), nomination_state="PROPOSED")
