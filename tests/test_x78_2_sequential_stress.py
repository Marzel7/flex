"""X78.2 Phase 15: sequential stress test -- 100 jobs with deliberately
variable-speed background enrichment (fast, slow, exceeding one bounded
wait, exceeding both), proving the job-boundary gate holds up over a long
run and not just a single isolated collision.

Expected (per the task spec): NestedDatabaseWriteError == 0, cross-job
write overlap == 0, no lost tracked descendants (every straggler is
eventually awaited and removed from _STRAGGLER_TASKS).
"""
from __future__ import annotations

import asyncio
import os
import random
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
def _isolate(tmp_path):
    cfw._STRAGGLER_TASKS.clear()
    _clear_thread_lease()
    yield
    cfw._STRAGGLER_TASKS.clear()
    _clear_thread_lease()


@pytest.mark.asyncio
async def test_100_sequential_jobs_zero_collisions(tmp_path):
    db_path = str(tmp_path / "x.db")
    random.seed(1337)

    nested_errors = 0
    completed_writes = 0
    N = 100

    async def background_enrichment(job_idx: int, speed: str):
        """Models _run_automatic_cex_detection / _try_blocksec_batch /
        run_post_launch_automation: acquire a lease, do "work" (an await
        point standing in for network I/O), release."""
        lease = acquire_write_lease(
            f"tracked:{os.path.realpath(db_path)}", db_path, str(uuid.uuid4()),
            f"automatic_cex_detection.py:413 (job {job_idx}, {speed})",
        )
        try:
            if speed == "fast":
                await asyncio.sleep(0)
            elif speed == "slow_within_one_wait":
                await asyncio.sleep(0.005)
            else:  # "slow_beyond_both_waits"
                await asyncio.sleep(0.02)
        finally:
            release_write_lease(lease)

    async def simulated_process_job(job_idx: int):
        nonlocal nested_errors, completed_writes

        # X78.2 gate -- must run before this job's own write, exactly as
        # wired into the real _process_job in creator_funding_worker.py.
        await cfw._await_stragglers_before_next_write()

        # This job's own primary write (models extraction_conn / _mark_*).
        try:
            lease = acquire_write_lease(
                f"tracked:{os.path.realpath(db_path)}", db_path, str(uuid.uuid4()),
                f"realtime_creator_funding_extractor.py:1305 (job {job_idx})",
            )
            completed_writes += 1
            release_write_lease(lease)
        except NestedDatabaseWriteError:
            nested_errors += 1
            raise

        # This job spawns its own background enrichment, fire-and-forget,
        # exactly like _spawn_background_task.
        speed = random.choice(["fast", "slow_within_one_wait", "slow_beyond_both_waits"])
        tasks_before = asyncio.all_tasks(asyncio.get_event_loop())
        asyncio.create_task(background_enrichment(job_idx, speed))
        await asyncio.sleep(0)  # let it start (mirrors real scheduling)

        # Bounded supervision sweep, same shape as _await_orphaned_tasks,
        # with a short timeout so "slow_beyond_both_waits" reliably straggles.
        orig_timeout = cfw.ORPHAN_TASK_WAIT_SECONDS
        cfw.ORPHAN_TASK_WAIT_SECONDS = 0.01
        try:
            await cfw._await_orphaned_tasks(tasks_before)
        finally:
            cfw.ORPHAN_TASK_WAIT_SECONDS = orig_timeout

    for i in range(N):
        await simulated_process_job(i)

    # Drain any final stragglers so the test doesn't leak tasks.
    await cfw._await_stragglers_before_next_write()

    assert nested_errors == 0, f"{nested_errors} NestedDatabaseWriteError collisions occurred"
    assert completed_writes == N
    assert not cfw._STRAGGLER_TASKS, "no descendant should be left untracked/lost"
