"""X78.4 Phase 5: regression tests for creator_funding_worker's
_retry_on_nested_write helper -- the actual fix for the timeout/
cancellation-grace-period lease leak observed live during X78.3's
sanity window.

Context: a cancelled extraction task's underlying to_thread-dispatched
write (e.g. _save_outgoing_transfer, which acquires a write lease
internally) can still be genuinely running, holding the lease, when
_process_job's cancellation handling gives up. Two detection-based
designs were attempted and both DISPROVEN with direct reproduction
(see test_wrapper_finally_signal_is_unreliable below, and
tests/test_x78_4_cancellation_grace_period_reproduction.py):
asyncio.Task.done()/.cancelled() and a threading.Event set in the
extraction wrapper's own `finally` BOTH report "finished" the instant
CancelledError propagates through `await asyncio.to_thread(...)`,
independent of whether the real OS thread has actually stopped.

Since detection is provably impossible at this boundary without
instrumenting every to_thread call site inside the extractor (out of
scope), the fix implements isolation instead: every write-shaped call
this worker's own loop/job processor makes is wrapped in
_retry_on_nested_write, which retries with exponential backoff on
NestedDatabaseWriteError -- proven transient (bounded by
DB_WRITE_LOCK's own 60s acquire-timeout).

Note on WHERE NestedDatabaseWriteError actually raises: NOT from
_db_connect/db_connect (opening a connection or running PRAGMA never
acquires the write lease) but from the first write-shaped
.execute()/.executemany() call on a connection. So the retry wraps each
write helper's full call (open-write-close), not connection-opening
alone.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid

import pytest

import src.core.creator_funding_worker as cfw
from src.core.database_write_service import (
    NestedDatabaseWriteError,
    acquire_write_lease,
    release_write_lease,
    _thread_write_lease,
)


def _clear_thread_lease():
    if hasattr(_thread_write_lease, "owner"):
        del _thread_write_lease.owner


@pytest.fixture(autouse=True)
def _isolate():
    _clear_thread_lease()
    yield
    _clear_thread_lease()


def test_wrapper_finally_signal_is_unreliable():
    """Permanent regression documenting WHY a detection-based fix (Task
    state or a completion-signal set in the calling coroutine's own
    `finally`) cannot work: a coroutine's `finally` block runs the
    instant CancelledError propagates through
    `await asyncio.to_thread(...)`, regardless of whether the underlying
    OS thread has actually stopped. If this test ever starts failing
    (the finally now correctly waits for real completion), a detection-
    based fix could be reconsidered -- but as of this Python version's
    asyncio semantics, it cannot, which is why _retry_on_nested_write
    exists instead."""
    async def check():
        real_thread_done = threading.Event()
        finally_ran = threading.Event()

        def slow():
            time.sleep(0.1)
            real_thread_done.set()

        async def wrapper():
            try:
                await asyncio.to_thread(slow)
            finally:
                finally_ran.set()

        task = asyncio.ensure_future(wrapper())
        await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        result = (finally_ran.is_set(), real_thread_done.is_set())
        while not real_thread_done.is_set():
            await asyncio.sleep(0.01)
        return result

    finally_ran, real_thread_done = asyncio.run(check())
    assert finally_ran is True
    assert real_thread_done is False, (
        "if this assertion fails, asyncio's cancellation semantics for "
        "to_thread have changed -- re-evaluate whether a detection-based "
        "fix is now viable before assuming the retry-based fix is still "
        "the only option"
    )


def test_nested_write_error_is_raised_by_execute_not_by_connect(tmp_path):
    """Confirms exactly where the collision actually happens (relevant to
    where the retry must be placed): opening a connection never raises
    NestedDatabaseWriteError; the first write-shaped statement does."""
    db_path = str(tmp_path / "x.db")
    from src.utils.db_locking import db_connect

    acquire_write_lease(
        f"tracked:{os.path.realpath(db_path)}", db_path, str(uuid.uuid4()),
        "straggler",
    )

    # Opening a connection and running PRAGMAs (what db_connect() itself
    # does) must NOT raise.
    conn = db_connect(db_path, timeout=5)
    assert conn is not None

    # The first WRITE-shaped statement on that connection DOES raise.
    with pytest.raises(NestedDatabaseWriteError):
        conn.execute("CREATE TABLE IF NOT EXISTS t (a TEXT)")

    conn.close()


def test_retry_on_nested_write_succeeds_once_straggler_releases(tmp_path):
    """Core X78.4 regression: _retry_on_nested_write must retry (not
    immediately raise) when the wrapped callable hits
    NestedDatabaseWriteError, and succeed once the lease is genuinely
    released -- proving the transient collision is survivable without
    needing to detect readiness in advance."""
    db_path = str(tmp_path / "x.db")
    cfw._WRITE_RETRY_BASE_SECONDS = 0.01

    release_gate = threading.Event()

    def hold_lease_then_release():
        lease = acquire_write_lease(
            f"tracked:{os.path.realpath(db_path)}", db_path, str(uuid.uuid4()),
            "realtime_creator_funding_extractor.py:977 in _save_outgoing_transfer",
        )
        while not release_gate.is_set():
            time.sleep(0.01)
        release_write_lease(lease)

    holder = threading.Thread(target=hold_lease_then_release)
    holder.start()
    time.sleep(0.05)

    def release_after_delay():
        time.sleep(0.1)
        release_gate.set()

    releaser = threading.Thread(target=release_after_delay)
    releaser.start()

    def write_something():
        from src.utils.db_locking import db_connect
        conn = db_connect(db_path, timeout=5)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS t (a TEXT)")
            conn.commit()
        finally:
            conn.close()
        return "ok"

    result = cfw._retry_on_nested_write(write_something)
    assert result == "ok"

    holder.join()
    releaser.join()


def test_retry_on_nested_write_gives_up_after_max_attempts(tmp_path):
    """Non-regression: retry is bounded, not infinite -- a genuinely
    permanent leak must still eventually surface as an error rather than
    retrying forever."""
    db_path = str(tmp_path / "x.db")
    cfw._WRITE_RETRY_MAX_ATTEMPTS = 2
    cfw._WRITE_RETRY_BASE_SECONDS = 0.01

    # Held forever -- never released, models a genuine leak.
    acquire_write_lease(
        f"tracked:{os.path.realpath(db_path)}", db_path, str(uuid.uuid4()),
        "realtime_creator_funding_extractor.py:977 in _save_outgoing_transfer",
    )

    def write_something():
        from src.utils.db_locking import db_connect
        conn = db_connect(db_path, timeout=5)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS t (a TEXT)")
        finally:
            conn.close()

    with pytest.raises(NestedDatabaseWriteError):
        cfw._retry_on_nested_write(write_something)


def test_retry_on_nested_write_is_noop_when_no_collision(tmp_path):
    """Non-regression: the happy path (no collision at all) must not be
    slowed down or altered by the retry wrapper."""
    db_path = str(tmp_path / "x.db")

    def write_something():
        from src.utils.db_locking import db_connect
        conn = db_connect(db_path, timeout=5)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS t (a TEXT)")
            conn.commit()
        finally:
            conn.close()
        return "ok"

    start = time.time()
    result = cfw._retry_on_nested_write(write_something)
    elapsed = time.time() - start
    assert result == "ok"
    assert elapsed < 0.5
