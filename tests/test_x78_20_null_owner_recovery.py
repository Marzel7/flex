import json
import multiprocessing
import os
import sqlite3
import threading
import time

import pytest

from src.core.database_write_service import (
    CrossProcessDatabaseWriteTimeout,
    _read_owner_metadata,
    acquire_write_lease,
    probe_kernel_flock,
    release_write_lease,
)
from src.utils import db_locking


def _hold_until_exit(db_path, ready):
    lease = acquire_write_lease("tracked:test", db_path, "child-tx", "child-holder")
    ready.set()
    time.sleep(30)
    release_write_lease(lease)


def _threaded_wait(db_path, timeout):
    result = {}
    def wait():
        try:
            acquire_write_lease("tracked:test", db_path, "waiter", "waiter", timeout=timeout)
        except Exception as exc:
            result["exception"] = exc
    thread = threading.Thread(target=wait, name="lease-waiter")
    thread.start()
    thread.join(timeout + 5)
    assert not thread.is_alive()
    return result.get("exception")


def test_stale_double_release_cannot_remove_successor_metadata(tmp_path):
    db_path = str(tmp_path / "flex.db")
    sqlite3.connect(db_path).close()
    first = acquire_write_lease("tracked:test", db_path, "tx-1", "first")
    release_write_lease(first)
    second = acquire_write_lease("tracked:test", db_path, "tx-2", "second")
    try:
        # The historical release path unlinked blindly.  An overdue second
        # cleanup of tx-1 could therefore erase tx-2's diagnostics.
        release_write_lease(first)
        owner = _read_owner_metadata(f"{os.path.realpath(db_path)}.write.lock.owner")
        assert owner is not None
        assert owner["transaction_id"] == "tx-2"
        assert owner["state"] == "ACTIVE"
        exc = _threaded_wait(db_path, 0.1)
        assert isinstance(exc, CrossProcessDatabaseWriteTimeout)
        assert exc.current_owner["transaction_id"] == "tx-2"
    finally:
        release_write_lease(second)


def test_foreign_close_retains_lane_and_registry_until_owner_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(db_locking, "_DB_WRITE_SERIALIZE", True)
    db_path = str(tmp_path / "flex.db")
    sqlite3.connect(db_path).execute("CREATE TABLE t(v INTEGER)").connection.close()
    shared = {}
    owner_continue = threading.Event()
    owner_ready = threading.Event()

    def owner():
        conn = db_locking.db_connect(db_path)
        conn.execute("INSERT INTO t VALUES (1)")
        shared["conn"] = conn
        shared["tracking_id"] = conn._db_tracking_id
        owner_ready.set()
        owner_continue.wait(5)
        conn.rollback()
        conn.close()

    thread = threading.Thread(target=owner, name="connection-owner")
    thread.start()
    assert owner_ready.wait(5)
    conn = shared["conn"]
    with pytest.raises(sqlite3.ProgrammingError):
        conn.close()
    record = db_locking._open_connections[shared["tracking_id"]]
    assert record["native_close_state"] == "CLOSE_FAILED_WRONG_THREAD"
    assert conn._holds_write_lock is True
    assert _read_owner_metadata(f"{os.path.realpath(db_path)}.write.lock.owner") is not None
    owner_continue.set()
    thread.join(5)
    assert not thread.is_alive()
    assert shared["tracking_id"] not in db_locking._open_connections
    assert _read_owner_metadata(f"{os.path.realpath(db_path)}.write.lock.owner") is None


def test_null_owner_wait_emits_one_bounded_episode(tmp_path, monkeypatch):
    db_path = str(tmp_path / "flex.db")
    sqlite3.connect(db_path).close()
    diagnostics = tmp_path / "episodes.jsonl"
    monkeypatch.setenv("DB_NULL_OWNER_DIAGNOSTICS_PATH", str(diagnostics))
    holder = acquire_write_lease("tracked:test", db_path, "holder", "holder")
    os.unlink(holder.owner_path)  # deterministic reproduction of old symptom
    try:
        exc = _threaded_wait(db_path, 1.1)
        assert isinstance(exc, CrossProcessDatabaseWriteTimeout)
    finally:
        release_write_lease(holder)
    rows = [json.loads(line) for line in diagnostics.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["application_owner"] is None
    assert rows[0]["wait_seconds"] >= 1.0
    assert rows[0]["blocked_episode_id"]


def test_process_death_releases_kernel_flock(tmp_path):
    db_path = str(tmp_path / "flex.db")
    sqlite3.connect(db_path).close()
    ready = multiprocessing.Event()
    child = multiprocessing.Process(target=_hold_until_exit, args=(db_path, ready))
    child.start()
    assert ready.wait(5)
    child.terminate()
    child.join(5)
    lease = acquire_write_lease("tracked:test", db_path, "parent", "parent", timeout=1)
    release_write_lease(lease)


def test_kernel_probe_attributes_holder_without_sidecar(tmp_path):
    db_path = str(tmp_path / "flex.db")
    sqlite3.connect(db_path).close()
    lease = acquire_write_lease("tracked:test", db_path, "physical-tx", "physical-holder")
    os.unlink(lease.owner_path)
    try:
        result = probe_kernel_flock(f"{os.path.realpath(db_path)}.write.lock")
        assert result["state"] == "HELD"
        assert result["lock_bound_owner"]["transaction_id"] == "physical-tx"
        assert result["lock_bound_owner"]["process_pid"] == os.getpid()
    finally:
        release_write_lease(lease)
    assert probe_kernel_flock(f"{os.path.realpath(db_path)}.write.lock")["state"] == "FREE"


def test_reaper_skips_connection_with_active_write_lane(tmp_path, monkeypatch):
    db_path = str(tmp_path / "flex.db")
    sqlite3.connect(db_path).close()
    conn = db_locking.db_connect(db_path)
    tracking_id = conn._db_tracking_id
    conn._holds_write_lock = True
    with db_locking._open_connections_lock:
        db_locking._open_connections[tracking_id]["opened_at"] = time.time() - 100
    monkeypatch.setattr(db_locking, "_MAX_CONNECTION_AGE_SECS", 0)
    assert db_locking._reap_stale_connections() == 0
    assert tracking_id in db_locking._open_connections
    conn._holds_write_lock = False
    conn.close()
