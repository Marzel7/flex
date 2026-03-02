#!/usr/bin/env python3
"""
Creator Outgoing Scan Scheduler

Runs every 60 seconds and processes due creators (K per tick).
Respects daily budget if configured.

Flow:
1. Check budget (exit if exhausted)
2. Get up to K due creators (ordered by tier, oldest first)
3. For each creator:
   - Load state (before_signature, last_head_signature)
   - Fetch 1 page (25 signatures)
   - Check change detection (first sig == last_head_signature?)
   - If no change: update next_scan_time only, done
   - If changed: proceed up to max_pages for tier
   - Update state, update tier
4. Sleep 60 seconds, loop
"""

import asyncio
import time
from datetime import datetime
from typing import Optional, List
import aiohttp

from creator_outgoing_scan_state import (
    migrate_creator_scan_state,
    ensure_creator_in_state,
    get_due_creators,
    load_creator_state,
    update_creator_state_after_scan,
    check_daily_budget,
    get_daily_budget_status,
)

# Must import after scan_state to avoid circular imports
from creator_outgoing_extractor import (
    rpc_get_signatures,
    helius_enhanced_parse,
    extract_outgoing_sol,
    insert_outgoing_rows,
    build_funding_chains_incremental,
    build_coordinated_edges_incremental,
    detect_and_update_networks_from_outgoing,
    calculate_and_store_self_funding,
    get_outgoing_limiter,
    OUTGOING_CONCURRENCY,
)

try:
    from rpc_metrics_recorder import record_request
except ImportError:
    def record_request(*args, **kwargs):
        pass


async def scan_creator_with_change_detection(
    session: aiohttp.ClientSession,
    creator_address: str,
    max_pages: int,
) -> tuple[List[str], Optional[str], Optional[str], int]:
    """
    Scan single creator with change detection and pagination.

    Returns (all_sigs, final_before_cursor, head_signature, credits_used)

    Flow:
    1. Load before_signature and last_head_signature from state
    2. Fetch 1 page (25 sigs)
    3. Check: first sig == last_head_signature? If yes, stop (no new work)
    4. Else: proceed up to max_pages, fetching and parsing

    Credits:
    - 10 per RPC call (getSignaturesForAddress)
    - 100 per batch enhanced parse (100 sigs = 1 request)
    """
    before_sig, last_head_sig = load_creator_state(creator_address)
    all_sigs = []
    head_signature = None
    final_before = None
    credits_used = 0
    sigs_for_batch = []

    # Fetch 1 page for change detection
    first_page = await rpc_get_signatures(
        session, creator_address, limit=25, before=before_sig,
        source_file="creator_outgoing_scheduler"
    )
    credits_used += 10  # 1 RPC call

    if not first_page:
        # No data; update and return
        update_creator_state_after_scan(creator_address, before_sig, last_head_sig, credits_used)
        return [], before_sig, last_head_sig, credits_used

    # Extract signatures
    page_sigs = [item.get("signature") for item in first_page if item.get("signature")]
    if not page_sigs:
        update_creator_state_after_scan(creator_address, before_sig, last_head_sig, credits_used)
        return [], before_sig, last_head_sig, credits_used

    # Change detection: is first sig same as last scan's head?
    if last_head_sig and page_sigs[0] == last_head_sig:
        # No new activity; just advance next_scan_time
        update_creator_state_after_scan(creator_address, before_sig, last_head_sig, credits_used)
        return [], before_sig, last_head_sig, credits_used

    # New activity detected; process up to max_pages
    all_sigs.extend(page_sigs)
    head_signature = page_sigs[0]
    final_before = page_sigs[-1] if page_sigs else before_sig
    sigs_for_batch.extend(page_sigs)

    # Fetch additional pages if max_pages > 1 and we haven't reached end
    for page_num in range(1, max_pages):
        if not page_sigs or len(page_sigs) < 25:
            # Reached end
            break

        next_page = await rpc_get_signatures(
            session, creator_address, limit=25, before=final_before,
            source_file="creator_outgoing_scheduler"
        )
        credits_used += 10  # Another RPC call

        if not next_page:
            break

        page_sigs = [item.get("signature") for item in next_page if item.get("signature")]
        if not page_sigs:
            break

        all_sigs.extend(page_sigs)
        final_before = page_sigs[-1]
        sigs_for_batch.extend(page_sigs)

    # Batch enhanced parse
    if sigs_for_batch:
        batches = [sigs_for_batch[i : i + 100] for i in range(0, len(sigs_for_batch), 100)]
        for batch_sigs in batches:
            parsed = await helius_enhanced_parse(session, batch_sigs)
            credits_used += 100  # 1 enhanced call per 100 sigs

            if parsed:
                outgoing_rows = extract_outgoing_sol(creator_address, parsed)
                if outgoing_rows:
                    insert_outgoing_rows(outgoing_rows)

    # Update state
    update_creator_state_after_scan(
        creator_address, final_before, head_signature, credits_used
    )

    return all_sigs, final_before, head_signature, credits_used


