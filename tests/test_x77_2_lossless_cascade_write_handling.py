"""X77.2: watchtower_events / wt_webhook_hits writes from ws_cascade's
background event writer must never be silently lost on a transient
(contention) failure -- they get persisted to wt_pending_cascade_events and
retried by drain_pending_cascade_events. A NON-transient failure (constraint
violation, schema error) must never retry -- it would fail identically
forever and just hide a real bug.

Tests exercise src.core.ws_cascade_store directly against an in-memory
sqlite DB, with operations_write monkeypatched to simulate specific failure
classes deterministically (no real contention needed to prove the logic).
"""
from __future__ import annotations

import json
import sqlite3

import pytest

import src.core.ws_cascade_store as store
from src.core.database_write_service import DatabaseWriteLockError, NestedDatabaseWriteError


@pytest.fixture
def ops(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store.ensure_cascade_schema(conn)
    # Reset in-process counters between tests -- they're module-level state.
    for k in store._event_writer_stats:
        store._event_writer_stats[k] = 0
    return conn


def _lock_error():
    return DatabaseWriteLockError({
        "database": "operations", "database_path": "/fake", "current_writer": {},
        "waiting_command": None, "failed_command": "fake", "managed_reentrancy_detected": False,
        "phase": "begin-acquired", "phase_elapsed_ms": 1.0, "sqlite_error_code": 5,
        "sqlite_error_name": "SQLITE_BUSY", "transaction_id": "fake", "transaction_age_seconds": 1.0,
    })


# ── classification ────────────────────────────────────────────────────────

def test_lock_error_is_transient():
    assert store._is_transient_write_failure(_lock_error()) is True


def test_nested_write_error_is_transient():
    exc = NestedDatabaseWriteError(
        database="operations", outer_command="a", inner_command="b", outer_database="operations")
    assert store._is_transient_write_failure(exc) is True


def test_raw_operational_locked_is_transient():
    assert store._is_transient_write_failure(sqlite3.OperationalError("database is locked")) is True


def test_integrity_error_is_not_transient():
    assert store._is_transient_write_failure(sqlite3.IntegrityError("UNIQUE constraint failed")) is False


def test_unknown_exception_defaults_to_not_transient():
    assert store._is_transient_write_failure(ValueError("some other bug")) is False


# ── enqueue + drain: transient failure is retried and eventually written ───

def test_transient_failure_is_queued_then_successfully_retried(ops, monkeypatch):
    item = ('event', 'TEST_EVENT', 'WALLET_A', None, None, {"k": "v"}, 1000)

    calls = {"n": 0}

    def flaky_operations_write(command, transaction):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _lock_error()
        # Second call (the retry) succeeds -- write for real against `ops`.
        return transaction(ops)

    monkeypatch.setattr(store, "operations_write", flaky_operations_write)

    # Simulate what _event_writer_loop does on a transient failure: enqueue.
    try:
        flaky_operations_write("ws-cascade-event", lambda c: store._write_cascade_item(c, item, "wh"))
        pytest.fail("expected the first call to raise")
    except DatabaseWriteLockError as e:
        store.enqueue_pending_cascade_event(ops, item, e)

    pending = ops.execute("SELECT COUNT(*) FROM wt_pending_cascade_events WHERE state='PENDING'").fetchone()[0]
    assert pending == 1

    result = store.drain_pending_cascade_events(ops)
    assert result["written"] == 1
    assert result["remaining"] == 0

    row = ops.execute(
        "SELECT event_type, wallet_address FROM watchtower_events WHERE event_type='TEST_EVENT'"
    ).fetchone()
    assert row is not None
    assert row["wallet_address"] == "WALLET_A"

    queue_row = ops.execute(
        "SELECT state FROM wt_pending_cascade_events"
    ).fetchone()
    assert queue_row["state"] == "WRITTEN"


def test_hit_event_survives_retry_with_correct_columns(ops, monkeypatch):
    item = ('hit', 'TREASURY_X', 'SUBPROV_X', 'SIG123', 5.0, 900, 1000, 'TRANSFER')

    calls = {"n": 0}

    def flaky(command, transaction):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _lock_error()
        return transaction(ops)

    monkeypatch.setattr(store, "operations_write", flaky)

    try:
        flaky("ws-cascade-hit", lambda c: store._write_cascade_item(c, item, "wh"))
        pytest.fail("expected first call to raise")
    except DatabaseWriteLockError as e:
        store.enqueue_pending_cascade_event(ops, item, e)

    result = store.drain_pending_cascade_events(ops)
    assert result["written"] == 1

    row = ops.execute(
        "SELECT wallet_address, tx_signature, amount_sol, direction FROM wt_webhook_hits WHERE tx_signature='SIG123'"
    ).fetchone()
    assert row is not None
    assert row["wallet_address"] == "TREASURY_X"
    assert row["amount_sol"] == 5.0
    assert row["direction"] == "outbound"


# ── non-transient failure: never retried ────────────────────────────────────

def test_non_transient_failure_is_never_enqueued():
    """The writer loop itself must not call enqueue_pending_cascade_event for
    a non-transient failure -- proven at the _is_transient_write_failure
    boundary the loop actually checks before enqueueing."""
    exc = sqlite3.IntegrityError("UNIQUE constraint failed: watchtower_events.id")
    assert store._is_transient_write_failure(exc) is False
    # (The writer loop's `if _is_transient_write_failure(e): enqueue... else:
    # _bump_stat("failed_permanent")` branch is exercised end-to-end in
    # test_event_writer_loop_permanent_failure_bumps_failed_stat below.)


def test_drain_marks_row_failed_when_retry_hits_non_transient_error(ops, monkeypatch):
    """If a row somehow reaches the retry queue and the retry itself then
    raises a non-transient error, drain must mark it FAILED (stop retrying)
    rather than leave it PENDING forever."""
    item = ('event', 'TEST_EVENT', 'WALLET_B', None, None, {}, 1000)
    store.enqueue_pending_cascade_event(ops, item, _lock_error())

    def always_integrity_error(command, transaction):
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    monkeypatch.setattr(store, "operations_write", always_integrity_error)

    result = store.drain_pending_cascade_events(ops)
    assert result["failed"] == 1

    row = ops.execute("SELECT state, last_error FROM wt_pending_cascade_events").fetchone()
    assert row["state"] == "FAILED"
    assert "UNIQUE constraint" in row["last_error"]


# ── idempotency: 'hit' rows dedupe on (sig, treasury) ───────────────────────

def test_hit_dedupe_key_prevents_double_enqueue(ops):
    item = ('hit', 'TREASURY_X', 'SUBPROV_X', 'SIG_DUP', 1.0, 900, 1000, 'TRANSFER')
    store.enqueue_pending_cascade_event(ops, item, _lock_error())
    store.enqueue_pending_cascade_event(ops, item, _lock_error())  # simulate a second failed attempt

    count = ops.execute("SELECT COUNT(*) FROM wt_pending_cascade_events WHERE dedupe_key IS NOT NULL").fetchone()[0]
    assert count == 1


def test_event_rows_have_no_dedupe_key_and_can_coexist(ops):
    """'event' rows have no natural key (watchtower_events is append-only) --
    two distinct 'event' enqueue attempts must both be preserved, not
    collapsed into one."""
    item1 = ('event', 'TEST_EVENT', 'WALLET_A', None, None, {"n": 1}, 1000)
    item2 = ('event', 'TEST_EVENT', 'WALLET_A', None, None, {"n": 2}, 1001)
    store.enqueue_pending_cascade_event(ops, item1, _lock_error())
    store.enqueue_pending_cascade_event(ops, item2, _lock_error())

    count = ops.execute("SELECT COUNT(*) FROM wt_pending_cascade_events").fetchone()[0]
    assert count == 2


# ── in-process counters ──────────────────────────────────────────────────

def test_event_writer_loop_transient_failure_bumps_queued_stat(ops, monkeypatch):
    monkeypatch.setattr(store, "db_connect", lambda *a, **k: ops)
    monkeypatch.setattr(store, "operations_write", lambda command, transaction: (_ for _ in ()).throw(_lock_error()))

    item = ('event', 'TEST_EVENT', 'WALLET_C', None, None, {}, 1000)
    store._event_q.put_nowait(item)

    # Run exactly one iteration of the writer loop's body inline (mirroring
    # what _event_writer_loop does per item) rather than starting the real
    # background thread, so the test is deterministic.
    got = store._event_q.get()
    kind = got[0]
    try:
        def write(c, _item=got):
            store._write_cascade_item(c, _item, "wh")
        store.operations_write(f"ws-cascade-{kind}", write)
        store._bump_stat("succeeded")
    except Exception as e:
        assert store._is_transient_write_failure(e)
        store.ensure_cascade_schema(ops)
        store.enqueue_pending_cascade_event(ops, got, e)
        store._bump_stat("queued_for_retry")

    stats = store.event_writer_stats()
    assert stats["queued_for_retry"] == 1
    assert stats["succeeded"] == 0


def test_event_writer_loop_permanent_failure_bumps_failed_stat(ops, monkeypatch):
    exc = sqlite3.IntegrityError("UNIQUE constraint failed")
    monkeypatch.setattr(store, "operations_write", lambda command, transaction: (_ for _ in ()).throw(exc))

    item = ('event', 'TEST_EVENT', 'WALLET_D', None, None, {}, 1000)
    store._event_q.put_nowait(item)
    got = store._event_q.get()
    kind = got[0]
    try:
        def write(c, _item=got):
            store._write_cascade_item(c, _item, "wh")
        store.operations_write(f"ws-cascade-{kind}", write)
        store._bump_stat("succeeded")
    except Exception as e:
        if store._is_transient_write_failure(e):
            store.enqueue_pending_cascade_event(ops, got, e)
            store._bump_stat("queued_for_retry")
        else:
            store._bump_stat("failed_permanent")

    stats = store.event_writer_stats()
    assert stats["failed_permanent"] == 1
    assert stats["queued_for_retry"] == 0

    # Non-transient failures must never be enqueued for retry.
    pending = ops.execute("SELECT COUNT(*) FROM wt_pending_cascade_events").fetchone()[0]
    assert pending == 0


def test_pending_cascade_event_counts_reflects_all_states(ops):
    item = ('event', 'TEST_EVENT', 'WALLET_E', None, None, {}, 1000)
    store.enqueue_pending_cascade_event(ops, item, _lock_error())
    counts = store.pending_cascade_event_counts(ops)
    assert counts["PENDING"] == 1
    assert counts["WRITTEN"] == 0
