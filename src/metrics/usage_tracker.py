"""
usage_tracker.py — unified credit/bandwidth tracking for RPC, WSS, and Webhooks.

Writes to three tables:
  rpc_metrics      — already exists, written by rpc_metrics_recorder
  wss_metrics      — new, written here for each WSS subscription
  webhook_metrics  — new, written here for each webhook event

All timestamps are unix epoch floats. All writes are fire-and-forget via a
background thread so callers are never blocked.
"""

import sqlite3
import time
import threading
import os
from typing import Optional

from src.utils.db_locking import bounded_write_wait

DB_PATH = os.getenv("DB_PATH", "database/flex_complete_database.db")

_lock = threading.Lock()
_queue: list = []
_thread: Optional[threading.Thread] = None
_started = False

# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS wss_metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    subscription TEXT   NOT NULL,  -- 'pumpswap_logs', 'pumpportal_births', etc.
    source_file TEXT    NOT NULL,
    msg_count   INTEGER NOT NULL DEFAULT 1,
    est_bytes   INTEGER NOT NULL DEFAULT 0,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS webhook_metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    webhook_id  TEXT    NOT NULL,
    source_file TEXT    NOT NULL,
    event_type  TEXT    NOT NULL,  -- 'birth', 'filtered_out', 'error'
    count       INTEGER NOT NULL DEFAULT 1,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_wss_ts         ON wss_metrics(ts);
