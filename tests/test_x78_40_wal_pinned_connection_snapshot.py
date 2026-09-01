"""X78.40: bounded connection-state snapshot embedded in CRITICAL_WAL_PINNED.

Covers both the classification helper (src.utils.db_locking.wal_pin_connection_snapshot)
and its wiring into creator_funding_worker's WAL watchdog terminal event.
"""
import importlib
import json
import sqlite3
import threading
import time
import weakref

import pytest


@pytest.fixture
def locking(monkeypatch):
    import src.utils.db_locking as mod
    importlib.reload(mod)
    with mod._open_connections_lock:
        mod._open_connections.clear()
    yield mod
    with mod._open_connections_lock:
        mod._open_connections.clear()


class _FakeConn:
    def __init__(self, in_transaction):
        self.in_transaction = in_transaction


def _register(locking, *, caller, thread, opened_at, in_transaction, path="database/flex_complete_database.db", mode="read_write"):
    conn = _FakeConn(in_transaction)
    tracking_id = id(conn)
    record = {
        "connection_id": f"c-{tracking_id}",
        "path": path,
        "caller": caller,
        "purpose": "test",
        "mode": mode,
        "opened_at": opened_at,
        "thread_id": threading.get_ident(),
        "thread": thread,
        "conn_ref": weakref.ref(conn),
    }
    with locking._open_connections_lock:
        locking._open_connections[tracking_id] = record
    return conn  # caller must keep this referenced so the weakref stays alive


def test_idle_connection_classified_open_idle(locking):
    conn = _register(locking, caller="a.py:1", thread="t1", opened_at=time.time(), in_transaction=False)
    snap = locking.wal_pin_connection_snapshot()
    assert snap["connections"][0]["state"] == "OPEN_IDLE"
    del conn


def test_in_transaction_connection_classified(locking):
    conn = _register(locking, caller="a.py:1", thread="t1", opened_at=time.time(), in_transaction=True)
    snap = locking.wal_pin_connection_snapshot()
    assert snap["connections"][0]["state"] == "IN_TRANSACTION"
    del conn


def test_long_lived_transaction_classified_and_prioritized(locking):
    old_conn = _register(locking, caller="old.py:1", thread="t-old", opened_at=time.time() - 999, in_transaction=True)
    new_conn = _register(locking, caller="new.py:1", thread="t-new", opened_at=time.time(), in_transaction=True)
    snap = locking.wal_pin_connection_snapshot()
    states = [c["state"] for c in snap["connections"]]
    assert states[0] == "LONG_LIVED_IN_TRANSACTION"
    assert snap["connections"][0]["caller"] == "old.py:1"
    del old_conn, new_conn


def test_deterministic_ordering_long_lived_then_in_transaction_then_unknown_then_idle(locking):
    idle = _register(locking, caller="idle.py", thread="t1", opened_at=time.time(), in_transaction=False)
    txn = _register(locking, caller="txn.py", thread="t2", opened_at=time.time(), in_transaction=True)
    long_txn = _register(locking, caller="long.py", thread="t3", opened_at=time.time() - 999, in_transaction=True)
    # unknown: conn_ref target already garbage collected
    ghost_id = -1
    with locking._open_connections_lock:
        locking._open_connections[ghost_id] = {
            "connection_id": "ghost", "path": "x", "caller": "ghost.py", "purpose": "test",
            "mode": "read_write", "opened_at": time.time(), "thread_id": 0, "thread": "ghost-thread",
            "conn_ref": lambda: None,
        }
    snap = locking.wal_pin_connection_snapshot(limit=10)
    states = [c["state"] for c in snap["connections"]]
    assert states == ["LONG_LIVED_IN_TRANSACTION", "IN_TRANSACTION", "UNKNOWN", "OPEN_IDLE"]
    del idle, txn, long_txn


def test_record_cap_and_truncation_flag(locking):
    kept = []
    for i in range(20):
        kept.append(_register(locking, caller=f"c{i}.py", thread=f"t{i}", opened_at=time.time() - i, in_transaction=False))
    snap = locking.wal_pin_connection_snapshot(limit=5)
    assert snap["total_count"] == 20
    assert snap["returned_count"] == 5
    assert snap["truncated"] is True
    assert len(snap["connections"]) == 5


def test_string_fields_are_bounded(locking):
    huge = "x" * 10_000
    conn = _register(locking, caller=huge, thread=huge, opened_at=time.time(), in_transaction=False)
    snap = locking.wal_pin_connection_snapshot()
    row = snap["connections"][0]
    assert len(row["caller"]) <= 120
    assert len(row["thread"]) <= 120
    del conn


def test_registry_read_failure_is_fail_open(locking, monkeypatch):
    class ExplodingLock:
        def __enter__(self):
            raise RuntimeError("lock unavailable")
        def __exit__(self, *a):
            return False
    real_lock = locking._open_connections_lock
    monkeypatch.setattr(locking, "_open_connections_lock", ExplodingLock())
    try:
        snap = locking.wal_pin_connection_snapshot()
        assert "error" in snap
        assert snap["connections"] == []
    finally:
        monkeypatch.setattr(locking, "_open_connections_lock", real_lock)


