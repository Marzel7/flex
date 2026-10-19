"""
Bounded retention for wt_cdc_outbound_events -- raw historical event rows
only. Does NOT touch wt_capital_distributor_candidates (the canonical CDC
authority) in any way.

Qualified by CDC_LIFECYCLE_QUALIFIED / this milestone's audit:
  - "CDC" in FLEX means Capital Distributor Candidate, not database Change
    Data Capture. wt_cdc_outbound_events is a granular per-recipient detail
    log written by record_cdc_outbound() (src/core/ws_cascade_store.py)
    alongside an aggregate update to the canonical
    wt_capital_distributor_candidates row -- every fact any current consumer
    uses (total_outbound_sol, recipient_count, fanout_count, largest_fanout)
    is already durably retained on that canonical row, independent of this
    table.
  - The sole reader (src/core/operation_dashboard_routes.py) queries only
    the latest 50 rows ORDER BY block_time DESC for a dashboard "recent
    activity" widget -- no aggregate, no historical-existence check, no
    lookback beyond "latest."
  - Zero replay/dedupe/cursor/high-water dependency found anywhere (full
    repo trace). Zero dependency on established Operations, Potential
    Operations, Walkback, or attribution (full trace across
    src/ops/living_potential_operations.py, generic_living_pipeline_v2.py,
    generic_living_lineage_metadata.py -- zero references).
  - At qualification time 100% of the 459,594 rows were >=38 days old and
    originated from a single historical CDC wallet; the write mechanism
    remains code-live (record_cdc_outbound() still callable, CDC candidate
    registration/observation/promotion all unaffected) but had produced zero
    new rows in the preceding 24h/7d/30d.

Retention timestamp: recorded_at (row-insert time), per this milestone's
explicit instruction to prefer it over block_time absent evidence otherwise
-- record_cdc_outbound() always sets recorded_at = int(time.time()) at write
time (src/core/ws_cascade_store.py), so it is a reliable, monotonic-with-
insertion lifecycle timestamp; block_time is chain-supplied event time used
only for the dashboard's display ordering, not row lifecycle.

Deletes ONLY rows from wt_cdc_outbound_events WHERE recorded_at < cutoff.
Hardcoded table name and column; no parameter or code path can widen this to
any other table (in particular, never wt_capital_distributor_candidates).
Mirrors src/ops/subprov_sig_retry_retention.py's bounded batch-delete
pattern exactly: fresh per-batch SELECT (no offset/cursor state, idempotent/
resumable), BEGIN IMMEDIATE + executemany DELETE + commit,
bounded_write_wait() write-lane acquisition, SQLITE_BUSY retry with backoff,
never raises.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Optional

from src.utils.db_locking import bounded_write_wait

_CDC_MAINTENANCE_LANE_TIMEOUT_SECONDS = 3.0
_CDC_MAINTENANCE_TOTAL_BUDGET_SECONDS = 10.0


def cleanup_old_cdc_outbound_events_batch(
    db_path: str, batch_size: int, cutoff_ts: float, busy_retry_attempts: int = 3
) -> int:
    """
    Bounded, idempotent deletion of wt_cdc_outbound_events rows where
    recorded_at < cutoff_ts.

    Deletes at most `batch_size` rows, oldest-recorded_at-first, in ONE
    transaction. Returns the exact count deleted. Never raises -- returns 0
    on any connection failure, empty result, exhausted retry budget, or
    overall time-budget exhaustion (matching the qualified subprov-DONE and
    wss_metrics retention primitives' contract exactly).

    Structurally cannot touch wt_capital_distributor_candidates or any other
    table -- the table name and WHERE clause are hardcoded in this function,
    never parameterized.
    """
    if batch_size <= 0:
        return 0

    call_start = time.monotonic()
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=_CDC_MAINTENANCE_LANE_TIMEOUT_SECONDS)
        cursor = conn.cursor()

        attempt = 0
        while True:
            if time.monotonic() - call_start >= _CDC_MAINTENANCE_TOTAL_BUDGET_SECONDS:
                conn.close()
                return 0
            try:
                with bounded_write_wait(_CDC_MAINTENANCE_LANE_TIMEOUT_SECONDS):
                    cursor.execute(
                        "SELECT id FROM wt_cdc_outbound_events "
                        "WHERE recorded_at < ? ORDER BY recorded_at ASC LIMIT ?",
                        (cutoff_ts, batch_size),
                    )
                    ids = [r[0] for r in cursor.fetchall()]
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

        if not ids:
            conn.close()
            return 0

        attempt = 0
        while True:
            if time.monotonic() - call_start >= _CDC_MAINTENANCE_TOTAL_BUDGET_SECONDS:
                try:
                    conn.rollback()
                except sqlite3.OperationalError:
                    pass
                conn.close()
                return 0
            try:
                with bounded_write_wait(_CDC_MAINTENANCE_LANE_TIMEOUT_SECONDS):
                    cursor.execute("BEGIN IMMEDIATE")
                    cursor.executemany(
                        "DELETE FROM wt_cdc_outbound_events WHERE id = ? AND recorded_at < ?",
                        [(i, cutoff_ts) for i in ids],
                    )
                    deleted = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else len(ids)
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


def count_old_cdc_outbound_events(db_path: str, cutoff_ts: float) -> int:
    """Read-only count of wt_cdc_outbound_events rows where recorded_at <
    cutoff_ts. Never raises."""
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM wt_cdc_outbound_events WHERE recorded_at < ?",
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
