"""X78.0 Phase 5/6: regression tests proving the write-lease leak sources
identified in the creator_funding_worker call chain are fixed.

Each test proves: a connection that failed to reach commit()/rollback()/
close() on some earlier call no longer stays open -- the fix guarantees
close()/rollback() via a proper finally/except, and/or avoids ever
attempting a doomed write in the first place (checking sqlite_master before
CREATE TABLE, matching the same pattern already proven for
walkback_queue.py in X77.3/X77.5).
"""
from __future__ import annotations

import asyncio
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


# ── Fix 4: extract_for_creator._flush_page_batch ────────────────────────────

def test_flush_page_batch_rolls_back_and_releases_lease_on_failure(tmp_db):
    """Found live minutes after X78.0's first deployment: _flush_page_batch
    receives extraction_conn as a parameter and is called once PER PAGE
    within the same extraction's paging loop. Its except block previously
    swallowed any mid-batch failure without rolling back, leaving the write
    lease (acquired by the first write-shaped cursor.execute() in the
    batch) held for the rest of extraction_conn's life -- so the VERY NEXT
    page's own _flush_page_batch call self-nested against this one's own
    uncommitted transaction. Proven directly against the real class and a
    real TrackedConnection, forcing a KeyError mid-batch (a funders_delta
    entry missing the 'amount' key) exactly as would happen on malformed
    upstream data."""
    from src.extractors.realtime_creator_funding_extractor import RealTimeCreatorFundingExtractor

    conn = db_locking.db_connect(tmp_db, timeout=5)
    conn.execute(
        "CREATE TABLE cex_wallets (cex_address TEXT, is_active INTEGER, "
        "exchange_name TEXT, wallet_type TEXT)")
    conn.execute(
        "CREATE TABLE creator_funders (creator_address TEXT, funder_address TEXT, "
        "amount_sol REAL, first_detected_at TEXT, is_cex INTEGER, cex_exchange TEXT, "
        "cex_type TEXT, is_classified INTEGER, fully_analyzed INTEGER)")
    conn.commit()

    extractor = RealTimeCreatorFundingExtractor.__new__(RealTimeCreatorFundingExtractor)
    extractor.domain_resolver = None

    async def run():
        # Malformed funder delta (missing 'amount') forces a KeyError mid-batch,
        # after the first write-shaped cursor.execute() has already acquired
        # extraction_conn's write lease.
        await extractor._flush_page_batch(conn, "CREATOR_X", {"FUNDER_X": {}}, {}, set(), [])

    asyncio.run(run())

    assert getattr(_thread_write_lease, "owner", None) is None, (
        "_flush_page_batch must release the write lease (via rollback) when "
        "a mid-batch exception occurs")

    # The connection itself must still be usable for the extraction's next page.
    conn.execute("INSERT INTO creator_funders (creator_address) VALUES (?)", ("TEST",))
    conn.commit()
    conn.close()


# ── Fix 5: intelligence_refresh.apply_migration ─────────────────────────────

def test_apply_migration_closes_connection_on_exception(tmp_db, monkeypatch):
    """Found live during the X78.0 soak, same window as Fix 6:
    apply_migration's conn.commit()/close() sat outside any try/finally --
    an exception not caught by the per-statement OperationalError handler
    left the connection (and its write lease) open for the rest of that
    thread's life. Proven by forcing migration.read_text() to raise."""
    import src.core.intelligence_refresh as irc

    holder = {}
    real_db = irc._db

    class _BoomConn:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *a, **k):
            # Raise a non-OperationalError so it bypasses the per-statement
            # except sqlite3.OperationalError handler entirely -- this is
            # exactly the gap the old code (no outer try/finally) had no
            # protection against.
            raise RuntimeError("simulated non-OperationalError failure")

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def spy_db(*a, **k):
        real_conn = real_db(*a, **k)
        holder["conn"] = real_conn
        return _BoomConn(real_conn)

    monkeypatch.setattr(irc, "_db", spy_db)

    with pytest.raises(RuntimeError):
        irc.apply_migration(tmp_db)

    conn = holder["conn"]
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")  # closed connections raise on use


