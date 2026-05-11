#!/usr/bin/env python3
"""
Shared database locking for cross-module consistency.

All SQLite writes must use this lock to prevent "database is locked" errors
in concurrent scenarios (token launches, clustering, extractors, etc.)
"""

import inspect
import logging
import sqlite3
import threading
import time
from collections import Counter
from typing import Optional

# Single lock instance used by all modules
DB_WRITE_LOCK = threading.RLock()

# ── Global lock error tracking (thread-safe) ─────────────────────────────────
_global_lock_errors: list[float] = []
_global_lock_errors_lock = threading.Lock()
_global_failed_writes: int = 0


def record_lock_error(caller: Optional[str] = None) -> None:
    """Called anywhere a 'database is locked' OperationalError is caught."""
    now = time.time()
    with _global_lock_errors_lock:
        _global_lock_errors.append(now)
        # trim to last hour
        cutoff = now - 3600
        while _global_lock_errors and _global_lock_errors[0] < cutoff:
            _global_lock_errors.pop(0)
    if caller:
        _db_logger.warning(f"[DB_LOCK_ERROR] caller={caller}")


def record_failed_write(caller: Optional[str] = None) -> None:
    global _global_failed_writes
    _global_failed_writes += 1
    if caller:
        _db_logger.error(f"[DB_WRITE_FAILED] caller={caller}")


def get_lock_error_metrics() -> dict:
    """Return lock error counts for /api/db-health."""
    now = time.time()
    with _global_lock_errors_lock:
        errors_1h = len(_global_lock_errors)
        errors_5m = sum(1 for t in _global_lock_errors if t >= now - 300)
    return {
        "lock_errors_1h": errors_1h,
        "lock_errors_5m": errors_5m,
        "failed_writes_total": _global_failed_writes,
    }

_db_logger = logging.getLogger("db_locking")
_open_connections = {}
_open_connections_lock = threading.Lock()


class TrackedConnection(sqlite3.Connection):
    """SQLite connection that unregisters itself when closed and tracks lock errors."""

    def execute(self, sql, parameters=()):
        try:
            return super().execute(sql, parameters)
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                caller = getattr(self, "_db_caller", None)
                record_lock_error(caller)
            raise

    def executemany(self, sql, parameters):
        try:
            return super().executemany(sql, parameters)
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                caller = getattr(self, "_db_caller", None)
                record_lock_error(caller)
            raise

    def close(self):
        tracking_id = getattr(self, "_db_tracking_id", None)
        if tracking_id is not None:
            with _open_connections_lock:
                _open_connections.pop(tracking_id, None)
            try:
                self._db_tracking_id = None
            except Exception:
                pass
        return super().close()


def _register_connection(conn: sqlite3.Connection, path: str, caller: str) -> None:
    tracking_id = id(conn)
    try:
        conn._db_tracking_id = tracking_id
    except Exception:
        return
    with _open_connections_lock:
        _open_connections[tracking_id] = {
            "path": path,
            "caller": caller,
            "opened_at": time.time(),
            "thread": threading.current_thread().name,
        }


def get_open_connection_summary(limit: int = 25) -> dict:
    """Return lightweight diagnostics for currently open tracked DB connections."""
    now = time.time()
    with _open_connections_lock:
        records = list(_open_connections.values())
    by_caller = Counter(record["caller"] for record in records)
    by_thread = Counter(record["thread"] for record in records)
    oldest = sorted(records, key=lambda record: record["opened_at"])[:limit]
    return {
        "open_count": len(records),
        "by_caller": by_caller.most_common(limit),
        "by_thread": by_thread.most_common(limit),
        "oldest": [
            {
                **record,
                "age_seconds": round(now - record["opened_at"], 1),
            }
            for record in oldest
        ],
    }


def db_connect(path: str, timeout: int = 30, row_factory=None) -> sqlite3.Connection:
    """
    Open a SQLite connection with safe defaults for concurrent access.

    - timeout=30: waits up to 30s for a write lock (Python-level)
    - busy_timeout=30000: mirrors at the SQLite C level (handles WAL checkpoints)
    - WAL journal mode for concurrent readers

    Use this instead of sqlite3.connect() everywhere to avoid "database is locked".
    Logs caller location and elapsed time when acquisition is slow (>1s).
    """
    # Identify caller for lock contention diagnostics
    frame = inspect.stack()[1]
    caller = f"{frame.filename.split('/')[-1]}:{frame.lineno} in {frame.function}"

    t0 = time.monotonic()
    try:
        conn = sqlite3.connect(path, timeout=timeout, factory=TrackedConnection)
    except Exception as e:
        elapsed = time.monotonic() - t0
        _db_logger.error(f"[DB_CONNECT_FAIL] caller={caller} elapsed={elapsed:.2f}s error={e}")
        raise

    elapsed = time.monotonic() - t0
    if elapsed > 1.0:
        _db_logger.warning(f"[DB_CONNECT_SLOW] caller={caller} elapsed={elapsed:.2f}s path={path}")

    _register_connection(conn, path, caller)
    try:
        conn._db_caller = caller
    except Exception:
        pass
    if row_factory is not None:
        conn.row_factory = row_factory
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # Auto-checkpoint every 1000 pages (~4 MB) — prevents WAL from growing unbounded
    # even when connections are long-lived. PASSIVE mode doesn't block writers.
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    return conn
