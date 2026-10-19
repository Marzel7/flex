"""X29.7 — Operations Summary.

Read-only aggregation, zero new intelligence calculation. Reuses:
  - src/ops/operation_identity.py's build_operations() for the treasury-mesh
    operation clusters themselves (unchanged).
  - Role counts derived the same way src/ops/operational_lineage.py derives
    them, over the SAME wt_provisioning_edges/wt_watchtower_launches facts.
  - Funding Mechanism / Behaviour / Funding Boundary distributions computed
    directly from the SAME persisted columns those modules already read
    (wt_watchtower_launches.funding_mechanism, wt_funding_boundary.
    boundary_status) -- scoped to one operation's launches rather than the
    whole corpus, which is the only difference from the existing
    corpus-wide classifiers. No new classification logic is introduced.

This module answers "summarize this operation" for Discovery's Operations
-first landing view and per-operation detail page; it never writes.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from src.ops.operation_identity import build_operations


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _mints_for_treasuries(conn: sqlite3.Connection, treasuries: list[str]) -> list[str]:
    if not treasuries or not _table_exists(conn, "wt_watchtower_launches"):
        return []
    placeholders = ",".join("?" for _ in treasuries)
    rows = conn.execute(
        f"SELECT mint FROM wt_watchtower_launches WHERE treasury_wallet IN ({placeholders})",
        tuple(treasuries),
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def _subprovider_wallets_for_treasuries(conn: sqlite3.Connection, treasuries: list[str]) -> set[str]:
    subprovs: set[str] = set()
    if not treasuries:
        return subprovs
    if _table_exists(conn, "wt_provisioning_edges"):
        placeholders = ",".join("?" for _ in treasuries)
        for r in conn.execute(
            f"SELECT DISTINCT to_wallet FROM wt_provisioning_edges "
            f"WHERE from_wallet IN ({placeholders}) AND edge_type='TREASURY_TO_SUBPROV'",
            tuple(treasuries),
        ):
            subprovs.add(r[0])
    if _table_exists(conn, "wt_watchtower_launches"):
        placeholders = ",".join("?" for _ in treasuries)
        for r in conn.execute(
            f"SELECT DISTINCT subprov_wallet FROM wt_watchtower_launches "
            f"WHERE treasury_wallet IN ({placeholders}) AND subprov_wallet IS NOT NULL",
            tuple(treasuries),
        ):
            subprovs.add(r[0])
    return subprovs


def _creators_for_treasuries(conn: sqlite3.Connection, treasuries: list[str]) -> set[str]:
    creators: set[str] = set()
    if not treasuries or not _table_exists(conn, "wt_watchtower_launches"):
        return creators
    placeholders = ",".join("?" for _ in treasuries)
    for r in conn.execute(
        f"SELECT DISTINCT creator_wallet FROM wt_watchtower_launches "
        f"WHERE treasury_wallet IN ({placeholders}) AND creator_wallet IS NOT NULL",
        tuple(treasuries),
    ):
        creators.add(r[0])
    return creators


def _mechanism_distribution(conn: sqlite3.Connection, mints: list[str]) -> dict[str, Any]:
    if not mints or not _table_exists(conn, "wt_watchtower_launches"):
        return {}
    placeholders = ",".join("?" for _ in mints)
    rows = conn.execute(
        f"SELECT funding_mechanism, COUNT(*) FROM wt_watchtower_launches "
        f"WHERE mint IN ({placeholders}) AND funding_mechanism IS NOT NULL "
        f"GROUP BY funding_mechanism",
        tuple(mints),
    ).fetchall()
    total = sum(n for _, n in rows)
    if not total:
        return {}
    return {
        mech: {"count": n, "pct": round(n / total * 100, 1)}
        for mech, n in rows
    }


def _boundary_distribution(conn: sqlite3.Connection, mints: list[str]) -> dict[str, Any]:
    if not mints or not _table_exists(conn, "wt_funding_boundary"):
        return {}
    placeholders = ",".join("?" for _ in mints)
    rows = conn.execute(
        f"SELECT boundary_status, COUNT(*) FROM wt_funding_boundary "
        f"WHERE launch_mint IN ({placeholders}) GROUP BY boundary_status",
        tuple(mints),
    ).fetchall()
    total = sum(n for _, n in rows)
    if not total:
        return {}
    known = sum(n for status, n in rows if status != "UNRESOLVED")
    return {
        "known_pct": round(known / total * 100, 1),
        "unresolved_pct": round((total - known) / total * 100, 1),
        "by_status": {status: n for status, n in rows},
    }


def summarize_operation(conn: sqlite3.Connection, operation: dict[str, Any]) -> dict[str, Any]:
    """Given one operation object from build_operations()['operations'],
    returns Discovery's Operation Summary shape -- role counts + intelligence
    distributions, all derived from already-persisted facts."""
    treasuries = [t["wallet"] for t in operation.get("treasuries", [])]
    mints = _mints_for_treasuries(conn, treasuries)
    subprovs = _subprovider_wallets_for_treasuries(conn, treasuries)
    creators = _creators_for_treasuries(conn, treasuries)

    last_launch_at = operation.get("last_launch_at")

    return {
        "operation_id": operation["operation_id"],
        "display_name": operation["display_name"],
        "confidence": operation.get("confidence", "CONFIRMED"),
        "treasury_count": len(treasuries),
        "subprovider_count": len(subprovs),
        "creator_count": len(creators),
        "recent_launch_count": operation.get("launch_count", 0),
        "last_activity": last_launch_at,
        "funding_mechanisms": _mechanism_distribution(conn, mints),
        "funding_boundary": _boundary_distribution(conn, mints),
    }


def build_operations_summary(ops_db_path: str) -> dict[str, Any]:
    """Top-level Discovery entry point: every known operation (from
    build_operations(), unchanged) plus its summary. Read-only, zero writes,
    zero new classification -- reuses operation_identity.py's existing
    treasury-mesh resolver entirely."""
    result = build_operations(ops_db_path)
    conn = sqlite3.connect(ops_db_path)
    conn.row_factory = sqlite3.Row
    try:
        summaries = [
            summarize_operation(conn, op)
            for op in result["operations"].values()
        ]
        summaries.sort(key=lambda s: s.get("last_activity") or 0, reverse=True)
        return {
            "operations": summaries,
            "total_operations": len(summaries),
        }
    finally:
        conn.close()
