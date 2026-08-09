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
            conn = sqlite3.connect(DB_PATH, timeout=10)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                for item in batch:
                    table = item.pop("_table")
                    cols = ", ".join(item.keys())
                    placeholders = ", ".join("?" for _ in item)
                    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(item.values()))
                conn.commit()
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
