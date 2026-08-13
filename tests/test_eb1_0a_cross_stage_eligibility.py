from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.evidence.contracts.cross_stage_eligibility import (
    CrossStageEligibilityError,
    project_cross_stage_eligibility,
    verify_cross_stage_eligibility,
)


FIXTURE = Path(__file__).parent / "fixtures/eb1_0a_cross_stage_eligibility.json"


def _records():
    return json.loads(FIXTURE.read_text())


def test_projection_is_order_independent_and_exactly_replayable():
    records = _records()
    forward = project_cross_stage_eligibility(records)
    reverse = project_cross_stage_eligibility(reversed(records))
    assert forward == reverse
    assert verify_cross_stage_eligibility(forward, records)
    assert forward.stage_count == 4
    assert forward.eligibility_counts == {"ELIGIBLE": 2, "INELIGIBLE_MISSING": 2}


def test_measured_missingness_is_not_converted_to_negative_outcome():
    projection = project_cross_stage_eligibility(_records())
    by_stage = {item.upstream_stage: item for item in projection.stages}
    assert by_stage["EB0.1"].eligibility_state == "INELIGIBLE_MISSING"
    assert "MISSING_EVIDENCE" in by_stage["EB0.1"].reason_codes
    assert by_stage["EB0.2"].reason_codes == (
        "COMPLETENESS_NOT_OBSERVED", "MISSING_EVIDENCE", "NO_OBSERVED_EVIDENCE",
    )


def test_supplemental_and_nomination_authority_lanes_remain_separate():
    projection = project_cross_stage_eligibility(_records())
    by_stage = {item.upstream_stage: item for item in projection.stages}
    assert by_stage["EB0.3"].authority_lane == "SUPPLEMENTAL_MARKET_NON_AUTHORITATIVE"
    assert by_stage["EB0.4"].authority_lane == "NOMINATION_NON_AUTHORITATIVE"
    records = _records(); records[2]["authority_lane"] = "CANONICAL_BIRTH_VALUATION"
    with pytest.raises(CrossStageEligibilityError, match="AUTHORITY_LANE_PROMOTION_REJECTED"):
        project_cross_stage_eligibility(records)


def test_conflict_takes_precedence_and_is_explicit():
    records = _records(); records[3].update(total_count=3, observed_count=3, missing_count=0, conflicting_count=1)
    stage = {x.upstream_stage: x for x in project_cross_stage_eligibility(records).stages}["EB0.4"]
    assert stage.eligibility_state == "INELIGIBLE_CONFLICTING"
    assert stage.reason_codes == ("CONFLICTING_EVIDENCE",)


def test_not_applicable_requires_empty_explicit_not_observed_scope():
    records = _records(); records[2].update(applicable=False, total_count=0, observed_count=0, missing_count=0, completeness_state="NOT_OBSERVED")
    stage = {x.upstream_stage: x for x in project_cross_stage_eligibility(records).stages}["EB0.3"]
    assert stage.eligibility_state == "NOT_APPLICABLE"
    records[2]["total_count"] = 1; records[2]["missing_count"] = 1
    with pytest.raises(CrossStageEligibilityError, match="NOT_APPLICABLE_CONTRADICTION"):
        project_cross_stage_eligibility(records)


@pytest.mark.parametrize("mutation,match", [
    ({"bundle_digest": "bad"}, "INVALID_DIGEST"),
    ({"engineering_revision": "not-git"}, "INVALID_ENGINEERING_REVISION"),
    ({"observed_count": 3}, "COUNT_RECONCILIATION_FAILED"),
    ({"completeness_state": "ASSUMED"}, "UNKNOWN_COMPLETENESS_STATE"),
])
def test_invalid_binding_counts_and_completeness_fail_closed(mutation, match):
    records = _records(); records[0].update(mutation)
    with pytest.raises(CrossStageEligibilityError, match=match):
        project_cross_stage_eligibility(records)


def test_exact_stage_set_schema_and_forbidden_analytical_fields_fail_closed():
    with pytest.raises(CrossStageEligibilityError, match="EXACT_STAGE_SET_REQUIRED"):
        project_cross_stage_eligibility(_records()[:-1])
    records = _records(); records[0]["score"] = 1
    with pytest.raises(CrossStageEligibilityError, match="SCHEMA_DRIFT|FORBIDDEN_FIELD"):
        project_cross_stage_eligibility(records)


def test_projection_tampering_fails_exact_replay():
    records = _records(); projection = project_cross_stage_eligibility(records)
    with pytest.raises(CrossStageEligibilityError, match="REPLAY_MISMATCH"):
        verify_cross_stage_eligibility(replace(projection, projection_digest="bad"), records)
