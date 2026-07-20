"""X29.3 — Boundary Participant Analytics (renamed from X29.2's Origin Participant Analytics).

Read-only aggregation over wt_funding_boundary, answering "is this boundary
wallet a recurring, potentially strong operational participant?" without
ever automatically promoting it to an operator ("No automated promotion
logic is required in this sprint" -- X29.2's brief, still true here since
this sprint changes terminology, not policy).

Weighting:
  PROVEN              -> strongest eligible evidence (also implies origin_proven)
  BOUNDED_OBSERVATION -> usable but explicitly bounded evidence
  STATIC_MATCH        -> annotation only, excluded from positive
                          relationship counts (no persisted signature/edge
                          — nothing to count as a real relationship)
  UNRESOLVED          -> no positive relationship evidence at all

Zero RPC — reads only wt_funding_boundary plus the already-persisted
wt_watchtower_launches/wt_attribution_outcomes for downstream context.
"""
from __future__ import annotations

import sqlite3
from typing import Any

# Statuses that count as positive relationship evidence for aggregation
# purposes (STATIC_MATCH/UNRESOLVED must not strengthen operation-clustering
# relationships).
_POSITIVE_STATUSES = ("PROVEN", "BOUNDED_OBSERVATION")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def boundary_wallet_profile(conn: sqlite3.Connection, boundary_wallet: str) -> dict[str, Any]:
    """Aggregates every wt_funding_boundary row for one boundary_wallet into
    launches_downstream, distinct_creators, distinct_subproviders,
    distinct_treasuries, known_operations_reached, first_seen, last_seen,
    boundary_type, status_distribution.

    Only PROVEN/BOUNDED_OBSERVATION rows count toward the positive
    relationship fields (launches_downstream, distinct_*); STATIC_MATCH and
    UNRESOLVED rows are visible in status_distribution but never inflate
    those counts."""
    if not _table_exists(conn, "wt_funding_boundary"):
        return _empty_profile(boundary_wallet)

    all_rows = conn.execute(
        "SELECT launch_mint, subject_wallet, boundary_status, boundary_type, "
        "       boundary_block_time, created_at "
        "FROM wt_funding_boundary WHERE boundary_wallet=?",
        (boundary_wallet,),
    ).fetchall()
    if not all_rows:
        return _empty_profile(boundary_wallet)

    positive_rows = [r for r in all_rows if r[2] in _POSITIVE_STATUSES]

    status_distribution: dict[str, int] = {}
    for r in all_rows:
        status_distribution[r[2]] = status_distribution.get(r[2], 0) + 1

    launches_downstream = len({r[0] for r in positive_rows})
    distinct_subjects = {r[1] for r in positive_rows if r[1]}

    # distinct_creators/subproviders/treasuries: this table stores the
    # *subject_wallet* the boundary funded (the creator, per the backfill's
    # current derivation) — subprov/treasury roles for that subject are
    # looked up from wt_active_subprov_sessions where available, read-only.
    distinct_creators = len(distinct_subjects)
    distinct_subproviders = 0
    distinct_treasuries = 0
    if _table_exists(conn, "wt_active_subprov_sessions") and distinct_subjects:
        placeholders = ",".join("?" for _ in distinct_subjects)
        distinct_subproviders = conn.execute(
            f"SELECT COUNT(DISTINCT subprov_wallet) FROM wt_active_subprov_sessions "
            f"WHERE subprov_wallet IN ({placeholders})",
            tuple(distinct_subjects),
        ).fetchone()[0]
        distinct_treasuries = conn.execute(
            f"SELECT COUNT(DISTINCT treasury_wallet) FROM wt_active_subprov_sessions "
            f"WHERE treasury_wallet IN ({placeholders}) AND treasury_wallet IS NOT NULL",
            tuple(distinct_subjects),
        ).fetchone()[0]

    known_operations_reached = 0
    if positive_rows and _table_exists(conn, "wt_attribution_outcomes"):
        mints = {r[0] for r in positive_rows}
        placeholders = ",".join("?" for _ in mints)
        known_operations_reached = conn.execute(
            f"SELECT COUNT(DISTINCT operator_id) FROM wt_attribution_outcomes "
            f"WHERE mint IN ({placeholders}) AND operator_id IS NOT NULL",
            tuple(mints),
        ).fetchone()[0]

    times = [r[5] for r in all_rows if r[5]]
    boundary_types = {r[3] for r in all_rows if r[3]}

    return {
        "boundary_wallet": boundary_wallet,
        "launches_downstream": launches_downstream,
        "distinct_creators": distinct_creators,
        "distinct_subproviders": distinct_subproviders,
        "distinct_treasuries": distinct_treasuries,
        "known_operations_reached": known_operations_reached,
        "first_seen": min(times) if times else None,
        "last_seen": max(times) if times else None,
        "boundary_type": sorted(boundary_types)[0] if len(boundary_types) == 1 else (sorted(boundary_types) or None),
        "status_distribution": status_distribution,
        "total_observations": len(all_rows),
    }