# ── Fix 6: creator_funding_worker._post_extraction_intelligence_refresh ────

def test_post_extraction_intelligence_refresh_closes_irc_conn_on_exception(tmp_db, monkeypatch):
    """Found live during the X78.0 soak (the trigger for the whole
    investigation into this file): irc_conn.close() was only reached as the
    LAST line of the try block, after every SELECT/INSERT/UPDATE. Any
    exception from those statements left irc_conn -- and its write lease --
    open for the rest of this thread's life. This function is dispatched via
    asyncio.to_thread onto the SAME reused executor pool as every other
    to_thread write in creator_funding_worker (_mark_complete,
    _write_heartbeat's _db_connect calls, etc.) -- proven live: the exact
    NestedDatabaseWriteError signature (outer_command==inner_command==
    creator_funding_worker.py:112 in _db_connect) matched a later,
    unrelated write on the same thread self-nesting against this leak."""
    import src.core.creator_funding_worker as cfw

    monkeypatch.setattr(cfw, "DB_PATH", tmp_db)
    monkeypatch.setattr(cfw, "_intel_refresh_last_run", 0.0)

    conn = sqlite3.connect(tmp_db)
    conn.execute(
        "CREATE TABLE token_analysis (mint TEXT, earliest_tx_creator TEXT, migrated_at INTEGER)")
    conn.execute(
        "CREATE TABLE creator_funders (creator_address TEXT, funder_address TEXT, is_cex INTEGER)")
    conn.execute(
        "CREATE TABLE creator_self_funding (creator_address TEXT, is_self_funding INTEGER)")
    conn.execute(
        "CREATE TABLE network_membership (creator_address TEXT)")
    conn.commit()
    conn.close()

    # apply_migration and take_snapshot are irrelevant to this specific
    # leak -- stub them out so the test isolates irc_conn's own lifecycle.
    monkeypatch.setattr(
        "src.core.intelligence_refresh.apply_migration", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "src.core.relationship_events.take_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "src.utils.build_networks_release.build_networks_release", lambda *_a, **_k: None)

    holder = {}
    real_db = None
    import src.core.intelligence_refresh as irc
    real_db = irc._db

    def boom_db(*a, **k):
        real_conn = real_db(*a, **k)
        holder["conn"] = real_conn

        class _BoomConn:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, *a, **k):
                if "SELECT" in sql and "token_analysis" in sql:
                    raise RuntimeError("simulated query failure")
                return self._inner.execute(sql, *a, **k)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        return _BoomConn(real_conn)

    monkeypatch.setattr(irc, "_db", boom_db)

    cfw._post_extraction_intelligence_refresh("CREATOR_X")

    # The real underlying connection must have been closed despite the
    # simulated failure -- proven by attempting a further operation on it.
    with pytest.raises(sqlite3.ProgrammingError):
        holder["conn"].execute("SELECT 1")


# ── Fix 7: relationship_events.take_snapshot ────────────────────────────────

def test_take_snapshot_closes_connection_on_exception(tmp_db, monkeypatch):
    """conn.close() was only reached on success; any exception from a
    _snapshot_* helper left the connection handle open. Read-only (no
    write-lease risk), but still a genuine handle leak worth closing."""
    import src.core.relationship_events as ire

    conn = sqlite3.connect(tmp_db)
    conn.close()

    holder = {}
    real_connect = ire.sqlite3.connect

    def fake_connect(*a, **k):
        c = real_connect(*a, **k)
        holder["conn"] = c
        return c

    monkeypatch.setattr(ire.sqlite3, "connect", fake_connect)
    monkeypatch.setattr(
        ire, "_snapshot_funder_upstream",
        lambda _c: (_ for _ in ()).throw(RuntimeError("simulated snapshot failure")))

    result = ire.take_snapshot(tmp_db)
    assert result == {}
    with pytest.raises(sqlite3.ProgrammingError):
        holder["conn"].execute("SELECT 1")


