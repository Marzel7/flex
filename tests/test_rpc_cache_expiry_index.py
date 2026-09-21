import sqlite3

import pytest

from src.ops.rpc_cache_expiry_index import (
    INDEX_NAME,
    create_expiry_index,
    drop_expiry_index,
    expiry_index_present,
)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE rpc_response_cache ("
        "cache_key TEXT PRIMARY KEY, response_json TEXT NOT NULL, "
        "method TEXT NOT NULL, cached_at REAL NOT NULL, "
        "ttl_seconds INTEGER NOT NULL, hit_count INTEGER NOT NULL DEFAULT 0)"
    )
    conn.executemany(
        "INSERT INTO rpc_response_cache "
        "(cache_key,response_json,method,cached_at,ttl_seconds) "
        "VALUES (?,?,?,?,?)",
        [
            ("live", "{}", "get", 100.0, 100),
            ("expired-a", "{}", "get", 10.0, 10),
            ("expired-b", "{}", "get", 20.0, 10),
        ],
    )
    return conn


def test_expiry_index_preserves_predicate_and_is_used_by_planner():
    conn = _db()
    sql = (
        "SELECT cache_key FROM rpc_response_cache "
        "WHERE cached_at + ttl_seconds <= ? LIMIT ?"
    )
    before = set(row[0] for row in conn.execute(sql, (50.0, 5)))
    assert before == {"expired-a", "expired-b"}
    create_expiry_index(conn)
    assert expiry_index_present(conn)
    create_expiry_index(conn)
    after = set(row[0] for row in conn.execute(sql, (50.0, 5)))
    assert after == before
    plan = " ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, (50.0, 5)))
    assert f"USING INDEX {INDEX_NAME}" in plan
    drop_expiry_index(conn)
    assert not expiry_index_present(conn)
    assert set(row[0] for row in conn.execute(sql, (50.0, 5))) == before


def test_migration_is_rollbackable_and_refuses_conflicting_index():
    conn = _db()
    conn.commit()
    conn.execute("BEGIN")
    create_expiry_index(conn)
    conn.rollback()
    assert not expiry_index_present(conn)
    conn.execute(f"CREATE INDEX {INDEX_NAME} ON rpc_response_cache (cached_at)")
    with pytest.raises(ValueError, match="unexpected definition"):
        create_expiry_index(conn)
    with pytest.raises(ValueError, match="unexpected definition"):
        drop_expiry_index(conn)
