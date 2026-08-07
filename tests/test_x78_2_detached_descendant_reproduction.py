"""X78.2 Phase 1: deterministic reproduction of the detached-background-task
collision identified (statically) in X78.1
(docs/audits/x78_1_creator_funding_worker_concurrency_root_cause.md).

Claim under test: a background task spawned during Job N's extraction
(via _spawn_background_task) can be left running past both bounded
supervision windows (wait_for_background_tasks, _await_orphaned_tasks).
If that straggler is suspended mid-transaction -- lease acquired, not yet
released -- when Job N+1 begins on the SAME event-loop thread, Job N+1's
own write-lease acquisition raises NestedDatabaseWriteError, because
_thread_write_lease is a threading.local() reentrancy guard keyed on the
OS thread, not on the coroutine/task.

This test exercises the REAL acquire_write_lease/release_write_lease
primitives from src.core.database_write_service (no mocks), and uses an
asyncio.Event to force the exact interleaving deterministically -- no
sleeps, no timing races.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid

import pytest

from src.core.database_write_service import (
    NestedDatabaseWriteError,
    acquire_write_lease,
    release_write_lease,
    _thread_write_lease,
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "x78_2_repro.db")


def _clear_thread_lease():
    if hasattr(_thread_write_lease, "owner"):
        del _thread_write_lease.owner


@pytest.mark.asyncio
async def test_straggler_lease_collides_with_next_job_on_same_thread(db_path):
    """Proves the exact mechanism X78.1 predicted, using the real lease
    primitives and real asyncio interleaving on a single event loop."""
    _clear_thread_lease()

    straggler_holds_lease = asyncio.Event()
    allow_straggler_to_release = asyncio.Event()
    collision: dict = {}

    async def straggler_background_task():
        """Models _run_automatic_cex_detection(): acquires a write lease,
        then suspends mid-transaction (simulating a slow network call or
        the classify_addresses_from_funding() body) before releasing it --
        exactly the state a bounded-wait-then-abandon supervisor leaves
        behind once its timeout fires."""
        lease = acquire_write_lease(
            f"tracked:{os.path.realpath(db_path)}",
            db_path,
            str(uuid.uuid4()),
            "automatic_cex_detection.py:413 (straggler)",
        )
        straggler_holds_lease.set()
        try:
            await allow_straggler_to_release.wait()
        finally:
            release_write_lease(lease)

    async def next_job():
        """Models Job N+1's own write path (_mark_complete / extraction_conn)
        attempting to acquire a write lease on the SAME event-loop thread
        while the straggler above is still mid-transaction."""
        await straggler_holds_lease.wait()
        return acquire_write_lease(
            f"tracked:{os.path.realpath(db_path)}",
            db_path,
            str(uuid.uuid4()),
            "realtime_creator_funding_extractor.py:1305 (job N+1)",
        )

    # Model _spawn_background_task: fire-and-forget, tracked but not awaited
    # inline -- exactly asyncio.create_task's contract in the real code.
    straggler_task = asyncio.create_task(straggler_background_task())

    # Model both bounded supervisors (wait_for_background_tasks,
    # _await_orphaned_tasks) expiring without cancelling: a short timeout
    # that elapses while the straggler still holds its lease.
    done, pending = await asyncio.wait({straggler_task}, timeout=0.01)
    assert straggler_task in pending, (
        "setup invariant: straggler must still be running (lease held, "
        "not yet released) when supervision gives up -- this is the exact "
        "state _await_orphaned_tasks leaves behind at line ~600-603"
    )

    # Job N's own _process_job returns here despite the pending straggler
    # (this is the documented, intentional behaviour being audited).

    # Job N+1 now starts on the same thread and attempts its own write.
    try:
        await next_job()
        pytest.fail(
            "expected NestedDatabaseWriteError: Job N+1 acquired a write "
            "lease while Job N's straggler still held one on the same "
            "thread -- if this did NOT raise, the reproduction preconditions "
            "are no longer valid and the diagnosis must be re-examined"
        )
    except NestedDatabaseWriteError as e:
        collision["outer_command"] = e.outer_command
        collision["inner_command"] = e.inner_command

    assert collision["outer_command"] == "automatic_cex_detection.py:413 (straggler)"
    assert collision["inner_command"] == "realtime_creator_funding_extractor.py:1305 (job N+1)"

    # Cleanup: let the straggler finish so it releases its lease and the
    # thread-local guard is clear for other tests.
    allow_straggler_to_release.set()
    await straggler_task
    _clear_thread_lease()


@pytest.mark.asyncio
async def test_no_collision_when_straggler_releases_before_next_job(db_path):
    """Negative control: if the straggler finishes (releases its lease)
    before Job N+1 attempts to acquire one, there is no collision. This
    proves the failure mode is specifically about lease lifetime overlap,
    not merely "a background task existed"."""
    _clear_thread_lease()

    async def straggler_that_finishes_promptly():
        lease = acquire_write_lease(
            f"tracked:{os.path.realpath(db_path)}",
            db_path,
            str(uuid.uuid4()),
            "automatic_cex_detection.py:413 (prompt straggler)",
        )
        release_write_lease(lease)

    straggler_task = asyncio.create_task(straggler_that_finishes_promptly())
    await straggler_task  # fully complete, lease released

    lease = acquire_write_lease(
        f"tracked:{os.path.realpath(db_path)}",
        db_path,
        str(uuid.uuid4()),
        "realtime_creator_funding_extractor.py:1305 (job N+1, no collision)",
    )
    release_write_lease(lease)
    _clear_thread_lease()
