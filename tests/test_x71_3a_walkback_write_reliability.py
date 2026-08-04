"""X71.3A — regression coverage for the walkback_worker nested-write deadlock.

Root cause: acquire_write_lease() guards re-entrancy with a thread-local
(_thread_write_lease.owner). release_write_lease() must clear that guard
unconditionally; before this fix, an OSError raised inside release_write_lease
(unlink/flock/close) propagated out before the thread-local was cleared,
permanently wedging every later write on that thread behind
NestedDatabaseWriteError -- observed as walkback_worker holding a lease for
~15.8h before its outer loop finally errored out and the process died.
"""
import fcntl
import os
import threading

import pytest

from src.core.database_write_service import (
    NestedDatabaseWriteError,
    WriteLease,
    _thread_write_lease,
    acquire_write_lease,
    release_write_lease,
)


@pytest.fixture(autouse=True)
def _clear_thread_local():
    # Isolate each test from leakage across the shared thread-local guard.
    if hasattr(_thread_write_lease, "owner"):
        del _thread_write_lease.owner
    yield
    if hasattr(_thread_write_lease, "owner"):
        del _thread_write_lease.owner


def test_release_clears_guard_even_when_unlink_raises(tmp_path, monkeypatch):
    db_path = str(tmp_path / "ops.db")
    lease = acquire_write_lease("tracked:" + db_path, db_path, "tx-1", "test:acquire")

    def _boom(_path):
        raise OSError("simulated unlink failure")

    monkeypatch.setattr(os, "unlink", _boom)
    with pytest.raises(OSError):
        release_write_lease(lease)

    # The thread-local reentrancy guard must be gone despite the OSError above --
    # this is the exact condition that wedged walkback_worker permanently.
    assert not hasattr(_thread_write_lease, "owner")

    monkeypatch.undo()
    # A fresh acquisition on this thread must succeed, not raise Nested.
    lease2 = acquire_write_lease("tracked:" + db_path, db_path, "tx-2", "test:acquire-2")
    release_write_lease(lease2)


def test_release_clears_guard_even_when_flock_raises(tmp_path, monkeypatch):
    db_path = str(tmp_path / "ops.db")
    lease = acquire_write_lease("tracked:" + db_path, db_path, "tx-1", "test:acquire")

    def _boom(_fd, _op):
        raise OSError("simulated flock failure")

    monkeypatch.setattr(fcntl, "flock", _boom)
    with pytest.raises(OSError):
        release_write_lease(lease)

    assert not hasattr(_thread_write_lease, "owner")

    monkeypatch.undo()
    lease2 = acquire_write_lease("tracked:" + db_path, db_path, "tx-2", "test:acquire-2")
    release_write_lease(lease2)


def test_nested_acquire_still_raises_while_lease_genuinely_held(tmp_path):
    db_path = str(tmp_path / "ops.db")
    lease = acquire_write_lease("tracked:" + db_path, db_path, "tx-1", "outer")
    try:
        with pytest.raises(NestedDatabaseWriteError):
            acquire_write_lease("tracked:" + db_path, db_path, "tx-2", "inner")
    finally:
        release_write_lease(lease)

    # And after a clean release, acquisition works again.
    lease2 = acquire_write_lease("tracked:" + db_path, db_path, "tx-3", "after-release")
    release_write_lease(lease2)


def test_tracked_connection_release_write_lane_survives_release_failure(tmp_path, monkeypatch):
    from src.utils.db_locking import TrackedConnection, _DB_WRITE_LOCK
    import src.utils.db_locking as db_locking

    monkeypatch.setattr(db_locking, "_DB_WRITE_SERIALIZE", True)
    db_path = str(tmp_path / "ops.db")
    conn = db_locking.db_connect(db_path, timeout=5)
    conn.execute("CREATE TABLE t(x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")

    def _boom(_lease):
        raise OSError("simulated release_write_lease failure")

    monkeypatch.setattr(
        "src.core.database_write_service.release_write_lease", _boom
    )
    # commit() surfaces the underlying failure (a real OSError should not be
    # swallowed), but must not leave _holds_write_lock True nor the process
    # lock held afterward -- that's the actual leak this fix closes.
    with pytest.raises(OSError):
        conn.commit()
    assert getattr(conn, "_holds_write_lock", False) is False
    # The global write lock must be acquirable again (proves it wasn't leaked).
    acquired = _DB_WRITE_LOCK.acquire(timeout=1)
    assert acquired
    _DB_WRITE_LOCK.release()

    monkeypatch.undo()
    conn.close()
