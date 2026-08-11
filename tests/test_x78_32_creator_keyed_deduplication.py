from __future__ import annotations

import sqlite3
import asyncio

import pytest

import src.core.creator_funding_worker as worker


def _queue_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE creator_funding_queue (
          creator_address TEXT NOT NULL,
          mint TEXT NOT NULL,
          migration_timestamp TEXT,
          create_tx_signature TEXT,
          attempts INTEGER DEFAULT 0,
          job_priority INTEGER DEFAULT 0,
          priority_reason TEXT,
          status TEXT DEFAULT 'pending',
          locked_until INTEGER DEFAULT 0,
          next_attempt_at INTEGER DEFAULT 0,
          created_at INTEGER NOT NULL,
          updated_at INTEGER,
          PRIMARY KEY (creator_address, mint)
        )
        """
    )
    conn.commit()
    conn.close()


def test_ready_selection_claims_at_most_one_row_per_creator(tmp_path, monkeypatch):
    path = str(tmp_path / "queue.db")
    _queue_db(path)
    conn = sqlite3.connect(path)
    conn.executemany(
        """INSERT INTO creator_funding_queue
           (creator_address,mint,created_at,status,locked_until,next_attempt_at,
            job_priority,priority_reason)
           VALUES (?,?,?,'pending',0,0,1,'test')""",
        [
            ("creator-a", "mint-a-old", 900),
            ("creator-a", "mint-a-new", 990),
            ("creator-b", "mint-b", 980),
            ("creator-c", "mint-c", 970),
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(worker, "DB_PATH", path)

    rows = worker._select_ready_rows(now=1_000, batch=3)

    assert [row["creator_address"] for row in rows] == [
        "creator-a",
        "creator-b",
        "creator-c",
    ]
    assert rows[0]["mint"] == "mint-a-new"


def test_creator_sibling_remains_pending_after_deduplicated_claim(tmp_path, monkeypatch):
    path = str(tmp_path / "queue.db")
    _queue_db(path)
    conn = sqlite3.connect(path)
    conn.executemany(
        """INSERT INTO creator_funding_queue
           (creator_address,mint,created_at,status,locked_until,next_attempt_at,
            job_priority,priority_reason)
           VALUES (?,?,?,'pending',0,0,1,'test')""",
        [("creator-a", "mint-a", 990), ("creator-a", "mint-b", 980)],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(worker, "DB_PATH", path)

    selected = worker._select_ready_rows(now=1_000, batch=5)
    claimed = worker._claim_selected_rows(now=1_000, rows=selected)

    assert [(row["creator_address"], row["mint"]) for row in claimed] == [
        ("creator-a", "mint-a")
    ]
    conn = sqlite3.connect(path)
    states = dict(conn.execute("SELECT mint,status FROM creator_funding_queue"))
    conn.close()
    assert states == {"mint-a": "running", "mint-b": "pending"}


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
async def test_two_slots_overlap_different_creators(monkeypatch):
    active = 0
    peak = 0
    both_active = asyncio.Event()

    async def process(_item):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            both_active.set()
        await asyncio.wait_for(both_active.wait(), timeout=1)
        active -= 1
        return "complete_fast"

    monkeypatch.setattr(worker, "_process_job", process)
    observed = {}
    results = await worker._run_claimed_rows(
        [_row("creator-a", "mint-a"), _row("creator-b", "mint-b")],
        slots=2,
        active_creators=observed,
    )

    assert peak == 2
    assert [outcome for _item, outcome in results] == [
        "complete_fast",
        "complete_fast",
    ]
    assert observed == {}


@pytest.mark.asyncio
async def test_same_creator_single_flight_waits_outside_slot(monkeypatch):
    active_deep = 0
    peak_deep = 0
    calls = 0

    async def process(_item):
        nonlocal active_deep, peak_deep, calls
        calls += 1
        if calls == 1:
            active_deep += 1
            peak_deep = max(peak_deep, active_deep)
            await asyncio.sleep(0.03)
            active_deep -= 1
            return "complete"
        # The sibling runs only after the leader's durable completion and
        # represents the authoritative known-creator fast path.
        assert active_deep == 0
        return "complete_fast"

    monkeypatch.setattr(worker, "_process_job", process)
    monkeypatch.setattr(worker, "_run_post_extraction_enrichment", lambda _c: asyncio.sleep(0))
    results = await worker._run_claimed_rows(
        [_row("creator-a", "mint-a"), _row("creator-a", "mint-b")],
        slots=2,
        active_creators={},
    )

    assert calls == 2
    assert peak_deep == 1
    assert [outcome for _item, outcome in results] == ["complete", "complete_fast"]


@pytest.mark.asyncio
async def test_one_creator_retry_does_not_cancel_other_slot(monkeypatch):
    async def process(item):
        if item["creator_address"] == "creator-a":
            await asyncio.sleep(0.03)
            return "retry"
        await asyncio.sleep(0.01)
        return "complete_fast"

    monkeypatch.setattr(worker, "_process_job", process)
    results = await worker._run_claimed_rows(
        [_row("creator-a", "mint-a"), _row("creator-b", "mint-b")],
        slots=2,
        active_creators={},
    )
    assert {item["creator_address"]: outcome for item, outcome in results} == {
        "creator-a": "retry",
        "creator-b": "complete_fast",
    }


@pytest.mark.asyncio
async def test_cancelled_creator_releases_single_flight(monkeypatch):
    started = asyncio.Event()

    async def process(_item):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "_process_job", process)
    flights = {}
    active = {}
    task = asyncio.create_task(
        worker._run_creator_scoped_row(
            _row("creator-a", "mint-a"), asyncio.Semaphore(1), flights, active
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert flights == {}
    assert active == {}
