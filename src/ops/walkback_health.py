"""Progress-based health for the WATCHTOWER walkback worker."""
from __future__ import annotations

import time
from typing import Any


DEFAULT_STALLED_AFTER_SECONDS = 180


def _scalar(conn, sql: str, args: tuple = ()) -> Any:
    row = conn.execute(sql, args).fetchone()
    return row[0] if row else None


def build_walkback_health(
    conn,
    *,
    now: int | None = None,
    stalled_after_seconds: int = DEFAULT_STALLED_AFTER_SECONDS,
    heartbeat_override: int | None = None,
) -> dict[str, Any]:
    """Measure useful work, queue pressure, latency, and failure evidence."""
    now = int(now or time.time())
    pending = int(_scalar(conn, "SELECT COUNT(*) FROM wt_walkback_queue WHERE status='pending'") or 0)
    running = int(_scalar(conn, "SELECT COUNT(*) FROM wt_walkback_queue WHERE status='running'") or 0)
    completed_minute = int(_scalar(
        conn,
        "SELECT COUNT(*) FROM wt_walkback_queue WHERE status='complete' AND completed_at>=?",
        (now - 60,),
    ) or 0)
    completed_hour = int(_scalar(
        conn,
        "SELECT COUNT(*) FROM wt_walkback_queue WHERE status='complete' AND completed_at>=?",
        (now - 3600,),
    ) or 0)
    average_latency = _scalar(
        conn,
        "SELECT AVG(completed_at-started_at) FROM wt_walkback_queue "
        "WHERE status='complete' AND completed_at>=? AND started_at IS NOT NULL "
        "AND completed_at>=started_at",
        (now - 3600,),
    )
    oldest_pending_at = _scalar(
        conn, "SELECT MIN(enqueued_at) FROM wt_walkback_queue WHERE status='pending'"
    )
    oldest_running_at = _scalar(
        conn, "SELECT MIN(started_at) FROM wt_walkback_queue WHERE status='running'"
    )
    stalled_running = int(_scalar(
        conn,
        "SELECT COUNT(*) FROM wt_walkback_queue WHERE status='running' "
        "AND COALESCE(started_at,updated_at,enqueued_at)<=?",
        (now - stalled_after_seconds,),
    ) or 0)
    write_failures = int(_scalar(
        conn,
        "SELECT COUNT(*) FROM wt_walkback_queue WHERE updated_at>=? AND ("
        "last_error LIKE '%NestedDatabaseWriteError%' OR "
        "last_error LIKE '%database is locked%' OR "
        "last_error LIKE '%DatabaseWriteLockError%')",
        (now - 3600,),
    ) or 0)
    nested_write_failures = int(_scalar(
        conn,
        "SELECT COUNT(*) FROM wt_walkback_queue WHERE last_error LIKE '%NestedDatabaseWriteError%' "
        "AND updated_at>=?",
        (now - 3600,),
    ) or 0)
    heartbeat_at = heartbeat_override
    if heartbeat_at is None:
        heartbeat_at = _scalar(
            conn,
            "SELECT last_seen FROM wt_worker_heartbeat WHERE worker_name='walkback_worker'",
        )
    heartbeat_age = now - heartbeat_at if heartbeat_at else None

    no_progress = pending > 0 and completed_minute == 0
    heartbeat_stale = heartbeat_age is None or heartbeat_age > stalled_after_seconds
    unhealthy_reasons = []
    if no_progress:
        unhealthy_reasons.append("pending work exists but completed_per_minute is zero")
    if stalled_running:
        unhealthy_reasons.append(f"{stalled_running} running job(s) are stalled")
    if heartbeat_stale:
        unhealthy_reasons.append("worker heartbeat is stale or absent")
    if nested_write_failures:
        unhealthy_reasons.append("nested database write failures are present")

    return {
        "status": "UNHEALTHY" if unhealthy_reasons else "HEALTHY",
        "healthy": not unhealthy_reasons,
        "reasons": unhealthy_reasons,
        "queue_depth": pending + running,
        "pending": pending,
        "running": running,
        "oldest_pending_at": oldest_pending_at,
        "oldest_pending_age_seconds": now - oldest_pending_at if oldest_pending_at else None,
        "completed_per_minute": completed_minute,
        "completed_last_hour": completed_hour,
        "average_completion_latency_seconds": round(float(average_latency), 3) if average_latency is not None else None,
        "heartbeat_at": heartbeat_at,
        "heartbeat_age_seconds": heartbeat_age,
        "oldest_running_at": oldest_running_at,
        "oldest_running_age_seconds": now - oldest_running_at if oldest_running_at else None,
        "stalled_running_jobs": stalled_running,
        "write_failures_last_hour": write_failures,
        "nested_write_failures_last_hour": nested_write_failures,
        "stalled_after_seconds": stalled_after_seconds,
        "generated_at": now,
    }


def recover_stalled_running_jobs(
    conn,
    *,
    now: int | None = None,
    stalled_after_seconds: int = DEFAULT_STALLED_AFTER_SECONDS,
    max_attempts: int = 3,
) -> dict[str, int]:
    """Recover crash-stranded claims without retrying an active operation.

    Jobs below the configured attempt ceiling are returned to ``pending``;
    exhausted jobs become explicit failures.  The update is deterministic and
    intended for worker startup before new rows are claimed.
    """
    now = int(now or time.time())
    cutoff = now - stalled_after_seconds
    retryable = int(_scalar(
        conn,
        "SELECT COUNT(*) FROM wt_walkback_queue WHERE status='running' "
        "AND COALESCE(started_at,updated_at,enqueued_at)<=? AND attempts<?",
        (cutoff, max_attempts),
    ) or 0)
    exhausted = int(_scalar(
        conn,
        "SELECT COUNT(*) FROM wt_walkback_queue WHERE status='running' "
        "AND COALESCE(started_at,updated_at,enqueued_at)<=? AND attempts>=?",
        (cutoff, max_attempts),
    ) or 0)
    from src.ops.walkback_cycle_trace import trace_boundary, trace_failure
    import time as _time
    try:
        trace_boundary("maintenance_write_attempted", extra={"phase": "requeue_stalled"})
        conn.execute(
            "UPDATE wt_walkback_queue SET status='pending',started_at=NULL,updated_at=?,"
            "last_error='RECOVERED_STALLED_RUNNING_JOB' "
            "WHERE status='running' AND COALESCE(started_at,updated_at,enqueued_at)<=? AND attempts<?",
            (now, cutoff, max_attempts),
        )
        trace_boundary("maintenance_write_attempted", extra={"phase": "fail_exhausted_stalled"})
        conn.execute(
            "UPDATE wt_walkback_queue SET status='failed',updated_at=?,"
            "last_error='STALLED_RUNNING_JOB_ATTEMPTS_EXHAUSTED' "
            "WHERE status='running' AND COALESCE(started_at,updated_at,enqueued_at)<=? AND attempts>=?",
            (now, cutoff, max_attempts),
        )
        _t0 = _time.monotonic()
        conn.commit()
        trace_boundary("maintenance_write_attempted", extra={"phase": "commit_completed", "elapsed_s": round(_time.monotonic() - _t0, 3)})
    except Exception as e:
        trace_failure("maintenance_write_attempted:recover_stalled_running_jobs", e)
        raise
    return {"requeued": retryable, "failed": exhausted}
