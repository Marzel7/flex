"""X78.4 Phase 1: deterministic reproduction of the timeout/cancellation-
grace-period lease leak observed live during X78.3's post-restart sanity
window.

Root cause (proven live + here): asyncio.CancelledError cannot interrupt
work already running inside asyncio.to_thread()'s underlying OS thread --
cancellation only takes effect at the calling coroutine's own await
point. creator_funding_worker._process_job's timeout/cancellation path
(the b779689/X78.0 fix) does:

    _extraction_task.cancel()
    try:
        await asyncio.wait_for(_extraction_task, timeout=EXTRACTION_CANCEL_GRACE_SECONDS)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        log(...); proceed anyway

If, at the moment of cancellation, the extraction coroutine is suspended
on `await asyncio.to_thread(some_sync_write_fn, ...)` (e.g.
_save_outgoing_transfer, which acquires a write lease internally per its
own comment at realtime_creator_funding_extractor.py:1193-1203), the
underlying thread-pool call keeps running to completion regardless --
it is synchronous Python code, immune to asyncio cancellation. If that
call is slow (e.g. blocked on DB_WRITE_LOCK contention) and outlives the
10s grace period, _process_job proceeds to the next job/heartbeat while
the to_thread call is STILL executing on a REUSED executor thread,
potentially still holding (or about to acquire) a write lease on that
exact thread when the next to_thread-dispatched write lands on it.

This reproduces the mechanism deterministically using real asyncio
cancellation semantics and the real write-lease primitives -- no mocks
of asyncio.to_thread's actual thread-pool behaviour.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid

import pytest

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


@pytest.mark.asyncio
async def test_cancellation_cannot_interrupt_synchronous_to_thread_work(tmp_path):
    """Proves the structural precondition: asyncio.Task.cancel() delivered
    while a coroutine awaits asyncio.to_thread(...) does NOT stop the
    underlying synchronous call -- it keeps running on its executor
    thread to completion."""
    db_path = str(tmp_path / "x.db")
    slow_call_started = threading.Event()
    slow_call_finished = threading.Event()

    def slow_sync_write():
        slow_call_started.set()
        lease = acquire_write_lease(
            f"tracked:{os.path.realpath(db_path)}", db_path, str(uuid.uuid4()),
            "realtime_creator_funding_extractor.py:977 in _save_outgoing_transfer",
        )
        time.sleep(0.3)  # models slow DB_WRITE_LOCK contention / disk I/O
        release_write_lease(lease)
        slow_call_finished.set()

    async def coroutine_doing_slow_write():
        await asyncio.to_thread(slow_sync_write)

    task = asyncio.create_task(coroutine_doing_slow_write())
    # Wait until the thread pool call has actually started.
    while not slow_call_started.is_set():
        await asyncio.sleep(0.001)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The task object is cancelled, but the underlying OS thread is NOT --
    # it keeps running because asyncio has no mechanism to interrupt
    # arbitrary synchronous code.
    assert not slow_call_finished.is_set(), (
        "setup invariant: the sync call must still be running at the "
        "moment the awaiting task reports cancelled, proving cancellation "
        "does not stop to_thread's underlying work"
    )

    # Wait for it to actually finish on its own (it will, eventually).
    while not slow_call_finished.is_set():
        await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_grace_period_overrun_leaves_lease_held_past_process_job_boundary(tmp_path):
    """The core X78.4 reproduction: mirrors _process_job's exact
    wait_for/cancel/wait_for(grace) sequence, and proves the specific
    claim that matters for the fix -- at the moment _process_job's
    cancellation handling gives up and "proceeds anyway", the write lease
    is STILL held by a thread this process does not control the
    lifetime of.

    Note on what this test does NOT attempt to prove: forcing the
    SPECIFIC next write to land on the SAME OS thread as the straggler
    is not reproducible with a plain ThreadPoolExecutor in a unit test --
    a pool never runs two callables on one worker concurrently (the
    second one queues and only starts once the first returns, at which
    point the first's lease is already released, since release happens
    synchronously before the callable returns). asyncio.to_thread's
    shared default executor behaves the same way per-thread; the
    collision observed live requires the specific interleaving of many
    concurrent to_thread dispatches across a multi-worker pool, which is
    a property of the live process's actual load, not something a
    controlled unit test should simulate by fighting the executor's own
    concurrency guarantees. What IS both provable and sufficient to
    justify the fix: the lease outlives _process_job's cancellation
    handling, for an UNBOUNDED and UNOBSERVABLE amount of time -- which
    is exactly the condition the fix (Phase 3/4) must close, regardless
    of which specific later write it eventually collides with."""
    db_path = str(tmp_path / "x.db")
    JOB_TIMEOUT = 0.02
    GRACE_PERIOD = 0.02
    SLOW_WRITE_DURATION = 0.15  # deliberately longer than JOB_TIMEOUT + GRACE_PERIOD

    lease_released = threading.Event()

    def slow_sync_write():
        lease = acquire_write_lease(
            f"tracked:{os.path.realpath(db_path)}", db_path, str(uuid.uuid4()),
            "realtime_creator_funding_extractor.py:977 in _save_outgoing_transfer",
        )
        time.sleep(SLOW_WRITE_DURATION)
        release_write_lease(lease)
        lease_released.set()

    async def extraction_like_coroutine():
        await asyncio.sleep(0.001)  # simulate some earlier async work
        await asyncio.to_thread(slow_sync_write)
        return {"status": "success"}

    # Mirrors _process_job's exact pattern.
    extraction_task = asyncio.ensure_future(extraction_like_coroutine())
    try:
        await asyncio.wait_for(asyncio.shield(extraction_task), timeout=JOB_TIMEOUT)
        pytest.fail("setup invariant: extraction must time out for this reproduction")
    except asyncio.TimeoutError:
        extraction_task.cancel()
        try:
            await asyncio.wait_for(extraction_task, timeout=GRACE_PERIOD)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass  # exactly what _process_job does: log and proceed anyway

    # THE CORE CLAIM: at this exact point -- where unfixed _process_job
    # would now claim the next job and write a heartbeat -- the lease is
    # still held, by a thread whose completion this coroutine has no way
    # to observe or wait for (it already gave up on the grace-period
    # wait_for above). This is the exact defect: proceeding here is
    # proceeding into an unknown, unbounded, unobserved hazard.
    assert not lease_released.is_set(), (
        "the straggler must still be running (lease not yet released) at "
        "the moment _process_job's cancellation handling gives up -- this "
        "is the exact unsafe window the X78.4 fix must close"
    )

    # Cleanup: wait for it to actually finish so it doesn't leak into
    # other tests.
    while not lease_released.is_set():
        await asyncio.sleep(0.01)
    _clear_thread_lease()