def test_lsof_failure_is_fail_open(monkeypatch):
    import src.core.creator_funding_worker as worker

    def boom(*a, **kw):
        raise OSError("lsof not found")
    monkeypatch.setattr(worker.subprocess, "run", boom)
    result = worker._external_open_handles()
    assert "error" in result
    assert result["handles"] == []


def test_external_pid_remains_external_open_handle(monkeypatch):
    import src.core.creator_funding_worker as worker

    class FakeResult:
        stdout = "COMMAND PID\npython3 12345\n"
    monkeypatch.setattr(worker.subprocess, "run", lambda *a, **kw: FakeResult())
    result = worker._external_open_handles()
    assert result["handles"][0]["state"] == "EXTERNAL_OPEN_HANDLE"
    assert result["handles"][0]["pid"] == "12345"
    # never anything stronger than EXTERNAL_OPEN_HANDLE
    assert all(h["state"] == "EXTERNAL_OPEN_HANDLE" for h in result["handles"])


def test_critical_wal_pinned_still_exits_when_diagnostics_raise(monkeypatch):
    import src.core.creator_funding_worker as worker

    monkeypatch.setattr(worker, "_wal_size_mb", lambda: 100.0)
    monkeypatch.setattr(worker, "_wal_busy", lambda: 1)
    monkeypatch.setattr(worker, "WAL_ALERT_MB", 1)
    monkeypatch.setattr(worker, "WAL_BUSY_CYCLES", 1)
    monkeypatch.setattr(worker, "WAL_CHECK_INTERVAL", 0)

    def boom_external():
        raise RuntimeError("external handles blew up")
    def boom_local():
        raise RuntimeError("local snapshot blew up")
    monkeypatch.setattr(worker, "_external_open_handles", boom_external)
    monkeypatch.setattr(worker, "_local_connection_snapshot", boom_local)

    exit_calls = []
    monkeypatch.setattr(worker.os, "_exit", lambda code: exit_calls.append(code) or (_ for _ in ()).throw(SystemExit()))
    monkeypatch.setattr(worker, "_STOP", False)

    def stop_after_one(*a, **kw):
        worker._STOP = True
    monkeypatch.setattr(worker, "_log", lambda msg: None)

    with pytest.raises(SystemExit):
        worker._wal_watchdog()

    assert exit_calls == [1]


def test_instrumentation_does_not_fire_on_ordinary_wal_cycle(monkeypatch):
    import src.core.creator_funding_worker as worker

    monkeypatch.setattr(worker, "_wal_size_mb", lambda: 100.0)
    monkeypatch.setattr(worker, "_wal_busy", lambda: 0)  # not busy -> never critical
    monkeypatch.setattr(worker, "WAL_CHECK_INTERVAL", 0)

    calls = []
    monkeypatch.setattr(worker, "_external_open_handles", lambda: calls.append("external") or {})
    monkeypatch.setattr(worker, "_local_connection_snapshot", lambda: calls.append("local") or {})

    def stop_soon():
        worker._STOP = True
    counter = {"n": 0}
    orig_sleep = worker.time.sleep
    def fake_sleep(_):
        counter["n"] += 1
        if counter["n"] >= 3:
            worker._STOP = True
    monkeypatch.setattr(worker.time, "sleep", fake_sleep)
    monkeypatch.setattr(worker, "_STOP", False)
    monkeypatch.setattr(worker, "_log", lambda msg: None)

    worker._wal_watchdog()

    assert calls == []


def test_instrumentation_fires_exactly_at_terminal_pinned_condition(monkeypatch):
    import src.core.creator_funding_worker as worker

    monkeypatch.setattr(worker, "_wal_size_mb", lambda: 100.0)
    monkeypatch.setattr(worker, "_wal_busy", lambda: 1)
    monkeypatch.setattr(worker, "WAL_ALERT_MB", 1)
    monkeypatch.setattr(worker, "WAL_BUSY_CYCLES", 2)
    monkeypatch.setattr(worker, "WAL_CHECK_INTERVAL", 0)

    calls = []
    monkeypatch.setattr(worker, "_external_open_handles", lambda: calls.append("external") or {"handles": []})
    monkeypatch.setattr(worker, "_local_connection_snapshot", lambda: calls.append("local") or {"connections": []})
    monkeypatch.setattr(worker.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit()))
    monkeypatch.setattr(worker, "_STOP", False)
    monkeypatch.setattr(worker, "_log", lambda msg: None)

    with pytest.raises(SystemExit):
        worker._wal_watchdog()

    # cycle 1 (busy_cycles=1, not yet >= WAL_BUSY_CYCLES=2): no snapshot calls
    # cycle 2 (busy_cycles=2, critical): exactly one snapshot pair
    assert calls == ["external", "local"]
