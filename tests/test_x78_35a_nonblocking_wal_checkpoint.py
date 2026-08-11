import sqlite3
import threading
import time

from src.core import pumpfun_curve_listener as listener


def _wal_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, value TEXT)")
    conn.commit()
    return conn


def test_routine_checkpoint_is_passive_zero_wait_and_outside_tracked_lane(tmp_path, monkeypatch):
    db_path = str(tmp_path / "routine.db")
    conn = _wal_db(db_path)
    conn.execute("INSERT INTO events(value) VALUES ('one')")
    conn.commit()
    conn.close()

    def tracked_connection_must_not_be_used(*args, **kwargs):
        raise AssertionError("routine checkpoint entered the application DB wrapper")

    monkeypatch.setattr(listener, "db_connect", tracked_connection_must_not_be_used)
    result = listener._run_routine_wal_checkpoint(db_path)

    assert result["mode"] == "PASSIVE"
    assert result["reason"] == "routine"
    assert result["application_write_lease_held"] is False
    assert result["application_write_lease_max_ms"] == 0.0
    assert result["duration_ms"] < 1_000
    assert result["status"] in {"ok", "busy"}


def test_reader_blocked_routine_checkpoint_does_not_block_unrelated_writer(tmp_path):
    db_path = str(tmp_path / "reader.db")
    seed = _wal_db(db_path)
    seed.execute("INSERT INTO events(value) VALUES ('before-reader')")
    seed.commit()

    reader = sqlite3.connect(db_path)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM events").fetchall()
    seed.execute("INSERT INTO events(value) VALUES ('after-reader')")
    seed.commit()

    started = time.monotonic()
    result = listener._run_routine_wal_checkpoint(db_path)
    checkpoint_elapsed = time.monotonic() - started

    writer = sqlite3.connect(db_path, timeout=1)
    writer.execute("INSERT INTO events(value) VALUES ('writer-progressed')")
    writer.commit()
    writer.close()
    reader.close()
    seed.close()

    assert checkpoint_elapsed < 1.0
    assert result["mode"] == "PASSIVE"
    assert result["remaining_frames"] >= 0


def test_checkpoint_failure_closes_connection_and_never_claims_write_lease(monkeypatch):
    class BrokenConnection:
        closed = False

        def execute(self, sql):
            if "wal_checkpoint" in sql:
                raise sqlite3.OperationalError("synthetic checkpoint failure")
            return self

        def close(self):
            self.closed = True

    broken = BrokenConnection()
    monkeypatch.setattr(listener.sqlite3, "connect", lambda *a, **k: broken)

    result = listener._run_routine_wal_checkpoint("/does/not/matter.db")

    assert broken.closed is True
    assert result["status"] == "failed"
    assert result["application_write_lease_held"] is False


def test_worker_shutdown_does_not_leak_checkpoint_thread(tmp_path):
    db_path = str(tmp_path / "shutdown.db")
    _wal_db(db_path).close()
    stop = threading.Event()
    worker = listener._start_wal_checkpoint_worker(db_path, interval_seconds=0.01, stop_event=stop)
    time.sleep(0.04)
    stop.set()
    worker.join(timeout=1)
    assert not worker.is_alive()


def test_heavy_threshold_checkpoint_remains_bounded_and_observable():
    from src.utils import db_locking

    assert db_locking._WAL_SIZE_THRESHOLD == 32 * 1024 * 1024
    assert db_locking._WAL_WATCHDOG_INTERVAL == 30
    constants = db_locking._wal_watchdog_loop.__code__.co_consts
    assert any("wal_checkpoint(TRUNCATE)" in str(value) for value in constants)
    assert any("checkpoint result=" in str(value) for value in constants)


def test_listener_has_no_routine_restart_checkpoint():
    source = listener.__file__
    with open(source, "r", encoding="utf-8") as handle:
        listener_source = handle.read()
    assert "wal_checkpoint(RESTART)" not in listener_source
