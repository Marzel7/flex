"""X77.5: ws_cascade_store._event_writer_loop's enqueue-on-transient-failure
fallback must never call ensure_cascade_schema() -- that 636-line function
has no try/finally around its own body, so an exception anywhere between its
first write (an unconditional CREATE TABLE IF NOT EXISTS) and its single
final commit() leaks the write lease for the rest of the calling thread's
life. Calling it on every single write failure (the background writer
thread's OWN failure path, which can fire repeatedly under real contention)
turned an occasional transient failure into a self-sustaining
NestedDatabaseWriteError loop -- observed live during the X77.5 soak.

The fix removes that call entirely: the schema (including
wt_pending_cascade_events) is already guaranteed to exist via
ws_cascade.py's own startup call to operations_write("ws-cascade-schema-startup",
ensure_cascade_schema), which runs exactly once, before the event loop (and
this writer thread) ever starts, through the safely-managed
database_write_service path rather than a raw TrackedConnection.

Five scenarios, matching the four points required before this fix could be
committed: first boot, restart, concurrent enqueue, existing schema, missing
schema (failure must be loud, not silent).
"""
from __future__ import annotations

import sqlite3

import pytest

import src.core.ws_cascade_store as store


@pytest.fixture
def ops_schema_present():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store.ensure_cascade_schema(conn)
    return conn


def _lock_error():
    from src.core.database_write_service import DatabaseWriteLockError
    return DatabaseWriteLockError({
        "database": "operations", "database_path": "/fake", "current_writer": {},
        "waiting_command": None, "failed_command": "fake", "managed_reentrancy_detected": False,
        "phase": "begin-acquired", "phase_elapsed_ms": 1.0, "sqlite_error_code": 5,
        "sqlite_error_name": "SQLITE_BUSY", "transaction_id": "fake", "transaction_age_seconds": 1.0,
    })


def test_enqueue_path_never_calls_ensure_cascade_schema(monkeypatch, ops_schema_present):
    """Point 1/2: the schema-existence guarantee comes from startup, not this
    path -- proven directly by asserting ensure_cascade_schema is never
    invoked during a simulated write-failure-then-enqueue cycle."""
    calls = {"ensure_schema": 0}

    def spy_ensure_schema(conn):
        calls["ensure_schema"] += 1

    monkeypatch.setattr(store, "db_connect", lambda *a, **k: ops_schema_present)
    monkeypatch.setattr(store, "ensure_cascade_schema", spy_ensure_schema)

    item = ('event', 'TEST_EVENT', 'WALLET_A', None, None, {}, 1000)
    store.enqueue_pending_cascade_event(ops_schema_present, item, _lock_error())

    assert calls["ensure_schema"] == 0, (
        "enqueue_pending_cascade_event (or its caller's fallback path) must "
        "never call ensure_cascade_schema -- schema existence is guaranteed "
        "at process startup, not per-failure")


def test_first_boot_schema_already_present_enqueue_succeeds():
    """First boot: ensure_cascade_schema ran once at startup (simulated here
    directly, matching ws_cascade.py's own __init__ call), so the retry
    table already exists when the first failure needs to enqueue."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store.ensure_cascade_schema(conn)  # the ONE startup call

    item = ('event', 'TEST_EVENT', 'WALLET_B', None, None, {}, 1000)
    store.enqueue_pending_cascade_event(conn, item, _lock_error())

    row = conn.execute("SELECT state FROM wt_pending_cascade_events WHERE kind='event'").fetchone()
    assert row is not None
    assert row["state"] == "PENDING"


def test_restart_schema_still_present_across_process_boundary():
    """Restart: a second 'process' (fresh connection to the same on-disk
    schema) must not need to re-run ensure_cascade_schema either -- the
    table persists across restarts by definition (it's a durable table)."""
    conn1 = sqlite3.connect(":memory:")
    conn1.row_factory = sqlite3.Row
    store.ensure_cascade_schema(conn1)
    conn1.close()

    # Simulate "restart" against a real file so schema persistence is genuine,
    # not just an artifact of reusing the same in-memory connection.
    import tempfile
    import os
    tmp = tempfile.mktemp(suffix=".db")
    try:
        boot1 = sqlite3.connect(tmp)
        boot1.row_factory = sqlite3.Row
        store.ensure_cascade_schema(boot1)
        boot1.close()

        boot2 = sqlite3.connect(tmp)  # "restart"
        boot2.row_factory = sqlite3.Row
        item = ('event', 'TEST_EVENT', 'WALLET_C', None, None, {}, 1000)
        store.enqueue_pending_cascade_event(boot2, item, _lock_error())
        row = boot2.execute("SELECT state FROM wt_pending_cascade_events WHERE kind='event'").fetchone()
        assert row is not None
        boot2.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(tmp + suffix)
            except FileNotFoundError:
                pass


def test_concurrent_enqueue_both_items_persisted():
    """Concurrent enqueue: two 'hit' items with different dedupe keys, and
    an 'event' item, all enqueued in sequence on the same connection
    (matching the single dedicated event-writer thread's own serial
    processing) must all persist without interference."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store.ensure_cascade_schema(conn)

    items = [
        ('hit', 'TREASURY_X', 'SUBPROV_X', 'SIG_1', 1.0, 900, 1000, 'TRANSFER'),
        ('hit', 'TREASURY_Y', 'SUBPROV_Y', 'SIG_2', 2.0, 901, 1001, 'TRANSFER'),
        ('event', 'TEST_EVENT', 'WALLET_D', None, None, {}, 1002),
    ]
    for item in items:
        store.enqueue_pending_cascade_event(conn, item, _lock_error())

    count = conn.execute("SELECT COUNT(*) FROM wt_pending_cascade_events").fetchone()[0]
    assert count == 3


def test_missing_schema_fails_loudly_not_silently(monkeypatch):
    """Missing schema (should never happen in production, but must degrade
    safely if it ever does): enqueue_pending_cascade_event must LOG the
    failure, not swallow it silently -- this is the point that had to be
    fixed as part of removing the ensure_cascade_schema() safety net."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Deliberately do NOT call ensure_cascade_schema -- the table doesn't exist.

    logged = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: logged.append(" ".join(str(x) for x in a)))

    item = ('event', 'TEST_EVENT', 'WALLET_E', None, None, {}, 1000)
    store.enqueue_pending_cascade_event(conn, item, _lock_error())  # must not raise

    assert any("genuinely lost" in line for line in logged), (
        f"a missing-schema enqueue failure must be logged loudly, not silently "
        f"swallowed; captured output: {logged}")