CREATE INDEX IF NOT EXISTS idx_wss_sub        ON wss_metrics(subscription);
CREATE INDEX IF NOT EXISTS idx_webhook_ts     ON webhook_metrics(ts);
CREATE INDEX IF NOT EXISTS idx_webhook_type   ON webhook_metrics(event_type);
"""

def ensure_schema():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.executescript(_DDL)
        conn.commit()
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Background writer
# ---------------------------------------------------------------------------

def _flush():
    global _queue
    while True:
        time.sleep(5)
        with _lock:
            batch = _queue[:]
            _queue = []
        if not batch:
            continue
        try:
            # X78.20 -- P3/housekeeping: WSS/webhook bandwidth telemetry,
            # never on the critical ingestion path.
            conn = sqlite3.connect(DB_PATH, timeout=10, priority=3)
            try:
                for item in batch:
                    table = item.pop("_table")
                    cols = ", ".join(item.keys())
                    placeholders = ", ".join("?" for _ in item)
                    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(item.values()))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        except Exception:
            pass  # never crash caller — connection is guaranteed closed by the finally above

def _ensure_started():
    """X78.18: failure-isolated startup. record_wss()/record_webhook() are
    called synchronously from hot paths (e.g. the PumpPortal reconnect loop's
    message handler) — a schema-write stall or CrossProcessDatabaseWriteTimeout
    here must never propagate into the caller, since that previously surfaced
    as a reconnect failure with no relation to the actual PumpPortal connection.
    ensure_schema() itself remains once-per-process (still gated by _started,
    set before the attempt so a failure doesn't retry it on every subsequent
    call); only its failure mode changes here, not its frequency."""
    global _started, _thread
    if _started:
        return
    _started = True
    try:
        ensure_schema()
    except Exception:
        pass  # metrics schema is best-effort; never block/fail the caller
    _thread = threading.Thread(target=_flush, daemon=True, name="usage-tracker-flush")
    _thread.start()

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_wss(
    subscription: str,
    source_file: str,
    msg_count: int = 1,
    est_bytes: int = 0,
    note: Optional[str] = None,
):
    """Record WSS messages received. Call once per message or per batch."""
    _ensure_started()
    with _lock:
        _queue.append({
            "_table": "wss_metrics",
            "ts": time.time(),
            "subscription": subscription,
            "source_file": source_file,
            "msg_count": msg_count,
            "est_bytes": est_bytes,
            "note": note,
        })


def record_webhook(
    webhook_id: str,
    source_file: str,
    event_type: str,
    count: int = 1,
    note: Optional[str] = None,
):
    """Record a webhook event received."""
    _ensure_started()
    with _lock:
        _queue.append({
            "_table": "webhook_metrics",
            "ts": time.time(),
            "webhook_id": webhook_id,
            "source_file": source_file,
            "event_type": event_type,
            "count": count,
            "note": note,
        })


# ---------------------------------------------------------------------------
# Bounded retention (WT_OPS_BOUNDED_MAINTENANCE)
# ---------------------------------------------------------------------------
#
# wss_metrics has exactly one proven current reader (/api/usage in main.py),
# which defaults to a 24h lookback and has no observed caller requesting a
# longer window. Retention here is a conservative 7-day exact cutoff -- no
# aggregation, since no current evidence requires long-term aggregate
# storage. This does NOT change /api/usage's query semantics in any way; it
# only bounds how far back rows exist for that query to see.

# Maintenance-path acquisition budget -- matches src/core/rpc_cache.py's
# MAINTENANCE_LANE_TIMEOUT_SECONDS/MAINTENANCE_TOTAL_BUDGET_SECONDS exactly.
# WT_OPS_BOUNDED_MAINTENANCE fix: this function's connections are opened via
# plain sqlite3.connect(), but the project globally monkeypatches
# sqlite3.connect to route through the same cross-process write-lane
# machinery as db_connect() -- so without bounded_write_wait() here, a
# lane-busy condition was observed to block for the process-wide 60s
# default (measured: a single acquisition attempt against
# flex_complete_database.db blocked ~60.3s in production), not the few
# seconds appropriate for optional housekeeping.
_WSS_MAINTENANCE_LANE_TIMEOUT_SECONDS = 3.0
_WSS_MAINTENANCE_TOTAL_BUDGET_SECONDS = 10.0


def cleanup_old_wss_metrics_batch(db_path: str, batch_size: int, cutoff_ts: float,
                                   busy_retry_attempts: int = 3) -> int:
    """
    Bounded, idempotent deletion of wss_metrics rows older than cutoff_ts.

    Deletes at most `batch_size` rows, oldest-first, in ONE transaction.
    Returns the exact count deleted. Never raises -- returns 0 on any
    connection failure, empty result, exhausted retry budget, or overall
    time-budget exhaustion.

    Does not touch webhook_metrics, rpc_metrics, or any other table, and does
    not alter /api/usage's query shape -- that route continues to read
    whatever rows exist in its requested window.
    """
    if batch_size <= 0:
        return 0

    call_start = time.monotonic()
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=_WSS_MAINTENANCE_LANE_TIMEOUT_SECONDS)
        cursor = conn.cursor()

        attempt = 0
        while True:
            if time.monotonic() - call_start >= _WSS_MAINTENANCE_TOTAL_BUDGET_SECONDS:
                conn.close()
                return 0
            try:
                with bounded_write_wait(_WSS_MAINTENANCE_LANE_TIMEOUT_SECONDS):
                    cursor.execute(
                        "SELECT id FROM wss_metrics WHERE ts < ? ORDER BY ts ASC LIMIT ?",
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
            if time.monotonic() - call_start >= _WSS_MAINTENANCE_TOTAL_BUDGET_SECONDS:
                try:
                    conn.rollback()
                except sqlite3.OperationalError:
                    pass
                conn.close()
                return 0
            try:
                with bounded_write_wait(_WSS_MAINTENANCE_LANE_TIMEOUT_SECONDS):
                    cursor.execute("BEGIN IMMEDIATE")
                    cursor.executemany(
                        "DELETE FROM wss_metrics WHERE id = ? AND ts < ?",
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


def count_old_wss_metrics(db_path: str, cutoff_ts: float) -> int:
    """Read-only count of wss_metrics rows older than cutoff_ts. Never raises."""
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM wss_metrics WHERE ts < ?", (cutoff_ts,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        return 0
