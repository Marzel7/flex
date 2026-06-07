"""
discovery_value.py — score discovery objects by EXPECTED NETWORK-EXPANSION VALUE.

This is a new axis, orthogonal to confidence / severity / attribution-certainty. It
answers one question only:

    "How much could investigating this object expand the WATCHTOWER graph —
     i.e. reveal NEW addresses, edges, or infrastructure we don't already have?"

Why it's orthogonal to confidence:
    A 100%-confident KNOWN operator has LOW discovery value — investigating it reveals
    nothing new. A 40%-confidence reservoir wallet has HIGH discovery value — its first
    conversion would expose a brand-new creator lineage before provisioning-hub detection
    even fires. Confidence ranks "how sure are we"; discovery value ranks "what could we
    learn". A dormant analyst should rank by information gain, not certainty.

Discovery value is HIGH / MEDIUM / LOW (+ a numeric score for ordering), with an explicit
"what could this reveal" string per object so the analyst sees the upside, not just a rank.
"""
from __future__ import annotations

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"
_BAND = {HIGH: 3, MEDIUM: 2, LOW: 1}

# ── Attribution linkage → network-expansion potential ────────────────────────
# The judgement: which linkage types point at infrastructure we can pivot from to
# find NEW addresses, vs which just re-confirm a wallet we already understand.
LINKAGE_VALUE = {
    # funded directly by infra → pivot to the funder reveals sibling creators + the
    # infra wallet's other edges. Highest expansion potential.
    "WATCHTOWER_DIRECT":       (HIGH,   "may reveal new operator funding edges (siblings of this creator)"),
    # terminates at a provisioning hub → the hub funds many creators; one hit can
    # unroll an entire un-seen cohort.
    "WATCHTOWER_PROVISIONING": (HIGH,   "may expose a provisioning hub's full creator cohort"),
    # multi-hop lineage → walking upstream can surface new intermediary infrastructure.
    "WATCHTOWER_LINEAGE":      (MEDIUM, "may expose upstream funders along the lineage"),
    # profit relay → extraction side; relay counterparties can be new, but it's the
    # money-out path, less likely to reveal NEW launch infrastructure.
    "WATCHTOWER_RELAY":        (MEDIUM, "relay counterparties may include unseen wallets"),
    # collector/aggregator flow → mostly converges on known aggregators.
    "WATCHTOWER_COLLECTOR":    (LOW,    "converges on known collectors — limited new ground"),
    # fingerprint-only → soft, already-held; confirms a pattern, rarely new edges.
    "WATCHTOWER_FINGERPRINT":  (LOW,    "pattern match only — confirms, rarely reveals new addresses"),
    "NONE":                    (LOW,    "no attribution linkage to pivot from"),
}


def attribution_discovery_value(linkage: str) -> tuple[str, str]:
    """(band, reveal_text) for an attributed creator, by its linkage type."""
    return LINKAGE_VALUE.get(linkage, (LOW, "limited expansion potential"))


def evidence_expansion_potential() -> dict:
    """Per-evidence-type expansion potential for the Attribution Intelligence panel.
    Maps the by_evidence buckets to HIGH/MEDIUM/LOW discovery value."""
    return {
        "direct_infrastructure": HIGH,
        "provisioning_hub":      HIGH,
        "lineage":               MEDIUM,
        "relay_funded":          MEDIUM,
        "collector_flow":        LOW,
        "fingerprint":           LOW,
    }


def reservoir_discovery_value(dormant: int, converted: int) -> tuple[str, str]:
    """
    Reservoir cohort discovery value. The intelligence upside is the FIRST conversion:
    a dormant wallet that launches reveals a new creator lineage *before* provisioning-
    hub detection would catch it — a genuine new-infrastructure lead. So an unconverted
    pool of dormant wallets is HIGH discovery value precisely because nothing is known yet.
    """
    if dormant <= 0:
        return (LOW, "no dormant wallets to convert")
    if converted == 0:
        return (HIGH, "first conversion could reveal a new creator lineage before "
                      "provisioning-hub detection")
    return (MEDIUM, "conversions in progress — each reveals a new launch lineage")


def cluster_discovery_value(confidence: float, members: int, provisioners: int,
                            growing: bool, new_counterparty: bool) -> tuple[str, str]:
    """
    Emerging-cluster discovery value = likelihood it leads to NEW attributable
    infrastructure. Driven by expansion signals (provisioners, new counterparties,
    growth, member breadth), NOT by raw confidence. A small static high-confidence
    cluster we already understand is LOW; a growing one with new funders is HIGH.
    """
    score = 0
    reasons = []
    if provisioners:      score += 2; reasons.append("shared provisioning signature")
    if new_counterparty:  score += 2; reasons.append("new funder/recipient observed")
    if growing:           score += 1; reasons.append("growing")
    if members >= 3:      score += 1; reasons.append("multi-creator")
    # confidence is a minor tie-breaker only — high confidence on a static cluster does
    # NOT make it high discovery value.
    band = HIGH if score >= 3 else MEDIUM if score >= 1 else LOW
    if not reasons:
        reasons.append("shared-funder pattern — may expose upstream funders")
    return (band, " · ".join(reasons))


def band_rank(band: str) -> int:
    return _BAND.get(band, 0)
