"""Operation-agnostic opening-to-lifecycle continuity semantics.

This module represents evidence gaps.  It never invents observations, calls a
provider, or changes the independently supplied opening fact.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


CONTRACT_VERSION = "OPERATION_OPENING_TO_LIFECYCLE_GAP_V1"
GAP_TYPE = "UNOBSERVED_LIFECYCLE_GAP"


@dataclass(frozen=True)
class LifecycleGap:
    operation_id: str
    mint: str
    gap_start: int
    gap_end: int
    pre_gap_fact_reference: str
    post_gap_fact_reference: str
    provenance: dict[str, Any]

    @property
    def seconds(self) -> int:
        return self.gap_end - self.gap_start

    def payload(self) -> dict[str, Any]:
        if self.seconds <= 0:
            raise ValueError("LIFECYCLE_GAP_REQUIRES_LATER_OBSERVATION")
        return {
            "OPERATION_ID": self.operation_id,
            "MINT": self.mint,
            "GAP_TYPE": GAP_TYPE,
            "GAP_START": self.gap_start,
            "GAP_END": self.gap_end,
            "GAP_SECONDS": self.seconds,
            "GAP_STATE": "UNOBSERVED",
            "PRE_GAP_FACT_REFERENCE": self.pre_gap_fact_reference,
            "POST_GAP_FACT_REFERENCE": self.post_gap_fact_reference,
            "EVIDENCE_COMPLETENESS": "PARTIAL",
            "PROVENANCE_DIGEST": hashlib.sha256(
                json.dumps(self.provenance, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "CONTRACT_VERSION": CONTRACT_VERSION,
        }


def gap_closure_requirement() -> str:
    return (
        "Qualified retained observations must cover the missing interval under "
        "the operation-specific observation contract; endpoint agreement, later "
        "candles, interpolation, chart appearance, and cross-token inference do not close a gap."
    )


def observation_offset_not_peak_time(opening_time: int, first_observation_time: int) -> dict[str, Any]:
    """Keep provider-window timing distinct from peak timing."""
    if first_observation_time <= opening_time:
        raise ValueError("FIRST_OBSERVATION_MUST_FOLLOW_OPENING")
    return {
        "FIRST_PROVIDER_OBSERVATION_OFFSET_SECONDS": first_observation_time - opening_time,
        "OPENING_TO_PEAK_TIME": "NOT_PROVEN",
        "INTERPOLATED_OBSERVATION_COUNT": 0,
    }
