"""Edge-case coverage for the deterministic funding-evidence classifier
(B2Z-P3 policy / B2Z-2H rejection rules). Fail-closed for structurally
impossible/ambiguous cases, candidate-review for service/distribution
ambiguity, never silent discard.
"""
from __future__ import annotations

from src.acquisition.funding_evidence_classifier import (
    LOCAL_EVIDENCE_SUFFICIENT_FOR_DISCOVERY,
    LOCAL_EVIDENCE_SUFFICIENT_RAW_VERIFICATION_OPTIONAL,
    MISSING_LOCAL_EVIDENCE,
    RAW_VERIFICATION_REQUIRED,
    CandidateEvidence,
    classify,
)


def ev(**overrides):
    base = dict(source="funderX", destination="creatorX", amount_lamports=1_000_000_000,
                signature="sigX", block_time=1000, reference_event_time=2000)
    base.update(overrides)
    return CandidateEvidence(**base)


# --- self-funding / self-loop ----------------------------------------------

def test_self_funding_creator_equals_direct_funder():
    result = classify(ev(source="walletA", destination="walletA"))
    assert result.category == RAW_VERIFICATION_REQUIRED
    assert "self_loop" in result.reasons
    assert result.rejected_pre_dispatch is True


def test_upstream_self_loop():
    """Same shape as direct self-funding, applied at the upstream hop --
    exactly the failure mode P1.7 measured (3/9 raw candidates)."""
    result = classify(ev(source="creatorX", destination="creatorX"))
    assert result.category == RAW_VERIFICATION_REQUIRED
    assert result.rejected_pre_dispatch is True


# --- dust --------------------------------------------------------------

def test_dust_amount_flagged_not_auto_rejected():
    result = classify(ev(amount_lamports=1))  # 1 lamport, matches P1.7's real measured dust candidates
    assert result.category == RAW_VERIFICATION_REQUIRED
    assert "dust_amount" in result.reasons
    assert result.rejected_pre_dispatch is False  # flagged, not pre-dispatch-rejected


def test_amount_exactly_at_dust_floor_is_not_dust():
    result = classify(ev(amount_lamports=10_000_000))  # exactly 0.01 SOL
    assert "dust_amount" not in result.reasons


def test_amount_just_below_dust_floor_is_dust():
    result = classify(ev(amount_lamports=9_999_999))
    assert "dust_amount" in result.reasons


# --- service wallet / mega-hub -------------------------------------------

def test_service_wallet_tag_flagged_non_gating():
    result = classify(ev(service_tag_present=True))
    assert result.service_distribution_review is True
    assert result.category != RAW_VERIFICATION_REQUIRED  # does not force RPC purely for the tag


def test_mega_hub_fan_out_flagged():
    result = classify(ev(fan_out_count=131))  # matches the real measured Axiom address
    assert result.service_distribution_review is True
    assert any("fan_out_131" in r for r in result.reasons)


def test_high_fan_out_below_threshold_not_flagged():
    result = classify(ev(fan_out_count=50))  # exactly at threshold, not exceeding
    assert result.service_distribution_review is False


def test_high_fan_in_above_threshold_flagged():
    result = classify(ev(fan_out_count=71))  # matches a real measured top-fan-in upstream
    assert result.service_distribution_review is True


# --- multiple candidates (handled by caller selection, classifier just scores one) --

def test_multiple_direct_candidates_each_classified_independently():
    a = classify(ev(signature="sigA", amount_lamports=1_000_000_000))
    b = classify(ev(signature="sigB", amount_lamports=1))
    assert a.category != b.category  # the dust one is flagged differently


# --- same-slot / temporal edge cases ---------------------------------------

def test_same_slot_transfer_at_exact_reference_time_is_impossible_ordering():
    result = classify(ev(block_time=2000, reference_event_time=2000))  # equal, not strictly before
    assert result.category == RAW_VERIFICATION_REQUIRED
    assert "impossible_temporal_ordering" in result.reasons
    assert result.rejected_pre_dispatch is True


def test_post_launch_funding_rejected():
    result = classify(ev(block_time=3000, reference_event_time=2000))  # AFTER reference event
    assert result.category == RAW_VERIFICATION_REQUIRED
    assert "impossible_temporal_ordering" in result.reasons


def test_temporal_gap_within_threshold_is_high_confidence():
    result = classify(ev(block_time=1900, reference_event_time=2000))  # 100s gap
    assert "temporal_gap_exceeds_threshold" not in result.reasons


def test_temporal_gap_exceeds_threshold_requires_verification():
    result = classify(ev(block_time=1000, reference_event_time=2000 + 3601))  # gap = 4601s > 3600
    assert result.category == RAW_VERIFICATION_REQUIRED
    assert "temporal_gap_exceeds_threshold" in result.reasons


# --- missing evidence -------------------------------------------------------

def test_missing_signature():
    result = classify(ev(signature=None))
    assert result.category == MISSING_LOCAL_EVIDENCE


def test_missing_timestamp():
    result = classify(ev(block_time=None))
    assert result.category == MISSING_LOCAL_EVIDENCE


def test_missing_amount():
    result = classify(ev(amount_lamports=None))
    assert result.category == MISSING_LOCAL_EVIDENCE


# --- extraction failure history --------------------------------------------

def test_documented_extraction_failure_requires_verification():
    """Matches the real measured 90s-timeout ordinals 11/15/18."""
    result = classify(ev(extraction_failed=True))
    assert result.category == RAW_VERIFICATION_REQUIRED
    assert "documented_extraction_failure" in result.reasons


# --- failed transaction / duplicate transfer (caller-level concerns, classifier assumes valid tx) --

def test_classifier_does_not_second_guess_a_valid_positive_amount_transfer():
    """Failed-transaction filtering happens upstream (transfer_index only
    contains successful transfers by construction) -- the classifier trusts
    its inputs are already positive-lamport, valid transfers, and correctly
    classifies a clean one as sufficient."""
    result = classify(ev())
    assert result.category in (LOCAL_EVIDENCE_SUFFICIENT_FOR_DISCOVERY, LOCAL_EVIDENCE_SUFFICIENT_RAW_VERIFICATION_OPTIONAL)


# --- conflicting sources / stale evidence (documented via reasons, never silently dropped) --

def test_clean_case_produces_no_reasons_requiring_verification():
    result = classify(ev(block_time=1900, reference_event_time=2000, amount_lamports=1_000_000_000))
    assert result.category in (LOCAL_EVIDENCE_SUFFICIENT_FOR_DISCOVERY, LOCAL_EVIDENCE_SUFFICIENT_RAW_VERIFICATION_OPTIONAL)
    assert result.reasons  # always has SOME explanation, never silently empty-and-unexplained


def test_all_reasons_are_always_populated_never_empty_tuple():
    """Every classification path must explain itself -- 'do not silently
    discard difficult evidence' per instruction."""
    cases = [
        ev(), ev(source="a", destination="a"), ev(amount_lamports=1),
        ev(signature=None), ev(extraction_failed=True), ev(fan_out_count=200),
        ev(block_time=2000, reference_event_time=2000),
    ]
    for case in cases:
        result = classify(case)
        assert len(result.reasons) > 0