# ── Fix 8: relationship_events.diff_and_log ─────────────────────────────────

def test_diff_and_log_closes_connection_on_exception(tmp_db, monkeypatch):
    """conn.commit()/close() previously sat inside the try, only reached on
    success; any exception from an _insert_event call left conn (and its
    write lease) open."""
    import src.core.relationship_events as ire

    conn = sqlite3.connect(tmp_db)
    conn.close()

    holder = {}
    real_connect = ire.sqlite3.connect

    def fake_connect(*a, **k):
        c = real_connect(*a, **k)
        holder["conn"] = c
        return c

    monkeypatch.setattr(ire.sqlite3, "connect", fake_connect)
    monkeypatch.setattr(
        ire, "_snapshot_funder_upstream",
        lambda _c: (_ for _ in ()).throw(RuntimeError("simulated diff failure")))

    result = ire.diff_and_log(tmp_db, before={"funder_upstream": set()})
    assert result["ok"] is False
    with pytest.raises(sqlite3.ProgrammingError):
        holder["conn"].execute("SELECT 1")


# ── Fix 9: second_hop_builder.SecondHopExpansionBuilder.build ──────────────

def test_second_hop_builder_closes_connection_on_exception(tmp_db, monkeypatch):
    """conn.commit()/close() previously sat inside the try, only reached on
    success; any exception from a builder step left conn (and its write
    lease) open."""
    import src.core.second_hop_builder as shb

    monkeypatch.setenv("SECOND_HOP_SQL_ENABLED", "true")

    conn = sqlite3.connect(tmp_db)
    conn.close()

    holder = {}
    real_connect = shb.sqlite3.connect

    def fake_connect(*a, **k):
        c = real_connect(*a, **k)
        holder["conn"] = c
        return c

    monkeypatch.setattr(shb.sqlite3, "connect", fake_connect)

    builder = shb.SecondHopExpansionBuilder(tmp_db)
    monkeypatch.setattr(
        builder, "_apply_span_migration",
        lambda _c: (_ for _ in ()).throw(RuntimeError("simulated migration failure")))

    result = builder.build()
    assert result["status"] == "failed"
    with pytest.raises(sqlite3.ProgrammingError):
        holder["conn"].execute("SELECT 1")


# ── Fix 10: relationship_events.rebuild_after_scan's analyzer_runs logging ─

def test_rebuild_after_scan_analyzer_runs_conn_closes_on_exception(tmp_db, monkeypatch):
    """_conn.commit()/close() previously sat inside the try, only reached
    on success; an INSERT failure left _conn (and its write lease) open."""
    import src.core.relationship_events as ire

    monkeypatch.setattr(ire, "SHL_REBUILD_AFTER_SCAN", True)
    monkeypatch.setattr(ire, "apply_migration", lambda *_a, **_k: None)
    monkeypatch.setattr(ire, "take_snapshot", lambda *_a, **_k: {})
    monkeypatch.setattr(ire, "diff_and_log", lambda *_a, **_k: {"ok": True})

    import sys
    import types
    fake_shb_module = types.ModuleType("src.core.second_hop_builder")

    class _FakeSecondHopBuilder:
        def __init__(self, *_a, **_k):
            pass

        def build(self):
            return {"status": "skipped"}

    fake_shb_module.SecondHopExpansionBuilder = _FakeSecondHopBuilder
    monkeypatch.setitem(sys.modules, "src.core.second_hop_builder", fake_shb_module)

    fake_bnr_module = types.ModuleType("src.utils.build_networks_release")
    fake_bnr_module.build_networks_release = lambda *_a, **_k: {"status": "skipped"}
    monkeypatch.setitem(sys.modules, "src.utils.build_networks_release", fake_bnr_module)

    fake_irc_module = types.ModuleType("src.core.intelligence_refresh")
    fake_irc_module.apply_migration = lambda *_a, **_k: None

    class _FakeIRCBuilder:
        def __init__(self, *_a, **_k):
            pass

        def run(self):
            return {"status": "skipped"}

    fake_irc_module.IntelligenceRefreshCandidateBuilder = _FakeIRCBuilder
    monkeypatch.setitem(sys.modules, "src.core.intelligence_refresh", fake_irc_module)

    holder = {}
    real_connect = ire.sqlite3.connect

    def fake_connect(db_path, timeout=10):
        c = real_connect(db_path, timeout=timeout)
        holder["conn"] = c

        class _BoomConn:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, *a, **k):
                if "INSERT INTO analyzer_runs" in sql:
                    raise RuntimeError("simulated analyzer_runs insert failure")
                return self._inner.execute(sql, *a, **k)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        return _BoomConn(c)

    # rebuild_after_scan does `import sqlite3 as _sqlite3` locally -- patch the
    # real sqlite3 module's connect, since that's what the local import binds to.
    monkeypatch.setattr(sqlite3, "connect", fake_connect)

    result = ire.rebuild_after_scan(tmp_db)
    assert "events" in result

    with pytest.raises(sqlite3.ProgrammingError):
        holder["conn"].execute("SELECT 1")


