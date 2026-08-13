"""B2AD: the reaper may request, never perform, foreign-thread cleanup."""
import sqlite3
import threading
import time

from src.utils import db_locking


def test_foreign_reaper_marks_once_and_owner_closes(tmp_path, monkeypatch):
    db_path = str(tmp_path / "flex.db")
    sqlite3.connect(db_path).close()
    ready, release = threading.Event(), threading.Event()
    shared = {}

    def owner():
        conn = db_locking.db_connect(db_path)
        shared["conn"] = conn
        shared["tracking_id"] = conn._db_tracking_id
        ready.set()
        release.wait(5)
        conn.close()

    thread = threading.Thread(target=owner, name="owner-thread")
    thread.start()
    assert ready.wait(5)
    with db_locking._open_connections_lock:
        db_locking._open_connections[shared["tracking_id"]]["opened_at"] = time.time() - 100
    monkeypatch.setattr(db_locking, "_MAX_CONNECTION_AGE_SECS", 0)

    assert db_locking._reap_stale_connections() == 0
    record = db_locking._open_connections[shared["tracking_id"]]
    assert record["native_close_state"] == "OWNER_THREAD_CLEANUP_REQUIRED"
    # The connection remains usable by its owner; a foreign close would have
    # raised sqlite3.ProgrammingError instead.
    assert shared["conn"]._db_owner_thread_id != threading.get_ident()

    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert shared["tracking_id"] not in db_locking._open_connections
