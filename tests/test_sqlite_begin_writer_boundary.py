"""Regression proof for SQLite writer-state acquisition before the flock."""

import sqlite3

import pytest

from src.utils import db_locking


def _database(tmp_path):
    path = str(tmp_path / "begin-boundary.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()
    return path


def test_previous_begin_immediate_gap_reproduces_under_flock_busy(tmp_path, monkeypatch):
    """The old classifier let SQLite writer state precede the app flock."""
    path = _database(tmp_path)
    old_prefixes = tuple(
        prefix for prefix in db_locking._WRITE_SQL_PREFIXES
        if not prefix.startswith("BEGIN")
    )
    monkeypatch.setattr(db_locking, "_WRITE_SQL_PREFIXES", old_prefixes)

    blocker = db_locking.db_connect(path)
    victim = db_locking.db_connect(path)
    try:
        # The production retention primitives use cursor.execute().
        blocker.cursor().execute("BEGIN IMMEDIATE")
        assert blocker.in_transaction
        assert not getattr(blocker, "_holds_write_lock", False)

        victim._acquire_write_lane()
        assert victim._holds_write_lock
        victim.execute("PRAGMA busy_timeout=1")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            victim.execute("INSERT INTO events(value) VALUES ('victim')")
    finally:
        victim.rollback()
        blocker.rollback()
        victim.close()
        blocker.close()


@pytest.mark.parametrize("statement", ["BEGIN IMMEDIATE", "BEGIN EXCLUSIVE"])
@pytest.mark.parametrize("via_cursor", [False, True])
def test_writer_begin_acquires_flock_before_sqlite_transaction(
    tmp_path, monkeypatch, statement, via_cursor
):
    path = _database(tmp_path)
    conn = db_locking.db_connect(path)
    observed = []

    # _acquire_write_lane imports the service function dynamically.  Wrap it
    # there to record the connection state at the exact flock boundary.
    from src.core import database_write_service as service

    real_acquire = service.acquire_write_lease

    def recording_acquire(*args, **kwargs):
        observed.append(bool(conn.in_transaction))
        return real_acquire(*args, **kwargs)

    monkeypatch.setattr(service, "acquire_write_lease", recording_acquire)
    try:
        executor = conn.cursor() if via_cursor else conn
        executor.execute(statement)
        assert observed == [False]
        assert conn._holds_write_lock
        assert conn.in_transaction
    finally:
        conn.rollback()
        conn.close()


def test_failed_writer_begin_releases_application_lane(tmp_path):
    path = _database(tmp_path)
    native = db_locking._sqlite3_connect_orig(path, timeout=0.01)
    managed = db_locking.db_connect(path)
    try:
        native.execute("BEGIN IMMEDIATE")
        managed.execute("PRAGMA busy_timeout=1")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            managed.execute("BEGIN IMMEDIATE")
        assert not managed.in_transaction
        assert not managed._holds_write_lock
    finally:
        managed.close()
        native.rollback()
        native.close()


def test_repeated_begin_cycles_release_transaction_and_lane(tmp_path):
    path = _database(tmp_path)
    conn = db_locking.db_connect(path)
    try:
        for value in range(100):
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO events(value) VALUES (?)", (str(value),))
            conn.commit()
            assert not conn.in_transaction
            assert not conn._holds_write_lock
    finally:
        conn.close()


def test_close_is_an_early_return_rollback_and_lane_release(tmp_path):
    path = _database(tmp_path)
    conn = db_locking.db_connect(path)
    conn.cursor().execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO events(value) VALUES ('discard-me')")
    assert conn.in_transaction and conn._holds_write_lock

    conn.close()
    verify = db_locking._sqlite3_connect_orig(path)
    try:
        assert verify.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    finally:
        verify.close()
    assert not conn._holds_write_lock
