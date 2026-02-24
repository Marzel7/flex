#!/usr/bin/env python3
"""
Shared database locking for cross-module consistency.

All SQLite writes must use this lock to prevent "database is locked" errors
in concurrent scenarios (token launches, clustering, extractors, etc.)
"""

import threading

# Single lock instance used by all modules
DB_WRITE_LOCK = threading.RLock()
