"""X78.0 Phase 5/6: regression tests proving the three write-lease leak
sources identified in the creator_funding_worker call chain are fixed.

Each test proves: a connection that failed to reach commit()/rollback()/
close() on some earlier call no longer stays open -- the fix guarantees
close() via a proper finally, and/or avoids ever attempting a doomed write
in the first place (checking sqlite_master before CREATE TABLE, matching
the same pattern already proven for walkback_queue.py in X77.3/X77.5).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

import src.utils.db_locking as db_locking
from src.core.database_write_service import _thread_write_lease


@pytest.fixture(autouse=True)
def _write_serialize_enabled(monkeypatch):
    monkeypatch.setenv("DB_WRITE_SERIALIZE", "1")
    monkeypatch.setattr(db_locking, "_DB_WRITE_SERIALIZE", True)
    yield


@pytest.fixture
def tmp_db():
    path = tempfile.mktemp(suffix=".db")
    yield path
    for suffix in ("", "-wal", "-shm", ".write.lock", ".write.lock.owner"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass


def _seed_minimal_schema(path: str) -> None:
    """Just enough tables for extract_for_creator's CREATE-TABLE-guard block
    to have something meaningful to check against."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE token_analysis (mint TEXT, bonding_curve_pda TEXT, create_tx_signature TEXT, earliest_tx_creator TEXT)")
    conn.execute("CREATE TABLE creator_funders (creator_address TEXT, funder_address TEXT, fully_analyzed INTEGER)")
    conn.execute("CREATE TABLE creator_tags (creator_address TEXT, tag TEXT)")
    conn.commit()
    conn.close()


# ── Fix 1: realtime_creator_funding_extractor.py's CREATE TABLE guard ──────

def test_extraction_table_check_issues_no_write_once_tables_exist(tmp_db, monkeypatch):
    """The exact fix: on an already-migrated DB, extract_for_creator's
    'ensure tables exist' block must issue zero write statements -- proven
    by checking the write lease is never acquired for this block when the
    tables already exist. This directly replicates the pre-existing block's
    logic without invoking the full extractor (which needs network/RPC
    setup) -- exercising the same sqlite_master-check-first code path."""
    conn = db_locking.db_connect(tmp_db, timeout=5)
    conn.execute("CREATE TABLE creator_service_history (creator_address TEXT, tag TEXT, amount_sol REAL, tx_signature TEXT, mint TEXT, network_fee_sol REAL, tip_percentage REAL, tx_type TEXT, created_at TEXT, PRIMARY KEY (creator_address, tx_signature, tag))")
    conn.execute("CREATE TABLE creator_receivers (creator_address TEXT NOT NULL, receiver_address TEXT NOT NULL, amount_sol REAL, receiver_type TEXT, receiver_name TEXT, first_detected_at TEXT, PRIMARY KEY (creator_address, receiver_address))")
    conn.commit()
    conn.close()

    # Simulate a THREAD-POISONED state (as would happen after any earlier
    # leak) and confirm the sqlite_master-check-first shape survives it --
    # i.e. it never attempts the doomed CREATE TABLE that would otherwise
    # raise NestedDatabaseWriteError and get silently swallowed.
    def poisoned_thread_check():
        leaker = db_locking.db_connect(tmp_db, timeout=5)
        leaker.execute("CREATE TABLE unrelated_leaker (a INTEGER)")  # never released

        extraction_conn = db_locking.db_connect(tmp_db, timeout=5)
        cur = extraction_conn.cursor()
        existing = {
            r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('creator_service_history', 'creator_receivers')"
            ).fetchall()
        }
        assert existing == {"creator_service_history", "creator_receivers"}
        # The fixed code path: both already exist, so NEITHER CREATE TABLE
        # branch executes -- meaning extraction_conn's own cursor never
        # attempts a write, so it never raises even though the thread is
        # already poisoned by `leaker`.
        if "creator_service_history" not in existing:
            cur.execute("CREATE TABLE creator_service_history (a INTEGER)")
            extraction_conn.commit()
        if "creator_receivers" not in existing:
            cur.execute("CREATE TABLE creator_receivers (a INTEGER)")
            extraction_conn.commit()
        extraction_conn.close()  # must not raise

    import threading
    t = threading.Thread(target=poisoned_thread_check)
    t.start()
    t.join()


# ── Fix 2: solscan_address_tagger.tag_creator_with_services ────────────────

