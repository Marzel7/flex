from __future__ import annotations

import inspect
import json
import os
import sqlite3
import threading

from src.utils import wal_watchdog_provenance as provenance


def _setup(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE item (id INTEGER)")
    conn.commit()
    conn.close()


def _rows(db, stream):
    result = provenance.collect_wal_pin_provenance(
        db_path=str(db), checkpoint={}, holder_pids=[os.getpid()], lifecycle_path=str(stream),
    )
    return result["holders"][0]["connections"]


def test_eed_deferred_read_regression_transaction_active_is_true(tmp_path, monkeypatch):
    """Regression for the prior lifecycle-parser transaction_active=false result."""
    from src.utils import db_locking

    db, stream = tmp_path / "ops.db", tmp_path / "lifecycle.jsonl"
    _setup(db)
    monkeypatch.setenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH", str(stream))
    conn = db_locking.db_connect(str(db), read_only=True)
    try:
        conn.execute("BEGIN")
        conn.execute("SELECT * FROM item").fetchall()
        row = _rows(db, stream)[0]
        assert row["transaction_active"] is True
        assert row["attribution"] == "ACTIVE_READ_TRANSACTION_CANDIDATE"
        conn.commit()
        assert _rows(db, stream)[0]["transaction_active"] is False
    finally:
        conn.close()


def test_rollback_close_and_independent_connections_clear_state(tmp_path, monkeypatch):
    from src.utils import db_locking

    db, stream = tmp_path / "ops.db", tmp_path / "lifecycle.jsonl"
    _setup(db)
    monkeypatch.setenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH", str(stream))
    first, second = db_locking.db_connect(str(db), read_only=True), db_locking.db_connect(str(db), read_only=True)
    second_closed = False
    try:
        first.execute("BEGIN")
        first.execute("SELECT * FROM item")
        assert sorted(row["transaction_active"] for row in _rows(db, stream)) == [False, True]
        first.rollback()
        assert sorted(row["transaction_active"] for row in _rows(db, stream)) == [False, False]
        second.execute("BEGIN")
        second.execute("SELECT * FROM item")
        second.close()
        second_closed = True
        assert len(_rows(db, stream)) == 1
    finally:
        first.close()
        if not second_closed:
            second.close()


def test_open_handle_and_checkpoint_gap_are_conservative(tmp_path, monkeypatch):
    db = str(tmp_path / "ops.db")
    stream = tmp_path / "lifecycle.jsonl"
    stream.write_text("\n".join([
        json.dumps({"event": "open", "pid": 7, "connection_id": "reader", "timestamp": 1,
                    "path": db, "mode": "read_only"}),
        json.dumps({"event": "sqlite_tx_begin", "pid": 7, "connection_id": "reader", "timestamp": 2}),
    ]) + "\n")
    monkeypatch.setattr(provenance, "_process_commands", lambda _pids: {7: "reader"})
    result = provenance.collect_wal_pin_provenance(
        db_path=db, checkpoint={"busy": 0, "log_frames": 90, "checkpointed_frames": 10},
        holder_pids=[7], lifecycle_path=str(stream), now=100, checkpoint_stalled=True,
    )
    assert result["holders"][0]["connections"][0]["attribution"] == "LONG_LIVED_READER_PIN_CANDIDATE"
    assert result["checkpoint_obstruction"] == "CHECKPOINT_GAP_STALLED_WITH_LONG_LIVED_READER_CANDIDATE"
    unknown = provenance.collect_wal_pin_provenance(
        db_path=db, checkpoint={"busy": 0, "log_frames": 90, "checkpointed_frames": 10},
        holder_pids=[], checkpoint_stalled=True,
    )
    assert unknown["checkpoint_obstruction"] == "UNKNOWN_OBSTRUCTION"
    writer = provenance.collect_wal_pin_provenance(
        db_path=db, checkpoint={"busy": 1, "log_frames": 90, "checkpointed_frames": 10}, holder_pids=[],
    )
    assert writer["checkpoint_obstruction"] == "WRITER_OR_CHECKPOINT_OBSTRUCTION"


def test_existing_watchdogs_and_write_lane_are_preserved(tmp_path, monkeypatch):
    from src.core import creator_funding_worker as funding
    from src.core import creator_resolution_worker as resolution
    from src.utils import db_locking

    before = {thread.ident for thread in threading.enumerate()}
    for worker in (funding, resolution):
        source = inspect.getsource(worker._wal_watchdog)
        assert "collect_wal_pin_provenance" in source
        assert "threading.Thread" not in source
    db = tmp_path / "ops.db"
    _setup(db)
    monkeypatch.setattr(db_locking, "_DB_WRITE_SERIALIZE", True)
    conn = db_locking.db_connect(str(db))
    try:
        conn.execute("INSERT INTO item VALUES (1)")
        assert conn._holds_write_lock is True
        conn.rollback()
        assert conn._holds_write_lock is False
    finally:
        conn.close()
    assert {thread.ident for thread in threading.enumerate()} == before
