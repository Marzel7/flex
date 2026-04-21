"""
Creator activity redesign — migration bridge.

This module manages the incremental rollout from the old token-scoped extraction
path to the new creator-centric pipeline.

PHASE FLAGS
-----------
phase_2_active  (DB-backed, no restart required)
    false → Phase 1: dual-write only.  Old creator_funding_queue runs unchanged.
    true  → Phase 2: cache check uses creator_profile; legacy extraction skipped for
            known creators.  Worker must be running before flipping this.

Activate:
    UPDATE system_config SET value='true',  updated_at=strftime('%s','now') WHERE key='phase_2_active';
Rollback:
    UPDATE system_config SET value='false', updated_at=strftime('%s','now') WHERE key='phase_2_active';

USAGE
-----
From _ensure_pf_ws_creator() (after pf_ws_creator is written):
    await dual_write_creator_resolved(creator, mint, ...)

From extract_funding_for_new_token() (replaces COUNT(*) check):
    profile = await repo.get_creator_profile(creator)
    if await should_skip_legacy_extraction(profile, repo):
        return {"skipped": True, "reason": "creator_profile_cache"}
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from typing import Optional

from src.creators.models import CoverageMode, HistoryStatus, JobType
from src.creators.repository import CreatorRepository
from src.creators.service import enqueue_creator_activity_job, should_run_creator_baseline

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase 2 toggle — DB-backed, no restart required.
#
# Activate:
#   UPDATE system_config SET value='true',  updated_at=strftime('%s','now') WHERE key='phase_2_active';
# Rollback:
#   UPDATE system_config SET value='false', updated_at=strftime('%s','now') WHERE key='phase_2_active';
#
# Cached for 1 second in-process to avoid a DB hit on every token.
# Rollback takes effect within 1 second of the SQL UPDATE.
# ---------------------------------------------------------------------------

_phase2_cache: tuple[bool, float] = (False, 0.0)   # (value, expiry)
_PHASE2_TTL = 1.0  # seconds


async def is_phase_2_active(repo: CreatorRepository) -> bool:
    """Return True when the phase_2_active config flag is 'true' in system_config."""
    global _phase2_cache
    value, expiry = _phase2_cache
    if time.monotonic() < expiry:
        return value
    try:
        value = await repo.get_config_bool("phase_2_active", default=False)
    except Exception as e:
        logger.warning("[MIGRATION_BRIDGE] Failed to read phase_2_active flag: %s — defaulting False", e)
        value = False
    _phase2_cache = (value, time.monotonic() + _PHASE2_TTL)
    return value


# ---------------------------------------------------------------------------
# Cache gate — replaces SELECT COUNT(*) FROM creator_funders
# ---------------------------------------------------------------------------

async def should_skip_legacy_extraction(
    profile: Optional[object],
    repo: CreatorRepository,
) -> bool:
    """
    Returns True when creator_profile tells us we already have this creator's data
    and can skip the full legacy extraction path.

    Phase 1 (phase_2_active=false): always False — preserves existing behaviour.
    Phase 2+: True when history_status is 'partial', 'baselined', or 'stale'.
              'unknown' always falls through to legacy extraction.
              'stale' skips because the baseline worker refreshes asynchronously.
    """
    if not await is_phase_2_active(repo):
        return False
    if profile is None:
        return False
    return profile.history_status != HistoryStatus.UNKNOWN


async def validate_bootstrap_coverage(
    *,
    db_path: str,
    db_lock: asyncio.Lock,
) -> dict:
    """
    Check what fraction of creators in creator_funders have a non-unknown profile row.
    Run this before flipping PHASE_2_ACTIVE to confirm bootstrap is complete.

    Returns:
        {
            "total_creators_in_funders": int,
            "profiles_non_unknown": int,
            "coverage_pct": float,
            "safe_to_activate": bool,   # True when coverage >= 95%
        }
    """
    async with db_lock:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT
                COUNT(DISTINCT cf.creator_address)                           AS total,
                COUNT(DISTINCT CASE
                    WHEN cp.history_status != 'unknown' AND cp.history_status IS NOT NULL
                    THEN cf.creator_address END)                              AS profiled
            FROM creator_funders cf
            LEFT JOIN creator_profile cp ON cp.creator_address = cf.creator_address
        """).fetchone()
        conn.close()

    total    = row["total"]    if row else 0
    profiled = row["profiled"] if row else 0
    pct      = round((profiled / total * 100), 1) if total else 0.0
    return {
        "total_creators_in_funders": total,
        "profiles_non_unknown":      profiled,
        "coverage_pct":              pct,
        "safe_to_activate":          pct >= 95.0,
    }


# ---------------------------------------------------------------------------
# Dual write — called after pf_ws_creator is confirmed
# ---------------------------------------------------------------------------

async def dual_write_creator_resolved(
    creator_address: str,
    mint: str,
    *,
    create_tx_signature: Optional[str],
    reason: str,
    repo: CreatorRepository,
) -> None:
    """
    Additive dual-write.  Does NOT replace any existing call in Phase 1.

    - Upserts creator_profile (first_seen, token_count, last_launch).
    - Enqueues a baseline job if history_status == unknown.
    - No-ops completely if profile already exists and is not unknown.
    """
    now = int(time.time())

    await repo.increment_creator_token_count(creator_address, last_launch_at=now)
    await repo.upsert_creator_profile(
        creator_address,
        last_create_tx_signature=create_tx_signature,
        last_activity_at=now,
        last_launch_at=now,
    )

    profile = await repo.get_creator_profile(creator_address)
    if should_run_creator_baseline(profile):
        await enqueue_creator_activity_job(
            creator_address,
            job_type=JobType.BASELINE,
            source_mint=mint,
            source_reason=reason,
            priority=100,
            delay_seconds=30,
            repo=repo,
        )


