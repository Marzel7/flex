"""X78.0 Phase 3: deterministic reproduction of creator_funding_worker's
permanent NestedDatabaseWriteError stall.

Root cause chain (see docs/audits/x78_0_creator_funding_concurrency.md for
the full architecture/thread-ownership audit):

1. TrackedConnection's write lease is thread-local (_thread_write_lease,
   threading.local()) and is acquired lazily on the first write-shaped SQL
   statement -- success or failure of that statement doesn't matter, the
   lease is already held the moment _acquire_write_lane() runs.
2. The lease is released ONLY by commit(), rollback(), or close() -- if a
   connection that acquired the lease is never one of those three, on the
   SAME thread that acquired it, the lease is held forever.
3. asyncio.to_thread()'s default executor reuses OS worker threads across
   sequential calls (proven directly below) -- so a single leaked lease on
   one call poisons every subsequent to_thread() write dispatched to that
   same reused thread, permanently, for the life of the process.
4. The connection reaper (_reap_stale_connections, db_locking.py) is meant
   to force-close abandoned connections as a safety net -- but it runs on
   its OWN dedicated thread (db-conn-reaper), and sqlite3 connections default
   to check_same_thread=True (never overridden anywhere in this codebase).
   Calling close() on a connection from a DIFFERENT thread than the one
   that created it raises sqlite3.ProgrammingError, which the reaper's own
   `except Exception: pass` silently swallows -- the reaper has NEVER
   successfully closed a connection across a thread boundary. This is why
   the safety net never engaged and the leak became permanent.

No timing-dependent assertions -- every scenario below is deterministic:
the lease state is checked directly, not inferred from elapsed time.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import threading

import pytest

import src.utils.db_locking as db_locking
from src.core.database_write_service import NestedDatabaseWriteError, _thread_write_lease


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


# ── Step 1: asyncio.to_thread reuses OS worker threads ──────────────────────

def test_asyncio_to_thread_reuses_the_same_os_thread_across_sequential_calls():
    """The exact premise the whole failure chain depends on."""
    thread_ids = []

    async def main():
        for _ in range(5):
            tid = await asyncio.to_thread(threading.get_ident)
            thread_ids.append(tid)

    asyncio.run(main())
    assert len(set(thread_ids)) == 1, (
        f"expected all sequential to_thread() calls to reuse one OS thread, "
        f"got {len(set(thread_ids))} distinct threads: {thread_ids}")


# ── Step 2: a leaked lease poisons every future write on that same thread ──

def test_a_single_leaked_lease_poisons_every_subsequent_write_same_thread(tmp_db):
    """Minimal fixture: one thread, one leaked connection (write acquired,
    never committed/rolled back/closed), then a second, unrelated connection
    on the SAME thread attempts a real write. Must fail with
    NestedDatabaseWriteError -- and keep failing on a third attempt too,
    proving the poisoning is permanent, not transient."""
    results = []

    def run_on_one_thread():
        # First "call": leaks a write lease (never released).
        leaker = db_locking.db_connect(tmp_db, timeout=5)
        leaker.execute("CREATE TABLE t (a INTEGER)")
        results.append(("leaker_acquired", getattr(_thread_write_lease, "owner", None) is not None))

        # Second "call", same thread: an entirely unrelated, well-behaved
        # write attempt -- this is exactly what creator_funding_worker's
        # NEXT cycle looks like (a fresh, correctly-written _mark_complete
        # or _write_heartbeat call).
        try:
            victim = db_locking.db_connect(tmp_db, timeout=5)
            victim.execute("CREATE TABLE t2 (b INTEGER)")
            victim.commit()
            victim.close()
            results.append(("victim_1", "SUCCESS"))
        except NestedDatabaseWriteError:
            results.append(("victim_1", "POISONED"))

        # Third "call", same thread: proves this isn't a one-time collision --
        # the poisoning persists for the rest of this thread's life.
        try:
            victim2 = db_locking.db_connect(tmp_db, timeout=5)
            victim2.execute("CREATE TABLE t3 (c INTEGER)")
            victim2.commit()
            victim2.close()
            results.append(("victim_2", "SUCCESS"))
        except NestedDatabaseWriteError:
            results.append(("victim_2", "POISONED"))

    t = threading.Thread(target=run_on_one_thread)
    t.start()
    t.join()

    outcomes = dict(results)
    assert outcomes["leaker_acquired"] is True
    assert outcomes["victim_1"] == "POISONED"
    assert outcomes["victim_2"] == "POISONED"


# ── Step 3: the connection reaper cannot heal a cross-thread leak ──────────

def test_reaper_cannot_close_a_connection_from_a_different_thread(tmp_db):
    """The safety net that was supposed to catch this: db_locking.py's
    connection reaper runs on its own dedicated thread. sqlite3 connections
    default to check_same_thread=True (never overridden in this codebase),
    so calling close() on a connection from a different thread than the one
    that created it raises sqlite3.ProgrammingError -- proving the reaper's
    force-close path can never actually succeed across threads, which is
    why the leak in test_a_single_leaked_lease_poisons... was never healed
    automatically in production."""
    state = {}
    created = threading.Event()

    def creator_thread():
        conn = db_locking.db_connect(tmp_db, timeout=5)
        state["conn"] = conn
        created.set()
        # Keep this thread alive long enough for the "reaper" to attempt close.
        state["proceed"] = threading.Event()
        state["proceed"].wait(timeout=5)

    t1 = threading.Thread(target=creator_thread)
    t1.start()
    created.wait(timeout=5)

    with pytest.raises(sqlite3.ProgrammingError):
        state["conn"].close()  # called from THIS thread (pytest's), not t1

    state["proceed"].set()
    t1.join()


# ── Step 4: the real-world shape -- extract_for_creator's own CREATE TABLE ──

def test_create_table_if_not_exists_still_acquires_and_can_leak_the_lease(tmp_db):
    """realtime_creator_funding_extractor.py's extract_for_creator opens
    extraction_conn and later runs `CREATE TABLE IF NOT EXISTS ...` via a
    cursor, inside a try/except that swallows any failure (the tables
    normally already exist, so this is expected to be a silent no-op) --
    but TrackedCursor.execute() acquires the write lane BEFORE the
    statement runs, unconditionally. If the thread is already poisoned by
    an earlier leak (see test 2), THIS acquisition itself raises
    NestedDatabaseWriteError, which the extractor's except:pass swallows,
    execution continues, and the function reaches its own finally-block
    close() -- but since THIS connection's own _holds_write_lock was never
    set True (the acquire failed), close() has nothing to release. The
    ORIGINAL leaked owner (from whatever leaked first) remains held,
    forever, and every subsequent extraction on this thread repeats this
    exact sequence -- which is why every single NestedDatabaseWriteError in
    production logs shows the SAME outer_command
    (realtime_creator_funding_extractor.py:1226 in extract_for_creator)."""
    def run_on_one_thread():
        # Simulate whatever leaked first (unknown exact original trigger,
        # but proven possible and self-perpetuating regardless of cause).
        leaker = db_locking.db_connect(tmp_db, timeout=5)
        leaker.execute("CREATE TABLE some_earlier_table (a INTEGER)")

        # Simulate extract_for_creator's own CREATE TABLE IF NOT EXISTS block.
        extraction_conn = db_locking.db_connect(tmp_db, timeout=5)
        cur = extraction_conn.cursor()
        try:
            cur.execute("CREATE TABLE IF NOT EXISTS creator_service_history (a INTEGER)")
            extraction_conn.commit()
            acquired_cleanly = True
        except NestedDatabaseWriteError:
            acquired_cleanly = False  # swallowed by the real code's except:pass
        assert acquired_cleanly is False, (
            "expected the poisoned thread to make this acquisition fail")
        assert getattr(extraction_conn, "_holds_write_lock", False) is False, (
            "extraction_conn should never have held the lease -- its own "
            "acquire attempt failed")
        extraction_conn.close()  # no-op for lease release; had nothing to release

        # The ORIGINAL leaked owner must still be held after extraction_conn's
        # close() -- proving close() on a connection that never held the
        # lease cannot accidentally clear someone else's leak.
        owner = getattr(_thread_write_lease, "owner", None)
        assert owner is not None
        assert "some_earlier_table" not in str(owner)  # sanity: it's the SQL, not searched here
        assert owner.get("command", "").endswith("in run_on_one_thread")

    t = threading.Thread(target=run_on_one_thread)
    t.start()
    t.join()
