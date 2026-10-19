"""X67.28 — Intelligence snapshot health classification & diagnostics.

Small, dependency-light module (no Flask import) so it can be used from
both the HTTP diagnostics route (operation_dashboard_routes.py) and the
standalone scheduler's --status CLI, and unit-tested without a running
server.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from src.ops.intelligence_snapshots import read_snapshot

# Health states (task's explicit "Add health classification" requirement).
FRESH = "FRESH"
STALE_REFRESHING = "STALE_REFRESHING"
STALE_FAILED = "STALE_FAILED"
NO_SNAPSHOT = "NO_SNAPSHOT"


def _lock_owner_alive(function: str, window_seconds: int) -> bool:
    """Best-effort: is a refresh for this exact key currently believed to
    be in flight, per the scheduler's own lock file? Import is local and
    defensive -- this module must keep working (reporting STALE_FAILED
    rather than crashing) even if the scheduler module/lock dir isn't
    present in a given environment (e.g. a test sandbox)."""
    try:
        from src.core.intelligence_snapshot_scheduler import _lock_path, _pid_alive
        import os
        path = _lock_path(function, window_seconds)
        if not os.path.exists(path):
            return False
        with open(path) as f:
            owner = int((f.read().strip() or "0"))
        return bool(owner) and _pid_alive(owner)
    except Exception:  # noqa: BLE001 -- diagnostics must never raise
        return False


def classify_snapshot_health(
    function: str, window_seconds: int, *,
    max_acceptable_age_sec: Optional[float] = None,
) -> dict[str, Any]:
    """Returns the full diagnostic record for one (function, window) key:
    snapshot_age, last_successful_refresh, refresh in-flight status, and
    a health classification. Never raises."""
    snapshot = read_snapshot(function, window_seconds)
    now = time.time()

    if snapshot is None:
        return {
            "function": function,
            "window_seconds": window_seconds,
            "health": NO_SNAPSHOT,
            "snapshot_age_seconds": None,
            "last_successful_refresh": None,
            "snapshot_version": None,
            "worker_id": None,
            "refresh_reason": None,
            "refresh_in_progress": _lock_owner_alive(function, window_seconds),
        }

    age = now - snapshot.computed_at
    refreshing = _lock_owner_alive(function, window_seconds)

    if max_acceptable_age_sec is None:
        # Fall back to the scheduler's own per-window thresholds when
        # available; otherwise a conservative default that never falsely
        # reports STALE_FAILED for a window this module doesn't recognise.
        try:
            from src.core.intelligence_snapshot_scheduler import (
                MAX_ACCEPTABLE_AGE_SEC, WINDOW_ORDER, window_seconds_for,
            )
            match = next(
                (w for w in WINDOW_ORDER if window_seconds_for(w) == window_seconds), None,
            )
            max_acceptable_age_sec = MAX_ACCEPTABLE_AGE_SEC.get(match, 3600) if match else 3600
        except Exception:  # noqa: BLE001
            max_acceptable_age_sec = 3600

    if age <= max_acceptable_age_sec:
        health = FRESH
    elif refreshing:
        health = STALE_REFRESHING
    else:
        health = STALE_FAILED

    return {
        "function": function,
        "window_seconds": window_seconds,
        "health": health,
        "snapshot_age_seconds": round(age, 1),
        "last_successful_refresh": snapshot.computed_at,
        "snapshot_version": snapshot.snapshot_version,
        "worker_id": snapshot.worker_id,
        "refresh_reason": snapshot.refresh_reason,
        "refresh_in_progress": refreshing,
        "build_duration_ms": snapshot.build_duration_ms,
    }
