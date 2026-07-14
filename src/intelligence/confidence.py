"""
Entity Intelligence — confidence derivation.

Confidence is DERIVED from evidence. Never invented.

Algorithm:
  1. Collect all confidence signals from knowledge items and operation observations.
  2. Ceiling = weakest signal (confidence can't exceed the weakest contributor).
  3. Floor   = strongest signal (lower bound — something is definitely known).
  4. Final level = floor, capped by ceiling.

Confidence vocabulary (ordered strongest → weakest):
  CERTAIN > HIGH > MEDIUM > LOW > UNKNOWN

Examples:
  - Treasury confirmed CERTAIN + knowledge rule HIGH  → ceiling=HIGH, floor=CERTAIN → HIGH
  - Only MEDIUM knowledge rule                        → MEDIUM
  - No data at all                                    → UNKNOWN
"""

from __future__ import annotations

from src.intelligence.models import (
    CONFIDENCE_LEVELS,
    CONFIDENCE_RANK,
    ConfidenceBreakdown,
    OperationObservation,
)


def _parse_confidence(raw: object) -> str:
    """Normalise a confidence value to one of CONFIDENCE_LEVELS."""
    if isinstance(raw, str) and raw.upper() in CONFIDENCE_RANK:
        return raw.upper()
    if isinstance(raw, (int, float)):
        # WATCHTOWER subprov stores 0.0–1.0 float
        if raw >= 0.9:
            return "HIGH"
        if raw >= 0.6:
            return "MEDIUM"
        if raw >= 0.3:
            return "LOW"
    return "UNKNOWN"


def derive_confidence(
    operations: list[OperationObservation],
    knowledge:  list[dict],
) -> ConfidenceBreakdown:
    """
    Derive entity-level confidence from operation observations and knowledge items.

    Returns a ConfidenceBreakdown explaining how the level was reached.
    """
    signals:             list[tuple[str, str]] = []   # (level, description)

    # ── Signals from operations ────────────────────────────────────────────────
    for obs in operations:
        raw_conf = obs.facts.get("confidence")
        if raw_conf is not None:
            level = _parse_confidence(raw_conf)
            signals.append((
                level,
                f"{obs.display_name} {obs.role}: confidence={raw_conf}",
            ))
        else:
            # Presence in an operation is itself a LOW signal
            signals.append((
                "LOW",
                f"Observed by {obs.display_name} as {obs.role} (no explicit confidence)",
            ))

    # ── Signals from knowledge items ───────────────────────────────────────────
    for item in knowledge:
        level = _parse_confidence(item.get("confidence", "UNKNOWN"))
        signals.append((
            level,
            f"Knowledge rule {item.get('type', '?')} ({item.get('category', '?')}): "
            f"confidence={item.get('confidence', 'UNKNOWN')}",
        ))

    # ── No data at all ─────────────────────────────────────────────────────────
    if not signals:
        return ConfidenceBreakdown(
            level              = "UNKNOWN",
            contributing_facts = ("No observations or knowledge derived.",),
            ceiling            = "UNKNOWN",
            floor              = "UNKNOWN",
        )

    levels  = [s[0] for s in signals]
    ranks   = [CONFIDENCE_RANK[lv] for lv in levels]

    # Ceiling = weakest contributor (highest rank number)
    ceiling_idx = max(range(len(ranks)), key=lambda i: ranks[i])
    ceiling     = levels[ceiling_idx]

    # Floor = strongest contributor (lowest rank number)
    floor_idx   = min(range(len(ranks)), key=lambda i: ranks[i])
    floor       = levels[floor_idx]

    # Final = floor capped by ceiling
    final_rank  = max(CONFIDENCE_RANK[floor], CONFIDENCE_RANK[ceiling])
    final_level = CONFIDENCE_LEVELS[final_rank]

    contributing_facts = tuple(desc for _, desc in signals)

    return ConfidenceBreakdown(
        level              = final_level,
        contributing_facts = contributing_facts,
        ceiling            = ceiling,
        floor              = floor,
    )
