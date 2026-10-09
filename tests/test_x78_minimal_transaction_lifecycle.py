from __future__ import annotations

import json
import os
import sqlite3
import threading

from src.utils import wal_watchdog_provenance as provenance


def _read_events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def _setup(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE item (id INTEGER)")
    conn.commit()
    conn.close()


def _provenance(db, stream):
    return provenance.collect_wal_pin_provenance(
        db_path=str(db), checkpoint={}, holder_pids=[os.getpid()], lifecycle_path=str(stream)
    )["holders"][0]["connections"]


def test_deferred_read_transaction_is_active_then_clears_on_commit(tmp_path, monkeypatch):
    from src.utils import db_locking

    db, stream = tmp_path / "ops.db", tmp_path / "lifecycle.jsonl"
    _setup(db)
    monkeypatch.setenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH", str(stream))
    conn = db_locking.db_connect(str(db), read_only=True)
    try:
        conn.execute("BEGIN")
        conn.execute("SELECT * FROM item").fetchall()
        rows = _provenance(db, stream)
        assert len(rows) == 1
        assert rows[0]["transaction_active"] is True
        assert rows[0]["read_transaction_age_seconds"] is not None
        assert rows[0]["attribution"] == "ACTIVE_READ_TRANSACTION_CANDIDATE"
        conn.commit()
        assert _provenance(db, stream)[0]["transaction_active"] is False
    finally:
        conn.close()


def test_rollback_and_close_clear_active_state(tmp_path, monkeypatch):
    from src.utils import db_locking

    db, stream = tmp_path / "ops.db", tmp_path / "lifecycle.jsonl"
    _setup(db)
    monkeypatch.setenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH", str(stream))
    conn = db_locking.db_connect(str(db), read_only=True)
    conn.execute("BEGIN")
    conn.execute("SELECT * FROM item")
    conn.rollback()
    assert _provenance(db, stream)[0]["transaction_active"] is False
    conn.execute("BEGIN")
    conn.execute("SELECT * FROM item")
    conn.close()
    assert _provenance(db, stream) == []


def test_open_handle_is_not_a_wal_pin_and_stream_is_payload_free(tmp_path, monkeypatch):
    from src.utils import db_locking

    db, stream = tmp_path / "ops.db", tmp_path / "lifecycle.jsonl"
    _setup(db)
    monkeypatch.setenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH", str(stream))
    conn = db_locking.db_connect(str(db), read_only=True)
    try:
        row = _provenance(db, stream)[0]
        assert row["transaction_active"] is False
        assert row["attribution"] == "OPEN_DATABASE_HANDLE"
        assert "sql" not in stream.read_text().lower()
    finally:
        conn.close()


def test_connections_are_independently_attributed(tmp_path, monkeypatch):
    from src.utils import db_locking

    db, stream = tmp_path / "ops.db", tmp_path / "lifecycle.jsonl"
    _setup(db)
    monkeypatch.setenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH", str(stream))
    first = db_locking.db_connect(str(db), read_only=True)
    second = db_locking.db_connect(str(db), read_only=True)
    try:
        first.execute("BEGIN")
        first.execute("SELECT * FROM item")
        rows = _provenance(db, stream)
        assert sorted(row["transaction_active"] for row in rows) == [False, True]
    finally:
        first.close()
        second.close()


def test_transaction_lifecycle_does_not_change_write_lane_or_import_threads(tmp_path, monkeypatch):
    from src.utils import db_locking

    before_threads = {thread.ident for thread in threading.enumerate()}
    db, stream = tmp_path / "ops.db", tmp_path / "lifecycle.jsonl"
    _setup(db)
    monkeypatch.setenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH", str(stream))
    monkeypatch.setattr(db_locking, "_DB_WRITE_SERIALIZE", True)
    conn = db_locking.db_connect(str(db))
    try:
        conn.execute("INSERT INTO item VALUES (1)")
        assert getattr(conn, "_holds_write_lock", False) is True
        conn.rollback()
        assert getattr(conn, "_holds_write_lock", False) is False
    finally:
        conn.close()
    assert {thread.ident for thread in threading.enumerate()} == before_threads
    events = _read_events(stream)
    assert any(event["event"] == "sqlite_tx_begin" for event in events)
    assert any(event["event"] == "rollback_end" for event in events)
