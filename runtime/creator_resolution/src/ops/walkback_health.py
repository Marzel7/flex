"""Progress-based health for the WATCHTOWER walkback worker."""
from __future__ import annotations

import time
import threading
from typing import Any


DEFAULT_STALLED_AFTER_SECONDS = 180


class WalkbackHealthSampler:
    """Best-effort detailed-health sampler, intentionally outside the live loop.

    The caller supplies a read-only connection factory.  A slow query can only
    occupy this daemon thread; queue claims, reconciliation, provider work, and
    the liveness heartbeat must never wait for it.
    """

    def __init__(self, connect_read_only, *, interval_seconds: int = 300):
        self._connect_read_only = connect_read_only
        self._interval_seconds = max(1, int(interval_seconds))
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] | None = None
        self._last_requested_at = 0.0
        self._running = False

    def request_refresh(self, *, now: int | None = None) -> bool:
        """Start a due refresh without waiting for it. Returns whether started."""
        timestamp = float(now if now is not None else time.time())
        with self._lock:
            if self._running or timestamp - self._last_requested_at < self._interval_seconds:
                return False
            self._running = True
            self._last_requested_at = timestamp
        threading.Thread(target=self._refresh, name="walkback-health-sampler", daemon=True).start()
        return True

    def _refresh(self) -> None:
        try:
            conn = self._connect_read_only()
            try:
                snapshot = build_walkback_health(conn)
            finally:
                conn.close()
            with self._lock:
                self._snapshot = snapshot
        finally:
            with self._lock:
                self._running = False

    def heartbeat_snapshot(self, *, now: int | None = None) -> dict[str, Any]:
        """Return bounded metadata for the liveness heartbeat without DB reads."""
        timestamp = int(now or time.time())
        with self._lock:
            snapshot = dict(self._snapshot) if self._snapshot else None
            running = self._running
        if snapshot is None:
            return {
                "status": "UNAVAILABLE",
                "health_state": "UNAVAILABLE",
                "generated_at": None,
                "staleness_seconds": None,
                "refresh_in_progress": running,
            }
        generated_at = int(snapshot.get("generated_at") or timestamp)
        staleness = max(0, timestamp - generated_at)
        state = "STALE" if staleness > self._interval_seconds else "FRESH"
        return {
            "status": snapshot.get("status", "UNHEALTHY"),
            "health_state": state,
            "generated_at": generated_at,
            "staleness_seconds": staleness,
            "refresh_in_progress": running,
        }


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
    # Keep the four diagnostic predicates exactly as before, but derive them
    # in one pass over the current queue.  The health endpoint previously
    # scanned this large table four times on every refresh.
    diagnostic_counts = conn.execute(
        "SELECT "
        "SUM(CASE WHEN status='complete' AND completed_at>=? THEN 1 ELSE 0 END),"
        "SUM(CASE WHEN status='complete' AND completed_at>=? THEN 1 ELSE 0 END),"
        "SUM(CASE WHEN updated_at>=? AND ("
        "last_error LIKE '%NestedDatabaseWriteError%' OR "
        "last_error LIKE '%database is locked%' OR "
        "last_error LIKE '%DatabaseWriteLockError%') THEN 1 ELSE 0 END),"
        "SUM(CASE WHEN last_error LIKE '%NestedDatabaseWriteError%' "
        "AND updated_at>=? THEN 1 ELSE 0 END),"
        "MAX(CASE WHEN status='complete' THEN completed_at END) "
        "FROM wt_walkback_queue",
        (now - 60, now - 3600, now - 3600, now - 3600),
    ).fetchone()
    completed_minute = int(diagnostic_counts[0] or 0)
    completed_hour = int(diagnostic_counts[1] or 0)
    write_failures = int(diagnostic_counts[2] or 0)
    nested_write_failures = int(diagnostic_counts[3] or 0)
    latest_completion_at = diagnostic_counts[4]
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
    heartbeat_at = heartbeat_override
    if heartbeat_at is None:
        heartbeat_at = _scalar(
            conn,
            "SELECT last_seen FROM wt_worker_heartbeat WHERE worker_name='walkback_worker'",
        )
    heartbeat_age = now - heartbeat_at if heartbeat_at else None

    oldest_pending_age = now - oldest_pending_at if oldest_pending_at else None
    # A newly-enqueued row can legitimately land between one-minute
    # completion buckets. Require the pending work itself to age past the
    # stalled threshold before calling that snapshot a progress failure.
    # Missing enqueue provenance remains fail-closed.
    no_progress = (
        pending > 0
        and completed_minute == 0
        and (oldest_pending_age is None or oldest_pending_age > stalled_after_seconds)
    )
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
        "oldest_pending_age_seconds": oldest_pending_age,
        "completed_per_minute": completed_minute,
        "completed_last_hour": completed_hour,
        "latest_completion_at": latest_completion_at,
        "latest_completion_age_seconds": (
            max(0, now - latest_completion_at) if latest_completion_at else None
        ),
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
