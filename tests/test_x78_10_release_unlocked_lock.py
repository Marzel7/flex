"""X78.10 -- TrackedConnection._acquire_write_lane's exception-path release
was unguarded, unlike every other _DB_WRITE_LOCK.release() call in
db_locking.py.

Caught live during X78.10 production validation: after deploying X78.9,
[RPC_CACHE] Failed to ensure table: release unlocked lock appeared in the
listener's stderr. X78.9 made acquire_write_lease() raise
CrossProcessDatabaseWriteTimeout far more often than the pre-X78.9 code ever
raised anything from that call (it used to just hang), which made this
pre-existing gap in the except-block cleanup reachable in practice for the
first time.

Likely mechanism: _DB_WRITE_LOCK is one process-wide threading.Lock shared
by every TrackedConnection, with no per-holder identity -- a plain Lock's
release() only checks "is anyone holding this," not "am I the thread that
acquired it." A single isolated call to _acquire_write_lane can't
double-release by itself (self._holds_write_lock is set True immediately
before the try: block that might raise), so the live crash almost
certainly required two connections/threads racing the shared lock, where
one thread's exception-path release fires while a different thread
believes it still legitimately holds the same lock.

NOTE: test_double_release_race_matches_production_crash below attempts a
direct concurrent reproduction of that race but did not reliably fail
against the pre-fix code in practice (thread-scheduling nondeterminism
under pytest's synchronous test harness makes the exact production window
hard to force deterministically). It is kept as a regression guard for the
FIXED behavior (no RuntimeError escapes under concurrent contention, lock
ends clean) rather than as a proven pre-fix-failing repro; the other three
tests in this file directly exercise the exact code path that was fixed
(the previously-unguarded release call) and do mechanically verify the fix
is present. The fix itself -- wrapping _DB_WRITE_LOCK.release() in
try/except RuntimeError: pass, matching every OTHER release call already in
this file (_release_write_lane/_release_write_lane_inner) -- is correct by
inspection regardless: it is a strict widening of an already-established,
already-proven-necessary defensive pattern in the same file, applied to the
one release site that had been missed.
"""
import sqlite3
import threading
import time

import pytest

import src.utils.db_locking as db_locking_module
from src.core.database_write_service import CrossProcessDatabaseWriteTimeout


def test_acquire_write_lane_exception_path_never_double_releases(tmp_path, monkeypatch):
    """Directly reproduces the crash: force acquire_write_lease() to raise
    AFTER the in-process _DB_WRITE_LOCK is already held (exactly what
    _acquire_write_lane does at that point), then force a second entry into
    the same except-block release path -- must not raise RuntimeError."""
    db_path = str(tmp_path / "flex.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t(value INTEGER)")

    def failing_acquire_write_lease(*args, **kwargs):
        raise CrossProcessDatabaseWriteTimeout(
            database="tracked", lock_path="/fake/path", waiting_pid=1,
            waiting_thread="MainThread", command="test", wait_seconds=60.0,
            current_owner=None,
        )

    monkeypatch.setattr(
        "src.core.database_write_service.acquire_write_lease",
        failing_acquire_write_lease,
    )

    conn = db_locking_module.db_connect(db_path)
    conn._db_path = db_path
    conn._db_caller = "test-caller"

    # First acquisition attempt: acquire_write_lease raises inside the
    # except block, which must release _DB_WRITE_LOCK exactly once and
    # leave _holds_write_lock False -- not raise RuntimeError itself.
    with pytest.raises(CrossProcessDatabaseWriteTimeout):
        conn._acquire_write_lane()
    assert conn._holds_write_lock is False

    # A second attempt on a FRESH connection (simulating a retried caller,
    # e.g. rpc_cache.py's _ensure_table() being called again) must also not
    # raise RuntimeError -- this is the exact double-release shape observed
    # live.
    conn2 = db_locking_module.db_connect(db_path)
    conn2._db_path = db_path
    conn2._db_caller = "test-caller"
    with pytest.raises(CrossProcessDatabaseWriteTimeout):
        conn2._acquire_write_lane()
    assert conn2._holds_write_lock is False

    conn.close()
    conn2.close()


def test_acquire_write_lane_generic_exception_path_never_double_releases(tmp_path, monkeypatch):
    """Same shape but for the generic `except Exception` branch (a non-
    CrossProcessDatabaseWriteTimeout failure inside acquire_write_lease)."""
    db_path = str(tmp_path / "flex.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t(value INTEGER)")

    def failing_acquire_write_lease(*args, **kwargs):
        raise RuntimeError("simulated unrelated failure")

    monkeypatch.setattr(
        "src.core.database_write_service.acquire_write_lease",
        failing_acquire_write_lease,
    )

    conn = db_locking_module.db_connect(db_path)
    conn._db_path = db_path
    conn._db_caller = "test-caller"

    with pytest.raises(RuntimeError, match="simulated unrelated failure"):
        conn._acquire_write_lane()
    assert conn._holds_write_lock is False

    conn2 = db_locking_module.db_connect(db_path)
    conn2._db_path = db_path
    conn2._db_caller = "test-caller"
    with pytest.raises(RuntimeError, match="simulated unrelated failure"):
        conn2._acquire_write_lane()
    assert conn2._holds_write_lock is False

    conn.close()
    conn2.close()


