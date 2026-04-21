"""
Creator activity redesign — service layer.

These functions sit between the listener/migration code and the repository.
They implement the Tier A / Tier B split and all decision logic.

Tier A (hot path, no expensive RPC):
  - _ensure_pf_ws_creator()
  - _process_repeat_creator_launch()
  - _start_creator_watch_if_needed()

Tier B (background, expensive):
  - _should_run_creator_baseline()
  - _enqueue_creator_activity_job()
  - _reconcile_creator_gap()
  - _handle_creator_stream_event()
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, TYPE_CHECKING

from src.creators.models import (
    CreatorProfile,
    CreatorActivityState,
    HistoryStatus,
    CoverageMode,
    JobType,
    JobStatus,
    StreamHealthStatus,
    WebhookStatus,
)
from src.creators.repository import CreatorRepository

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Staleness threshold: if last full scan was older than this, consider a refresh.
_STALE_FULL_SCAN_SECONDS = 7 * 24 * 3600   # 7 days
# Staleness for incremental: more aggressive
_STALE_INCREMENTAL_SECONDS = 6 * 3600      # 6 hours
# Reconcile window: how far back we look for streaming gaps
_RECONCILE_LOOKBACK_SIGNATURES = 50


# ---------------------------------------------------------------------------
# Tier A — hot path
# ---------------------------------------------------------------------------

async def _ensure_pf_ws_creator(
    mint: str,
    reason: str,
    *,
    repo: CreatorRepository,
    db_path: str,
    db_lock: asyncio.Lock,
    get_transaction_fn,   # async (signature: str) -> Optional[dict]
    validate_create_fn,   # (tx_data: dict) -> dict  with {"is_pumpfun_create": bool}
    infer_creator_fn,     # (tx_data: dict) -> Optional[str]
    get_creator_fallback_fn,  # async (mint: str) -> Optional[str]  — Path B, expensive
    enqueue_funding_fn,   # async (...) — existing _enqueue_creator_funding_job
) -> Optional[str]:
    """
    Preferred single-RPC creator resolution.  Hot path safe.

    Decision:
      1. Read token_analysis for create_tx_signature, pf_ws_creator, earliest_tx_creator.
      2. If pf_ws_creator already set → touch creator_profile, return immediately.
      3. If create_tx_signature available → getTransaction (1 credit), validate, infer.
      4. If create_tx_signature missing and we are NOT on the hot path → kick off Path B job.
      5. Write pf_ws_creator to DB.
      6. Upsert creator_profile (first_seen, token_count).
      7. Enqueue funding job via existing mechanism.
      8. Start creator watch if needed.
    """
    from src.utils.db_locking import db_connect
    import sqlite3

    # --- Step 1: read token state ---
    try:
        async with db_lock:
            conn = db_connect(db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT create_tx_signature, pf_ws_creator, earliest_tx_creator FROM token_analysis WHERE mint = ? LIMIT 1",
                (mint,),
            ).fetchone()
            conn.close()
    except Exception as e:
        logger.warning("[PF_WS_CREATOR] DB read failed for %s: %s", mint[:16], e)
        return None

    if not row:
        return None

    create_tx_signature = row["create_tx_signature"]
    existing_pf_ws_creator = row["pf_ws_creator"]
    earliest_tx_creator = row["earliest_tx_creator"]

    # --- Step 2: already resolved ---
    if existing_pf_ws_creator:
        await _touch_creator_profile(existing_pf_ws_creator, repo=repo)
        return existing_pf_ws_creator

    # --- Step 3: resolve via create_tx_signature (Path A) ---
    creator: Optional[str] = None
    if create_tx_signature:
        tx_data = await get_transaction_fn(create_tx_signature)
        if tx_data:
            validation = validate_create_fn(tx_data)
            if validation.get("is_pumpfun_create"):
                creator = infer_creator_fn(tx_data)
        if not creator:
            logger.warning("[PF_WS_CREATOR] Path A failed for %s reason=%s", mint[:16], reason)

    # --- Step 4: no create_tx_signature on hot path → use earliest_tx_creator if available,
    #             otherwise schedule baseline job and return None ---
    if not creator:
        if earliest_tx_creator:
            creator = earliest_tx_creator
        else:
            # Schedule Path B as a background job rather than blocking the hot path
            if reason in ("curve_complete", "migration", "migration:pre_tracked"):
                await _enqueue_creator_activity_job(
                    creator_address=None,   # unknown — job will resolve it
                    mint=mint,
                    reason=reason,
                    job_type=JobType.BASELINE,
                    repo=repo,
                    # Stub: no creator yet, job picks up mint and derives creator internally
                    priority=10,
                )
            return None

    # --- Step 5: write pf_ws_creator ---
    try:
        async with db_lock:
            conn = db_connect(db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "UPDATE token_analysis SET pf_ws_creator = ?, updated_at = ? WHERE mint = ? AND pf_ws_creator IS NULL",
                (creator, int(time.time()), mint),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.warning("[PF_WS_CREATOR] DB write failed for %s: %s", mint[:16], e)
        return None

    # --- Step 6: upsert creator_profile ---
    now = int(time.time())
    await repo.upsert_creator_profile(
        creator,
        last_create_tx_signature=create_tx_signature,
        last_launch_at=now,
        last_activity_at=now,
    )
    await repo.increment_creator_token_count(creator, last_launch_at=now)

    # --- Step 7: enqueue funding job via existing mechanism ---
    await enqueue_funding_fn(creator, mint=mint, create_tx_signature=create_tx_signature, reason=reason)

    # --- Step 8: start creator watch ---
    await _start_creator_watch_if_needed(creator, repo=repo)

    return creator


async def _touch_creator_profile(creator_address: str, *, repo: CreatorRepository) -> None:
    """Update last_seen_at without any expensive work."""
    await repo.upsert_creator_profile(creator_address, last_activity_at=int(time.time()))


async def _process_repeat_creator_launch(
    creator_address: str,
    mint: str,
    create_tx_signature: Optional[str],
    *,
    repo: CreatorRepository,
    enqueue_funding_fn,
) -> None:
    """
    Tier A hot path for a creator we already know.

    Decision tree:
      - Increment token count.
      - Always enqueue funding job (cheap; existing queue deduplicates).
      - If profile is stale → schedule incremental_reconcile job (Tier B, background).
      - Never run full historical scan again.
    """
    now = int(time.time())
    profile = await repo.get_creator_profile(creator_address)

    await repo.increment_creator_token_count(creator_address, last_launch_at=now)

    # Always pass through existing funding queue (deduplicates internally)
    await enqueue_funding_fn(creator_address, mint=mint, create_tx_signature=create_tx_signature, reason="repeat_launch")

    if profile and _is_profile_stale_for_incremental(profile):
        await _enqueue_creator_activity_job(
            creator_address=creator_address,
            mint=mint,
            reason="repeat_launch_stale",
            job_type=JobType.INCREMENTAL_RECONCILE,
            repo=repo,
            priority=50,
        )


def _is_profile_stale_for_incremental(profile: CreatorProfile) -> bool:
    now = int(time.time())
    last = profile.last_incremental_scan_at or profile.last_full_scan_at
    if not last:
        return True
    return (now - last) > _STALE_INCREMENTAL_SECONDS


async def _start_creator_watch_if_needed(
    creator_address: str,
    *,
    repo: CreatorRepository,
    webhook_register_fn=None,   # optional: async (creator_address) -> bool
) -> None:
    """
    Register a wallet-level subscription/webhook once per creator.
    Currently a stub; wire in Helius webhook registration when available.
    """
    profile = await repo.get_creator_profile(creator_address)
    if profile and profile.webhook_status == WebhookStatus.ACTIVE:
        return  # already watching

    now = int(time.time())
    if webhook_register_fn:
        try:
            ok = await webhook_register_fn(creator_address)
            status = WebhookStatus.ACTIVE if ok else WebhookStatus.ERROR
        except Exception:
            status = WebhookStatus.ERROR
    else:
        # No webhook integration yet — mark as stopped so we know watch was attempted
        status = WebhookStatus.STOPPED

    await repo.upsert_creator_profile(
        creator_address,
        webhook_started_at=now if status == WebhookStatus.ACTIVE else None,
        webhook_status=status.value,
    )


# ---------------------------------------------------------------------------
# Tier B decision gate
# ---------------------------------------------------------------------------

def _should_run_creator_baseline(
    profile: Optional[CreatorProfile],
    activity_state: Optional[CreatorActivityState],
) -> bool:
    """
    Returns True if we should enqueue a full baseline scan.

    Rules:
      - Never if history_status == 'baselined' and last_full_scan_at is recent.
      - Always if history_status == 'unknown'.
      - If 'partial' and resume_cursor exists → re-enqueue to continue.
      - If 'baselined' but stale (> 7 days) → True for a refresh baseline.
      - If 'stale' → True.
    """
    if profile is None:
        return True

    if profile.history_status == HistoryStatus.UNKNOWN:
        return True

    if profile.history_status == HistoryStatus.PARTIAL:
        # Resume incomplete baseline
        return True

    if profile.history_status == HistoryStatus.STALE:
        return True

    if profile.history_status == HistoryStatus.BASELINED:
        now = int(time.time())
        if profile.last_full_scan_at and (now - profile.last_full_scan_at) > _STALE_FULL_SCAN_SECONDS:
            return True
        return False

    return False


# ---------------------------------------------------------------------------
# Enqueue helper
# ---------------------------------------------------------------------------

async def _enqueue_creator_activity_job(
    creator_address: Optional[str],
    mint: str,
    reason: str,
    job_type: JobType,
    *,
    repo: CreatorRepository,
    priority: int = 100,
    delay_seconds: int = 0,
) -> Optional[int]:
    """
    Enqueue a Tier B job.  Skipped if an active job of the same type already exists.
    Returns the new job ID or None if skipped.
    """
    if not creator_address:
        # Creator unresolved — we cannot key the job yet; caller should retry after resolution
        logger.debug("[CREATOR_JOB] Cannot enqueue %s job: creator_address unknown for mint=%s", job_type.value, mint[:16])
        return None

    job_id = await repo.enqueue_creator_activity_job(
        creator_address,
        job_type,
        priority=priority,
        source_mint=mint,
        source_reason=reason,
        delay_seconds=delay_seconds,
    )
    if job_id:
        logger.info("[CREATOR_JOB] Enqueued %s job id=%s creator=%s mint=%s", job_type.value, job_id, creator_address[:16], mint[:16])
    else:
        logger.debug("[CREATOR_JOB] Skipped duplicate %s job for creator=%s", job_type.value, creator_address[:16])
    return job_id


# ---------------------------------------------------------------------------
# Stream event handler (called from webhook callback / subscription handler)
# ---------------------------------------------------------------------------

async def _handle_creator_stream_event(
    event: dict,
    *,
    repo: CreatorRepository,
) -> None:
    """
    Process a streamed wallet event for a creator.

    event dict keys expected:
      creator_address, signature, slot, timestamp, event_type

    Updates creator_activity_state cursor.
    Flags gap if incoming slot < last_seen_slot - threshold.
    """
    creator_address = event.get("creator_address")
    signature       = event.get("signature")
    slot            = event.get("slot")
    ts              = event.get("timestamp") or int(time.time())

    if not creator_address or not signature:
        return

    now = int(time.time())
    state = await repo.get_creator_activity_state(creator_address)

    GAP_SLOT_THRESHOLD = 50  # slots; tune based on observed missed-event frequency

    needs_reconcile = False
    last_gap_detected_at = None

    if state and state.last_seen_slot and slot:
        slot_delta = slot - state.last_seen_slot
        if slot_delta > GAP_SLOT_THRESHOLD:
            logger.warning(
                "[CREATOR_STREAM] Gap detected creator=%s slot_delta=%d last=%s new=%s",
                creator_address[:16], slot_delta, state.last_seen_slot, slot,
            )
            needs_reconcile = True
            last_gap_detected_at = now

    await repo.upsert_creator_activity_state(
        creator_address,
        last_seen_signature=signature,
        last_seen_slot=slot,
        stream_health_status=StreamHealthStatus.GAP_DETECTED if needs_reconcile else StreamHealthStatus.HEALTHY,
        needs_reconcile=needs_reconcile if needs_reconcile else None,
        last_gap_detected_at=last_gap_detected_at,
    )

    await repo.upsert_creator_profile(creator_address, last_activity_at=ts)

    if needs_reconcile:
        await _enqueue_creator_activity_job(
            creator_address,
            mint="",
            reason="stream_gap",
            job_type=JobType.INCREMENTAL_RECONCILE,
            repo=repo,
            priority=20,
        )


# ---------------------------------------------------------------------------
# Reconciliation (Tier B, background)
# ---------------------------------------------------------------------------

async def _reconcile_creator_gap(
    creator_address: str,
    *,
    repo: CreatorRepository,
    get_signatures_fn,  # async (address, before=None, limit=50) -> list[dict]
    process_signature_fn,  # async (creator, sig_info) -> None
) -> None:
    """
    Bounded reconciliation after a detected streaming gap.

    Scans from last_seen_signature backwards by at most _RECONCILE_LOOKBACK_SIGNATURES.
    Stops when we hit oldest_scanned_signature (already processed).
    Updates cursor and clears needs_reconcile.
    """
    state = await repo.get_creator_activity_state(creator_address)
    before_sig = state.last_seen_signature if state else None
    oldest_known = state.oldest_scanned_signature if state else None

    sigs = await get_signatures_fn(creator_address, before=before_sig, limit=_RECONCILE_LOOKBACK_SIGNATURES)

    newest: Optional[str] = None
    oldest: Optional[str] = None

    for sig_info in sigs:
        sig = sig_info.get("signature")
        if sig == oldest_known:
            break   # We've reached previously processed territory
        if newest is None:
            newest = sig
        oldest = sig
        await process_signature_fn(creator_address, sig_info)

    now = int(time.time())
    await repo.upsert_creator_activity_state(
        creator_address,
        newest_scanned_signature=newest or before_sig,
        oldest_scanned_signature=oldest or oldest_known,
        last_reconciled_at=now,
        needs_reconcile=False,
        stream_health_status=StreamHealthStatus.HEALTHY,
    )
    await repo.upsert_creator_profile(creator_address, last_incremental_scan_at=now)
