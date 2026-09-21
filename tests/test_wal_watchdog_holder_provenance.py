from __future__ import annotations

import json

from src.utils import wal_watchdog_provenance as provenance


class _Result:
    def __init__(self, stdout: str):
        self.stdout = stdout


def test_collects_command_connection_and_transaction_age(tmp_path, monkeypatch):
    stream = tmp_path / "lifecycle.jsonl"
    db = str(tmp_path / "live.db")
    events = [
        {"event": "open", "pid": 72923, "connection_id": "c1", "timestamp": 90.0,
         "path": db, "caller": "reader.py:10 in scan", "purpose": "cache", "mode": "read_only"},
        {"event": "sqlite_tx_begin", "pid": 72923, "connection_id": "c1", "timestamp": 95.0},
    ]
    stream.write_text("".join(json.dumps(row) + "\n" for row in events))
    monkeypatch.setattr(provenance.subprocess, "run", lambda *a, **k: _Result("python -m retained_reader\n"))

    result = provenance.collect_wal_pin_provenance(
        db_path=db,
        checkpoint={"busy": 1, "log_frames": 22223, "checkpointed_frames": 397},
        holder_pids=[72923], lifecycle_path=str(stream), now=100.0,
    )

    assert result["checkpoint"] == {"busy": 1, "log_frames": 22223, "checkpointed_frames": 397}
    holder = result["holders"][0]
    assert holder["command"] == "python -m retained_reader"
    assert holder["connection_provenance_available"] is True
    assert holder["connections"][0]["connection_age_seconds"] == 10.0
    assert holder["connections"][0]["read_transaction_age_seconds"] == 5.0
    assert holder["connections"][0]["transaction_active"] is True


def test_commit_close_and_wrong_database_fail_closed(tmp_path, monkeypatch):
    stream = tmp_path / "lifecycle.jsonl"
    db = str(tmp_path / "live.db")
    rows = [
        {"event": "open", "pid": 7, "connection_id": "closed", "timestamp": 1, "path": db},
        {"event": "sqlite_tx_begin", "pid": 7, "connection_id": "closed", "timestamp": 2},
        {"event": "commit_end", "pid": 7, "connection_id": "closed", "timestamp": 3},
        {"event": "close", "pid": 7, "connection_id": "closed", "timestamp": 4},
        {"event": "open", "pid": 7, "connection_id": "other", "timestamp": 1,
         "path": str(tmp_path / "other.db")},
    ]
    stream.write_text("bad-json\n" + "".join(json.dumps(row) + "\n" for row in rows))
    monkeypatch.setattr(provenance.subprocess, "run", lambda *a, **k: _Result("worker\n"))
    result = provenance.collect_wal_pin_provenance(
        db_path=db, checkpoint={}, holder_pids=[7], lifecycle_path=str(stream), now=10,
    )
    assert result["holders"][0]["connections"] == []
    assert result["holders"][0]["connection_provenance_available"] is False


def test_snapshot_is_bounded_and_payload_free(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance.subprocess, "run", lambda *a, **k: _Result("x" * 1000))
    result = provenance.collect_wal_pin_provenance(
        db_path=str(tmp_path / "live.db"), checkpoint={},
        holder_pids=range(1, 100), lifecycle_path=None, now=10,
    )
    assert result["holder_count"] == provenance.MAX_HOLDERS
    assert all(len(row["command"]) <= provenance.MAX_FIELD for row in result["holders"])
    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value)) if value else set()
        return set()

    assert "sql" not in keys(result)
    assert "parameters" not in keys(result)


def test_worker_threshold_contracts_are_unchanged():
    from src.core import creator_funding_worker as funding
    from src.core import creator_resolution_worker as resolution

    assert funding._wal_is_critically_pinned(63.9, 99) is False
    assert funding._wal_is_critically_pinned(64.0, 2) is False
    assert funding._wal_is_critically_pinned(64.0, 3) is True
    assert resolution._wal_is_critically_pinned(63.9, 99) is False
    assert resolution._wal_is_critically_pinned(64.0, 2) is False
    assert resolution._wal_is_critically_pinned(64.0, 3) is True


def test_tracked_deferred_read_transaction_is_visible(tmp_path, monkeypatch):
    from src.utils import db_locking

    db = str(tmp_path / "live.db")
    stream = str(tmp_path / "lifecycle.jsonl")
    monkeypatch.setenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH", stream)
    monkeypatch.setattr(provenance, "_process_commands", lambda pids: {pid: "test" for pid in pids})
    conn = db_locking.db_connect(db)
    try:
        conn.execute("CREATE TABLE item (id INTEGER)")
        conn.commit()
        conn.execute("BEGIN")
        conn.execute("SELECT * FROM item").fetchall()
        result = provenance.collect_wal_pin_provenance(
            db_path=db, checkpoint={}, holder_pids=[__import__("os").getpid()],
            lifecycle_path=stream,
        )
        rows = result["holders"][0]["connections"]
        assert len(rows) == 1
        assert rows[0]["transaction_active"] is True
        assert rows[0]["read_transaction_age_seconds"] is not None
    finally:
        conn.rollback()
        conn.close()
    events = [__import__("json").loads(line) for line in (tmp_path / "lifecycle.jsonl").read_text().splitlines()]
    lifecycle = [event["event"] for event in events]
    assert lifecycle.count("sqlite_tx_begin") == 1
    assert lifecycle.count("commit_end") == 0
    assert lifecycle.count("rollback_end") == 1
    assert lifecycle[-1] == "close"
    assert all("sql" not in event and "parameters" not in event for event in events)
