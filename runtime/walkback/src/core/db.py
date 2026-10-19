"""
Canonical database path constants — single source of truth.

All modules that need OPS_DB_PATH or DB_PATH should import from here
rather than each defining their own path construction.
"""
from __future__ import annotations

import os

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DB_DIR    = os.path.join(_REPO_ROOT, "database")

# Hot token / live app database (flex_complete_database.db)
DB_PATH = os.path.abspath(
    os.environ.get("DB_PATH", os.path.join(_DB_DIR, "flex_complete_database.db"))
)

# Explicit semantic alias used by intelligence engines.  Keeping it beside the
# canonical path prevents route modules from inventing a second live DB path.
LIVE_DB_PATH = DB_PATH

# Operations / WATCHTOWER intelligence database (wt_ops_v2.db)
OPS_DB_PATH = os.path.abspath(
    os.environ.get("WT_OPS_DB_PATH", os.path.join(_DB_DIR, "wt_ops_v2.db"))
)
