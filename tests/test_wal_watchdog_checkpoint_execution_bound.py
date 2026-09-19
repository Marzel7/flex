import os
import sqlite3
import time

from src.utils import db_locking


def _seed_reader_pinned_wal(path):
    writer = db_locking._sqlite3_connect_orig(path, timeout=1)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, value TEXT)")
    writer.execute("INSERT INTO events(value) VALUES ('before-reader')")
    writer.commit()

    reader = db_locking._sqlite3_connect_orig(path, timeout=1)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM events").fetchall()

    writer.execute("INSERT INTO events(value) VALUES ('after-reader')")
    writer.commit()
    writer.close()
    return reader


def test_watchdog_overrides_wrapped_connection_busy_timeout_before_lane(monkeypatch):
    events = []

    class Result:
        def fetchone(self):
            events.append("fetchone")
            return (1, 2, 1)

    class Connection:
        def execute(self, sql):
            events.append(sql)
            return Result()

        def _acquire_write_lane(self):
            events.append("acquire")

        def _release_write_lane(self):
            events.append("release")

        def close(self):
            events.append("close")

    monkeypatch.setattr(db_locking.sqlite3, "connect", lambda *args, **kwargs: Connection())

    assert db_locking._run_wal_watchdog_checkpoint("ignored.db") == (1, 2, 1)
    assert events == [
        "PRAGMA busy_timeout=250",
        "acquire",
        "PRAGMA wal_checkpoint(TRUNCATE)",
        "fetchone",
        "release",
        "close",
    ]


def test_reader_pinned_truncate_releases_writer_lane_within_maintenance_budget(tmp_path, monkeypatch):
    db_path = str(tmp_path / "pinned.db")
    monkeypatch.setattr(db_locking, "_OPS_DB_ABS", os.path.realpath(db_path))
    reader = _seed_reader_pinned_wal(db_path)
    started = time.monotonic()
    try:
        result = db_locking._run_wal_watchdog_checkpoint(db_path)
        elapsed = time.monotonic() - started

        assert result[0] == 1  # SQLite reports BUSY instead of waiting indefinitely.
        assert elapsed < 1.0

        writer = db_locking._sqlite3_connect_orig(db_path, timeout=1)
        writer.execute("INSERT INTO events(value) VALUES ('writer-progressed')")
        writer.commit()
        writer.close()
    finally:
        reader.close()


def test_checkpoint_exception_still_releases_lane_and_closes(monkeypatch):
    events = []

    class Connection:
        def execute(self, sql):
            events.append(sql)
            if "wal_checkpoint" in sql:
                raise sqlite3.OperationalError("synthetic checkpoint failure")
            return self

        def _acquire_write_lane(self):
            events.append("acquire")

        def _release_write_lane(self):
            events.append("release")

        def close(self):
            events.append("close")

    monkeypatch.setattr(db_locking.sqlite3, "connect", lambda *args, **kwargs: Connection())

    try:
        db_locking._run_wal_watchdog_checkpoint("ignored.db")
    except sqlite3.OperationalError as exc:
        assert "synthetic" in str(exc)
    else:
        raise AssertionError("checkpoint failure did not propagate")

    assert events[-2:] == ["release", "close"]
