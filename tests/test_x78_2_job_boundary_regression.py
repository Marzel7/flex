"""X78.2 Phase 13-14: the permanent job-boundary regression, plus
cancellation-safety coverage, for the detached-descendant fix in
src/core/creator_funding_worker.py (_STRAGGLER_TASKS /
_await_stragglers_before_next_write / _await_orphaned_tasks).

Core regression (Phase 14): Job N cannot let Job N+1 begin its own writes
while an unsafe write-capable descendant from Job N could still later
collide with Job N+1 on the same thread-local write-lease context.

These tests exercise the REAL module-level functions in
src.core.creator_funding_worker (not a reimplementation), using the real
acquire_write_lease/release_write_lease primitives to prove the lease
guard itself never fires once the gate is in place.
"""
from __future__ import annotations

import asyncio
import os
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
def _isolate_straggler_state():
    """_STRAGGLER_TASKS is module-level (intentionally, so it survives
    across _process_job calls in production) -- reset it around every test
    so tests can't leak into each other."""
    cfw._STRAGGLER_TASKS.clear()
    _clear_thread_lease()
    yield
    cfw._STRAGGLER_TASKS.clear()
    _clear_thread_lease()


@pytest.mark.asyncio
async def test_orphaned_tasks_handoff_populates_straggler_set(tmp_path):
    """_await_orphaned_tasks, on bounded-wait timeout, must hand the still-
    pending task to _STRAGGLER_TASKS rather than silently dropping it --
    that handoff is the entire fix."""
    db_path = str(tmp_path / "x.db")
    release_gate = asyncio.Event()

    async def slow_background_write():
        lease = acquire_write_lease(
            f"tracked:{os.path.realpath(db_path)}", db_path, str(uuid.uuid4()),
            "automatic_cex_detection.py:413 (slow)",
        )
        try:
            await release_gate.wait()
        finally:
            release_write_lease(lease)

    tasks_before = asyncio.all_tasks(asyncio.get_event_loop())
    bg_task = asyncio.create_task(slow_background_write())
    await asyncio.sleep(0)  # let it start and acquire the lease

    orig_timeout = cfw.ORPHAN_TASK_WAIT_SECONDS
    cfw.ORPHAN_TASK_WAIT_SECONDS = 0.01
    try:
        await cfw._await_orphaned_tasks(tasks_before)
    finally:
        cfw.ORPHAN_TASK_WAIT_SECONDS = orig_timeout

    assert bg_task in cfw._STRAGGLER_TASKS, (
        "a task still pending after the bounded wait must be tracked in "
        "_STRAGGLER_TASKS so the next job's gate waits for it"
    )

    release_gate.set()
    await bg_task


@pytest.mark.asyncio
async def test_next_job_gate_blocks_until_straggler_releases_lease(tmp_path):
    """The core X78.2 regression: with the gate in place, a next-job-style
    write-lease acquisition performed AFTER _await_stragglers_before_next_write()
    must NEVER collide with a straggler still holding the lease -- because
    the gate makes it wait instead of racing."""
    db_path = str(tmp_path / "x.db")
    release_gate = asyncio.Event()
    order: list = []

    async def straggler():
        lease = acquire_write_lease(
            f"tracked:{os.path.realpath(db_path)}", db_path, str(uuid.uuid4()),
            "automatic_cex_detection.py:413 (straggler)",
        )
        order.append("straggler_acquired")
        try:
            await release_gate.wait()
        finally:
            release_write_lease(lease)
            order.append("straggler_released")

    bg_task = asyncio.create_task(straggler())
    await asyncio.sleep(0)  # let it acquire the lease
    cfw._STRAGGLER_TASKS.add(bg_task)

    async def next_job_write_attempt():
        await cfw._await_stragglers_before_next_write()
        # By the time this line runs, the straggler MUST have released.
        lease = acquire_write_lease(
            f"tracked:{os.path.realpath(db_path)}", db_path, str(uuid.uuid4()),
            "realtime_creator_funding_extractor.py:1305 (job N+1)",
        )
        order.append("next_job_acquired")
        release_write_lease(lease)

    gate_task = asyncio.create_task(next_job_write_attempt())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not gate_task.done(), "gate must block while straggler still holds the lease"

    release_gate.set()
    await gate_task
    await bg_task

    assert order.index("straggler_released") < order.index("next_job_acquired"), (
        "next job's write must not happen until AFTER the straggler released "
        "its lease -- this is the exact invariant that prevents "
        "NestedDatabaseWriteError"
    )


@pytest.mark.asyncio
async def test_gate_is_noop_when_no_stragglers(tmp_path):
    """No prior stragglers => the gate must not add latency or block."""
    assert not cfw._STRAGGLER_TASKS
    await cfw._await_stragglers_before_next_write()  # must return immediately


@pytest.mark.asyncio
async def test_gate_does_not_cancel_stragglers(tmp_path):
    """Explicit non-regression: the fix must never cancel a straggler --
    only wait for it. Cancelling a mid-write task is the exact outcome the
    original design (and this fix) both refuse to risk."""
    db_path = str(tmp_path / "x.db")
    release_gate = asyncio.Event()
    cancelled = False

    async def straggler():
        nonlocal cancelled
        lease = acquire_write_lease(
            f"tracked:{os.path.realpath(db_path)}", db_path, str(uuid.uuid4()),
            "automatic_cex_detection.py:413 (straggler)",
        )
        try:
            await release_gate.wait()
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            release_write_lease(lease)

    bg_task = asyncio.create_task(straggler())
    await asyncio.sleep(0)
    cfw._STRAGGLER_TASKS.add(bg_task)

    gate_task = asyncio.create_task(cfw._await_stragglers_before_next_write())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not gate_task.done()
    assert not bg_task.cancelled()
    assert not cancelled

    release_gate.set()
    await gate_task
    await bg_task
    assert not cancelled, "the gate must never cancel a straggler task"


@pytest.mark.asyncio
async def test_straggler_set_cleared_after_successful_wait(tmp_path):
    """_STRAGGLER_TASKS must not grow unboundedly -- completed stragglers
    are pruned from the tracked set once waited-on."""
    db_path = str(tmp_path / "x.db")

    async def fast_straggler():
        lease = acquire_write_lease(
            f"tracked:{os.path.realpath(db_path)}", db_path, str(uuid.uuid4()),
            "automatic_cex_detection.py:413 (fast)",
        )
        release_write_lease(lease)

    bg_task = asyncio.create_task(fast_straggler())
    await bg_task
    cfw._STRAGGLER_TASKS.add(bg_task)

    await cfw._await_stragglers_before_next_write()
    assert bg_task not in cfw._STRAGGLER_TASKS
