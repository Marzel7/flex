"""Operation-agnostic, read-only capital-continuity qualification.

Nomination belongs to an operation-specific detector.  This module only
compares a candidate's already-retained causal route with causal routes of
existing canonical members of a requested operation.  It never writes a
treasury, membership, event, or review lead.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from src.ops.watchtower_deep_prospective import assess_prospective_route


CAPITAL_CONTINUITY_VERSION = "CAPITAL_CONTINUITY_CAUSAL_ROUTE_V1"


def _route_key(result: dict[str, Any]) -> tuple[str, str] | None:
    route = result.get("route") or {}
    pool, coordinator = route.get("pool"), route.get("coordinator")
    return (str(pool), str(coordinator)) if pool and coordinator else None


def operation_route_anchors(conn: sqlite3.Connection, operation_id: str) -> set[tuple[str, str]]:
    """Return only causal routes from canonical member mints.

    The called assessor supplies the existing slot/transaction/evidence gates;
    this helper adds no address allow-list or amount threshold.
    """
    mints = [r[0] for r in conn.execute(
        "SELECT mint FROM operator_launch_membership WHERE operator_id=? ORDER BY mint",
        (operation_id,),
    )]
    anchors: set[tuple[str, str]] = set()
    for mint in mints:
        result = assess_prospective_route(conn, mint, diagnostic_ignore_ownership=True)
        key = _route_key(result)
        if result.get("state") == "REVIEW_CANDIDATE" and key:
            anchors.add(key)
    return anchors


def assess_capital_continuity(
    conn: sqlite3.Connection, mint: str, operation_id: str,
) -> dict[str, Any]:
    """Assess one nominated candidate against canonical operation route anchors.

    QUALIFIED requires an assessor-proven causal route and an exact retained
    root/coordinator anchor match.  Unknown or absent anchor evidence is
    INSUFFICIENT, never contradictory.  A selected-upstream conflict remains
    CONFLICT and can never be weakened by a shared root.
    """
    candidate = assess_prospective_route(conn, mint)
    if candidate.get("state") == "CONFLICT":
        return {"state": "CONFLICT", "reason": candidate.get("reason"),
                "authority": "NONE", "capital_continuity": "CONTRADICTORY"}
    if candidate.get("state") != "REVIEW_CANDIDATE":
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": candidate.get("reason"),
                "authority": "NONE", "capital_continuity": "INSUFFICIENT"}
    key = _route_key(candidate)
    anchors = operation_route_anchors(conn, operation_id)
    if not key or not anchors:
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "no_canonical_causal_anchor",
                "authority": "NONE", "capital_continuity": "INSUFFICIENT"}
    if key not in anchors:
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "no_matching_causal_anchor",
                "authority": "NONE", "capital_continuity": "INSUFFICIENT",
                "candidate_route": candidate["route"], "anchor_count": len(anchors)}
    return {"state": "QUALIFIED_PROSPECTIVE_MEMBER",
            "reason": "canonical_causal_route_continuity",
            "authority": "QUALIFICATION_ONLY", "capital_continuity": "QUALIFIED",
            "candidate_route": candidate["route"], "anchor": {"pool": key[0], "coordinator": key[1]},
            "automatic_membership_allowed": False,
            "new_treasury_candidate": True}
