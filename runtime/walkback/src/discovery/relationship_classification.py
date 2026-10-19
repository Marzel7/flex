"""X75.3A Structural Graph Context & Data Integrity Validation.

The platform must never render these five distinct relationship kinds as
if they were interchangeable:

  1. DIRECT_RELATIONSHIP    -- an evidenced transactional/funding edge
                               between two specific wallets (a real
                               wt_provisioning_edges row, a
                               wt_wrap_close_candidates lineage link, etc.)
  2. STRUCTURAL_MEMBERSHIP  -- two wallets co-occur in the same
                               Investigation Population because they pass
                               EmergingOperatorService's cohesion test
                               (shared treasuries + mechanism + creator
                               fan-out pattern similarity) -- NOT because
                               they ever transacted with each other.
  3. SHARED_INFRASTRUCTURE  -- a wallet is a member/role of something
                               (a population, a treasury family) without
                               being confirmed as controlling it.
  4. REVIEW_DECISION        -- a human analyst's Treasury Review verdict
                               (APPROVED/REJECTED/PENDING_REVIEW/etc.) on
                               a specific wallet.
  5. CANONICAL_IDENTITY     -- a wallet's confirmed role (operator_entities
                               entry) under a specific, CONFIRMED Operator.

This module is a pure read-only classifier: given two wallet addresses, it
answers "what kind of relationship, if any, connects them" using ONLY
already-persisted evidence -- it invents nothing, infers nothing, and
never treats co-membership as proof of a direct edge. It performs no
detection, no RPC, no writes, and does not alter attribution,
reconciliation, promotion, or resolver logic in any way.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

DIRECT_RELATIONSHIP = "DIRECT_RELATIONSHIP"
STRUCTURAL_MEMBERSHIP = "STRUCTURAL_MEMBERSHIP"
SHARED_INFRASTRUCTURE = "SHARED_INFRASTRUCTURE"
REVIEW_DECISION = "REVIEW_DECISION"
CANONICAL_IDENTITY = "CANONICAL_IDENTITY"


@dataclass(frozen=True, slots=True)
class DirectEdgeCheck:
    exists: bool
    edges: tuple[dict[str, Any], ...] = ()


def find_direct_edges(conn: sqlite3.Connection, wallet_a: str, wallet_b: str) -> DirectEdgeCheck:
    """Search every authoritative edge/lineage table this platform persists
    for a DIRECT wallet_a<->wallet_b relationship (either direction). Never
    infers an edge from population co-membership."""
    edges: list[dict[str, Any]] = []

    def _tables(c: sqlite3.Connection) -> set[str]:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    tables = _tables(conn)

    if "wt_provisioning_edges" in tables:
        rows = conn.execute(
            "SELECT * FROM wt_provisioning_edges WHERE "
            "(from_wallet=? AND to_wallet=?) OR (from_wallet=? AND to_wallet=?)",
            (wallet_a, wallet_b, wallet_b, wallet_a),
        ).fetchall()
        for r in rows:
            edges.append({"source": "wt_provisioning_edges", **dict(r)})

    if "wt_wrap_close_candidates" in tables:
        rows = conn.execute(
            "SELECT * FROM wt_wrap_close_candidates WHERE "
            "(creator=? AND lineage_source_treasury=?) OR (creator=? AND lineage_source_treasury=?) "
            "OR (creator=? AND subprov_wallet=?) OR (creator=? AND subprov_wallet=?)",
            (wallet_a, wallet_b, wallet_b, wallet_a, wallet_a, wallet_b, wallet_b, wallet_a),
        ).fetchall()
        for r in rows:
            edges.append({"source": "wt_wrap_close_candidates", **dict(r)})

    if "wt_discovered_subprovs" in tables:
        rows = conn.execute(
            "SELECT * FROM wt_discovered_subprovs WHERE "
            "(subprov=? AND treasury=?) OR (subprov=? AND treasury=?) "
            "OR (subprov=? AND immediate_funder=?) OR (subprov=? AND immediate_funder=?)",
            (wallet_a, wallet_b, wallet_b, wallet_a, wallet_a, wallet_b, wallet_b, wallet_a),
        ).fetchall()
        for r in rows:
            edges.append({"source": "wt_discovered_subprovs", **dict(r)})

    return DirectEdgeCheck(exists=bool(edges), edges=tuple(edges))


@dataclass(frozen=True, slots=True)
class StructuralMembership:
    shared_populations: tuple[dict[str, Any], ...]
    reasons: tuple[str, ...]


def dedupe_populations_by_family_id(*population_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Callers typically have several EmergingOperatorService.list() buckets
    (confirmed_operations_reconciled, active_investigations_reconciled,
    etc.) which can overlap. Merge them into one deduped-by-family_id list
    before passing to find_structural_membership/build_entity_context, so
    a population appearing in multiple buckets is reported once, not N
    times."""
    seen: dict[str, dict[str, Any]] = {}
    for population_list in population_lists:
        for family in population_list or ():
            fid = str(family.get("family_id"))
            if fid not in seen:
                seen[fid] = family
    return list(seen.values())


