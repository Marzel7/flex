"""
Bounded retention for wt_subprov_sig_retry -- status='DONE' rows only.

Qualified by SUBPROV_SIG_RETRY_LIFECYCLE_QUALIFIED / this milestone's audit:
  - wt_subprov_sig_retry has ZERO reader dependency from any operation,
    Potential Operation, P3R, behavioural, Walkback, or attribution system
    (proven by full reader-lineage trace across src/ops/living_potential_
    operations.py, src/ops/generic_living_pipeline_v2.py,
    src/ops/generic_living_lineage_metadata.py, src/ops/operator_routes.py --
    zero references found).
  - DONE_IS_CURRENT_DEDUPE_AUTHORITY for (subprov_wallet, signature): a DONE
    row is the only durable marker of "this signature was already processed"
    consumed by the live dedupe check in src/core/ws_cascade.py.
  - Retained instrumentation (wt_subprov_sig_dedupe_stats /
    wt_subprov_sig_dedupe_summary) proves the real dedupe-hit age
    distribution across a ~43-day observation window: max observed hit age
    ~19.7 days, zero hits ever recorded beyond 30 days. 7 days is NOT safe
    (16 real hits landed in the 14d-30d bucket). 30 days is the smallest
    window supported by actual measured evidence.

Deletes ONLY rows matching status='DONE' AND last_attempt_at < cutoff.
Structurally cannot touch PENDING/RUNNING/FAILED -- every query in this
module hardcodes "status='DONE'"; there is no parameter or code path that
can widen it to another status.

Mirrors src/metrics/usage_tracker.py::cleanup_old_wss_metrics_batch's bounded
batch-delete pattern exactly: fresh per-batch SELECT (no offset/cursor state,
idempotent/resumable), BEGIN IMMEDIATE + executemany DELETE + commit,
bounded_write_wait() write-lane acquisition, SQLITE_BUSY retry with backoff,
never raises.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Optional

from src.utils.db_locking import bounded_write_wait

_RETRY_MAINTENANCE_LANE_TIMEOUT_SECONDS = 3.0
_RETRY_MAINTENANCE_TOTAL_BUDGET_SECONDS = 10.0


def cleanup_old_subprov_sig_retry_done_batch(
    db_path: str, batch_size: int, cutoff_ts: float, busy_retry_attempts: int = 3
) -> int:
    """
    Bounded, idempotent deletion of wt_subprov_sig_retry rows where
    status='DONE' AND last_attempt_at < cutoff_ts.

    Deletes at most `batch_size` rows, oldest-last_attempt_at-first, in ONE
    transaction. Returns the exact count deleted. Never raises -- returns 0
    on any connection failure, empty result, exhausted retry budget, or
    overall time-budget exhaustion (matching cleanup_old_wss_metrics_batch's
    contract exactly, including MAINTENANCE_SKIPPED_WRITE_LANE_BUSY callers
    distinguishing "busy" from "genuinely nothing eligible" via a preflight
    count, same as the runner does for RPC/WSS).

    The WHERE clause hardcodes status='DONE' in both the SELECT and the
    DELETE -- PENDING, RUNNING, and FAILED rows can never be selected or
    deleted by this function regardless of caller input.
    """
    if batch_size <= 0:
        return 0

    call_start = time.monotonic()
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=_RETRY_MAINTENANCE_LANE_TIMEOUT_SECONDS)
        cursor = conn.cursor()

        attempt = 0
        while True:
            if time.monotonic() - call_start >= _RETRY_MAINTENANCE_TOTAL_BUDGET_SECONDS:
                conn.close()
                return 0
            try:
                with bounded_write_wait(_RETRY_MAINTENANCE_LANE_TIMEOUT_SECONDS):
                    cursor.execute(
                        "SELECT subprov_wallet, signature FROM wt_subprov_sig_retry "
                        "WHERE status='DONE' AND last_attempt_at < ? "
                        "ORDER BY last_attempt_at ASC LIMIT ?",
                        (cutoff_ts, batch_size),
                    )
                    keys = cursor.fetchall()
                break
            except sqlite3.OperationalError as e:
                attempt += 1
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    if attempt >= busy_retry_attempts:
                        conn.close()
                        return 0
                    time.sleep(0.2 * attempt)
                    continue
                raise

        if not keys:
            conn.close()
            return 0

        attempt = 0
        while True:
            if time.monotonic() - call_start >= _RETRY_MAINTENANCE_TOTAL_BUDGET_SECONDS:
                try:
                    conn.rollback()
                except sqlite3.OperationalError:
                    pass
                conn.close()
                return 0
            try:
                with bounded_write_wait(_RETRY_MAINTENANCE_LANE_TIMEOUT_SECONDS):
                    cursor.execute("BEGIN IMMEDIATE")
                    cursor.executemany(
                        "DELETE FROM wt_subprov_sig_retry "
                        "WHERE subprov_wallet = ? AND signature = ? "
                        "AND status='DONE' AND last_attempt_at < ?",
                        [(w, s, cutoff_ts) for (w, s) in keys],
                    )
                    deleted = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else len(keys)
                    conn.commit()
                conn.close()
                return deleted
            except sqlite3.OperationalError as e:
                try:
                    conn.rollback()
                except sqlite3.OperationalError:
                    pass
                attempt += 1
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    if attempt >= busy_retry_attempts:
                        conn.close()
                        return 0
                    time.sleep(0.2 * attempt)
                    continue
                conn.close()
                return 0

    except Exception:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        return 0


def count_old_subprov_sig_retry_done(db_path: str, cutoff_ts: float) -> int:
    """Read-only count of wt_subprov_sig_retry rows where status='DONE' AND
    last_attempt_at < cutoff_ts. Never raises."""
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM wt_subprov_sig_retry "
            "WHERE status='DONE' AND last_attempt_at < ?",
            (cutoff_ts,),
        )
        row = cursor.fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        return 0
