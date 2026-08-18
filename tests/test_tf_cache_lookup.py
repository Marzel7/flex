"""Tests for the read-only tf_transaction_cache lookup adapter (B2Z-2H
design support). Fixture-based, plus one real read-only check against the
actual committed transaction_first_lineage.db to prove the adapter works
end-to-end without ever writing to it.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.acquisition.tf_cache_lookup import TransactionFirstLineageCacheLookup


def _build_fixture_cache(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE tf_transaction_cache (signature TEXT PRIMARY KEY, block_time INTEGER, "
        "transaction_json TEXT, fetched_at INTEGER NOT NULL, source TEXT NOT NULL, "
        "rpc_verified INTEGER NOT NULL DEFAULT 0, parse_status TEXT NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO tf_transaction_cache VALUES (?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()


def test_lookup_hit(tmp_path):
    db = tmp_path / "cache.db"
    _build_fixture_cache(db, [
        ("sig1", 1000, json.dumps({"result": {"ok": True}}), 2000, "RPC_CACHE_IMPORT", 1, "OK"),
    ])
    lookup = TransactionFirstLineageCacheLookup(db)
    result = lookup.lookup("sig1")
    assert result is not None
    assert result.rpc_verified is True
    assert result.transaction_json == {"result": {"ok": True}}


def test_lookup_miss(tmp_path):
    db = tmp_path / "cache.db"
    _build_fixture_cache(db, [])
    lookup = TransactionFirstLineageCacheLookup(db)
    assert lookup.lookup("nonexistent") is None


def test_lookup_unverified_row(tmp_path):
    db = tmp_path / "cache.db"
    _build_fixture_cache(db, [("sig-unverified", None, None, 2000, "CACHE_MISS", 0, "PENDING")])
    lookup = TransactionFirstLineageCacheLookup(db)
    result = lookup.lookup("sig-unverified")
    assert result.rpc_verified is False
    assert result.transaction_json is None


def test_lookup_many_bounded(tmp_path):
    db = tmp_path / "cache.db"
    _build_fixture_cache(db, [
        ("sig1", 100, json.dumps({"a": 1}), 200, "s", 1, "OK"),
        ("sig2", 101, json.dumps({"a": 2}), 200, "s", 1, "OK"),
    ])
    lookup = TransactionFirstLineageCacheLookup(db)
    result = lookup.lookup_many(["sig1", "sig2", "sig3-missing"])
    assert set(result.keys()) == {"sig1", "sig2"}


def test_lookup_many_empty_list_returns_empty_dict(tmp_path):
    db = tmp_path / "cache.db"
    _build_fixture_cache(db, [])
    lookup = TransactionFirstLineageCacheLookup(db)
    assert lookup.lookup_many([]) == {}


def test_cache_hit_rate(tmp_path):
    db = tmp_path / "cache.db"
    _build_fixture_cache(db, [
        ("sig1", 100, json.dumps({}), 200, "s", 1, "OK"),
        ("sig2", 100, None, 200, "s", 0, "PENDING"),
    ])
    lookup = TransactionFirstLineageCacheLookup(db)
    stats = lookup.cache_hit_rate(["sig1", "sig2", "sig3-missing", "sig4-missing"])
    assert stats["total"] == 4
    assert stats["hits"] == 2
    assert stats["verified_hits"] == 1
    assert stats["hit_rate"] == 0.5
    assert stats["verified_hit_rate"] == 0.25


def test_cache_hit_rate_empty_input():
    lookup = TransactionFirstLineageCacheLookup(Path("/nonexistent/does/not/matter"))
    stats = lookup.cache_hit_rate([])
    assert stats == {"total": 0, "hits": 0, "verified_hits": 0, "hit_rate": 0.0, "verified_hit_rate": 0.0}


def test_connection_is_structurally_read_only(tmp_path):
    """The adapter opens SQLite in URI mode=ro -- any attempted write must
    fail at the OS/SQLite layer, not merely by convention."""
    db = tmp_path / "cache.db"
    _build_fixture_cache(db, [("sig1", 100, json.dumps({}), 200, "s", 1, "OK")])
    lookup = TransactionFirstLineageCacheLookup(db)
    conn = lookup._connect()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO tf_transaction_cache VALUES ('sig2', 1, '{}', 1, 's', 1, 'OK')")
    conn.close()


# --- real, read-only integration check against the actual production cache -

def test_real_cache_lookup_against_committed_db_read_only():
    """Uses the ACTUAL committed database/transaction_first_lineage.db, but
    only ever reads -- proves the adapter works against real data without
    any write risk. Skips gracefully if the file is not present in this
    environment."""
    real_path = Path("database/transaction_first_lineage.db")
    if not real_path.exists():
        pytest.skip("transaction_first_lineage.db not present in this environment")

    mtime_before = real_path.stat().st_mtime
    lookup = TransactionFirstLineageCacheLookup(real_path)

    conn = sqlite3.connect(f"file:{real_path.resolve()}?mode=ro", uri=True)
    sample_sig = conn.execute("SELECT signature FROM tf_transaction_cache LIMIT 1").fetchone()
    conn.close()
    assert sample_sig is not None, "expected at least one row in the real cache"

    result = lookup.lookup(sample_sig[0])
    assert result is not None
    assert result.signature == sample_sig[0]

    stats = lookup.cache_hit_rate([sample_sig[0], "definitely-not-a-real-signature-xyz"])
    assert stats["hits"] == 1

    mtime_after = real_path.stat().st_mtime
    assert mtime_after == mtime_before  # confirms zero write occurred