async def process_due_creators(limit: int = 50, concurrency: int = OUTGOING_CONCURRENCY):
    """
    Process up to K due creators.

    Parameters:
    - limit: max creators per tick (default 50)
    - concurrency: in-flight requests (default 3)

    Returns (creators_processed, credits_used)
    """
    # Check budget before starting
    if not check_daily_budget():
        budget_status = get_daily_budget_status()
        print(
            f"[SCHEDULER] 💰 Daily budget exhausted: {budget_status['used']}/{budget_status['budget']}",
            flush=True,
        )
        return 0, 0

    # Get due creators
    due = get_due_creators(limit=limit)
    if not due:
        print(f"[SCHEDULER] ℹ️ No due creators", flush=True)
        return 0, 0

    print(
        f"[SCHEDULER] 🔄 Processing {len(due)} due creators "
        f"({sum(1 for _, t, _ in due if t == 'hot')} hot, "
        f"{sum(1 for _, t, _ in due if t == 'warm')} warm, "
        f"{sum(1 for _, t, _ in due if t == 'cold')} cold)",
        flush=True,
    )

    async with aiohttp.ClientSession() as session:
        sem = asyncio.Semaphore(concurrency)
        total_credits = 0
        processed = 0

        async def process_one(creator_address: str, tier: str, max_pages: int):
            nonlocal total_credits, processed
            async with sem:
                try:
                    # Check budget before each creator
                    if not check_daily_budget():
                        return

                    all_sigs, final_before, head_sig, credits = await scan_creator_with_change_detection(
                        session, creator_address, max_pages
                    )
                    total_credits += credits
                    processed += 1

                    status = "changed" if all_sigs else "unchanged"
                    print(
                        f"[SCHEDULER] ✅ {creator_address[:8]}... ({tier}) "
                        f"{status}: {len(all_sigs)} sigs, {credits} cr",
                        flush=True,
                    )
                except Exception as e:
                    print(
                        f"[SCHEDULER] ❌ {creator_address[:8]}... error: {str(e)[:60]}",
                        flush=True,
                    )

        # Run all concurrently
        tasks = [
            process_one(creator_address, tier, max_pages)
            for creator_address, tier, max_pages in due
        ]
        await asyncio.gather(*tasks)

    # Post-process: rebuild chains, networks, self-funding
    if processed > 0:
        print(
            f"[SCHEDULER] 🔗 Rebuilding chains and networks for {processed} creators...",
            flush=True,
        )
        build_funding_chains_incremental()
        build_coordinated_edges_incremental()
        detect_and_update_networks_from_outgoing()
        calculate_and_store_self_funding()
        print(
            f"[SCHEDULER] ✅ Done: {processed} creators, {total_credits} credits",
            flush=True,
        )

    return processed, total_credits


def scheduler_loop_sync(tick_interval: int = 60, limit: int = 50):
    """
    Synchronous wrapper for scheduler loop.

    Runs every tick_interval seconds, processing up to limit creators per tick.
    """
    print(
        f"[SCHEDULER] 🚀 Starting scheduler (tick={tick_interval}s, limit={limit})",
        flush=True,
    )

    # Initialize schema and ensure state exists for all creators
    migrate_creator_scan_state()

    tick = 0
    while True:
        tick += 1
        start = time.time()

        try:
            # Run async scan
            processed, credits = asyncio.run(process_due_creators(limit=limit))

            elapsed = time.time() - start
            budget_status = get_daily_budget_status()

            if budget_status["status"] == "unlimited":
                print(
                    f"[SCHEDULER] 📊 Tick {tick}: {processed} creators, "
                    f"{credits} credits (elapsed: {elapsed:.1f}s)",
                    flush=True,
                )
            else:
                print(
                    f"[SCHEDULER] 📊 Tick {tick}: {processed} creators, "
                    f"{credits} credits, budget: {budget_status['remaining']}/{budget_status['budget']} "
                    f"({budget_status['percent_used']:.1f}% used) (elapsed: {elapsed:.1f}s)",
                    flush=True,
                )
        except Exception as e:
            print(f"[SCHEDULER] 💥 Tick {tick} error: {str(e)[:100]}", flush=True)

        # Sleep remainder of interval
        elapsed = time.time() - start
        sleep_time = max(0, tick_interval - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)


async def scheduler_loop_async(tick_interval: int = 60, limit: int = 50):
    """
    Async version of scheduler loop (for embedding in async app).

    Runs every tick_interval seconds, processing up to limit creators per tick.
    """
    print(
        f"[SCHEDULER] 🚀 Starting async scheduler (tick={tick_interval}s, limit={limit})",
        flush=True,
    )

    # Initialize schema
    migrate_creator_scan_state()

    tick = 0
    while True:
        tick += 1
        start = time.time()

        try:
            processed, credits = await process_due_creators(limit=limit)

            elapsed = time.time() - start
            budget_status = get_daily_budget_status()

            if budget_status["status"] == "unlimited":
                print(
                    f"[SCHEDULER] 📊 Tick {tick}: {processed} creators, "
                    f"{credits} credits (elapsed: {elapsed:.1f}s)",
                    flush=True,
                )
            else:
                print(
                    f"[SCHEDULER] 📊 Tick {tick}: {processed} creators, "
                    f"{credits} credits, budget: {budget_status['remaining']}/{budget_status['budget']} "
                    f"({budget_status['percent_used']:.1f}% used) (elapsed: {elapsed:.1f}s)",
                    flush=True,
                )
        except Exception as e:
            print(f"[SCHEDULER] 💥 Tick {tick} error: {str(e)[:100]}", flush=True)

        # Sleep remainder of interval
        elapsed = time.time() - start
        sleep_time = max(0, tick_interval - elapsed)
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)


if __name__ == "__main__":
    # Run scheduler synchronously
    scheduler_loop_sync(tick_interval=60, limit=50)
