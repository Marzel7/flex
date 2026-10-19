"""Fail-open quality classification for Unknown repeat-funder observations.

This deliberately does *not* decide whether an address is an operation.  It only
answers whether a retained funding observation may contribute to the Unknown
recurrence count.  Amount and wallet age are supporting data, never gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet


class EdgeQuality(str, Enum):
    QUALIFYING_FUNDING_EDGE = "QUALIFYING_FUNDING_EDGE"
    DUST_SPAM_EDGE = "DUST_SPAM_EDGE"
    ENVIRONMENTAL_EDGE = "ENVIRONMENTAL_EDGE"
    INSUFFICIENT_TO_CLASSIFY = "INSUFFICIENT_TO_CLASSIFY"


@dataclass(frozen=True)
class FundingObservation:
    """Evidence facts only; callers must not infer missing facts as false."""

    proven_funding_role: bool = False
    launch_coupling: bool = False
    transaction_role_consistent: bool = False
    role_inconsistent: bool = False
    environmental_or_post_launch: bool = False
    broad_unrelated_fanout: bool = False
    repeated_unsolicited_tiny_transfers: bool = False
    broadcast_style_amount_pattern: bool = False
    creator_specific_coupling_absent: bool = False
    amount_lamports: int | None = None
    funder_account_age_seconds: int | None = None


def classify_unknown_funder_edge(observation: FundingObservation) -> tuple[EdgeQuality, FrozenSet[str]]:
    """Classify one observation with a hard false-positive bias.

    Strong role/coupling evidence always wins.  A dust result requires weak
    semantics *and two independent non-amount spam signals*.  Novelty and value
    are intentionally omitted from decision predicates.
    """
    reasons: set[str] = set()
    if observation.proven_funding_role:
        reasons.add("PROVEN_FUNDING_ROLE")
    if observation.launch_coupling:
        reasons.add("LAUNCH_COUPLING")
    if observation.transaction_role_consistent:
        reasons.add("TRANSACTION_ROLE_CONSISTENT")
    if observation.proven_funding_role or (
        observation.launch_coupling and observation.transaction_role_consistent
    ):
        return EdgeQuality.QUALIFYING_FUNDING_EDGE, frozenset(reasons)

    weak_semantics = (
        observation.creator_specific_coupling_absent
        or observation.role_inconsistent
        or observation.environmental_or_post_launch
    )
    signals = {
        "BROAD_UNRELATED_CREATOR_FANOUT": observation.broad_unrelated_fanout,
        "REPEATED_UNSOLICITED_TINY_TRANSFERS": observation.repeated_unsolicited_tiny_transfers,
        "BROADCAST_STYLE_AMOUNT_PATTERN": observation.broadcast_style_amount_pattern,
        "ROLE_INCONSISTENCY": observation.role_inconsistent,
        "ENVIRONMENTAL_OR_POST_LAUNCH_TIMING": observation.environmental_or_post_launch,
        "NO_CREATOR_SPECIFIC_LAUNCH_COUPLING": observation.creator_specific_coupling_absent,
    }
    active = {reason for reason, is_active in signals.items() if is_active}
    if observation.environmental_or_post_launch and observation.role_inconsistent:
        return EdgeQuality.ENVIRONMENTAL_EDGE, frozenset(active | {"WEAK_LAUNCH_SEMANTICS"})
    if weak_semantics and len(active - {"NO_CREATOR_SPECIFIC_LAUNCH_COUPLING"}) >= 2:
        return EdgeQuality.DUST_SPAM_EDGE, frozenset(active | {"WEAK_LAUNCH_SEMANTICS"})
    return EdgeQuality.INSUFFICIENT_TO_CLASSIFY, frozenset(reasons | active)
