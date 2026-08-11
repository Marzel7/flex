from __future__ import annotations

import asyncio

import pytest

import src.core.creator_funding_worker as worker
import src.extractors.realtime_creator_funding_extractor as extractor


def _row(creator: str, mint: str) -> dict:
    return {
        "creator_address": creator,
        "mint": mint,
        "migration_timestamp": "2026-08-10T00:00:00Z",
        "create_tx_signature": "sig",
        "attempts": 0,
        "job_priority": 1,
        "priority_reason": "test",
    }


@pytest.mark.asyncio
async def test_post_enrichment_releases_slot_but_keeps_creator_flight(monkeypatch):
    enrichment_started = asyncio.Event()
    release_enrichment = asyncio.Event()
    b_started = asyncio.Event()

    async def process(row):
        if row["creator_address"] == "creator-b":
            b_started.set()
        return "complete"

    async def enrich(creator):
        if creator == "creator-a":
            enrichment_started.set()
            await release_enrichment.wait()

    monkeypatch.setattr(worker, "_process_job", process)
    monkeypatch.setattr(worker, "_run_post_extraction_enrichment", enrich)
    flights = {}
    active = {}
    sem = asyncio.Semaphore(1)
    a = asyncio.create_task(worker._run_creator_scoped_row(_row("creator-a", "a"), sem, flights, active))
    await enrichment_started.wait()
    b = asyncio.create_task(worker._run_creator_scoped_row(_row("creator-b", "b"), sem, flights, active))
    await asyncio.wait_for(b_started.wait(), 0.2)
    assert "creator-a" in flights
    assert "creator-a" not in active
    release_enrichment.set()
    await asyncio.gather(a, b)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [5, 6])
async def test_rolling_refill_has_no_batch_tail_barrier(monkeypatch, count):
    rows = [_row(f"creator-{i}", f"mint-{i}") for i in range(count)]
    waiting = rows[2:]
    active = 0
    peak = 0
    starts = []

    async def process(row):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        starts.append(row["mint"])
        await asyncio.sleep(0.01 if row["mint"] == "mint-0" else 0.04)
        active -= 1
        return "complete_fast"

    async def claim_more(free, _excluded):
        claimed = waiting[:free]
        del waiting[:free]
        return claimed

    monkeypatch.setattr(worker, "_process_job", process)
    results = await worker._run_rolling_claim_window(
        rows[:2], slots=2, max_jobs=count, claim_more=claim_more,
        active_creators={},
    )
    assert peak == 2
    assert len(results) == count
    # The third job starts when the short first job finishes; it does not wait
    # for the second member of the original pair.
    assert starts[:3] == ["mint-0", "mint-1", "mint-2"]


@pytest.mark.asyncio
async def test_rpc_semaphore_records_wait_without_changing_ceiling():
    sem = extractor._TimedRPCSemaphore(1)
    ledger = {"rpc_calls": 0, "rpc_sem_wait_ms": 0.0, "rpc_sem_wait_max_ms": 0.0}
    token = extractor._ACTIVE_PHASE_LEDGER.set(ledger)
    try:
        await sem.acquire()
        waiter = asyncio.create_task(sem.acquire())
        await asyncio.sleep(0.015)
        sem.release()
        await waiter
        sem.release()
    finally:
        extractor._ACTIVE_PHASE_LEDGER.reset(token)
    assert ledger["rpc_calls"] == 2
    assert ledger["rpc_sem_wait_ms"] >= 10
    assert ledger["rpc_sem_wait_max_ms"] >= 10

