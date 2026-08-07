"""X78.4 Phase 5 (continued): end-to-end regression proving the real
_process_job survives the exact live-observed failure sequence --
timeout, cancellation, grace-period overrun, straggling to_thread write
still holding the lease -- and that the WORKER'S NEXT WRITE (not just the
straggler itself) eventually succeeds via retry, instead of stalling
permanently like the pre-X78.4 worker did.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid

import pytest

import src.core.creator_funding_worker as cfw
import src.extractors.realtime_creator_funding_extractor as rtcfe
from src.core.database_write_service import _thread_write_lease


def _clear_thread_lease():
    if hasattr(_thread_write_lease, "owner"):
        del _thread_write_lease.owner


@pytest.fixture(autouse=True)
def _isolate():
    _clear_thread_lease()
    yield
    _clear_thread_lease()


@pytest.mark.asyncio
async def test_process_job_recovers_from_grace_period_overrun_via_retry(monkeypatch, tmp_path):
    """Mirrors the live sequence exactly: a job times out, its extraction
    task is cancelled, cancellation cleanup doesn't finish within the
    grace period because a to_thread-dispatched write is still genuinely
    running and holding the lease -- and THIS job's own retry/fail
    bookkeeping write must still eventually succeed (via
    _retry_on_nested_write), rather than permanently failing/stalling
    like the pre-X78.4 worker did (observed live: 27+ minutes of
    unbroken NestedDatabaseWriteError after an identical sequence)."""
    monkeypatch.setattr(cfw, "JOB_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(cfw, "EXTRACTION_CANCEL_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(cfw, "_WRITE_RETRY_BASE_SECONDS", 0.01)
    monkeypatch.setattr(cfw, "DB_PATH", str(tmp_path / "x.db"))

    import sqlite3
    schema_conn = sqlite3.connect(cfw.DB_PATH)
    schema_conn.execute("""
        CREATE TABLE IF NOT EXISTS creator_funding_queue (
            creator_address TEXT NOT NULL,
            mint TEXT NOT NULL,
            migration_timestamp TEXT,
            create_tx_signature TEXT,
            status TEXT DEFAULT 'pending',
            source TEXT,
            job_priority INTEGER DEFAULT 0,
            priority_reason TEXT,
            next_attempt_at INTEGER DEFAULT 0,
            locked_until INTEGER DEFAULT 0,
            attempts INTEGER DEFAULT 0,
            last_error TEXT,
            funding_extracted_at INTEGER,
            created_at INTEGER DEFAULT (strftime('%s','now')),
            updated_at INTEGER DEFAULT (strftime('%s','now')),
            PRIMARY KEY (creator_address, mint)
        )
    """)
    schema_conn.execute(
        "INSERT INTO creator_funding_queue (creator_address, mint, status, attempts) "
        "VALUES ('creatorAddrE2E', 'mintAddrE2E', 'running', 0)"
    )
    schema_conn.commit()
    schema_conn.close()

    release_gate = threading.Event()
    straggler_finished = threading.Event()

    async def fake_extract_funding_for_new_token(creator, migration_timestamp, create_tx_signature, mint):
        from src.core.database_write_service import acquire_write_lease, release_write_lease

        def slow_write_holding_lease():
            lease = acquire_write_lease(
                f"tracked:{os.path.realpath(cfw.DB_PATH)}", cfw.DB_PATH, str(uuid.uuid4()),
                "realtime_creator_funding_extractor.py:977 in _save_outgoing_transfer",
            )
            while not release_gate.is_set():
                time.sleep(0.01)
            release_write_lease(lease)
            straggler_finished.set()

        await asyncio.to_thread(slow_write_holding_lease)
        return {"status": "success"}

    monkeypatch.setattr(rtcfe, "extract_funding_for_new_token", fake_extract_funding_for_new_token)

    row = {
        "creator_address": "creatorAddrE2E",
        "mint": "mintAddrE2E",
        "migration_timestamp": "2024-01-01T00:00:00Z",
        "create_tx_signature": None,
        "attempts": 0,
        "job_priority": 0,
        "priority_reason": "test",
    }

    job_task = asyncio.ensure_future(cfw._process_job(row))

    # Give the job time to time out, attempt cancellation, and exceed the
    # grace period -- release the straggler shortly after, well within
    # the retry loop's window, so the job's own retry/fail write can
    # eventually succeed.
    async def release_after_delay():
        await asyncio.sleep(0.2)
        release_gate.set()

    asyncio.ensure_future(release_after_delay())

    # The core assertion: _process_job must eventually complete (not
    # stall forever) once the straggler genuinely finishes.
    await asyncio.wait_for(job_task, timeout=10.0)

    assert straggler_finished.is_set()
    assert getattr(_thread_write_lease, "owner", None) is None, (
        "no write lease should remain held after the job and its "
        "straggler have both finished"
    )
