"""Read-only current-rate projection for missing CREATE anchors.

Historical waiting debt is deliberately excluded from the headline.  The
projection uses only retained queue timestamps and never changes eligibility.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

WINDOWS = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}


def build(db_path: str, *, now: int | None = None) -> dict[str, Any]:
    now = int(time.time() if now is None else now)
    empty = {name: 0 for name in WINDOWS}
    result: dict[str, Any] = {
        "headline_window": "24h",
        "new": dict(empty), "recovered": dict(empty), "recent_unresolved": dict(empty),
        "historical_unresolved": 0, "latest_new_missing_at": None,
        "latest_new_missing_mint": None, "trend": {"24h": "STABLE", "7d": "STABLE"},
        "semantics": "waiting rows with WAITING_FOR_CREATE_ANCHOR; historical_unresolved is context only",
    }
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT mint,enqueued_at,anchor_recovered_at FROM wt_walkback_queue "
            "WHERE path_state='WAITING_FOR_CREATE_ANCHOR' OR anchor_recovered_at IS NOT NULL"
        ).fetchall()
        unresolved = conn.execute(
            "SELECT count(*) FROM wt_walkback_queue WHERE status='waiting' "
            "AND path_state='WAITING_FOR_CREATE_ANCHOR'"
        ).fetchone()[0]
        conn.close()
    except Exception as exc:
        result["error"] = str(exc)[:120]
        return result
    result["historical_unresolved"] = unresolved
    latest = None
    for row in rows:
        enqueued, recovered = row["enqueued_at"], row["anchor_recovered_at"]
        for label, seconds in WINDOWS.items():
            cutoff = now - seconds
            if enqueued is not None and cutoff <= int(enqueued) < now:
                result["new"][label] += 1
            if recovered is not None and cutoff <= int(recovered) < now:
                result["recovered"][label] += 1
        if enqueued is not None and int(enqueued) < now and (latest is None or int(enqueued) > latest[0]):
            latest = (int(enqueued), row["mint"])
    for label, seconds in WINDOWS.items():
        cutoff = now - seconds
        result["recent_unresolved"][label] = sum(
            1 for row in rows if row["anchor_recovered_at"] is None and row["enqueued_at"] is not None
            and cutoff <= int(row["enqueued_at"]) < now
        )
    if latest:
        result["latest_new_missing_at"], result["latest_new_missing_mint"] = latest
    for label in ("24h", "7d"):
        net = result["new"][label] - result["recovered"][label]
        result["trend"][label] = "GROWING" if net > 0 else "SHRINKING" if net < 0 else "STABLE"
    return result
