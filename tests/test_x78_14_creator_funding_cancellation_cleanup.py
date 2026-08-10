from __future__ import annotations

import asyncio
import threading
import time

import pytest

from src.extractors.realtime_creator_funding_extractor import ExtractionWorkScope
from src.utils import db_locking


@pytest.mark.asyncio
async def test_owned_async_children_are_cancelled_with_scope():
    scope = ExtractionWorkScope()
    stopped = asyncio.Event()

    async def child():
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    task = scope.track_task(asyncio.create_task(child()))
    await asyncio.sleep(0)
    pending = await scope.cancel_and_wait(timeout=0.5)

    assert pending == 0
    assert task.cancelled()
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_owned_executor_future_has_truthful_lifecycle():
    """Cancelling an awaiter does not hide its still-running OS thread."""
    scope = ExtractionWorkScope()
    release = threading.Event()
    finished = threading.Event()

    def blocking_work():
        release.wait(1.0)
        finished.set()

    owner = asyncio.create_task(scope.run_sync(blocking_work))
    await asyncio.sleep(0.02)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner

    assert not finished.is_set()
    assert len(scope.executor_futures) == 1
    release.set()
    assert await scope.cancel_and_wait(timeout=0.5) == 0
    assert finished.is_set()
    assert not scope.executor_futures


def test_bounded_write_wait_is_context_local():
    assert db_locking._WRITE_WAIT_TIMEOUT_SECONDS.get() == 60.0


def test_write_wait_deadline_preserves_budget_then_truncates():
    deadline = time.monotonic() + 0.05
    with db_locking.write_wait_deadline(deadline):
        remaining = db_locking._effective_write_wait_timeout()
        assert 0.0 < remaining <= 0.05
    assert db_locking._effective_write_wait_timeout() == 60.0
    with db_locking.bounded_write_wait(2.0):
        assert db_locking._WRITE_WAIT_TIMEOUT_SECONDS.get() == 2.0
    assert db_locking._WRITE_WAIT_TIMEOUT_SECONDS.get() == 60.0


@pytest.mark.asyncio
async def test_repeated_timeout_cleanup_does_not_accumulate_owned_work():
    baseline_threads = threading.active_count()
    for _ in range(20):
        scope = ExtractionWorkScope()
        task = scope.track_task(asyncio.create_task(asyncio.sleep(60)))
        await asyncio.sleep(0)
        assert await scope.cancel_and_wait(timeout=0.25) == 0
        assert task.cancelled()
        assert not scope.tasks
        assert not scope.executor_futures

    # No per-job executor was used here, and async children are all reaped.
    assert threading.active_count() == baseline_threads
    assert len(asyncio.all_tasks()) == 1
