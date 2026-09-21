"""Read-only health for durable Migration -> Walkback handoffs."""
from __future__ import annotations

import time
import sqlite3
from typing import Any

from src.core.walkback_queue import classify_creator

MAX_MIGRATION_COHORT = 10


def build_migration_walkback_handoff_health_from_paths(
    live_db_path: str, ops_db_path: str, *, since: int = 0,
    now: int | None = None, max_migrations: int = MAX_MIGRATION_COHORT,
) -> dict[str, Any]:
    """Read the bounded handoff invariant from its canonical local authorities."""
    try:
        live = sqlite3.connect(f"file:{live_db_path}?mode=ro", uri=True)
        ops = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True)
        live.row_factory = sqlite3.Row
        ops.row_factory = sqlite3.Row
        try:
            return build_migration_walkback_handoff_health(
                live, ops, since=since, now=now, max_migrations=max_migrations,
            )
        finally:
            live.close()
            ops.close()
    except Exception as exc:
        return {"state": "INSUFFICIENT_EVIDENCE", "error": type(exc).__name__,
                "cohort_bound": max_migrations}


def build_migration_walkback_handoff_health(
    live_conn, ops_conn, *, since: int = 0, now: int | None = None,
    max_migrations: int = MAX_MIGRATION_COHORT,
) -> dict[str, Any]:
    """Classify a bounded, durable-only cohort without writes or RPC."""
    if max_migrations < 1 or max_migrations > 50:
        raise ValueError("max_migrations must be between 1 and 50")
    now = int(time.time() if now is None else now)
    try:
        migrations = live_conn.execute(
            "SELECT mint, migrated_at, migration_tx, earliest_tx_creator "
            "FROM token_analysis WHERE migrated_at IS NOT NULL AND migrated_at>=? "
            "ORDER BY migrated_at DESC LIMIT ?", (since, max_migrations)
        ).fetchall()
    except Exception as exc:
        return {"state": "INSUFFICIENT_EVIDENCE", "error": type(exc).__name__, "cohort_bound": max_migrations}
    if not migrations:
        return {"state": "INSUFFICIENT_EVIDENCE", "eligible_checked": 0, "cohort_bound": max_migrations}
    eligible: list[dict[str, Any]] = []
    try:
        for row in migrations:
            mint, migrated_at, migration_tx, creator = row[0], row[1], row[2], row[3]
            if not migration_tx:
                continue
            classification = classify_creator(creator, ops_conn, live_conn)[0]
            if classification != "FULL_WALKBACK":
                continue
            queue = ops_conn.execute(
                "SELECT enqueued_at, status FROM wt_walkback_queue WHERE mint=?", (mint,)
            ).fetchone()
            eligible.append({"mint": mint, "migrated_at": migrated_at, "queue_present": bool(queue),
                             "enqueued_at": queue[0] if queue else None, "queue_status": queue[1] if queue else None})
    except Exception as exc:
        return {"state": "INSUFFICIENT_EVIDENCE", "error": type(exc).__name__, "cohort_bound": max_migrations}
    missing = [item for item in eligible if not item["queue_present"]]
    state = "NO_ELIGIBLE_TRAFFIC" if not eligible else ("FAILED" if len(missing) >= 2 else "DEGRADED" if missing else "HEALTHY")
    successes = [item for item in eligible if item["queue_present"]]
    return {"state": state, "generated_at": now, "cohort_bound": max_migrations,
            "migrations_examined": len(migrations), "eligible_checked": len(eligible),
            "durable_handoffs": len(successes), "missing_handoffs": len(missing),
            "eligible": list(reversed(eligible)),
            "most_recent_eligible": eligible[0] if eligible else None,
            "most_recent_successful_enqueue": successes[0] if successes else None}
