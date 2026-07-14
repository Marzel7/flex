"""Rolling persisted-data funnel for the canonical WATCHTOWER control case."""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Any

from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID


def _ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _rows(conn, table: str, columns: str, where: str, args: tuple = ()) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in conn.execute(f"SELECT {columns} FROM {table} WHERE {where}", args)]
    except sqlite3.Error:
        return []


def _stage(
    key: str,
    label: str,
    items: list[dict[str, Any]],
    prior_count: int | None,
    *,
    href: str,
    next_missing: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    timestamps = [int(i["observed_at"]) for i in items if i.get("observed_at")]
    stuck = [int(i["observed_at"]) for i in (next_missing or []) if i.get("observed_at")]
    count = len(items)
    return {
        "key": key,
        "label": label,
        "count": count,
        "loss": None if prior_count is None else prior_count - count,
        "conversion_pct": 100.0 if prior_count is None else round(100.0 * count / prior_count, 2) if prior_count else 0.0,
        "freshness_at": max(timestamps) if timestamps else None,
        "oldest_stuck_at": min(stuck) if stuck else None,
        "href": href,
    }


def build_watchtower_funnel(
    ops_db_path: str,
    core_db_path: str,
    *,
    now: int | None = None,
    window_seconds: int = 72 * 3600,
) -> dict[str, Any]:
    """Build a sequential launch-to-analyst funnel from persisted records only."""
    now = int(now or time.time())
    cutoff = now - int(window_seconds)
    with _ro(core_db_path) as core:
        launches = _rows(
            core,
            "token_analysis",
            "mint,COALESCE(pf_ws_creator,earliest_tx_creator) AS creator,CAST(analyzed_at AS INTEGER) AS observed_at",
            "analyzed_at>=? AND analyzed_at<?",
            (cutoff, now),
        )

    with _ro(ops_db_path) as ops:
        tables = _tables(ops)
        queue = {
            r["mint"]: r for r in _rows(
                ops, "wt_walkback_queue",
                "mint,creator,subprov,treasury,status,enqueued_at,started_at,completed_at,updated_at",
                "enqueued_at>=? AND enqueued_at<?", (cutoff, now),
            )
        } if "wt_walkback_queue" in tables else {}
        strict = {
            r["mint"]: r for r in _rows(
                ops, "wt_watchtower_launches",
                "mint,creator_wallet,subprov_wallet,treasury_wallet,create_time,recorded_at",
                "COALESCE(create_time,recorded_at)>=? AND COALESCE(create_time,recorded_at)<?",
                (cutoff, now),
            )
        } if "wt_watchtower_launches" in tables else {}
        attribution = {
            r["mint"]: r for r in _rows(
                ops, "watchtower_token_attribution",
                "mint,creator,matched_subprov,matched_treasury,scored_at",
                "scored_at>=? AND scored_at<?", (cutoff, now),
            )
        } if "watchtower_token_attribution" in tables else {}
        confirmed = {
            r[0] for r in ops.execute("SELECT treasury FROM wt_confirmed_treasuries")
        } if "wt_confirmed_treasuries" in tables else set()
        canonical = {
            r[0] for r in ops.execute(
                "SELECT entity_address FROM operator_entities WHERE operator_id=?",
                (WATCHTOWER_OPERATOR_ID,),
            )
        } if "operator_entities" in tables else set()
        outcome_rows = _rows(
            ops, "wt_attribution_outcomes",
            "mint,outcome_type,stop_reason,terminal_entity,terminal_entity_type,confidence,"
            "operator_id,should_seed_emerging_operator,should_retry,completed_at",
            "completed_at>=? AND completed_at<?", (cutoff, now),
        ) if "wt_attribution_outcomes" in tables else []
        discovery_tokens: set[str] = set()
        for table, column in (
            ("migrated_tokens", "mint"),
            ("wt_token_lifecycle", "mint"),
            ("wt_watchtower_launches", "mint"),
            ("watchtower_token_attribution", "mint"),
        ):
            if table in tables:
                discovery_tokens.update(
                    r[0] for r in ops.execute(
                        f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"
                    )
                )

    # Collapse accidental duplicate launch rows to one mint and retain first observation.
    by_mint: dict[str, dict[str, Any]] = {}
    for launch in launches:
        mint = launch.get("mint")
        if not mint:
            continue
        existing = by_mint.get(mint)
        if not existing or (launch.get("observed_at") or now) < (existing.get("observed_at") or now):
            by_mint[mint] = launch
    launches = list(by_mint.values())

    creators = [row for row in launches if row.get("creator")]
    started = [row for row in creators if row["mint"] in queue]
    completed = [row for row in started if queue[row["mint"]].get("status") == "complete"]

    def resolved(row: dict[str, Any]) -> tuple[Any, Any]:
        mint = row["mint"]
        q, s, a = queue.get(mint, {}), strict.get(mint, {}), attribution.get(mint, {})
        subprov = q.get("subprov") or s.get("subprov_wallet") or a.get("matched_subprov")
        treasury = q.get("treasury") or s.get("treasury_wallet") or a.get("matched_treasury")
        return subprov, treasury

    subprovs = [row for row in completed if resolved(row)[0]]
    treasuries = [row for row in subprovs if resolved(row)[1]]
    known = [row for row in treasuries if resolved(row)[1] in confirmed]
    canonical_rows = [row for row in known if resolved(row)[1] in canonical]
    discovery = [row for row in canonical_rows if row["mint"] in discovery_tokens]
    # The significance-separated Mission Control WATCHTOWER stream is defined
    # directly from the same confirmed+canonical launch materialisation.
    mission = list(canonical_rows)

    populations = [
        ("launches", "Launches", launches, "/live-launches"),
        ("creators", "Creators Resolved", creators, "/discovery?type=creator"),
        ("walkbacks_started", "Walkbacks Started", started, "/api/ops/walkback-queue"),
        ("walkbacks_completed", "Walkbacks Completed", completed, "/api/ops/walkback-queue"),
        ("subprovisioners", "Sub-Provisioners", subprovs, "/discovery?type=sub_provisioner"),
        ("treasuries", "Treasuries", treasuries, "/discovery?type=treasury"),
        ("known_treasuries", "Confirmed Treasuries", known, "/discovery?type=treasury"),
        ("canonical_operators", "Canonical Operators", canonical_rows, f"/intelligence/operator/{WATCHTOWER_OPERATOR_ID}"),
        ("discovery_visible", "Discovery Visible", discovery, "/discovery"),
        ("mission_control", "Mission Control", mission, "/ops-os"),
    ]
    stages = []
    for index, (key, label, items, href) in enumerate(populations):
        prior = populations[index - 1][2] if index else None
        next_items = populations[index + 1][2] if index + 1 < len(populations) else items
        next_ids = {i["mint"] for i in next_items}
        missing = [i for i in items if i["mint"] not in next_ids]
        stages.append(_stage(
            key, label, items, len(prior) if prior is not None else None,
            href=href, next_missing=missing,
        ))

    outcome_counts: dict[str, int] = {}
    for row in outcome_rows:
        key = row["outcome_type"]
        outcome_counts[key] = outcome_counts.get(key, 0) + 1
    outcomes = [
        {
            "outcome_type": key,
            "count": count,
            "should_seed_emerging_operator": key == "UNKNOWN_INFRASTRUCTURE",
            "href": f"/api/ops-v2/attribution-outcomes?outcome_type={key}",
        }
        for key, count in sorted(outcome_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    return {
        "ok": True,
        "generated_at": now,
        "window_seconds": window_seconds,
        "window_start": cutoff,
        "window_end": now,
        "stages": stages,
        "outcomes": outcomes,
        "outcome_total": len(outcome_rows),
        "healthy": stages[-1]["count"] == stages[-3]["count"],
        "control_operator_id": WATCHTOWER_OPERATOR_ID,
    }