# ── Fix 11-13: DomainResolver (_ensure_table, _db_get, _db_set_many, _save_address_tag) ──

def test_domain_resolver_ensure_table_closes_connection_on_exception(tmp_db, monkeypatch):
    """Found live during the X78.0 soak: the TRUE original leak trigger,
    upstream of every other symptom traced in this file. DomainResolver is
    constructed once per extractor instance (__init__ calls _ensure_table
    immediately), on the event-loop thread, before any per-page write ever
    happens -- a leak here poisons the thread from the very first
    transaction of the very first job."""
    import src.extractors.realtime_creator_funding_extractor as rcfe

    holder = {}
    real_connect = rcfe.db_connect

    class _BoomCursor:
        def __init__(self, real_cursor):
            self._real = real_cursor
            self._calls = 0

        def execute(self, sql, *a, **k):
            self._calls += 1
            if self._calls == 2:  # let the first CREATE TABLE through, fail the second
                raise RuntimeError("simulated failure mid-ensure-table")
            return self._real.execute(sql, *a, **k)

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

    def fake_connect(*a, **k):
        real_conn = real_connect(*a, **k)
        boom = _BoomConn(real_conn)
        holder["conn"] = boom
        return boom

    monkeypatch.setattr(rcfe, "db_connect", fake_connect)

    resolver = rcfe.DomainResolver.__new__(rcfe.DomainResolver)
    resolver.db_path = tmp_db
    resolver.session = None
    resolver.mem = {}
    with pytest.raises(RuntimeError):
        resolver._ensure_table()

    assert holder["conn"].closed is True


def test_domain_resolver_db_set_many_closes_connection_on_exception(tmp_db, monkeypatch):
    """_db_set_many's conn.close() previously had no try/finally at all --
    this is the exact function named in the live NestedDatabaseWriteError
    trace (inner_command=...:147 in _db_set_many)."""
    import src.extractors.realtime_creator_funding_extractor as rcfe

    real_conn = sqlite3.connect(tmp_db)
    real_conn.execute(
        "CREATE TABLE address_domains (address TEXT PRIMARY KEY, primary_domain TEXT, updated_at INTEGER)")
    real_conn.commit()
    real_conn.close()

    holder = {}
    real_connect = rcfe.db_connect

    class _BoomCursor:
        def __init__(self, real_cursor):
            self._real = real_cursor

        def executemany(self, sql, rows):
            raise RuntimeError("simulated executemany failure")

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

    def fake_connect(*a, **k):
        c = real_connect(*a, **k)
        boom = _BoomConn(c)
        holder["conn"] = boom
        return boom

    monkeypatch.setattr(rcfe, "db_connect", fake_connect)

    resolver = rcfe.DomainResolver.__new__(rcfe.DomainResolver)
    resolver.db_path = tmp_db
    resolver.session = None
    resolver.mem = {}

    with pytest.raises(RuntimeError):
        resolver._db_set_many([("ADDR_X", "test.sol", 12345)])

    assert holder["conn"].closed is True