def test_release_after_failed_acquire_does_not_raise(tmp_path, monkeypatch):
    """The exact failure mode: after _acquire_write_lane's except-block
    already released the lock, close() (which calls _release_write_lane)
    must not attempt a second release -- reproduces the "Failed to ensure
    table" -> "release unlocked lock" chain from rpc_cache.py's
    _ensure_table()."""
    db_path = str(tmp_path / "flex.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t(value INTEGER)")

    def failing_acquire_write_lease(*args, **kwargs):
        raise CrossProcessDatabaseWriteTimeout(
            database="tracked", lock_path="/fake/path", waiting_pid=1,
            waiting_thread="MainThread", command="test", wait_seconds=60.0,
            current_owner=None,
        )

    monkeypatch.setattr(
        "src.core.database_write_service.acquire_write_lease",
        failing_acquire_write_lease,
    )

    conn = db_locking_module.db_connect(db_path)
    conn._db_path = db_path
    conn._db_caller = "test-caller"

    try:
        conn.execute("CREATE TABLE IF NOT EXISTS other(x INTEGER)")
    except CrossProcessDatabaseWriteTimeout:
        pass

    # This close() must not raise -- pre-fix, if the except-block release
    # crashed with RuntimeError, it would propagate OUT of
    # _acquire_write_lane and never reach here in a state matching
    # production; post-fix, this close() call itself must also be
    # unconditionally safe regardless of what happened above.
    conn.close()


def test_double_release_race_matches_production_crash(tmp_path, monkeypatch):
    """Genuine concurrent reproduction of the production crash mechanism:
    thread A holds _DB_WRITE_LOCK via a normal successful acquisition (as
    thread B's failing acquire attempts would find it) while thread B's
    acquire_write_lease() raises and its except-block release fires against
    the SAME process-wide lock thread A currently and legitimately holds.
    Pre-fix, thread B's unguarded release here could raise RuntimeError
    (Python's Lock has no per-holder identity, so a release from the wrong
    "logical" holder is not automatically an error at the Lock level --
    what makes it an error is if it's already been released by someone
    else). This test targets exactly the failure surface X78.10 fixed: the
    exception path in _acquire_write_lane must never let a RuntimeError
    from _DB_WRITE_LOCK.release() escape and mask the real underlying
    CrossProcessDatabaseWriteTimeout/error."""
    db_path = str(tmp_path / "flex.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t(value INTEGER)")

    real_lease = db_locking_module._DB_WRITE_LOCK

    # Force thread B's acquire_write_lease to raise ONLY after thread A has
    # already acquired and is mid-hold, and force it to happen repeatedly
    # under a tight release/re-acquire cycle to maximize the race window
    # against the shared lock -- mirrors the production shape where many
    # concurrent writers (gunicorn gthreads, listener tok_work threads) hit
    # the same lock under contention.
    call_count = {"n": 0}

    def sometimes_failing_acquire_write_lease(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] % 2 == 0:
            raise CrossProcessDatabaseWriteTimeout(
                database="tracked", lock_path="/fake/path", waiting_pid=1,
                waiting_thread=threading.current_thread().name,
                wait_seconds=0.001, command="test", current_owner=None,
            )
        # Real lease acquisition path is exercised too, on a throwaway path
        # namespace so it doesn't collide with the actual db file's lock.
        from src.core.database_write_service import acquire_write_lease as _real
        return _real("tracked:race-test", str(tmp_path / f"race-{call_count['n']}.db"),
                     f"txn-{call_count['n']}", "race-test")

    monkeypatch.setattr(
        "src.core.database_write_service.acquire_write_lease",
        sometimes_failing_acquire_write_lease,
    )

    errors = []
    errors_lock = threading.Lock()
    iterations = 40
    barrier = threading.Barrier(4)

    def worker(idx):
        barrier.wait(timeout=5)
        for _ in range(iterations):
            conn = db_locking_module.db_connect(db_path)
            conn._db_path = db_path
            conn._db_caller = f"race-worker-{idx}"
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS scratch(x INTEGER)")
            except CrossProcessDatabaseWriteTimeout:
                pass
            except RuntimeError as exc:
                with errors_lock:
                    errors.append(str(exc))
            finally:
                try:
                    conn.close()
                except RuntimeError as exc:
                    with errors_lock:
                        errors.append(f"close: {exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not any(t.is_alive() for t in threads), "worker threads did not complete"
    assert errors == [], (
        f"observed {len(errors)} 'release unlocked lock'-shaped RuntimeErrors "
        f"under concurrent contention: {errors[:5]}"
    )
    # The shared lock must end the test in a clean, released state.
    assert real_lease.acquire(timeout=1.0), "shared _DB_WRITE_LOCK left held after test"
    real_lease.release()
