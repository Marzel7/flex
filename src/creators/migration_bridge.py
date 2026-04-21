"""
Creator activity redesign — migration bridge.

Dual-write shim that keeps the OLD creator_funding_queue running
while incrementally populating the NEW creator_profile / creator_activity_jobs tables.

Drop this file when Phase 3 migration is complete.

Usage — call dual_write_creator_resolved() from _ensure_pf_ws_creator() and
_enqueue_creator_funding_job() until Phase 2 is done.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from src.creators.models import (
    HistoryStatus,
    CoverageMode,
    JobType,
)
from src.creators.repository import CreatorRepository
from src.creators.service import (
    _should_run_creator_baseline,
    _enqueue_creator_activity_job,
    _start_creator_watch_if_needed,
)

logger = logging.getLogger(__name__)


async def dual_write_creator_resolved(
    creator_address: str,
    mint: str,
    *,
    create_tx_signature: Optional[str],
    reason: str,
    repo: CreatorRepository,
) -> None:
    """
    Called immediately after pf_ws_creator is confirmed (either from cache or fresh resolution).

    Phase 1 behaviour:
      - Upsert creator_profile (first_seen, token_count, last_launch).
      - Decide whether to enqueue a Tier B baseline job.
      - Old creator_funding_queue path continues unchanged in parallel.

    This function is additive only — it does NOT replace any existing call.
    """
    now = int(time.time())

    # Upsert profile
    await repo.increment_creator_token_count(creator_address, last_launch_at=now)
    await repo.upsert_creator_profile(
        creator_address,
        last_create_tx_signature=create_tx_signature,
        last_activity_at=now,
        last_launch_at=now,
    )

    # Read back to make a baseline decision
    profile = await repo.get_creator_profile(creator_address)
    state   = await repo.get_creator_activity_state(creator_address)

    if _should_run_creator_baseline(profile, state):
        # Phase 1: enqueue but do not run yet — the worker is not live until Phase 2.
        await _enqueue_creator_activity_job(
            creator_address,
            mint=mint,
            reason=reason,
            job_type=JobType.BASELINE,
            repo=repo,
            priority=100,           # low priority during phase 1 rollout
            delay_seconds=30,       # small delay so hot-path token analysis wins
        )

    # Start watch (no-op if already active or no webhook integration)
    await _start_creator_watch_if_needed(creator_address, repo=repo)


async def migrate_existing_creator_funders(
    repo: CreatorRepository,
    *,
    db_path: str,
    db_lock: asyncio.Lock,
    batch_size: int = 500,
) -> int:
    """
    One-time bootstrap: populate creator_profile from existing creator_funders rows.

    Run this once as a startup migration or a background task.
    Returns the number of creators processed.

    This does NOT trigger baseline jobs — it only establishes profile rows
    for creators we already have funding data for (so the cache check
    switches from COUNT(*) on creator_funders to creator_profile.history_status).
    """
    from src.utils.db_locking import db_connect
    import sqlite3

    logger.info("[MIGRATION_BRIDGE] Starting creator_profile bootstrap from creator_funders")

    async with db_lock:
        conn = db_connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT creator_address, COUNT(*) AS funder_count, MIN(detected_at) AS first_seen
            FROM creator_funders
            GROUP BY creator_address
            LIMIT ?
        """, (batch_size,)).fetchall()
        conn.close()

    count = 0
    for row in rows:
        creator = row["creator_address"]
        first_seen = row["first_seen"]
        try:
            await repo.upsert_creator_profile(
                creator,
                history_status=HistoryStatus.BASELINED,  # we already have their data
                coverage_mode=CoverageMode.FULL,
            )
            count += 1
        except Exception as e:
            logger.warning("[MIGRATION_BRIDGE] Failed to bootstrap profile for %s: %s", creator[:16], e)

    logger.info("[MIGRATION_BRIDGE] Bootstrap complete: %d creators", count)
    return count


def should_use_profile_cache(profile: Optional[object]) -> bool:
    """
    Phase 2+ cache check: replaces `SELECT COUNT(*) FROM creator_funders`.

    Returns True if we can skip full extraction based on creator_profile state.
    During Phase 1 this always returns False to preserve existing behaviour.
    During Phase 2+ returns True when profile is baselined or partial.
    """
    # Flip to True when Phase 2 worker is live and profiles are reliably populated.
    PHASE_2_ACTIVE = False

    if not PHASE_2_ACTIVE:
        return False

    if profile is None:
        return False

    from src.creators.models import HistoryStatus
    return profile.history_status in (HistoryStatus.BASELINED, HistoryStatus.PARTIAL)
