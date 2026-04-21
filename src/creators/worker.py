"""
Creator activity redesign — async background worker.

Polls creator_activity_jobs, dispatches by job_type, persists progress.
Designed to run as a long-lived asyncio task alongside the existing listener.

Job types:
  baseline              — first-time full historical scan
  incremental_reconcile — bounded gap-fill after streaming drop
  backfill              — explicit delayed deep scan (low priority)
  refresh               — lightweight re-classification of existing data

The worker never creates duplicate active jobs for the same creator+type.
The UNIQUE index on creator_activity_jobs enforces this at the DB layer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, Callable, Awaitable

from src.creators.models import JobType, JobStatus, HistoryStatus, CoverageMode
from src.creators.repository import CreatorRepository
from src.creators.service import (
    _reconcile_creator_gap,
    _should_run_creator_baseline,
)

logger = logging.getLogger(__name__)

_WORKER_POLL_INTERVAL = 5      # seconds between polls when queue is empty
_WORKER_LOCK_SECONDS  = 180    # how long before a running job is considered stale
_BASELINE_TIMEOUT     = 90     # seconds before we save cursor and yield
_MAX_CONCURRENT_JOBS  = 2      # Tier B concurrency cap


class CreatorActivityWorker:
    """
    Long-lived async background worker.

    Inject the RPC callables at construction time so the worker has no direct
    dependency on the listener or RPC client.
    """

    def __init__(
        self,
        repo: CreatorRepository,
        *,
        # Injected callables — supply from the listener or a shared RPC client
        run_baseline_scan_fn:       Callable[[str, Optional[dict]], Awaitable[dict]],
        run_incremental_reconcile_fn: Callable[[str], Awaitable[None]],
        run_backfill_fn:            Callable[[str], Awaitable[None]],
        run_refresh_fn:             Callable[[str], Awaitable[None]],
        get_signatures_fn:          Callable[..., Awaitable[list]],
        process_signature_fn:       Callable[..., Awaitable[None]],
    ) -> None:
        self._repo                   = repo
        self._run_baseline           = run_baseline_scan_fn
        self._run_incremental        = run_incremental_reconcile_fn
        self._run_backfill           = run_backfill_fn
        self._run_refresh            = run_refresh_fn
        self._get_signatures         = get_signatures_fn
        self._process_signature      = process_signature_fn
        self._active_semaphore       = asyncio.Semaphore(_MAX_CONCURRENT_JOBS)
        self._running                = False

    async def run(self) -> None:
        """Main loop.  Runs until cancelled."""
        self._running = True
        logger.info("[CREATOR_WORKER] Starting creator activity worker")
        while self._running:
            try:
                job = await self._repo.get_next_creator_activity_job(lock_seconds=_WORKER_LOCK_SECONDS)
                if job is None:
                    await asyncio.sleep(_WORKER_POLL_INTERVAL)
                    continue
                asyncio.create_task(self._process_job(job))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("[CREATOR_WORKER] Unexpected error in poll loop: %s", e)
                await asyncio.sleep(10)

    async def stop(self) -> None:
        self._running = False

    # -----------------------------------------------------------------------
    # Job dispatch
    # -----------------------------------------------------------------------

    async def _process_job(self, job) -> None:
        async with self._active_semaphore:
            creator = job.creator_address
            job_id  = job.id
            logger.info(
                "[CREATOR_WORKER] Processing job id=%s type=%s creator=%s",
                job_id, job.job_type.value, creator[:16],
            )
            try:
                if job.job_type == JobType.BASELINE:
                    await self._handle_baseline(job)

                elif job.job_type == JobType.INCREMENTAL_RECONCILE:
                    await _reconcile_creator_gap(
                        creator,
                        repo=self._repo,
                        get_signatures_fn=self._get_signatures,
                        process_signature_fn=self._process_signature,
                    )
                    await self._repo.mark_creator_activity_job_complete(job_id)

                elif job.job_type == JobType.BACKFILL:
                    await self._run_backfill(creator)
                    await self._repo.mark_creator_activity_job_complete(job_id)

                elif job.job_type == JobType.REFRESH:
                    await self._run_refresh(creator)
                    await self._repo.mark_creator_activity_job_complete(job_id)

                else:
                    logger.warning("[CREATOR_WORKER] Unknown job_type %s for job %s", job.job_type, job_id)
                    await self._repo.mark_creator_activity_job_failed(job_id, "unknown_job_type")

            except asyncio.CancelledError:
                await self._repo.mark_creator_activity_job_failed(job_id, "cancelled")
                raise
            except Exception as e:
                logger.exception("[CREATOR_WORKER] Job %s failed: %s", job_id, e)
                await self._repo.mark_creator_activity_job_failed(
                    job_id, str(e)[:500], retry_delay=60, max_attempts=5
                )

    # -----------------------------------------------------------------------
    # Baseline handler — supports resumable partial scans
    # -----------------------------------------------------------------------

    async def _handle_baseline(self, job) -> None:
        creator = job.creator_address
        job_id  = job.id

        # Read existing resume cursor if this is a continuation
        state = await self._repo.get_creator_activity_state(creator)
        resume_cursor = state.resume_cursor if state else None

        # Mark creator as partial immediately so a crash leaves a clear state
        await self._repo.upsert_creator_profile(
            creator,
            history_status=HistoryStatus.PARTIAL,
        )

        try:
            # run_baseline_scan_fn: accepts optional cursor dict, returns:
            # {"complete": bool, "cursor": dict|None, "newest_sig": str, "oldest_sig": str}
            result = await asyncio.wait_for(
                self._run_baseline(creator, resume_cursor),
                timeout=_BASELINE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            # Save progress cursor and reschedule
            logger.warning("[CREATOR_WORKER] Baseline timeout for %s — saving cursor", creator[:16])
            await self._repo.mark_creator_activity_job_failed(
                job_id, "timeout", retry_delay=30, max_attempts=10
            )
            # Caller's run_baseline_scan_fn is responsible for flushing cursor to activity_state
            return

        now = int(time.time())

        if result.get("complete"):
            await self._repo.upsert_creator_profile(
                creator,
                history_status=HistoryStatus.BASELINED,
                coverage_mode=CoverageMode.FULL,
                last_full_scan_at=now,
            )
            await self._repo.upsert_creator_activity_state(
                creator,
                oldest_scanned_signature=result.get("oldest_sig"),
                newest_scanned_signature=result.get("newest_sig"),
                resume_cursor=None,  # clear cursor on completion
                needs_backfill=False,
            )
            await self._repo.mark_creator_activity_job_complete(job_id)
        else:
            # Partial completion — save cursor and re-enqueue
            cursor = result.get("cursor")
            if cursor:
                await self._repo.upsert_creator_activity_state(creator, resume_cursor=cursor)
            await self._repo.mark_creator_activity_job_failed(
                job_id, "partial_completion", retry_delay=10, max_attempts=20
            )