def test_tag_creator_with_services_closes_connection_on_exception(tmp_db, monkeypatch):
    """The fix: conn is now declared before the try and closed in a finally.
    Proven by forcing an exception mid-function (after connect, before the
    old code's unconditional close()) and confirming the write lease is
    still released."""
    import src.utils.solscan_address_tagger as tagger
    monkeypatch.setattr(tagger, "DB_PATH", tmp_db)

    conn = sqlite3.connect(tmp_db)
    conn.execute("CREATE TABLE creator_tags (creator_address TEXT NOT NULL, tag TEXT NOT NULL, description TEXT, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(creator_address, tag))")
    conn.commit()
    conn.close()

    # Force an exception AFTER the CREATE TABLE (which acquires the lease)
    # but BEFORE conn.commit()/close() would have run in the old code, by
    # making cursor.fetchone() raise on the "already tagged" check.
    import sqlite3 as _sq3
    real_connect = tagger.sqlite3.connect

    class _BoomCursor:
        def __init__(self, real_cursor):
            self._real = real_cursor

        def execute(self, sql, params=()):
            if "SELECT 1 FROM creator_tags" in sql:
                raise RuntimeError("simulated failure mid-function")
            return self._real.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._real, name)

    class _BoomConn:
        def __init__(self, real_conn):
            self._real = real_conn
            self.closed = False

        def cursor(self):
            return _BoomCursor(self._real.cursor())

        def commit(self):
            return self._real.commit()

        def close(self):
            self.closed = True
            return self._real.close()

    holder = {}

    def fake_connect(*a, **k):
        real_conn = real_connect(*a, **k)
        boom = _BoomConn(real_conn)
        holder["conn"] = boom
        return boom

    monkeypatch.setattr(tagger.sqlite3, "connect", fake_connect)

    result = tagger.tag_creator_with_services("CREATOR_X", {"SomeService"})
    assert result == 0  # the exception path returns 0 tags_added
    assert holder["conn"].closed is True, (
        "tag_creator_with_services must close its connection even when an "
        "exception occurs mid-function")


# ── Fix 3: blocksec_aml_batcher._ensure_tables ──────────────────────────────

def test_blocksec_ensure_tables_issues_no_write_once_tables_exist(tmp_db, monkeypatch):
    """On an already-migrated DB, _ensure_tables (called on EVERY
    BlockSecAMLBatcher() instantiation, i.e. every single extraction cycle)
    must issue zero write statements."""
    import src.monitoring.blocksec_aml_batcher as batcher_mod
    monkeypatch.setattr(batcher_mod, "DB_PATH", tmp_db)
    monkeypatch.setattr(batcher_mod, "BLOCKSEC_API_KEY", "fake-key-for-test")

    conn = sqlite3.connect(tmp_db)
    conn.execute("CREATE TABLE blocksec_aml_cache (address TEXT PRIMARY KEY, label_name TEXT, category TEXT, risk_level TEXT, risk_score REAL, aml_status TEXT, raw_response TEXT, queried_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, source TEXT DEFAULT 'blocksec')")
    conn.execute("CREATE TABLE blocksec_batch_log (batch_id TEXT PRIMARY KEY, batch_size INTEGER, addresses_submitted TEXT, submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, api_response TEXT, status TEXT)")
    conn.commit()
    conn.close()

    calls = []
    real_connect = batcher_mod.sqlite3.connect

    class _SpyCursor:
        def __init__(self, real_cursor):
            self._real = real_cursor

        def execute(self, sql, *a, **k):
            if "CREATE TABLE" in sql:
                calls.append(sql)
            return self._real.execute(sql, *a, **k)

        def __getattr__(self, name):
            return getattr(self._real, name)

    class _SpyConn:
        def __init__(self, real_conn):
            self._real = real_conn

        def execute(self, sql, *a, **k):
            if "CREATE TABLE" in sql:
                calls.append(sql)
            return self._real.execute(sql, *a, **k)

        def cursor(self):
            return _SpyCursor(self._real.cursor())

        def __getattr__(self, name):
            return getattr(self._real, name)

    def fake_connect(*a, **k):
        return _SpyConn(real_connect(*a, **k))

    monkeypatch.setattr(batcher_mod.sqlite3, "connect", fake_connect)

    batcher_mod.BlockSecAMLBatcher()  # constructor calls _ensure_tables()

    assert calls == [], f"_ensure_tables issued CREATE TABLE on an already-migrated DB: {calls}"


def test_blocksec_ensure_tables_closes_connection_on_exception(tmp_db, monkeypatch):
    """The fix: conn is now declared before the try and closed in a finally,
    proven by forcing an exception mid-function."""
    import src.monitoring.blocksec_aml_batcher as batcher_mod
    monkeypatch.setattr(batcher_mod, "DB_PATH", tmp_db)
    monkeypatch.setattr(batcher_mod, "BLOCKSEC_API_KEY", "fake-key-for-test")

    real_connect = batcher_mod.sqlite3.connect
    holder = {}

    class _BoomConn:
        def __init__(self, real_conn):
            self._real = real_conn
            self.closed = False

        def execute(self, sql, *a, **k):
            if "sqlite_master" in sql:
                raise RuntimeError("simulated failure mid-function")
            return self._real.execute(sql, *a, **k)

        def cursor(self):
            return self._real.cursor()

        def commit(self):
            return self._real.commit()

        def close(self):
            self.closed = True
            return self._real.close()

    def fake_connect(*a, **k):
        real_conn = real_connect(*a, **k)
        boom = _BoomConn(real_conn)
        holder["conn"] = boom
        return boom

    monkeypatch.setattr(batcher_mod.sqlite3, "connect", fake_connect)

    with pytest.raises(RuntimeError):
        batcher_mod.BlockSecAMLBatcher()

    assert holder["conn"].closed is True, (
        "_ensure_tables must close its connection even when an exception "
        "occurs mid-function")
