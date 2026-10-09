from __future__ import annotations

import json
import os
import sqlite3
import threading
import inspect

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


def test_checkpoint_gap_classification_is_conservative(tmp_path, monkeypatch):
    db = str(tmp_path / "ops.db")
    stream = tmp_path / "lifecycle.jsonl"
    stream.write_text("\n".join([
        json.dumps({"event": "open", "pid": 7, "connection_id": "read", "timestamp": 1,
                    "path": db, "mode": "read_only"}),
        json.dumps({"event": "sqlite_tx_begin", "pid": 7, "connection_id": "read", "timestamp": 2}),
    ]) + "\n")
    monkeypatch.setattr(provenance, "_process_commands", lambda _pids: {7: "reader"})
    candidate = provenance.collect_wal_pin_provenance(
        db_path=db, checkpoint={"busy": 0, "log_frames": 900, "checkpointed_frames": 10},
        holder_pids=[7], lifecycle_path=str(stream), now=100, checkpoint_stalled=True,
    )
    assert candidate["holders"][0]["connections"][0]["attribution"] == "LONG_LIVED_READER_PIN_CANDIDATE"
    assert candidate["checkpoint_obstruction"] == "CHECKPOINT_GAP_STALLED_WITH_LONG_LIVED_READER_CANDIDATE"
    unknown = provenance.collect_wal_pin_provenance(
        db_path=db, checkpoint={"busy": 0, "log_frames": 900, "checkpointed_frames": 10},
        holder_pids=[], lifecycle_path=None, now=100, checkpoint_stalled=True,
    )
    assert unknown["checkpoint_obstruction"] == "UNKNOWN_OBSTRUCTION"
    writer = provenance.collect_wal_pin_provenance(
        db_path=db, checkpoint={"busy": 1, "log_frames": 900, "checkpointed_frames": 10},
        holder_pids=[], lifecycle_path=None, now=100,
    )
    assert writer["checkpoint_obstruction"] == "WRITER_OR_CHECKPOINT_OBSTRUCTION"


def test_both_existing_wal_watchdogs_compose_parser_without_new_threads():
    from src.core import creator_funding_worker as funding
    from src.core import creator_resolution_worker as resolution

    for worker in (funding, resolution):
        source = inspect.getsource(worker._wal_watchdog)
        assert "collect_wal_pin_provenance" in source
        assert "wal_pin_provenance=" in source
        assert "threading.Thread" not in source
    assert funding._wal_is_critically_pinned(63.9, 99) is False
    assert funding._wal_is_critically_pinned(64.0, 2) is False
    assert funding._wal_is_critically_pinned(64.0, 3) is True
    assert resolution._wal_is_critically_pinned(63.9, 99) is False
    assert resolution._wal_is_critically_pinned(64.0, 2) is False
    assert resolution._wal_is_critically_pinned(64.0, 3) is True
