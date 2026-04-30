"""
Second-hop confidence scorer.

Takes metrics about an upstream wallet bridging two first-hop networks
and returns a confidence score (0-100), risk level, and reason codes.
"""

from dataclasses import dataclass, field
from typing import List

# Reason code constants
REASON_SHARED_UPSTREAM   = "shared_upstream_funder"
REASON_FUNDER_TO_FUNDER  = "funder_to_funder_transfer"
REASON_BURST_TIMING      = "same_upstream_and_burst_timing"
REASON_AMOUNT_SIMILARITY = "similar_transfer_amounts"
REASON_CLUSTER_OVERLAP   = "overlap_with_wallet_cluster"
REASON_FARM_OVERLAP      = "overlap_with_farm_cluster"
REASON_MULTI_BRIDGE      = "upstream_bridges_3plus_networks"


@dataclass
class SecondHopScore:
    confidence_score: float
    risk_level: str
    reason_codes: List[str] = field(default_factory=list)


def score_upstream_bridge(
    upstream_address: str,
    funders_bridged: int,
    networks_bridged: int,
    amount_stddev: float,       # stddev of avg_transfer_sol per funder row
    burst_detected: bool,
    in_wallet_cluster: bool,
    in_farm_cluster: bool,
    is_excluded: bool,
) -> SecondHopScore:
    """
    Score an upstream wallet's coordination signal.

    Points breakdown (max 100):
      40 — funders bridged (core signal)
      20 — networks bridged (breadth)
      15 — burst timing
      10 — amount similarity (low stddev)
      10 — cluster overlap (wallet + farm combined, capped at 10)
           cluster points only count when a structural signal is present:
           funders_bridged >= 3, burst_detected, or networks_bridged >= 3
    """
    if is_excluded:
        return SecondHopScore(0.0, "LOW", [])

    score = 0.0
    reasons: List[str] = []

    # Core: how many distinct known funders does this upstream reach?
    if funders_bridged >= 5:
        score += 40
        reasons.append(REASON_SHARED_UPSTREAM)
    elif funders_bridged >= 3:
        score += 28
        reasons.append(REASON_SHARED_UPSTREAM)
    elif funders_bridged >= 2:
        score += 15
        reasons.append(REASON_SHARED_UPSTREAM)

    # Breadth: how many distinct networks does it bridge?
    if networks_bridged >= 3:
        score += 20
        reasons.append(REASON_MULTI_BRIDGE)
    elif networks_bridged >= 2:
        score += 12

    # Burst timing: funders activated close together
    if burst_detected:
        score += 15
        reasons.append(REASON_BURST_TIMING)

    # Amount consistency: low stddev across per-funder avg_transfer_sol
    if amount_stddev < 0.1:
        score += 10
        reasons.append(REASON_AMOUNT_SIMILARITY)
    elif amount_stddev < 0.5:
        score += 5

    # Cluster overlap — capped at 10 pts combined, and only when at least one
    # structural signal is present (high funder count, burst, or multi-network).
    # Without a structural signal, cluster co-membership alone is too weak a basis.
    has_structural_signal = funders_bridged >= 3 or burst_detected or networks_bridged >= 3
    if has_structural_signal:
        cluster_pts = 0
        if in_wallet_cluster:
            cluster_pts += 7
            reasons.append(REASON_CLUSTER_OVERLAP)
        if in_farm_cluster:
            cluster_pts += 7
            reasons.append(REASON_FARM_OVERLAP)
        score += min(10, cluster_pts)

    score = min(100.0, score)

    if score >= 80:
        risk = "CRITICAL"
    elif score >= 60:
        risk = "HIGH"
    elif score >= 35:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    return SecondHopScore(score, risk, reasons)
