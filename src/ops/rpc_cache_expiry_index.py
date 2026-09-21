"""Offline-qualified schema contract for bounded RPC-cache expiry lookup.

Calling code must supply an already-authorized writer-lane connection. This
module does not open a database or run a migration on import.
"""

from __future__ import annotations

import sqlite3


INDEX_NAME = "ix_rpc_response_cache_expires_at_key"
INDEX_SQL = (
    "CREATE INDEX ix_rpc_response_cache_expires_at_key "
    "ON rpc_response_cache ((cached_at + ttl_seconds), cache_key)"
)


def expiry_index_present(conn: sqlite3.Connection) -> bool:
    """Fail closed if the expected name exists with an unexpected definition."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        (INDEX_NAME,),
    ).fetchone()
    if row is None:
        return False
    normalized = " ".join(row[0].split()).lower()
    if normalized != INDEX_SQL.lower():
        raise ValueError(f"{INDEX_NAME} exists with an unexpected definition")
    return True


def create_expiry_index(conn: sqlite3.Connection) -> None:
    """Create within the caller's transaction; never commit on its behalf."""
    if not expiry_index_present(conn):
        conn.execute(INDEX_SQL)
    if not expiry_index_present(conn):
        raise RuntimeError("RPC cache expiry index verification failed")


def drop_expiry_index(conn: sqlite3.Connection) -> None:
    """Rollback helper; refuse to drop an index with unexpected provenance."""
    if expiry_index_present(conn):
        conn.execute(f"DROP INDEX {INDEX_NAME}")
