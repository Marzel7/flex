#!/usr/bin/env python3
"""
Shared database locking for cross-module consistency.

All SQLite writes must use this lock to prevent "database is locked" errors
in concurrent scenarios (token launches, clustering, extractors, etc.)
"""

import sqlite3
import threading

# Single lock instance used by all modules
DB_WRITE_LOCK = threading.RLock()


def db_connect(path: str, timeout: int = 15, row_factory=None) -> sqlite3.Connection:
    """
    Open a SQLite connection with safe defaults for concurrent access.

    - timeout=15: waits up to 15s for a write lock instead of failing immediately
    - busy_timeout=15000: mirrors timeout at the SQLite C level (handles WAL checkpoints)
    - WAL + NORMAL sync: already set at DB level; re-asserting here is a no-op but safe

    Use this instead of sqlite3.connect() everywhere to avoid "database is locked".
    """
    conn = sqlite3.connect(path, timeout=timeout)
    if row_factory is not None:
        conn.row_factory = row_factory
    conn.execute("PRAGMA busy_timeout=15000")
    return conn