# ---------------------------------------------------------------------------
# One-time bootstrap — run once at startup or as a background task
# ---------------------------------------------------------------------------

async def bootstrap_creator_profiles(
    repo: CreatorRepository,
    *,
    db_path: str,
    db_lock: asyncio.Lock,
    batch_size: int = 2000,
) -> int:
    """
    Populate creator_profile from existing creator_funders rows.

    Sets history_status = 'baselined' and coverage_mode = 'full' for every creator
    that already has funder records, so the Phase 2 cache check fires immediately
    for all historical creators without needing a fresh baseline scan.

    Returns the number of creator_profile rows upserted.

    Safe to re-run — uses INSERT OR IGNORE / upsert logic.
    """
    logger.info("[MIGRATION_BRIDGE] Bootstrap: reading creator_funders...")
    async with db_lock:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT
                creator_address,
                COUNT(*)          AS funder_count,
                MIN(detected_at)  AS first_seen_raw
            FROM creator_funders
            GROUP BY creator_address
            LIMIT ?
        """, (batch_size,)).fetchall()
        conn.close()

    if not rows:
        logger.info("[MIGRATION_BRIDGE] Bootstrap: no creator_funders rows found")
        return 0

    now   = int(time.time())
    count = 0

    for row in rows:
        creator    = row["creator_address"]
        first_seen = row["first_seen_raw"]

        # Convert detected_at string → epoch if needed
        if isinstance(first_seen, str):
            try:
                from datetime import datetime
                first_seen = int(datetime.fromisoformat(first_seen.replace("Z", "+00:00")).timestamp())
            except Exception:
                first_seen = None

        try:
            async with db_lock:
                conn = sqlite3.connect(db_path, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT INTO creator_profile
                        (creator_address, history_status, coverage_mode,
                         first_seen_at, baselined_at, created_at, updated_at)
                    VALUES (?, 'baselined', 'full', ?, ?, ?, ?)
                    ON CONFLICT(creator_address) DO UPDATE SET
                        history_status = CASE
                            WHEN history_status = 'unknown' THEN 'baselined'
                            ELSE history_status
                        END,
                        coverage_mode  = CASE
                            WHEN coverage_mode = 'forward_only' THEN 'full'
                            ELSE coverage_mode
                        END,
                        baselined_at   = COALESCE(creator_profile.baselined_at, excluded.baselined_at),
                        updated_at     = excluded.updated_at
                """, (creator, first_seen or now, now, now, now))
                conn.commit()
                conn.close()
            count += 1
        except Exception as e:
            logger.warning("[MIGRATION_BRIDGE] Bootstrap failed for %s: %s", creator[:16], e)

    logger.info("[MIGRATION_BRIDGE] Bootstrap complete: %d profiles upserted", count)
    return count


# ---------------------------------------------------------------------------
# creator_tokens backfill — run once before Phase 2 to make token_count_seen
# consistent with COUNT(*) from creator_tokens for historical creators.
#
# Source: token_analysis.pf_ws_creator (most authoritative creator→mint mapping).
# Safe to re-run — INSERT OR IGNORE + UPDATE only increases counts.
#
# After running, verify with:
#   SELECT p.creator_address, p.token_count_seen, COUNT(t.mint) AS actual
#   FROM creator_profile p
#   LEFT JOIN creator_tokens t USING (creator_address)
#   GROUP BY p.creator_address
#   HAVING p.token_count_seen != actual;
# ---------------------------------------------------------------------------

async def backfill_creator_tokens(
    *,
    db_path: str,
    db_lock: asyncio.Lock,
) -> dict:
    """
    Populate creator_tokens from token_analysis.pf_ws_creator and reconcile
    token_count_seen in creator_profile to match.

    Returns {"links_inserted": int, "profiles_updated": int}.
    """
    logger.info("[MIGRATION_BRIDGE] creator_tokens backfill: reading token_analysis...")

    async with db_lock:
        conn = sqlite3.connect(db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row

        # Step 1: insert all known (creator, mint) pairs
        conn.execute("""
            INSERT OR IGNORE INTO creator_tokens (creator_address, mint, created_at)
            SELECT pf_ws_creator, mint, COALESCE(created_at, strftime('%s','now'))
            FROM token_analysis
            WHERE pf_ws_creator IS NOT NULL
        """)
        links_inserted = conn.execute("SELECT changes()").fetchone()[0]

        # Step 2: reconcile token_count_seen to match creator_tokens count
        conn.execute("""
            UPDATE creator_profile
            SET token_count_seen = (
                SELECT COUNT(*) FROM creator_tokens t
                WHERE t.creator_address = creator_profile.creator_address
            ),
            updated_at = strftime('%s','now')
            WHERE creator_address IN (
                SELECT DISTINCT creator_address FROM creator_tokens
            )
        """)
        profiles_updated = conn.execute("SELECT changes()").fetchone()[0]

        conn.commit()
        conn.close()

    logger.info(
        "[MIGRATION_BRIDGE] creator_tokens backfill complete: "
        "%d links inserted, %d profiles reconciled",
        links_inserted, profiles_updated,
    )
    return {"links_inserted": links_inserted, "profiles_updated": profiles_updated}