def _empty_profile(boundary_wallet: str) -> dict[str, Any]:
    return {
        "boundary_wallet": boundary_wallet,
        "launches_downstream": 0,
        "distinct_creators": 0,
        "distinct_subproviders": 0,
        "distinct_treasuries": 0,
        "known_operations_reached": 0,
        "first_seen": None,
        "last_seen": None,
        "boundary_type": None,
        "status_distribution": {},
        "total_observations": 0,
    }


def recurring_boundary_wallets(conn: sqlite3.Connection, *, min_launches: int = 2) -> list[dict[str, Any]]:
    """Read-only candidate list: boundary wallets with >=min_launches DISTINCT
    downstream launches via PROVEN/BOUNDED_OBSERVATION evidence only —
    a strong candidate relationship for later operation discovery and human
    review, never an automatic operator promotion."""
    if not _table_exists(conn, "wt_funding_boundary"):
        return []
    rows = conn.execute(
        f"SELECT boundary_wallet, COUNT(DISTINCT launch_mint) as n "
        f"FROM wt_funding_boundary "
        f"WHERE boundary_wallet IS NOT NULL AND boundary_status IN {tuple(_POSITIVE_STATUSES)} "
        f"GROUP BY boundary_wallet HAVING n >= ? ORDER BY n DESC",
        (min_launches,),
    ).fetchall()
    return [boundary_wallet_profile(conn, r[0]) for r in rows]


def funding_boundary_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    """Aggregate metrics: funding_boundary_total, by_status, by_type,
    non_causal, missing_signature, history_exhausted, pagination_limited,
    age_buckets — computed directly from the persisted table, zero RPC."""
    if not _table_exists(conn, "wt_funding_boundary"):
        return {
            "funding_boundary_total": 0, "funding_boundary_by_status": {}, "funding_boundary_by_type": {},
            "funding_boundary_non_causal": 0, "funding_boundary_missing_signature": 0,
            "funding_boundary_history_exhausted": 0, "funding_boundary_pagination_limited": 0,
            "funding_boundary_age_buckets": {},
        }
    total = conn.execute("SELECT COUNT(*) FROM wt_funding_boundary").fetchone()[0]
    by_status = dict(conn.execute(
        "SELECT boundary_status, COUNT(*) FROM wt_funding_boundary GROUP BY boundary_status"
    ).fetchall())
    by_type = dict(conn.execute(
        "SELECT boundary_type, COUNT(*) FROM wt_funding_boundary GROUP BY boundary_type"
    ).fetchall())
    non_causal = conn.execute(
        "SELECT COUNT(*) FROM wt_funding_boundary WHERE resolution_reason='NON_CAUSAL_FUNDING_EVENT'"
    ).fetchone()[0]
    missing_sig = conn.execute(
        "SELECT COUNT(*) FROM wt_funding_boundary WHERE boundary_signature IS NULL"
    ).fetchone()[0]
    history_exhausted = conn.execute(
        "SELECT COUNT(*) FROM wt_funding_boundary WHERE history_exhausted=1"
    ).fetchone()[0]
    pagination_limited = conn.execute(
        "SELECT COUNT(*) FROM wt_funding_boundary WHERE pagination_limit_reached=1"
    ).fetchone()[0]

    from src.ops.funding_boundary import age_bucket_for
    age_rows = conn.execute("SELECT boundary_age_at_launch_seconds FROM wt_funding_boundary").fetchall()
    age_buckets: dict[str, int] = {}
    for (age,) in age_rows:
        b = age_bucket_for(age)
        age_buckets[b] = age_buckets.get(b, 0) + 1

    return {
        "funding_boundary_total": total,
        "funding_boundary_by_status": by_status,
        "funding_boundary_by_type": by_type,
        "funding_boundary_non_causal": non_causal,
        "funding_boundary_missing_signature": missing_sig,
        "funding_boundary_history_exhausted": history_exhausted,
        "funding_boundary_pagination_limited": pagination_limited,
        "funding_boundary_age_buckets": age_buckets,
    }
