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

# Single lock instance used by all modules
DB_WRITE_LOCK = threading.RLock()

_db_logger = logging.getLogger("db_locking")


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
        conn = sqlite3.connect(path, timeout=timeout)
    except Exception as e:
        elapsed = time.monotonic() - t0
        _db_logger.error(f"[DB_CONNECT_FAIL] caller={caller} elapsed={elapsed:.2f}s error={e}")
        raise

    elapsed = time.monotonic() - t0
    if elapsed > 1.0:
        _db_logger.warning(f"[DB_CONNECT_SLOW] caller={caller} elapsed={elapsed:.2f}s path={path}")

    if row_factory is not None:
        conn.row_factory = row_factory
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    # Auto-checkpoint every 1000 pages (~4 MB) — prevents WAL from growing unbounded
    # even when connections are long-lived. PASSIVE mode doesn't block writers.
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    return conn