def find_structural_membership(
    wallet_a: str, wallet_b: str, populations: list[dict[str, Any]],
) -> StructuralMembership:
    """populations: the family list from EmergingOperatorService (already
    computed, never re-derived here). Finds populations where BOTH wallets
    appear in any member/role field, and reports why (the family's own
    recorded cohesion evidence if present, else a generic structural note).
    Never claims this implies a direct edge."""
    shared = []
    reasons: set[str] = set()
    for family in populations:
        member_fields = ("member_wallets", "treasuries", "member_treasuries", "client_wallets", "provisioning_clients")
        all_members: set[str] = set()
        for field_name in member_fields:
            value = family.get(field_name)
            if isinstance(value, (list, tuple, set)):
                all_members.update(value)
        if wallet_a in all_members and wallet_b in all_members:
            shared.append({
                "family_id": family.get("family_id"),
                "family_name": family.get("family_name"),
                "dominant_topology": family.get("dominant_topology"),
            })
            if family.get("treasuries") and len(family.get("treasuries") or []) >= 2:
                reasons.add("shared treasuries")
            if family.get("funding_mechanisms"):
                reasons.add("common funding mechanism")
            unique_creators = family.get("unique_creators")
            creator_count = len(unique_creators) if isinstance(unique_creators, list) else (unique_creators or 0)
            if creator_count and creator_count >= 5:
                reasons.add("creator fan-out")
            if not reasons:
                reasons.add("structural similarity")
    return StructuralMembership(shared_populations=tuple(shared), reasons=tuple(sorted(reasons)))


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    treasury: str
    status: str | None
    reviewed_by: str | None
    reviewed_at: int | None
    detected_via: str | None


def find_review_decision(conn: sqlite3.Connection, wallet: str) -> ReviewDecision | None:
    row = conn.execute(
        "SELECT treasury, status, reviewed_by, reviewed_at, detected_via "
        "FROM wt_treasury_review WHERE treasury=?", (wallet,),
    ).fetchone()
    if not row:
        return None
    return ReviewDecision(
        treasury=row["treasury"], status=row["status"], reviewed_by=row["reviewed_by"],
        reviewed_at=row["reviewed_at"], detected_via=row["detected_via"],
    )


@dataclass(frozen=True, slots=True)
class CanonicalIdentity:
    operator_id: str
    display_name: str
    entity_type: str
    confidence: str | None
    confirmed_at: int | None


def find_canonical_identity(conn: sqlite3.Connection, wallet: str) -> CanonicalIdentity | None:
    row = conn.execute(
        "SELECT oe.operator_id, o.display_name, oe.entity_type, oe.confidence "
        "FROM operator_entities oe JOIN operators o ON o.operator_id=oe.operator_id "
        "WHERE oe.entity_address=? AND o.status='CONFIRMED' LIMIT 1", (wallet,),
    ).fetchone()
    if not row:
        return None
    confirmed = conn.execute(
        "SELECT confirmed_at FROM wt_confirmed_treasuries WHERE treasury=?", (wallet,),
    ).fetchone()
    return CanonicalIdentity(
        operator_id=row["operator_id"], display_name=row["display_name"],
        entity_type=row["entity_type"], confidence=row["confidence"],
        confirmed_at=confirmed["confirmed_at"] if confirmed else None,
    )


@dataclass(frozen=True, slots=True)
class EntityContext:
    """The complete cross-system view of ONE wallet: what it directly
    is (canonical identity), what a human decided about it (review), and
    what looser structures it participates in (populations) -- kept as
    separate, explicitly-labelled fields, never merged into one summary."""
    wallet: str
    canonical_identity: CanonicalIdentity | None
    review_decision: ReviewDecision | None
    structural_populations: tuple[dict[str, Any], ...]


def build_entity_context(conn: sqlite3.Connection, wallet: str, populations: list[dict[str, Any]]) -> EntityContext:
    canonical = find_canonical_identity(conn, wallet)
    review = find_review_decision(conn, wallet)
    pops = []
    for family in populations:
        member_fields = ("member_wallets", "treasuries", "member_treasuries", "client_wallets", "provisioning_clients")
        all_members: set[str] = set()
        for field_name in member_fields:
            value = family.get(field_name)
            if isinstance(value, (list, tuple, set)):
                all_members.update(value)
        if wallet in all_members:
            role = "anchor" if family.get("family_anchor") == wallet else (
                "treasury" if wallet in (family.get("treasuries") or []) else "member"
            )
            pops.append({
                "family_id": family.get("family_id"),
                "family_name": family.get("family_name"),
                "role": role,
            })
    return EntityContext(
        wallet=wallet, canonical_identity=canonical, review_decision=review,
        structural_populations=tuple(pops),
    )


def relationship_between(
    conn: sqlite3.Connection, wallet_a: str, wallet_b: str, populations: list[dict[str, Any]],
) -> dict[str, Any]:
    """The single entry point PART 3/4/5's UI code should call. Returns a
    dict distinguishing exactly what kind(s) of relationship exist between
    two wallets -- never collapsing them into one label."""
    direct = find_direct_edges(conn, wallet_a, wallet_b)
    structural = find_structural_membership(wallet_a, wallet_b, populations)
    return {
        "wallet_a": wallet_a,
        "wallet_b": wallet_b,
        "direct_relationship": {
            "observed": direct.exists,
            "edges": list(direct.edges),
        },
        "structural_membership": {
            "observed": bool(structural.shared_populations),
            "populations": list(structural.shared_populations),
            "reasons": list(structural.reasons),
        },
    }
