"""Deterministic, provider-free classification of a candidate funding
relationship into the B2Z-P3 five-category policy
(docs/audits/overnight_b2z_p3_selective_rpc_policy.json), plus the explicit
rejection flags defined in the B2Z-2H contract
(docs/audits/overnight_b2z_2h_upstream_funding_contract.json).

Pure function, no I/O, no provider dependency -- consumes already-fetched
local evidence fields and returns a classification. Reuses the existing
repository thresholds (MIN_UPSTREAM_SOL, MAX_UPSTREAM_FUNDERS from
src/core/second_hop_builder.py) rather than inventing new ones.
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_UPSTREAM_SOL = 0.01  # matches src/core/second_hop_builder.py MIN_UPSTREAM_SOL exactly
MAX_UPSTREAM_FUNDERS = 50  # matches src/core/second_hop_builder.py MAX_UPSTREAM_FUNDERS exactly
DEFAULT_TEMPORAL_GAP_THRESHOLD_SECONDS = 3600  # preserves B2Z-P1.6's HIGH-confidence threshold verbatim

LOCAL_EVIDENCE_SUFFICIENT_FOR_DISCOVERY = "LOCAL_EVIDENCE_SUFFICIENT_FOR_DISCOVERY"
LOCAL_EVIDENCE_SUFFICIENT_RAW_VERIFICATION_OPTIONAL = "LOCAL_EVIDENCE_SUFFICIENT_RAW_VERIFICATION_OPTIONAL"
RAW_VERIFICATION_REQUIRED = "RAW_VERIFICATION_REQUIRED"
MISSING_LOCAL_EVIDENCE = "MISSING_LOCAL_EVIDENCE"


@dataclass(frozen=True)
class CandidateEvidence:
    source: str | None
    destination: str | None
    amount_lamports: int | None
    signature: str | None
    block_time: int | None
    reference_event_time: int | None  # e.g. migration_time or direct_funding_time
    extraction_failed: bool = False
    fan_out_count: int | None = None  # distinct downstream entities reached by `source`, if known
    service_tag_present: bool = False


@dataclass(frozen=True)
class ClassificationResult:
    category: str
    reasons: tuple[str, ...]
    service_distribution_review: bool
    rejected_pre_dispatch: bool


def classify(evidence: CandidateEvidence, *,
             temporal_gap_threshold_seconds: int = DEFAULT_TEMPORAL_GAP_THRESHOLD_SECONDS) -> ClassificationResult:
    reasons: list[str] = []

    # Step 1: missing evidence
    if evidence.signature is None or evidence.amount_lamports is None or evidence.block_time is None:
        return ClassificationResult(MISSING_LOCAL_EVIDENCE, ("no_signature_bound_row",), False, False)

    # Step 2: self-loop (source == destination) -- REJECT pre-dispatch, never a usable candidate
    if evidence.source is not None and evidence.source == evidence.destination:
        return ClassificationResult(RAW_VERIFICATION_REQUIRED, ("self_loop",), False, True)

    # Step 3: impossible temporal ordering -- REJECT pre-dispatch
    if evidence.reference_event_time is not None and evidence.block_time >= evidence.reference_event_time:
        return ClassificationResult(RAW_VERIFICATION_REQUIRED, ("impossible_temporal_ordering",), False, True)

    # Step 4: dust
    amount_sol = evidence.amount_lamports / 1_000_000_000
    is_dust = amount_sol < MIN_UPSTREAM_SOL
    if is_dust:
        reasons.append("dust_amount")

    # Step 5: documented extraction failure
    if evidence.extraction_failed:
        reasons.append("documented_extraction_failure")

    # Step 6: temporal gap
    temporal_gap = None
    gap_too_large = False
    if evidence.reference_event_time is not None:
        temporal_gap = evidence.reference_event_time - evidence.block_time
        gap_too_large = temporal_gap > temporal_gap_threshold_seconds
        if gap_too_large:
            reasons.append("temporal_gap_exceeds_threshold")

    # Orthogonal: service/distribution review (non-gating)
    is_mega_hub = evidence.fan_out_count is not None and evidence.fan_out_count > MAX_UPSTREAM_FUNDERS
    service_review = is_mega_hub or evidence.service_tag_present
    if is_mega_hub:
        reasons.append(f"fan_out_{evidence.fan_out_count}_exceeds_{MAX_UPSTREAM_FUNDERS}")
    if evidence.service_tag_present:
        reasons.append("known_service_bot_tag")

    if is_dust or evidence.extraction_failed or gap_too_large:
        return ClassificationResult(RAW_VERIFICATION_REQUIRED, tuple(reasons), service_review, False)

    if not reasons:
        return ClassificationResult(LOCAL_EVIDENCE_SUFFICIENT_RAW_VERIFICATION_OPTIONAL, ("all_corroborating_signals_present",), service_review, False)

    return ClassificationResult(LOCAL_EVIDENCE_SUFFICIENT_FOR_DISCOVERY, tuple(reasons) or ("baseline",), service_review, False)
