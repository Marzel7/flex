"""Fixture-only tests for the B2Z-P2 calibration comparison engine."""
from __future__ import annotations

from src.acquisition.b2z_calibration_comparison import (
    AGREEMENT,
    DISAGREEMENT,
    EXECUTION_EXCLUDED,
    LOCAL_MISSING,
    NOT_YET_EXECUTED,
    RAW_MISSING,
    compare_member,
    compute_metrics,
)


def prediction(**overrides):
    base = {
        "ordinal": 1, "mint": "mintX", "local_creator": "creatorX",
        "local_direct_funder": "funderX", "local_direct_funding_signature": "sigX",
        "local_funding_amount_lamports": 1000, "local_block_time": 500,
        "stage2_skip": False, "frozen_stage3_signature_if_skip": None,
        "fan_out_review_flag": False,
    }
    base.update(overrides)
    return base


def raw(**overrides):
    base = {
        "creator": "creatorX", "funding_signature": "sigX", "funding_source": "funderX",
        "creator_destination": "creatorX", "amount_lamports": 1000, "funding_time": 500,
    }
    base.update(overrides)
    return base


def test_clean_agreement():
    c = compare_member(prediction=prediction(), raw_result=raw(), excluded=False)
    assert c["overall_state"] == AGREEMENT
    assert all(f["state"] == AGREEMENT for f in c["field_comparisons"].values())


def test_creator_disagreement():
    c = compare_member(prediction=prediction(), raw_result=raw(creator="different-creator"), excluded=False)
    assert c["field_comparisons"]["creator"]["state"] == DISAGREEMENT
    assert c["overall_state"] == DISAGREEMENT


def test_signature_disagreement():
    c = compare_member(prediction=prediction(), raw_result=raw(funding_signature="other-sig"), excluded=False)
    assert c["field_comparisons"]["funding_signature"]["state"] == DISAGREEMENT
    assert c["overall_state"] == DISAGREEMENT


def test_funder_disagreement():
    c = compare_member(prediction=prediction(), raw_result=raw(funding_source="other-funder"), excluded=False)
    assert c["field_comparisons"]["direct_funder"]["state"] == DISAGREEMENT


def test_amount_disagreement():
    c = compare_member(prediction=prediction(), raw_result=raw(amount_lamports=9999), excluded=False)
    assert c["field_comparisons"]["amount"]["state"] == DISAGREEMENT


def test_temporal_disagreement():
    c = compare_member(prediction=prediction(), raw_result=raw(funding_time=999999), excluded=False)
    assert c["field_comparisons"]["temporal"]["state"] == DISAGREEMENT


def test_missing_local_evidence():
    p = prediction(local_funding_amount_lamports=None)
    c = compare_member(prediction=p, raw_result=raw(), excluded=False)
    assert c["field_comparisons"]["amount"]["state"] == LOCAL_MISSING


def test_missing_raw_evidence():
    c = compare_member(prediction=prediction(), raw_result=raw(amount_lamports=None), excluded=False)
    assert c["field_comparisons"]["amount"]["state"] == RAW_MISSING


def test_execution_exclusion():
    c = compare_member(prediction=prediction(), raw_result=None, excluded=True,
                        exclusion_reason="CREDENTIAL_INPUT_CORRUPTION")
    assert c["overall_state"] == EXECUTION_EXCLUDED
    assert c["exclusion_reason"] == "CREDENTIAL_INPUT_CORRUPTION"
    assert all(f["state"] == EXECUTION_EXCLUDED for f in c["field_comparisons"].values())


def test_partial_run_not_yet_executed():
    c = compare_member(prediction=prediction(), raw_result=None, excluded=False)
    assert c["overall_state"] == NOT_YET_EXECUTED
    assert c["executed"] is False


def test_service_distribution_review_flag_carried_through():
    p = prediction(fan_out_review_flag=True)
    c = compare_member(prediction=p, raw_result=raw(), excluded=False)
    assert c["fan_out_review_flag"] is True


def test_stage2_seeded_signature_confirmed():
    p = prediction(stage2_skip=True, frozen_stage3_signature_if_skip="sigX")
    c = compare_member(prediction=p, raw_result=raw(funding_signature="sigX"), excluded=False)
    assert c["stage2_skip_confirmation"] == "CONFIRMED"


