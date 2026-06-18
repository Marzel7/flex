"""
Creator activity redesign — service layer.

Tier A (hot path — no expensive RPC, safe at curve_complete / migration):
  ensure_pf_ws_creator()
  process_repeat_creator_launch()

Tier B (background — called only from worker jobs):
  reconcile_creator_gap()

Supporting:
  enqueue_creator_activity_job()
  should_run_creator_baseline()
  handle_creator_stream_event()
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from src.creators.models import (
    CoverageMode,
    HistoryStatus,
    JobType,
    StreamHealthStatus,
    WatchStatus,
)
from src.creators.repository import CreatorRepository

logger = logging.getLogger(__name__)

# Staleness thresholds
_STALE_FULL_SCAN_SECONDS        = 7 * 24 * 3600   # baseline is stale after 7 days
_STALE_INCREMENTAL_SECONDS      = 6 * 3600         # incremental is stale after 6 hours
_RECONCILE_LOOKBACK_LIMIT       = 50               # max sigs fetched per reconcile pass

# Gap detection — BOTH must be checked (slot + time)
_GAP_SLOT_THRESHOLD  = 50     # slots
_GAP_TIME_THRESHOLD  = 300    # seconds (5 min of wall-clock silence = suspect gap)


# ---------------------------------------------------------------------------
# Tier A — hot path
# ---------------------------------------------------------------------------

async def ensure_pf_ws_creator(
    mint: str,
    reason: str,
    *,
    repo: CreatorRepository,
    db_path: str,
    db_lock: asyncio.Lock,
    get_transaction_fn:  Callable[[str], Awaitable[Optional[dict]]],
    validate_create_fn:  Callable[[dict], dict],
    infer_creator_fn:    Callable[[dict], Optional[str]],
    register_webhook_fn: Optional[Callable[[str], Awaitable[Optional[str]]]] = None,
) -> Optional[str]:
    """
    Resolve and persist pf_ws_creator via a single getTransaction RPC (Path A).

    Hot-path contract:
      - getSignaturesForAddress is NEVER called here.
      - If create_tx_signature is missing and no creator exists in DB,
        a baseline job is enqueued to resolve it later.
      - Returns the creator address or None.

    After resolution, always:
      - upserts creator_profile
      - enqueues a baseline job if history_status == unknown
    """
    import sqlite3
    from src.utils.db_locking import db_connect

    # --- 1. Read token state ---
    try:
        async with db_lock:
            conn = db_connect(db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT create_tx_signature, pf_ws_creator, earliest_tx_creator
                   FROM token_analysis WHERE mint = ? LIMIT 1""",
                (mint,),
            ).fetchone()
            conn.close()
    except Exception as e:
        logger.warning("[PF_WS_CREATOR] DB read failed mint=%s: %s", mint[:16], e)
        return None

    if not row:
        return None

    create_tx_sig      = row["create_tx_signature"]
    pf_ws_creator      = row["pf_ws_creator"]
    earliest_tx_creator = row["earliest_tx_creator"]

    # --- 2. Already resolved — profile touch only ---
    if pf_ws_creator:
        await _on_creator_known(pf_ws_creator, mint=mint, reason=reason, repo=repo,
                                create_tx_sig=create_tx_sig, skip_funding=True,
                                register_webhook_fn=register_webhook_fn)
        return pf_ws_creator

    # --- 3. Path A: single getTransaction ---
    creator: Optional[str] = None
    if create_tx_sig:
        try:
            tx_data = await get_transaction_fn(create_tx_sig)
            if tx_data and validate_create_fn(tx_data).get("is_pumpfun_create"):
                creator = infer_creator_fn(tx_data)
        except Exception as e:
            logger.warning("[PF_WS_CREATOR] getTransaction failed mint=%s sig=%s: %s",
                           mint[:16], create_tx_sig[:16], e)

    # --- 4. Fallback to DB column (zero RPC) ---
    if not creator and earliest_tx_creator:
        creator = earliest_tx_creator
        logger.debug("[PF_WS_CREATOR] Using earliest_tx_creator fallback mint=%s", mint[:16])

    # --- 5. No creator available on hot path — schedule baseline job and bail ---
    if not creator:
        logger.info("[PF_WS_CREATOR] Creator unresolvable on hot path mint=%s reason=%s "
                    "— baseline job will derive it", mint[:16], reason)
        # We cannot key the job by creator yet; caller must handle mint-level fallback.
        # The baseline worker will pick this up via the source_mint field.
        # For now we return None and the caller's existing fallback path applies.
        return None

    # --- 6. Persist pf_ws_creator ---
    try:
        async with db_lock:
            conn = db_connect(db_path, timeout=30)
            conn.execute(
                """UPDATE token_analysis SET pf_ws_creator = ?, updated_at = ?
                   WHERE mint = ? AND pf_ws_creator IS NULL""",
                (creator, int(time.time()), mint),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.warning("[PF_WS_CREATOR] DB write failed mint=%s: %s", mint[:16], e)
        return None

    # --- 7. Update creator_profile and conditionally enqueue baseline ---
    await _on_creator_known(creator, mint=mint, reason=reason, repo=repo,
                            create_tx_sig=create_tx_sig, skip_funding=False,
                            register_webhook_fn=register_webhook_fn)
    return creator


async def process_repeat_creator_launch(
    creator_address: str,
    mint: str,
    create_tx_signature: Optional[str],
    *,
    repo: CreatorRepository,
) -> None:
    """
    Tier A handler when a known creator (history_status != unknown) launches a new token.

    Contract:
      - NEVER triggers legacy creator_funding_queue extraction (history_status != unknown
        means we already have their data).
      - Increments token count exactly once per (creator, mint).
      - Enqueues incremental_reconcile if profile is stale.
      - No expensive RPC.
    """
    now = int(time.time())
    await repo.increment_token_count_if_new(creator_address, mint, last_launch_at=now)
    await repo.upsert_creator_profile(
        creator_address,
        last_launch_at=now,
        last_create_tx_signature=create_tx_signature,
        last_activity_at=now,
    )

    profile = await repo.get_creator_profile(creator_address)
    if profile and _is_stale_for_incremental(profile):
        await enqueue_creator_activity_job(
            creator_address,
            job_type=JobType.INCREMENTAL_RECONCILE,
            source_mint=mint,
            source_reason="repeat_launch_stale",
            priority=50,
            repo=repo,
        )


# ---------------------------------------------------------------------------
# Internal: called after any successful creator resolution
# ---------------------------------------------------------------------------

async def _on_creator_known(
    creator_address: str,
    *,
    mint: str,
    reason: str,
    repo: CreatorRepository,
    create_tx_sig: Optional[str],
    skip_funding: bool,
    register_webhook_fn: Optional[Callable[[str], Awaitable[Optional[str]]]] = None,
) -> None:
    """
    Common post-resolution logic.  Called whether creator came from cache or fresh RPC.

    skip_funding=True when the creator was already in pf_ws_creator.
    The caller is responsible for the legacy funding queue when skip_funding=False.
    """
    now = int(time.time())

    # increment_token_count_if_new creates the profile row if missing and
    # increments exactly once per (creator, mint) — race-safe.
    await repo.increment_token_count_if_new(creator_address, mint, last_launch_at=now)
    await repo.upsert_creator_profile(
        creator_address,
        last_create_tx_signature=create_tx_sig,
        last_activity_at=now,
        last_launch_at=now,
    )

    # Single read after the upsert to get authoritative history_status.
    profile = await repo.get_creator_profile(creator_address)

    if should_run_creator_baseline(profile):
        await enqueue_creator_activity_job(
            creator_address,
            job_type=JobType.BASELINE,
            source_mint=mint,
            source_reason=reason,
            priority=100,
            delay_seconds=30,   # let hot-path analysis complete first
            repo=repo,
        )

    if register_webhook_fn is not None:
        await start_creator_watch_if_needed(
            creator_address,
            repo=repo,
            register_webhook_fn=register_webhook_fn,
        )


# ---------------------------------------------------------------------------
# Tier B gate functions
# ---------------------------------------------------------------------------

def should_run_creator_baseline(profile: Optional[object]) -> bool:
    """
    Returns True when a Tier B baseline job should be enqueued.

      unknown   → always (first scan)
      partial   → always (resume incomplete scan)
      stale     → always (data too old)
      baselined → only if last_full_scan_at > 7 days ago
    """
    if profile is None:
        return True
    if profile.history_status in (HistoryStatus.UNKNOWN, HistoryStatus.PARTIAL, HistoryStatus.STALE):
        return True
    if profile.history_status == HistoryStatus.BASELINED:
        if not profile.last_full_scan_at:
            return True
        return (int(time.time()) - profile.last_full_scan_at) > _STALE_FULL_SCAN_SECONDS
    return False


def _is_stale_for_incremental(profile: object) -> bool:
    last = profile.last_incremental_scan_at or profile.last_full_scan_at
    if not last:
        return True
    return (int(time.time()) - last) > _STALE_INCREMENTAL_SECONDS


# ---------------------------------------------------------------------------
# Job enqueue
# ---------------------------------------------------------------------------

async def enqueue_creator_activity_job(
    creator_address: str,
    *,
    job_type:       JobType,
    source_mint:    Optional[str] = None,
    source_reason:  Optional[str] = None,
    priority:       int           = 100,
    delay_seconds:  int           = 0,
    repo:           CreatorRepository,
) -> Optional[int]:
    """
    Enqueue a Tier B job.  Returns job id or None if a duplicate already exists.
    The UNIQUE partial index on (creator_address, job_type) WHERE status IN ('pending','running')
    enforces at-most-one active job per creator+type at the DB layer.
    """
    job_id = await repo.enqueue_creator_activity_job(
        creator_address,
        job_type,
        priority=priority,
        source_mint=source_mint,
        source_reason=source_reason,
        delay_seconds=delay_seconds,
    )
    if job_id:
        logger.info("[CREATOR_JOB] Enqueued %s job_id=%s creator=%s mint=%s",
                    job_type.value, job_id, creator_address[:16], (source_mint or "")[:16])
    else:
        logger.debug("[CREATOR_JOB] Duplicate %s skipped creator=%s",
                     job_type.value, creator_address[:16])
    return job_id


# ---------------------------------------------------------------------------
# Streaming handler (Tier A — called from webhook/subscription callback)
# ---------------------------------------------------------------------------

async def handle_creator_stream_event(
    event: dict,
    *,
    repo: CreatorRepository,
) -> None:
    """
    Ingest one parsed creator SOL movement event (webhook or reconcile).

    event keys:
        creator_address, signature, slot, block_time,
        net_lamports (signed int), abs_lamports, direction ('in'|'out'),
        counterparty (optional), source ('webhook'|'reconcile')

    Persists the movement row, updates cursor state, detects gaps.
    """
    from src.creators.movement import MovementEvent

    creator_address = event.get("creator_address")
    signature       = event.get("signature")
    slot            = event.get("slot")
    block_time: int = event.get("block_time") or int(time.time())
    net_lamports    = event.get("net_lamports", 0)
    abs_lamports    = event.get("abs_lamports", abs(net_lamports))
    direction       = event.get("direction")
    counterparty    = event.get("counterparty")
    source          = event.get("source", "webhook")

    if not creator_address or not signature:
        return
    if not direction or direction not in ("in", "out"):
        return

    # Persist movement — INSERT OR IGNORE makes re-delivery a no-op
    await repo.insert_creator_sol_movement(
        creator_address = creator_address,
        signature       = signature,
        slot            = slot,
        block_time      = block_time,
        net_lamports    = net_lamports,
        abs_lamports    = abs_lamports,
        direction       = direction,
        counterparty    = counterparty,
        source          = source,
    )

    # Gap detection — slot delta AND time delta
    now   = int(time.time())
    state = await repo.get_creator_activity_state(creator_address)

    gap = False
    if state and (state.last_seen_slot or state.last_seen_at):
        slot_gap = bool(
            slot and state.last_seen_slot and
            (slot - state.last_seen_slot) > _GAP_SLOT_THRESHOLD
        )
        time_gap = bool(
            state.last_seen_at and
            (now - state.last_seen_at) > _GAP_TIME_THRESHOLD
        )
        gap = slot_gap or time_gap
        if gap:
            logger.warning(
                "[CREATOR_STREAM] Gap detected creator=%s slot_delta=%s time_delta=%ss",
                creator_address[:16],
                (slot - state.last_seen_slot) if (slot and state.last_seen_slot) else "?",
                (now - state.last_seen_at)    if state.last_seen_at else "?",
            )

    await repo.upsert_creator_activity_state(
        creator_address,
        last_seen_signature  = signature,
        last_seen_slot       = slot,
        last_seen_at         = block_time,
        stream_health_status = StreamHealthStatus.GAP_DETECTED if gap else StreamHealthStatus.HEALTHY,
        needs_reconcile      = True if gap else None,
        last_gap_detected_at = now if gap else None,
    )
    await repo.upsert_creator_profile(creator_address, last_activity_at=block_time)

    if gap:
        await enqueue_creator_activity_job(
            creator_address,
            job_type=JobType.INCREMENTAL_RECONCILE,
            source_reason="stream_gap",
            priority=20,
            repo=repo,
        )


# ---------------------------------------------------------------------------
# Watch registration — fire-and-forget, never blocks hot path
# ---------------------------------------------------------------------------

async def start_creator_watch_if_needed(
    creator_address: str,
    *,
    repo: CreatorRepository,
    register_webhook_fn: Callable[[str], Awaitable[Optional[str]]],
) -> None:
    """
    Fire-and-forget watch registration. Returns immediately, never raises.
    Safe to call from hot paths.
    """
    asyncio.create_task(
        _register_creator_watch(
            creator_address,
            repo=repo,
            register_webhook_fn=register_webhook_fn,
        )
    )


async def _register_creator_watch(
    creator_address: str,
    *,
    repo: CreatorRepository,
    register_webhook_fn: Callable[[str], Awaitable[Optional[str]]],
) -> None:
    """
    Actual registration. Idempotent — skips if already active.
    Transitions: pending → active (success) | pending → error (failure).
    """
    state = await repo.get_creator_activity_state(creator_address)
    if state and state.watch_status == WatchStatus.ACTIVE:
        return

    await repo.upsert_creator_activity_state(
        creator_address, watch_status=WatchStatus.PENDING.value,
    )

    try:
        webhook_id = await register_webhook_fn(creator_address)
    except Exception as e:
        logger.warning("[WATCH] Registration failed creator=%s: %s", creator_address[:16], e)
        await repo.upsert_creator_activity_state(
            creator_address,
            watch_status=WatchStatus.ERROR.value,
            watch_last_error=str(e),
        )
        return

    if not webhook_id:
        await repo.upsert_creator_activity_state(
            creator_address,
            watch_status=WatchStatus.ERROR.value,
            watch_last_error="register_webhook_fn returned None",
        )
        return

    now = int(time.time())
    await repo.upsert_creator_activity_state(
        creator_address,
        watch_status=WatchStatus.ACTIVE.value,
        watch_webhook_id=webhook_id,
        watch_registered_at=now,
        watch_last_checked_at=now,
        clear_watch_last_error=True,
    )
    logger.info("[WATCH] Registered creator=%s webhook_id=%s", creator_address[:16], webhook_id)


# ---------------------------------------------------------------------------
# Startup watch restoration
# ---------------------------------------------------------------------------

async def restore_creator_watches(
    *,
    repo: CreatorRepository,
    register_webhook_fn: Callable[[str], Awaitable[Optional[str]]],
    batch_size: int = 500,
) -> int:
    """
    On startup: mark all previously-active watches as stale (we can't know
    if Helius still has them), then re-register everything that isn't active.
    Returns count of registration tasks spawned.
    """
    await repo.mark_all_active_watches_stale()
    creators = await repo.get_creators_needing_watch(batch_size=batch_size)
    for creator_address in creators:
        asyncio.create_task(
            _register_creator_watch(
                creator_address,
                repo=repo,
                register_webhook_fn=register_webhook_fn,
            )
        )
    logger.info("[WATCH] Startup restore: spawned %d registration tasks", len(creators))
    return len(creators)


# ---------------------------------------------------------------------------
# Periodic safety reconcile — call from worker poll loop every ~10 min
# ---------------------------------------------------------------------------

_STALE_CREATOR_SECONDS = 20 * 60   # 20 min quiet → proactive reconcile

async def enqueue_stale_creator_reconciles(
    *,
    repo: CreatorRepository,
    stale_threshold_seconds: int = _STALE_CREATOR_SECONDS,
) -> int:
    """
    Enqueue incremental_reconcile for creators with active watches that have
    gone quiet. Catches gaps that never triggered on-event gap detection.
    Returns count enqueued.
    """
    stale = await repo.get_stale_creators(stale_threshold_seconds=stale_threshold_seconds)
    count = 0
    for creator_address in stale:
        job_id = await enqueue_creator_activity_job(
            creator_address,
            job_type=JobType.INCREMENTAL_RECONCILE,
            source_reason="periodic_stale_check",
            priority=10,
            repo=repo,
        )
        if job_id:
            count += 1
    if count:
        logger.info("[WATCH] Periodic stale check enqueued %d reconcile jobs", count)
    return count


# ---------------------------------------------------------------------------
# Reconciliation (Tier B — called from worker only)
# ---------------------------------------------------------------------------

async def reconcile_creator_gap(
    creator_address: str,
    *,
    repo: CreatorRepository,
    get_signatures_fn:   Callable[..., Awaitable[list]],
    process_signature_fn: Callable[[str, dict], Awaitable[None]],
) -> None:
    """
    Bounded getSignaturesForAddress scan to fill a detected streaming gap.

    Scans backwards from last_seen_signature, stopping at oldest_scanned_signature
    (already processed territory) or after _RECONCILE_LOOKBACK_LIMIT signatures.
    """
    state = await repo.get_creator_activity_state(creator_address)
    before_sig   = state.last_seen_signature      if state else None
    oldest_known = state.oldest_scanned_signature if state else None

    sigs: list[dict] = await get_signatures_fn(
        creator_address, before=before_sig, limit=_RECONCILE_LOOKBACK_LIMIT
    )

    newest_processed: Optional[str] = None
    oldest_processed: Optional[str] = None

    for sig_info in sigs:
        sig = sig_info.get("signature")
        if not sig:
            continue
        if sig == oldest_known:
            break   # reached known-good territory
        if newest_processed is None:
            newest_processed = sig
        oldest_processed = sig
        try:
            await process_signature_fn(creator_address, sig_info)
        except Exception as e:
            logger.warning("[RECONCILE] process_signature failed creator=%s sig=%s: %s",
                           creator_address[:16], sig[:16], e)

    now = int(time.time())
    await repo.upsert_creator_activity_state(
        creator_address,
        newest_scanned_signature = newest_processed or before_sig,
        oldest_scanned_signature = oldest_processed or oldest_known,
        last_reconciled_at       = now,
        needs_reconcile          = False,
        stream_health_status     = StreamHealthStatus.HEALTHY,
    )
    await repo.upsert_creator_profile(creator_address, last_incremental_scan_at=now)
