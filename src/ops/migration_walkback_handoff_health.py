"""Read-only current Migration -> Walkback handoff health."""
from __future__ import annotations

import sqlite3
import time
from typing import Any

from src.core.walkback_queue import classify_creator

MAX_MIGRATION_COHORT = 10
FRESH_WINDOW_SECONDS = 3600


def build_migration_walkback_handoff_health_from_paths(live_db_path: str, ops_db_path: str, *, now: int | None = None, max_migrations: int = MAX_MIGRATION_COHORT) -> dict[str, Any]:
    try:
        live = sqlite3.connect(f"file:{live_db_path}?mode=ro", uri=True)
        ops = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True)
        try:
            return build_migration_walkback_handoff_health(live, ops, now=now, max_migrations=max_migrations)
        finally:
            live.close(); ops.close()
    except Exception as exc:
        return {"state": "UNKNOWN", "error": type(exc).__name__}


def build_migration_walkback_handoff_health(live_conn, ops_conn, *, now: int | None = None, max_migrations: int = MAX_MIGRATION_COHORT) -> dict[str, Any]:
    """Use only current, mint-identity and time-aligned durable evidence."""
    if not 1 <= max_migrations <= 50:
        raise ValueError("max_migrations must be between 1 and 50")
    now = int(time.time() if now is None else now)
    try:
        rows = live_conn.execute(
            "SELECT mint, migrated_at, migration_tx, earliest_tx_creator FROM token_analysis "
            "WHERE migrated_at>=? ORDER BY migrated_at DESC LIMIT ?", (now - FRESH_WINDOW_SECONDS, max_migrations)
        ).fetchall()
    except Exception as exc:
        return {"state": "UNKNOWN", "error": type(exc).__name__, "eligible_checked": 0}
    eligible, missing_identity, missing = [], 0, []
    try:
        for mint, migrated_at, migration_tx, creator in rows:
            if not migration_tx or not creator:
                missing_identity += 1
                continue
            if classify_creator(creator, ops_conn, live_conn)[0] != "FULL_WALKBACK":
                continue
            queue = ops_conn.execute("SELECT enqueued_at, status FROM wt_walkback_queue WHERE mint=?", (mint,)).fetchone()
            item = {"mint": mint, "migrated_at": migrated_at, "queue_present": bool(queue), "enqueued_at": queue[0] if queue else None, "queue_status": queue[1] if queue else None}
            eligible.append(item)
            if not queue or queue[0] is None or int(queue[0]) < int(migrated_at):
                missing.append(item)
    except Exception as exc:
        return {"state": "UNKNOWN", "error": type(exc).__name__, "eligible_checked": len(eligible)}
    if missing_identity:
        state = "UNKNOWN"
    elif not eligible:
        state = "UNKNOWN"
    elif missing:
        state = "FAILED"
    else:
        state = "HEALTHY"
    return {"state": state, "generated_at": now, "cohort_bound": max_migrations,
            "migrations_examined": len(rows), "eligible_checked": len(eligible),
            "missing_identity_evidence": missing_identity, "missing_handoffs": len(missing),
            "durable_handoffs": len(eligible) - len(missing), "eligible": eligible}
