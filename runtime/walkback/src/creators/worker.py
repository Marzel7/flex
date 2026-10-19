"""
Creator activity redesign — async background worker.

Dispatches baseline and incremental_reconcile jobs only.
Runs as a long-lived asyncio task alongside the existing listener.

Key design points:
  - Semaphore caps Tier B concurrency to avoid starving the hot path.
  - Baseline handler marks history_status=partial immediately on start.
  - Resumable: on timeout, cursor is persisted so next run continues where left off.
  - The UNIQUE index on creator_activity_jobs prevents duplicate active jobs —
    the worker never needs to check for duplicates itself.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from src.creators.models import CoverageMode, HistoryStatus, JobType
from src.creators.repository import CreatorRepository
from src.creators.service import reconcile_creator_gap

logger = logging.getLogger(__name__)

_POLL_INTERVAL      = 5     # seconds to sleep when queue is empty
_LOCK_SECONDS       = 180   # stale-lock release threshold
_BASELINE_TIMEOUT   = 90    # seconds before timeout saves cursor and retries
_MAX_CONCURRENCY    = 2     # cap concurrent Tier B jobs


class CreatorActivityWorker:
    """
    Long-lived async worker.  Inject RPC callables at construction time.

    run_baseline_scan_fn signature:
        async (creator_address: str, resume_cursor: Optional[dict]) -> dict
        Returns: {"complete": bool, "cursor": Optional[dict],
                  "newest_sig": Optional[str], "oldest_sig": Optional[str]}

    get_signatures_fn signature:
        async (address: str, before: Optional[str] = None, limit: int = 50) -> list[dict]

    process_signature_fn signature:
        async (creator_address: str, sig_info: dict) -> None
    """

    def __init__(
        self,
        repo: CreatorRepository,
        *,
        run_baseline_scan_fn:  Callable[[str, Optional[dict]], Awaitable[dict]],
        get_signatures_fn:     Callable[..., Awaitable[list]],
        process_signature_fn:  Callable[[str, dict], Awaitable[None]],
    ) -> None:
        self._repo               = repo
        self._run_baseline       = run_baseline_scan_fn
        self._get_signatures     = get_signatures_fn
        self._process_signature  = process_signature_fn
        self._sem                = asyncio.Semaphore(_MAX_CONCURRENCY)
        self._running            = False

    async def run(self) -> None:
        self._running = True
        logger.info("[CREATOR_WORKER] Started")
        while self._running:
            try:
                job = await self._repo.get_next_creator_activity_job(lock_seconds=_LOCK_SECONDS)
                if job is None:
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue
                asyncio.create_task(self._dispatch(job))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("[CREATOR_WORKER] Poll error: %s", e)
                await asyncio.sleep(10)
        logger.info("[CREATOR_WORKER] Stopped")

    async def stop(self) -> None:
        self._running = False

    async def _dispatch(self, job) -> None:
        async with self._sem:
            job_id  = job.id
            creator = job.creator_address
            logger.info("[CREATOR_WORKER] job_id=%s type=%s creator=%s",
                        job_id, job.job_type.value, creator[:16])
            try:
                if job.job_type == JobType.BASELINE:
                    await self._handle_baseline(job)
                elif job.job_type == JobType.INCREMENTAL_RECONCILE:
                    await reconcile_creator_gap(
                        creator,
                        repo=self._repo,
                        get_signatures_fn=self._get_signatures,
                        process_signature_fn=self._process_signature,
                    )
                    await self._repo.mark_creator_activity_job_complete(job_id)
                else:
                    logger.error("[CREATOR_WORKER] Unknown job_type %s id=%s", job.job_type, job_id)
                    await self._repo.mark_creator_activity_job_failed(job_id, "unknown_job_type")

            except asyncio.CancelledError:
                await self._repo.mark_creator_activity_job_failed(job_id, "cancelled")
                raise
            except Exception as e:
                logger.exception("[CREATOR_WORKER] job_id=%s failed: %s", job_id, e)
                await self._repo.mark_creator_activity_job_failed(
                    job_id, str(e)[:500], retry_delay=60, max_attempts=5
                )

    async def _handle_baseline(self, job) -> None:
        creator = job.creator_address
        job_id  = job.id

        # Read existing resume cursor — may exist if a prior attempt timed out
        state = await self._repo.get_creator_activity_state(creator)
        resume_cursor = state.resume_cursor if state else None

        # Mark partial immediately — crash-safe state
        await self._repo.upsert_creator_profile(
            creator,
            history_status=HistoryStatus.PARTIAL,
            coverage_mode=CoverageMode.FORWARD_ONLY,
        )

        _BASELINE_MAX_ATTEMPTS = 20

        try:
            result: dict = await asyncio.wait_for(
                self._run_baseline(creator, resume_cursor),
                timeout=_BASELINE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("[CREATOR_WORKER] Baseline timeout creator=%s attempt=%d/%d",
                           creator[:16], job.attempt_count + 1, _BASELINE_MAX_ATTEMPTS)
            await self._repo.mark_creator_activity_job_failed(
                job_id, "timeout", retry_delay=30, max_attempts=_BASELINE_MAX_ATTEMPTS
            )
            await self._maybe_mark_stale(creator, job.attempt_count + 1, _BASELINE_MAX_ATTEMPTS)
            return

        now = int(time.time())

        if result.get("complete"):
            await self._repo.upsert_creator_profile(
                creator,
                history_status=HistoryStatus.BASELINED,
                coverage_mode=CoverageMode.FULL,
                last_full_scan_at=now,
                baselined_at=now,
            )
            await self._repo.upsert_creator_activity_state(
                creator,
                oldest_scanned_signature=result.get("oldest_sig"),
                newest_scanned_signature=result.get("newest_sig"),
                clear_resume_cursor=True,
            )
            await self._repo.mark_creator_activity_job_complete(job_id)
            logger.info("[CREATOR_WORKER] Baseline complete creator=%s", creator[:16])
        else:
            # Partial — save cursor, job retries automatically via mark_failed backoff
            cursor = result.get("cursor")
            if cursor:
                await self._repo.upsert_creator_activity_state(creator, resume_cursor=cursor)
            await self._repo.mark_creator_activity_job_failed(
                job_id, "partial_completion", retry_delay=10, max_attempts=_BASELINE_MAX_ATTEMPTS
            )
            await self._maybe_mark_stale(creator, job.attempt_count + 1, _BASELINE_MAX_ATTEMPTS)

    async def _maybe_mark_stale(self, creator: str, attempts: int, max_attempts: int) -> None:
        """
        When a baseline job exhausts all retries, flip history_status to 'stale'.
        This prevents the creator from being permanently stuck in 'partial' while
        still allowing a future retry (should_run_creator_baseline treats stale as retriable).
        """
        if attempts < max_attempts:
            return
        logger.warning(
            "[CREATOR_WORKER] Baseline exhausted max_attempts=%d creator=%s — marking stale",
            max_attempts, creator[:16],
        )
        try:
            profile = await self._repo.get_creator_profile(creator)
            # Only downgrade if still partial — don't touch baselined profiles.
            if profile and profile.history_status == HistoryStatus.PARTIAL:
                await self._repo.upsert_creator_profile(
                    creator,
                    history_status=HistoryStatus.STALE,
                )
        except Exception as e:
            logger.warning("[CREATOR_WORKER] Failed to mark stale creator=%s: %s", creator[:16], e)
