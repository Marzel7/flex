"""Operation-agnostic infrastructure-role replay semantics.

This pure module deliberately has no database or operation-name knowledge.  It
allows a replay to retain transaction/path evidence while preventing an
infrastructure role from serving as the sole proof of operational continuity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

INFRASTRUCTURE_ROLES = frozenset({
    "RELAY_SOLVER", "ROUTER", "SERVICE_WALLET",
    "KNOWN_INFRASTRUCTURE_INTERMEDIARY",
})


@dataclass(frozen=True)
class MembershipEvidence:
    mint: str
    direct_roles: tuple[str, ...] = ()
    independently_proven: bool = False
    depends_on_contaminated_entity: bool = False


def classify_membership(row: MembershipEvidence) -> str:
    """Classify one row without deleting its underlying evidence.

    An independently proven membership always survives.  A direct
    infrastructure-only route is excluded; a dependency propagated from such
    a route is explicitly distinguished as indirect rather than silently
    discarded.
    """
    has_infrastructure = bool(INFRASTRUCTURE_ROLES.intersection(row.direct_roles))
    if row.independently_proven:
        return "RELAY_PRESENT_BUT_INDEPENDENTLY_PROVEN" if has_infrastructure else "UNAFFECTED"
    if has_infrastructure:
        return "DIRECT_RELAY_DEPENDENT"
    if row.depends_on_contaminated_entity:
        return "INDIRECT_RELAY_DEPENDENT"
    return "UNAFFECTED"


def replay_research_cohort(rows: Iterable[MembershipEvidence]) -> dict[str, str]:
    """Return research-only replay dispositions keyed by mint."""
    result: dict[str, str] = {}
    for row in rows:
        classification = classify_membership(row)
        if classification == "DIRECT_RELAY_DEPENDENT":
            result[row.mint] = "RELAY_CONTAMINATED_REMOVE_FROM_RESEARCH_COHORT"
        elif classification == "INDIRECT_RELAY_DEPENDENT":
            result[row.mint] = "INSUFFICIENT_AFTER_RELAY_EXCLUSION"
        elif classification == "RELAY_PRESENT_BUT_INDEPENDENTLY_PROVEN":
            result[row.mint] = "RETAINED_WATCHTOWER"
        else:
            result[row.mint] = "UNAFFECTED"
    return result
