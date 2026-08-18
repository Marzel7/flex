"""B2Z-P2: deterministic local-vs-raw calibration comparison engine.

Consumes (A) the frozen local prediction corpus and (B) whatever raw result
corpus exists (partial or complete) and produces a per-member comparison plus
aggregate metrics with explicit numerators/denominators. Never infers or
fabricates a raw result for a member that hasn't been executed; never treats
an execution-excluded member as an evidence disagreement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

AGREEMENT = "AGREEMENT"
DISAGREEMENT = "DISAGREEMENT"
LOCAL_MISSING = "LOCAL_MISSING"
RAW_MISSING = "RAW_MISSING"
EXECUTION_EXCLUDED = "EXECUTION_EXCLUDED"
NOT_YET_EXECUTED = "NOT_YET_EXECUTED"

FIELD_STATES = {AGREEMENT, DISAGREEMENT, LOCAL_MISSING, RAW_MISSING, EXECUTION_EXCLUDED, NOT_YET_EXECUTED}


@dataclass(frozen=True)
class FieldComparison:
    field: str
    state: str
    local_value: Any
    raw_value: Any


def _compare_field(field: str, local_value: Any, raw_value: Any, *, excluded: bool, executed: bool) -> FieldComparison:
    if excluded:
        return FieldComparison(field, EXECUTION_EXCLUDED, local_value, raw_value)
    if not executed:
        return FieldComparison(field, NOT_YET_EXECUTED, local_value, raw_value)
    if local_value is None and raw_value is None:
        return FieldComparison(field, RAW_MISSING, local_value, raw_value)
    if local_value is None:
        return FieldComparison(field, LOCAL_MISSING, local_value, raw_value)
    if raw_value is None:
        return FieldComparison(field, RAW_MISSING, local_value, raw_value)
    state = AGREEMENT if local_value == raw_value else DISAGREEMENT
    return FieldComparison(field, state, local_value, raw_value)


def compare_member(*, prediction: dict[str, Any], raw_result: dict[str, Any] | None,
                    excluded: bool, exclusion_reason: str | None = None) -> dict[str, Any]:
    """Compare one member's frozen local prediction against its raw B2Z
    result (if any). raw_result=None means the member has not been executed
    yet (NOT a disagreement, NOT a missing-evidence finding)."""
    executed = raw_result is not None and not excluded
    raw = raw_result or {}

    fields = {
        "creator": _compare_field("creator", prediction.get("local_creator"), raw.get("creator"),
                                   excluded=excluded, executed=executed),
        "funding_signature": _compare_field("funding_signature", prediction.get("local_direct_funding_signature"),
                                             raw.get("funding_signature"), excluded=excluded, executed=executed),
        "direct_funder": _compare_field("direct_funder", prediction.get("local_direct_funder"),
                                         raw.get("funding_source"), excluded=excluded, executed=executed),
        "destination": _compare_field("destination", prediction.get("local_creator"), raw.get("creator_destination"),
                                       excluded=excluded, executed=executed),
        "amount": _compare_field("amount", prediction.get("local_funding_amount_lamports"), raw.get("amount_lamports"),
                                  excluded=excluded, executed=executed),
        "temporal": _compare_field("temporal", prediction.get("local_block_time"), raw.get("funding_time"),
                                    excluded=excluded, executed=executed),
    }

    if excluded:
        overall = EXECUTION_EXCLUDED
    elif not executed:
        overall = NOT_YET_EXECUTED
    elif all(f.state == AGREEMENT for f in fields.values()):
        overall = AGREEMENT
    elif any(f.state == DISAGREEMENT for f in fields.values()):
        overall = DISAGREEMENT
    else:
        overall = "PARTIAL"  # some fields missing but none disagree outright

    stage2_skip = bool(prediction.get("stage2_skip"))
    stage2_skip_confirmation = None
    if stage2_skip and executed:
        frozen_sig = prediction.get("frozen_stage3_signature_if_skip")
        raw_sig = raw.get("funding_signature")
        if frozen_sig and raw_sig:
            stage2_skip_confirmation = "CONFIRMED" if frozen_sig == raw_sig else "REJECTED"
        elif frozen_sig and not raw_sig:
            stage2_skip_confirmation = "AMBIGUOUS_NO_RAW_SIGNATURE"

    return {
        "ordinal": prediction["ordinal"],
        "mint": prediction["mint"],
        "excluded": excluded,
        "exclusion_reason": exclusion_reason,
        "executed": executed,
        "overall_state": overall,
        "field_comparisons": {k: {"state": v.state, "local": v.local_value, "raw": v.raw_value} for k, v in fields.items()},
        "fan_out_review_flag": bool(prediction.get("fan_out_review_flag")),
        "stage2_skip": stage2_skip,
        "stage2_skip_confirmation": stage2_skip_confirmation,
    }


def compute_metrics(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute explicit numerator/denominator metrics. Excludes
    EXECUTION_EXCLUDED and NOT_YET_EXECUTED members from all agreement-rate
    denominators -- they are reported separately, never folded into the
    evidence-quality metrics."""
    executed = [c for c in comparisons if c["executed"]]
    excluded = [c for c in comparisons if c["excluded"]]
    not_yet = [c for c in comparisons if not c["executed"] and not c["excluded"]]

    def field_rate(field: str) -> dict[str, Any]:
        states = [c["field_comparisons"][field]["state"] for c in executed]
        agree = states.count(AGREEMENT)
        disagree = states.count(DISAGREEMENT)
        local_missing = states.count(LOCAL_MISSING)
        raw_missing = states.count(RAW_MISSING)
        denom = len(states)
        return {
            "agreement_count": agree, "disagreement_count": disagree,
            "local_missing_count": local_missing, "raw_missing_count": raw_missing,
            "denominator": denom,
            "agreement_rate": round(agree / denom, 4) if denom else None,
        }

    complete_agreement = sum(1 for c in executed if c["overall_state"] == AGREEMENT)
    local_false_positives = sum(
        1 for c in executed
        if c["field_comparisons"]["amount"]["state"] == DISAGREEMENT
        or c["field_comparisons"]["funding_signature"]["state"] == DISAGREEMENT
    )
    # A local false negative would require a member with NO local prediction
    # that raw execution nonetheless finds valid evidence for -- structurally
    # not measurable from this 20-member fixed cohort (all 20 have SOME local
    # prediction per P1.6/P1.7), so this is reported as a measurement
    # limitation rather than assumed zero.
    local_false_negatives = "NOT_MEASURABLE_FROM_THIS_COHORT (all 20 members have local predictions; a false-negative requires a member with local_missing evidence that raw execution nonetheless validates)"

    raw_verification_failures = sum(1 for c in executed if c["overall_state"] == "RAW_VERIFICATION_FAILED")
    service_review = sum(1 for c in comparisons if c["fan_out_review_flag"])

    stage2_skips_executed = [c for c in executed if c["stage2_skip"]]
    stage2_confirmations = {
        "CONFIRMED": sum(1 for c in stage2_skips_executed if c["stage2_skip_confirmation"] == "CONFIRMED"),
        "REJECTED": sum(1 for c in stage2_skips_executed if c["stage2_skip_confirmation"] == "REJECTED"),
        "AMBIGUOUS_NO_RAW_SIGNATURE": sum(1 for c in stage2_skips_executed if c["stage2_skip_confirmation"] == "AMBIGUOUS_NO_RAW_SIGNATURE"),
    }

    return {
        "population": {
            "total_cohort": len(comparisons),
            "executed": len(executed),
            "execution_excluded": len(excluded),
            "not_yet_executed": len(not_yet),
        },
        "creator_agreement": field_rate("creator"),
        "funding_signature_agreement": field_rate("funding_signature"),
        "direct_funder_agreement": field_rate("direct_funder"),
        "destination_agreement": field_rate("destination"),
        "amount_agreement": field_rate("amount"),
        "temporal_agreement": field_rate("temporal"),
        "complete_relationship_agreement": {
            "agreement_count": complete_agreement, "denominator": len(executed),
            "agreement_rate": round(complete_agreement / len(executed), 4) if executed else None,
        },
        "local_false_positive_count": local_false_positives,
        "local_false_negative_count": local_false_negatives,
        "raw_verification_failure_count": raw_verification_failures,
        "service_distribution_review_count": service_review,
        "stage2_skip_calibration": {
            "skip_members_executed": len(stage2_skips_executed),
            "confirmations": stage2_confirmations,
            "confirmation_rate": (
                round(stage2_confirmations["CONFIRMED"] / len(stage2_skips_executed), 4)
                if stage2_skips_executed else None
            ),
        },
    }