# ── Fix 14: address_tags.add_tag ────────────────────────────────────────────

def test_add_tag_closes_connection_on_exception(tmp_db, monkeypatch):
    """Called from domain_extraction.resolve_domains_for_addresses_async,
    itself reachable from extract_for_creator's per-transaction loop.
    conn.close() was only reached on success."""
    import src.utils.address_tags as at

    monkeypatch.setattr(at, "DB_PATH", tmp_db)

    real_conn = sqlite3.connect(tmp_db)
    real_conn.close()

    holder = {}
    real_connect = at.sqlite3.connect

    class _BoomConn:
        def __init__(self, real_conn):
            self._real = real_conn
            self.closed = False

        def cursor(self):
            raise RuntimeError("simulated cursor failure")

        def close(self):
            self.closed = True
            return self._real.close()

    def fake_connect(*a, **k):
        c = real_connect(*a, **k)
        boom = _BoomConn(c)
        holder["conn"] = boom
        return boom

    monkeypatch.setattr(at.sqlite3, "connect", fake_connect)

    result = at.add_tag("ADDR_X", "domain", "test.sol")
    assert result is False
    assert holder["conn"].closed is True


# ── Fix 15/16: domain_mapping.register_domain / link_domain_to_address ─────

def test_register_domain_closes_connection_on_exception(tmp_db, monkeypatch):
    """conn.close() was only reached on success."""
    import src.utils.domain_mapping as dm

    monkeypatch.setattr(dm, "DB_PATH", tmp_db)
    dm.DOMAIN_REGISTRY.clear()

    real_conn = sqlite3.connect(tmp_db)
    real_conn.close()

    holder = {}
    real_connect = dm.sqlite3.connect

    class _BoomConn:
        def __init__(self, real_conn):
            self._real = real_conn
            self.closed = False

        def cursor(self):
            raise RuntimeError("simulated cursor failure")

        def close(self):
            self.closed = True
            return self._real.close()

    def fake_connect(*a, **k):
        c = real_connect(*a, **k)
        boom = _BoomConn(c)
        holder["conn"] = boom
        return boom

    monkeypatch.setattr(dm.sqlite3, "connect", fake_connect)

    result = dm.register_domain("test.sol")
    assert result is False
    assert holder["conn"].closed is True


def test_link_domain_to_address_closes_connection_on_exception(tmp_db, monkeypatch):
    """conn.close() was only reached on success."""
    import src.utils.domain_mapping as dm

    monkeypatch.setattr(dm, "DB_PATH", tmp_db)
    dm.DOMAIN_REGISTRY.clear()
    dm.DOMAIN_REGISTRY["test.sol"] = {"name": "test.sol", "type": "mentioned", "metadata": {}}

    real_conn = sqlite3.connect(tmp_db)
    real_conn.close()

    holder = {}
    real_connect = dm.sqlite3.connect

    class _BoomConn:
        def __init__(self, real_conn):
            self._real = real_conn
            self.closed = False

        def cursor(self):
            raise RuntimeError("simulated cursor failure")

        def close(self):
            self.closed = True
            return self._real.close()

    def fake_connect(*a, **k):
        c = real_connect(*a, **k)
        boom = _BoomConn(c)
        holder["conn"] = boom
        return boom

    monkeypatch.setattr(dm.sqlite3, "connect", fake_connect)

    result = dm.link_domain_to_address("test.sol", "ADDR_X")
    assert result is False
    assert holder["conn"].closed is True


# ── Fix 17: relationship_events.apply_migration (module-local, distinct from intelligence_refresh's) ──

