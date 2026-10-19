"""X25.4 — Operation Identity Resolver.

Read-only. Derives the first real Operation Identity layer from the X25.0
design: a connected set of CONFIRMED treasury wallets linked by
treasury-to-treasury funding that occurred before the funded treasury's
first observed launch, together with the launches attributed to those
treasury members.

Canonical rule (X25.0 §1, revalidated against live schema in X25.4 Phase 1):
  An edge funder -> treasury qualifies only when:
    - funder is itself a CONFIRMED treasury (wt_confirmed_treasuries.treasury)
    - treasury is a CONFIRMED treasury (wt_confirmed_treasuries.treasury)
    - the funding is not a subprov-sweep artifact (wt_treasury_funders.is_subprov_sweep = 0)
    - the funding's last_seen timestamp is strictly before the funded
      treasury's own first observed launch (MIN(wt_watchtower_launches.create_time)
      WHERE treasury_wallet = treasury)
  If the funded treasury has no launch with a non-null create_time at all,
  the funding-precedes-launch condition cannot be established from persisted
  data — the edge is REJECTED as insufficient evidence, never guessed.

No behavioural signal (mechanism, capital size, cadence, dormancy,
infrastructure endpoint, vanity-prefix similarity, canonical operator) is
ever used to qualify an edge or merge two treasuries. Only the confirmed-
treasury-funding-mesh timing rule above. This module performs no DB writes.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Any, Optional

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OPS_DB_PATH = os.environ.get("OPS_V2_DB_PATH", os.path.join(_REPO_ROOT, "database", "wt_ops_v2.db"))


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def operation_id_for(treasuries: list[str]) -> str:
    """Deterministic operation_id: sha256 of the sorted, pipe-joined, full
    (never truncated) treasury addresses. Stable across process restarts and
    row-order changes because the input is sorted before hashing — never
    derived from a DB row id, a random UUID, or a timestamp."""
    canonical = "|".join(sorted(treasuries))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"op_{digest[:16]}"


def display_name_for(operation_id: str) -> str:
    """Human-readable label derived from the operation_id's own hash suffix
    — never an abbreviated wallet prefix (X25.0/X25.4: vanity-prefix
    similarity must never be used as or imply identity)."""
    suffix = operation_id[3:9] if operation_id.startswith("op_") else operation_id[:6]
    return f"Operation {suffix.upper()}"


def _find_component(root: str, adjacency: dict[str, set[str]]) -> set[str]:
    """BFS over an undirected adjacency view of the qualifying edges. Cycles
    are handled naturally by the visited-set guard (X25.4 Phase 5: a funding
    cycle still resolves to exactly one connected component, not an infinite
    loop or a duplicate)."""
    seen = {root}
    frontier = [root]
    while frontier:
        nxt = []
        for node in frontier:
            for neighbour in adjacency.get(node, ()):
                if neighbour not in seen:
                    seen.add(neighbour)
                    nxt.append(neighbour)
        frontier = nxt
    return seen


def build_operations(ops_db_path: str = OPS_DB_PATH) -> dict[str, Any]:
    """Loads confirmed treasuries, finds qualifying treasury-to-treasury
    funding edges, builds connected components via transitive closure, and
    assigns every launch to the operation containing its treasury.

    Returns:
      {
        "operations": {operation_id: <operation object, Phase 4 shape>},
        "treasury_to_operation": {treasury_wallet: operation_id},
        "rejected_edges": [{"from":..., "to":..., "reason":...}, ...],
      }

    No DB writes. Read-only across wt_confirmed_treasuries,
    wt_treasury_funders, wt_watchtower_launches.
    """
    if not os.path.exists(ops_db_path):
        return {"operations": {}, "treasury_to_operation": {}, "rejected_edges": []}
    conn = _connect(ops_db_path)
    try:
        if not (_table_exists(conn, "wt_confirmed_treasuries")
                and _table_exists(conn, "wt_treasury_funders")
                and _table_exists(conn, "wt_watchtower_launches")):
            return {"operations": {}, "treasury_to_operation": {}, "rejected_edges": []}

        confirmed_treasuries = {
            r["treasury"] for r in conn.execute("SELECT treasury FROM wt_confirmed_treasuries")
        }

        # first_launch_at / launch_count per confirmed treasury, from persisted
        # wt_watchtower_launches rows only — never guessed.
        launch_stats: dict[str, dict[str, Any]] = {}
        for row in conn.execute(
            "SELECT treasury_wallet, MIN(create_time) first_launch_at, "
            "MAX(create_time) last_launch_at, COUNT(*) launch_count "
            "FROM wt_watchtower_launches WHERE treasury_wallet IS NOT NULL "
            "GROUP BY treasury_wallet"
        ):
            launch_stats[row["treasury_wallet"]] = {
                "first_launch_at": row["first_launch_at"],
                "last_launch_at": row["last_launch_at"],
                "launch_count": row["launch_count"],
            }

        # Qualify edges: funder and treasury both confirmed, not a subprov
        # sweep, and last_seen strictly precedes the destination treasury's
        # own first observed launch (which must itself be known).
        qualifying_edges: list[dict[str, Any]] = []
        rejected_edges: list[dict[str, Any]] = []
        for row in conn.execute(
            "SELECT funder, treasury, total_sol, first_seen, last_seen, is_subprov_sweep "
            "FROM wt_treasury_funders"
        ):
            funder, treasury = row["funder"], row["treasury"]
            if funder not in confirmed_treasuries or treasury not in confirmed_treasuries:
                continue  # not a treasury-to-treasury edge at all; out of scope for this resolver
            if row["is_subprov_sweep"]:
                rejected_edges.append({
                    "from": funder, "to": treasury,
                    "reason": "SUBPROV_SWEEP_ARTIFACT",
                })
                continue
            dest_stats = launch_stats.get(treasury)
            first_launch_at = dest_stats["first_launch_at"] if dest_stats else None
            if first_launch_at is None:
                rejected_edges.append({
                    "from": funder, "to": treasury,
                    "reason": "MISSING_FIRST_LAUNCH_TIME",
                })
                continue
            last_seen = row["last_seen"]
            if last_seen is None or last_seen >= first_launch_at:
                rejected_edges.append({
                    "from": funder, "to": treasury,
                    "reason": "FUNDING_NOT_BEFORE_FIRST_LAUNCH",
                })
                continue
            qualifying_edges.append({
                "from": funder, "to": treasury,
                "amount_sol": row["total_sol"],
                "block_time": last_seen,
                "qualifying_reason": "FUNDED_BEFORE_FIRST_LAUNCH",
            })

        # Build undirected adjacency over confirmed treasuries that either
        # have a launch, participate in a qualifying edge, or both — a
        # confirmed treasury with neither is not part of any operation.
        adjacency: dict[str, set[str]] = {}
        for edge in qualifying_edges:
            adjacency.setdefault(edge["from"], set()).add(edge["to"])
            adjacency.setdefault(edge["to"], set()).add(edge["from"])

        nodes = set(adjacency.keys()) | set(launch_stats.keys())
        nodes &= confirmed_treasuries  # X25.4 Phase 5: unconfirmed wallets never expand the mesh

        assigned: set[str] = set()
        operations: dict[str, Any] = {}
        treasury_to_operation: dict[str, str] = {}

        for node in sorted(nodes):
            if node in assigned:
                continue
            component = _find_component(node, adjacency) & nodes
            assigned |= component
            member_edges = [
                e for e in qualifying_edges
                if e["from"] in component and e["to"] in component
            ]
            # Root = the treasury with no qualifying INCOMING edge from
            # within this component (the funding source(s)); if every member
            # has an incoming edge (a cycle), no member is uniquely root and
            # all members are reported as MEMBER (X25.4 Phase 5: a funding
            # cycle is still one operation, with no fabricated root).
            funded_within = {e["to"] for e in member_edges}
            roots = sorted(component - funded_within)

            treasuries_out = []
            total_launches = 0
            first_launch_at = None
            last_launch_at = None
            for member in sorted(component):
                stats = launch_stats.get(member, {})
                lc = stats.get("launch_count", 0) or 0
                total_launches += lc
                fla = stats.get("first_launch_at")
                lla = stats.get("last_launch_at")
                if fla is not None:
                    first_launch_at = fla if first_launch_at is None else min(first_launch_at, fla)
                if lla is not None:
                    last_launch_at = lla if last_launch_at is None else max(last_launch_at, lla)
                treasuries_out.append({
                    "wallet": member,
                    "role": "ROOT" if member in roots else "MEMBER",
                    "first_launch_at": fla,
                    "launch_count": lc,
                })

            op_id = operation_id_for(sorted(component))
            provenance = [
                {
                    "from": e["from"], "to": e["to"],
                    "reason": e["qualifying_reason"],
                    "block_time": e["block_time"],
                    "amount_sol": e["amount_sol"],
                }
                for e in member_edges
            ]
            operations[op_id] = {
                "operation_id": op_id,
                "display_name": display_name_for(op_id),
                "identity_basis": "TREASURY_FUNDING_MESH",
                "confidence": "CONFIRMED",
                "treasuries": treasuries_out,
                "funding_edges": member_edges,
                "launch_count": total_launches,
                "first_launch_at": first_launch_at,
                "last_launch_at": last_launch_at,
                "provenance": provenance,
            }
            for member in component:
                treasury_to_operation[member] = op_id

        return {
            "operations": operations,
            "treasury_to_operation": treasury_to_operation,
            "rejected_edges": rejected_edges,
        }
    finally:
        conn.close()


def operation_for_treasury(treasury: Optional[str], ops_db_path: str = OPS_DB_PATH) -> Optional[dict[str, Any]]:
    """Convenience lookup: return the full operation object for a given
    treasury wallet, or None if the treasury is absent/unconfirmed/has no
    operation membership. Never fabricates a placeholder operation."""
    if not treasury:
        return None
    result = build_operations(ops_db_path)
    op_id = result["treasury_to_operation"].get(treasury)
    if op_id is None:
        return None
    return result["operations"][op_id]
