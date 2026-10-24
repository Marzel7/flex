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


from src.core.paired_binding_guard import validate_pair as _validate_pair
_validate_pair(core_path=DB_PATH, ops_path=OPS_DB_PATH, purpose="active")


def resolve_ops_db_path() -> str:
    """Resolve OPS beside the configured LIVE authority, never beside a checkout."""
    explicit = os.environ.get("WT_OPS_DB_PATH")
    if explicit:
        resolved = os.path.realpath(explicit)
        _validate_pair(core_path=DB_PATH, ops_path=resolved, purpose="active")
        return resolved
    live = os.environ.get("DB_PATH") or os.environ.get("FLEX_DB_PATH")
    if not live:
        raise RuntimeError("OPS authority requires DB_PATH, FLEX_DB_PATH, or WT_OPS_DB_PATH")
    resolved = os.path.join(os.path.dirname(os.path.realpath(live)), "wt_ops_v2.db")
    _validate_pair(core_path=DB_PATH, ops_path=resolved, purpose="active")
    return resolved
