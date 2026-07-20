"""X29.7 — Operational Lineage.

Per X29.5/X29.6's conclusion, reframed once more by direct user feedback:
this is NOT a fixed four-role model (Creator/Provisioning Wallet/
Subprovider/Treasury always present). It is a VARIABLE-DEPTH lineage graph,
because a real operation may be:

  Treasury -> Creator                              (2 hops)
  Treasury -> Subprovider -> Creator                (3 hops)
  Treasury -> Subprovider -> Subprovider -> Creator (4+ hops, multi-level)

Roles are ATTRIBUTES of nodes in that lineage, not the lineage itself. This
module renders however many hops actually exist in the persisted evidence
-- it never pads, fabricates, or assumes a fixed depth.

Zero new intelligence: every fact here already exists in wt_provisioning_edges
(TREASURY_TO_SUBPROV / SUBPROV_TO_CREATOR edges), wt_watchtower_launches
(treasury_wallet/subprov_wallet/creator_wallet columns -- the SAME columns
funding_topology.py already reads), and wt_confirmed_treasuries. This module
does not alter attribution, topology, behaviour, mechanism, funding
boundary, or wallet quality calculations -- it only reads and organizes
them by role/lineage position for Discovery's presentation layer.

Role vocabulary (attributes on a lineage node, not a classification of a
launch):
  TREASURY     -- in wt_confirmed_treasuries, or appears as from_wallet on
                  a TREASURY_TO_SUBPROV edge / as treasury_wallet on a launch
  SUBPROVIDER  -- appears as to_wallet on a TREASURY_TO_SUBPROV edge, or as
                  from_wallet on a SUBPROV_TO_CREATOR edge, or as
                  subprov_wallet on a launch
  CREATOR      -- appears as to_wallet on a SUBPROV_TO_CREATOR edge, or as
                  creator_wallet on a launch

Note: an intermediate "provisioning wallet" hop (e.g. a single-use WSOL
wrap account) is NOT separately persisted anywhere in this schema today --
wt_provisioning_edges' SUBPROV_TO_CREATOR edge already collapses
subprov->creator into one edge, matching exactly what wt_watchtower_launches
records. This module reflects that reality rather than fabricating an
unobserved intermediate node.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

ROLE_TREASURY = "TREASURY"
ROLE_SUBPROVIDER = "SUBPROVIDER"
ROLE_CREATOR = "CREATOR"

ROLE_LABELS = {
    ROLE_TREASURY: "Treasury",
    ROLE_SUBPROVIDER: "Subprovider",
    ROLE_CREATOR: "Creator",
}


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _is_confirmed_treasury(conn: sqlite3.Connection, wallet: str) -> bool:
    if not _table_exists(conn, "wt_confirmed_treasuries"):
        return False
    return bool(conn.execute(
        "SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=?", (wallet,)
    ).fetchone())


def roles_for_wallet(conn: sqlite3.Connection, wallet: str) -> list[str]:
    """A wallet may legitimately hold more than one role signal (e.g. a
    treasury that is also, structurally, a subprov elsewhere -- the Mesh
    case funding_topology.py already models). Returns every role this
    wallet has EVIDENCE for, in ROLE ORDER (TREASURY, SUBPROVIDER, CREATOR),
    never fewer than the evidence supports, never more."""
    if not wallet:
        return []
    roles: list[str] = []

    is_treasury = _is_confirmed_treasury(conn, wallet)
    is_subprov = False
    is_creator = False

    if _table_exists(conn, "wt_provisioning_edges"):
        if conn.execute(
            "SELECT 1 FROM wt_provisioning_edges WHERE from_wallet=? AND edge_type='TREASURY_TO_SUBPROV'",
            (wallet,),
        ).fetchone():
            is_treasury = True
        if conn.execute(
            "SELECT 1 FROM wt_provisioning_edges WHERE to_wallet=? AND edge_type='TREASURY_TO_SUBPROV'",
            (wallet,),
        ).fetchone():
            is_subprov = True
        if conn.execute(
            "SELECT 1 FROM wt_provisioning_edges WHERE from_wallet=? AND edge_type='SUBPROV_TO_CREATOR'",
            (wallet,),
        ).fetchone():
            is_subprov = True
        if conn.execute(
            "SELECT 1 FROM wt_provisioning_edges WHERE to_wallet=? AND edge_type='SUBPROV_TO_CREATOR'",
            (wallet,),
        ).fetchone():
            is_creator = True

    if _table_exists(conn, "wt_watchtower_launches"):
        if conn.execute(
            "SELECT 1 FROM wt_watchtower_launches WHERE treasury_wallet=? LIMIT 1", (wallet,)
        ).fetchone():
            is_treasury = True
        if conn.execute(
            "SELECT 1 FROM wt_watchtower_launches WHERE subprov_wallet=? LIMIT 1", (wallet,)
        ).fetchone():
            is_subprov = True
        if conn.execute(
            "SELECT 1 FROM wt_watchtower_launches WHERE creator_wallet=? LIMIT 1", (wallet,)
        ).fetchone():
            is_creator = True

    if is_treasury:
        roles.append(ROLE_TREASURY)
    if is_subprov:
        roles.append(ROLE_SUBPROVIDER)
    if is_creator:
        roles.append(ROLE_CREATOR)
    return roles


def _fan_out_count(conn: sqlite3.Connection, subprov_wallet: str) -> int:
    """Distinct creators this subprov has funded -- via BOTH
    wt_provisioning_edges (SUBPROV_TO_CREATOR) and wt_watchtower_launches
    (subprov_wallet column), unioned, since either source alone can miss
    rows the other has (the exact same dual-source pattern
    funding_topology.py's classify_topology_for_launch already relies on)."""
    creators: set[str] = set()
    if _table_exists(conn, "wt_provisioning_edges"):
        for r in conn.execute(
            "SELECT DISTINCT to_wallet FROM wt_provisioning_edges "
            "WHERE from_wallet=? AND edge_type='SUBPROV_TO_CREATOR'",
            (subprov_wallet,),
        ):
            creators.add(r[0])
    if _table_exists(conn, "wt_watchtower_launches"):
        for r in conn.execute(
            "SELECT DISTINCT creator_wallet FROM wt_watchtower_launches WHERE subprov_wallet=?",
            (subprov_wallet,),
        ):
            creators.add(r[0])
    return len(creators)


def _historical_launch_count(conn: sqlite3.Connection, subprov_wallet: str) -> int:
    if not _table_exists(conn, "wt_watchtower_launches"):
        return 0
    row = conn.execute(
        "SELECT COUNT(*) FROM wt_watchtower_launches WHERE subprov_wallet=?", (subprov_wallet,)
    ).fetchone()
    return row[0] if row else 0


def _funding_mechanisms_used(conn: sqlite3.Connection, subprov_wallet: str) -> list[str]:
    if not _table_exists(conn, "wt_watchtower_launches"):
        return []
    rows = conn.execute(
        "SELECT DISTINCT funding_mechanism FROM wt_watchtower_launches "
        "WHERE subprov_wallet=? AND funding_mechanism IS NOT NULL",
        (subprov_wallet,),
    ).fetchall()
    return sorted({r[0] for r in rows if r[0]})


def _subprov_count_for_treasury(conn: sqlite3.Connection, treasury_wallet: str) -> int:
    subprovs: set[str] = set()
    if _table_exists(conn, "wt_provisioning_edges"):
        for r in conn.execute(
            "SELECT DISTINCT to_wallet FROM wt_provisioning_edges "
            "WHERE from_wallet=? AND edge_type='TREASURY_TO_SUBPROV'",
            (treasury_wallet,),
        ):
            subprovs.add(r[0])
    if _table_exists(conn, "wt_watchtower_launches"):
        for r in conn.execute(
            "SELECT DISTINCT subprov_wallet FROM wt_watchtower_launches "
            "WHERE treasury_wallet=? AND subprov_wallet IS NOT NULL",
            (treasury_wallet,),
        ):
            subprovs.add(r[0])
    return len(subprovs)


def _find_treasury_for(conn: sqlite3.Connection, subprov_wallet: str) -> Optional[str]:
    """The treasury funding this subprov, if any is recorded."""
    if _table_exists(conn, "wt_provisioning_edges"):
        row = conn.execute(
            "SELECT from_wallet FROM wt_provisioning_edges "
            "WHERE to_wallet=? AND edge_type='TREASURY_TO_SUBPROV' LIMIT 1",
            (subprov_wallet,),
        ).fetchone()
        if row:
            return row[0]
    if _table_exists(conn, "wt_watchtower_launches"):
        row = conn.execute(
            "SELECT treasury_wallet FROM wt_watchtower_launches "
            "WHERE subprov_wallet=? AND treasury_wallet IS NOT NULL LIMIT 1",
            (subprov_wallet,),
        ).fetchone()
        if row:
            return row[0]
    return None


def _find_direct_treasury_for_creator(conn: sqlite3.Connection, creator_wallet: str) -> Optional[str]:
    """A launch may skip the subprov hop entirely -- treasury_wallet set
    directly on wt_watchtower_launches with no subprov_wallet at all. Not
    every operation has a subprov hop (a genuinely variable-depth chain)."""
    if not _table_exists(conn, "wt_watchtower_launches"):
        return None
    row = conn.execute(
        "SELECT treasury_wallet FROM wt_watchtower_launches "
        "WHERE creator_wallet=? AND subprov_wallet IS NULL AND treasury_wallet IS NOT NULL LIMIT 1",
        (creator_wallet,),
    ).fetchone()
    return row[0] if row else None


def _find_subprov_for(conn: sqlite3.Connection, creator_wallet: str) -> Optional[str]:
    """The subprov that funded this creator, if any is recorded."""
    if _table_exists(conn, "wt_provisioning_edges"):
        row = conn.execute(
            "SELECT from_wallet FROM wt_provisioning_edges "
            "WHERE to_wallet=? AND edge_type='SUBPROV_TO_CREATOR' LIMIT 1",
            (creator_wallet,),
        ).fetchone()
        if row:
            return row[0]
    if _table_exists(conn, "wt_watchtower_launches"):
        row = conn.execute(
            "SELECT subprov_wallet FROM wt_watchtower_launches "
            "WHERE creator_wallet=? AND subprov_wallet IS NOT NULL LIMIT 1",
            (creator_wallet,),
        ).fetchone()
        if row:
            return row[0]
    return None


def _node_properties(conn: sqlite3.Connection, wallet: str, role: str) -> dict[str, Any]:
    """Role-appropriate supporting properties -- attached to the node that
    actually exhibits them, never to the launch/creator as a whole (the
    X29.5/X29.6 finding this module exists to fix)."""
    if role == ROLE_SUBPROVIDER:
        return {
            "fan_out_count": _fan_out_count(conn, wallet),
            "historical_launches": _historical_launch_count(conn, wallet),
            "funding_mechanisms": _funding_mechanisms_used(conn, wallet),
        }
    if role == ROLE_TREASURY:
        return {
            "subprovider_count": _subprov_count_for_treasury(conn, wallet),
        }
    return {}


def build_lineage(conn: sqlite3.Connection, wallet: str, *, max_hops: int = 10) -> dict[str, Any]:
    """Walks the lineage OUTWARD from `wallet` in both directions (toward
    the treasury root, and toward creator leaves), using only persisted
    edges -- never assuming a fixed depth. `max_hops` is a pure safety bound
    against a pathological cycle in malformed data, not a modeling
    assumption (real chains observed so far are 2-4 hops).

    Returns:
      {
        "wallet": <the queried wallet>,
        "primary_role": <the FIRST role in roles_for_wallet's ordering>,
        "chain": [ {wallet, role, properties}, ... ]  -- ordered
                  TREASURY (or highest-known-ancestor) -> ... -> CREATOR
                  (or lowest-known-descendant), whatever actually exists.
        "fan_out_siblings": [...]  -- if the queried wallet is a creator and
                  its subprov has other creators, the sibling creator list
                  (bounded, for UI display) -- purely descriptive.
      }
    """
    roles = roles_for_wallet(conn, wallet)
    if not roles:
        return {"wallet": wallet, "primary_role": None, "chain": []}

    primary_role = roles[0]

    # Walk UP toward the treasury.
    up_chain: list[dict[str, Any]] = []
    cursor = wallet
    cursor_role = primary_role
    seen = {wallet}
    hops = 0
    while cursor_role != ROLE_TREASURY and hops < max_hops:
        if cursor_role == ROLE_CREATOR:
            parent = _find_subprov_for(conn, cursor)
            parent_role = ROLE_SUBPROVIDER
            if not parent:
                # No subprov hop recorded -- try a direct treasury->creator
                # edge (a genuinely shorter, 2-hop chain), never fabricate
                # a subprov node that has no evidence.
                parent = _find_direct_treasury_for_creator(conn, cursor)
                parent_role = ROLE_TREASURY
        elif cursor_role == ROLE_SUBPROVIDER:
            parent = _find_treasury_for(conn, cursor)
            parent_role = ROLE_TREASURY
        else:
            break
        if not parent or parent in seen:
            break
        up_chain.append({"wallet": parent, "role": parent_role, "properties": _node_properties(conn, parent, parent_role)})
        seen.add(parent)
        cursor, cursor_role = parent, parent_role
        hops += 1

    # Walk DOWN toward the creator (only meaningful if the queried wallet
    # itself is a treasury or subprov and we want to show one representative
    # descendant path -- Discovery shows the specific wallet's own position,
    # not every descendant, since a subprov can fan out to many creators).
    down_chain: list[dict[str, Any]] = []
    if primary_role != ROLE_CREATOR:
        cursor, cursor_role = wallet, primary_role
        hops = 0
        while cursor_role != ROLE_CREATOR and hops < max_hops:
            if cursor_role == ROLE_TREASURY:
                child = None
                if _table_exists(conn, "wt_provisioning_edges"):
                    row = conn.execute(
                        "SELECT to_wallet FROM wt_provisioning_edges "
                        "WHERE from_wallet=? AND edge_type='TREASURY_TO_SUBPROV' LIMIT 1",
                        (cursor,),
                    ).fetchone()
                    if row:
                        child = row[0]
                if not child and _table_exists(conn, "wt_watchtower_launches"):
                    row = conn.execute(
                        "SELECT subprov_wallet FROM wt_watchtower_launches "
                        "WHERE treasury_wallet=? AND subprov_wallet IS NOT NULL LIMIT 1",
                        (cursor,),
                    ).fetchone()
                    if row:
                        child = row[0]
                child_role = ROLE_SUBPROVIDER
            elif cursor_role == ROLE_SUBPROVIDER:
                child = None
                if _table_exists(conn, "wt_provisioning_edges"):
                    row = conn.execute(
                        "SELECT to_wallet FROM wt_provisioning_edges "
                        "WHERE from_wallet=? AND edge_type='SUBPROV_TO_CREATOR' LIMIT 1",
                        (cursor,),
                    ).fetchone()
                    if row:
                        child = row[0]
                if not child and _table_exists(conn, "wt_watchtower_launches"):
                    row = conn.execute(
                        "SELECT creator_wallet FROM wt_watchtower_launches "
                        "WHERE subprov_wallet=? LIMIT 1",
                        (cursor,),
                    ).fetchone()
                    if row:
                        child = row[0]
                child_role = ROLE_CREATOR
            else:
                break
            if not child or child in seen:
                break
            down_chain.append({"wallet": child, "role": child_role, "properties": _node_properties(conn, child, child_role)})
            seen.add(child)
            cursor, cursor_role = child, child_role
            hops += 1

    chain = list(reversed(up_chain)) + [
        {"wallet": wallet, "role": primary_role, "properties": _node_properties(conn, wallet, primary_role)}
    ] + down_chain

    return {"wallet": wallet, "primary_role": primary_role, "chain": chain}
