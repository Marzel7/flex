"""Read-only validation against frozen generated SQLite schema manifests."""
from __future__ import annotations

import hashlib
import json
import sqlite3


def manifest(conn: sqlite3.Connection, *, include_tables: frozenset[str] | None = None) -> dict:
    """Return complete table/constraint/index/view metadata using SELECT/PRAGMA.

    This function never owns a connection or transaction and never performs
    DDL/DML.  SQLite-created PK/UNIQUE indexes are retained because they are
    correctness constraints even when sqlite_master has no SQL text for them.
    """
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    tables, indexes, views = {}, {}, {}
    for object_type, name, sql in rows:
        if object_type == "table":
            if include_tables is not None and name not in include_tables:
                continue
            tables[name] = [
                {"name": row[1], "type": row[2], "not_null": row[3],
                 "default": row[4], "pk_position": row[5]}
                for row in conn.execute(f"PRAGMA table_info({name})")
            ]
        elif object_type == "view":
            views[name] = " ".join((sql or "").split())
    for table in sorted(tables):
        for _, name, unique, origin, partial in conn.execute(f"PRAGMA index_list({table})"):
            indexes[name] = {
                "table": table, "unique": unique, "origin": origin, "partial": partial,
                "columns": [
                    {"name": row[2], "order": "DESC" if row[3] else "ASC"}
                    for row in conn.execute(f"PRAGMA index_xinfo({name})") if row[5]
                ],
            }
    return {"tables": tables, "indexes": indexes, "views": views}


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_frozen_manifest(conn: sqlite3.Connection, *, expected_digest: str | tuple[str, ...], include_tables: frozenset[str]) -> tuple[bool, str]:
    """Compare metadata only; return a compact deterministic failure class."""
    observed = manifest(conn, include_tables=include_tables)
    observed_digest = digest(observed)
    expected = {expected_digest} if isinstance(expected_digest, str) else set(expected_digest)
    if observed_digest in expected:
        return True, observed_digest
    return False, observed_digest