def test_stage2_seeded_signature_rejected():
    p = prediction(stage2_skip=True, frozen_stage3_signature_if_skip="sigX")
    c = compare_member(prediction=p, raw_result=raw(funding_signature="a-different-sig"), excluded=False)
    assert c["stage2_skip_confirmation"] == "REJECTED"


def test_stage2_not_skipped_has_no_confirmation_state():
    p = prediction(stage2_skip=False)
    c = compare_member(prediction=p, raw_result=raw(), excluded=False)
    assert c["stage2_skip_confirmation"] is None


# --- aggregate metrics ------------------------------------------------------

def test_metrics_exclude_execution_excluded_and_not_yet_executed_from_denominators():
    comparisons = [
        compare_member(prediction=prediction(ordinal=1), raw_result=None, excluded=True, exclusion_reason="CREDENTIAL_INPUT_CORRUPTION"),
        compare_member(prediction=prediction(ordinal=2), raw_result=raw(), excluded=False),
        compare_member(prediction=prediction(ordinal=3), raw_result=None, excluded=False),
    ]
    m = compute_metrics(comparisons)
    assert m["population"]["total_cohort"] == 3
    assert m["population"]["executed"] == 1
    assert m["population"]["execution_excluded"] == 1
    assert m["population"]["not_yet_executed"] == 1
    assert m["creator_agreement"]["denominator"] == 1  # only the 1 executed member counts


def test_metrics_full_disagreement_and_agreement_mix():
    comparisons = [
        compare_member(prediction=prediction(ordinal=1), raw_result=raw(), excluded=False),
        compare_member(prediction=prediction(ordinal=2), raw_result=raw(creator="other"), excluded=False),
    ]
    m = compute_metrics(comparisons)
    assert m["creator_agreement"] == {
        "agreement_count": 1, "disagreement_count": 1, "local_missing_count": 0, "raw_missing_count": 0,
        "denominator": 2, "agreement_rate": 0.5,
    }
    assert m["complete_relationship_agreement"]["agreement_count"] == 1
    assert m["complete_relationship_agreement"]["denominator"] == 2


def test_metrics_denominator_zero_when_nothing_executed():
    comparisons = [compare_member(prediction=prediction(ordinal=1), raw_result=None, excluded=True, exclusion_reason="x")]
    m = compute_metrics(comparisons)
    assert m["creator_agreement"]["denominator"] == 0
    assert m["creator_agreement"]["agreement_rate"] is None


def test_metrics_stage2_confirmation_rate():
    comparisons = [
        compare_member(prediction=prediction(ordinal=1, stage2_skip=True, frozen_stage3_signature_if_skip="s1"),
                        raw_result=raw(funding_signature="s1"), excluded=False),
        compare_member(prediction=prediction(ordinal=2, stage2_skip=True, frozen_stage3_signature_if_skip="s2"),
                        raw_result=raw(funding_signature="different"), excluded=False),
        compare_member(prediction=prediction(ordinal=3, stage2_skip=False), raw_result=raw(), excluded=False),
    ]
    m = compute_metrics(comparisons)
    assert m["stage2_skip_calibration"]["skip_members_executed"] == 2
    assert m["stage2_skip_calibration"]["confirmations"]["CONFIRMED"] == 1
    assert m["stage2_skip_calibration"]["confirmations"]["REJECTED"] == 1
    assert m["stage2_skip_calibration"]["confirmation_rate"] == 0.5


def test_service_distribution_review_count_includes_excluded():
    comparisons = [
        compare_member(prediction=prediction(ordinal=1, fan_out_review_flag=True), raw_result=raw(), excluded=False),
        compare_member(prediction=prediction(ordinal=2, fan_out_review_flag=False), raw_result=raw(), excluded=False),
    ]
    m = compute_metrics(comparisons)
    assert m["service_distribution_review_count"] == 1


def test_local_false_negative_reported_as_measurement_limitation_not_zero():
    comparisons = [compare_member(prediction=prediction(ordinal=1), raw_result=raw(), excluded=False)]
    m = compute_metrics(comparisons)
    assert isinstance(m["local_false_negative_count"], str)
    assert "NOT_MEASURABLE" in m["local_false_negative_count"]