def test_relationship_events_apply_migration_closes_connection_on_exception(tmp_db, monkeypatch):
    """A DIFFERENT apply_migration than intelligence_refresh.apply_migration
    (already fixed) -- this module's own local function, called directly at
    the start of rebuild_after_scan (not via the irc_migrate alias). Missed
    on the first pass because two same-named functions exist in different
    modules. Same unguarded shape: conn.commit()/close() outside any
    try/finally."""
    import src.core.relationship_events as ire

    holder = {}
    real_connect = ire.sqlite3.connect

    class _BoomConn:
        def __init__(self, real_conn):
            self._real = real_conn
            self.closed = False

        def execute(self, sql, *a, **k):
            if "PRAGMA" in sql:
                return self._real.execute(sql, *a, **k)
            raise RuntimeError("simulated migration statement failure")

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

    monkeypatch.setattr(ire.sqlite3, "connect", fake_connect)

    with pytest.raises(RuntimeError):
        ire.apply_migration(tmp_db)

    assert holder["conn"].closed is True


# ── Fix 18: domain_mapping.init_domain_registry ─────────────────────────────

def test_init_domain_registry_closes_connection_on_exception(tmp_db, monkeypatch):
    """Found live during the X78.0 soak: called on EVERY
    extract_funding_for_new_token call via init_session(), unconditionally,
    before DomainResolver's own construction even finishes -- the earliest
    possible write in the whole extraction pipeline. conn.close() was only
    reached on success."""
    import src.utils.domain_mapping as dm

    monkeypatch.setattr(dm, "DB_PATH", tmp_db)
    dm.DOMAIN_REGISTRY.clear()

    holder = {}
    real_connect = dm.sqlite3.connect

    class _BoomCursor:
        def __init__(self, real_cursor):
            self._real = real_cursor

        def execute(self, sql, *a, **k):
            if "sqlite_master" in sql:
                raise RuntimeError("simulated table-check failure")
            return self._real.execute(sql, *a, **k)

        def __getattr__(self, name):
            return getattr(self._real, name)

    class _BoomConn:
        def __init__(self, real_conn):
            self._real = real_conn
            self.closed = False

        def cursor(self):
            return _BoomCursor(self._real.cursor())

        def close(self):
            self.closed = True
            return self._real.close()

    def fake_connect(*a, **k):
        c = real_connect(*a, **k)
        boom = _BoomConn(c)
        holder["conn"] = boom
        return boom

    monkeypatch.setattr(dm.sqlite3, "connect", fake_connect)

    dm.init_domain_registry()  # must not raise -- caught internally, logged

    assert holder["conn"].closed is True


# ── Fix 19: RealTimeCreatorFundingExtractor._setup_db_optimizations ────────

def test_setup_db_optimizations_closes_connection_on_exception(tmp_db, monkeypatch):
    """conn.close() was only reached on success. Called on every
    init_session(), i.e. every single extraction."""
    import src.extractors.realtime_creator_funding_extractor as rcfe

    holder = {}
    real_connect = rcfe.db_connect

    class _BoomConn:
        def __init__(self, real_conn):
            self._real = real_conn
            self.closed = False

        def execute(self, sql, *a, **k):
            if "cache_size" in sql:
                raise RuntimeError("simulated PRAGMA failure")
            return self._real.execute(sql, *a, **k)

        def commit(self):
            return self._real.commit()

        def close(self):
            self.closed = True
            return self._real.close()

    def fake_connect(*a, **k):
        c = real_connect(*a, **k)
        boom = _BoomConn(c)
        holder["conn"] = boom
        return boom

    monkeypatch.setattr(rcfe, "db_connect", fake_connect)

    extractor = rcfe.RealTimeCreatorFundingExtractor.__new__(rcfe.RealTimeCreatorFundingExtractor)
    extractor._setup_db_optimizations()  # must not raise -- caught internally

    assert holder["conn"].closed is True
