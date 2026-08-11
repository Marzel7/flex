from __future__ import annotations

import asyncio
from contextlib import contextmanager
import sqlite3

import pytest

import src.core.creator_funding_worker as worker
import src.core.pumpfun_curve_listener as listener_module
import src.extractors.realtime_creator_funding_extractor as extractor_module


def _queue_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE creator_funders (
            creator_address TEXT, funder_address TEXT, fully_analyzed INTEGER,
            last_analyzed INTEGER
        );
        CREATE TABLE creator_funding_queue (
            creator_address TEXT NOT NULL, mint TEXT NOT NULL,
            migration_timestamp TEXT, create_tx_signature TEXT,
            status TEXT DEFAULT 'pending', source TEXT,
            next_attempt_at INTEGER DEFAULT 0, locked_until INTEGER DEFAULT 0,
            attempts INTEGER DEFAULT 0, last_error TEXT,
            funding_enqueued_at INTEGER, funding_extracted_at INTEGER,
            curve_completed_slot INTEGER, enqueued_slot INTEGER,
            job_priority INTEGER DEFAULT 0, priority_reason TEXT,
            observation_required INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER, updated_at INTEGER,
            PRIMARY KEY (creator_address, mint)
        );
        CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, funding_job_enqueued_slot INTEGER);
        """
    )


@pytest.mark.asyncio
async def test_marked_enqueue_bypasses_listener_cache_and_persists_marker(tmp_path, monkeypatch):
    path = tmp_path / "queue.db"
    connection = sqlite3.connect(path)
    _queue_schema(connection)
    connection.execute(
        "INSERT INTO creator_funders VALUES ('creator','funder',1,?)",
        (2_000_000_000,),
    )
    connection.execute("INSERT INTO token_analysis VALUES ('fresh-mint',NULL)")
    connection.commit()
    connection.close()

    monkeypatch.setattr(listener_module, "DB_PATH", str(path))
    monkeypatch.setattr(listener_module.time, "time", lambda: 2_000_000_001)

    class Subject:
        db_lock = asyncio.Lock()
        _creator_funding_queue_wakeup = asyncio.Event()
        DISCOVERY_CRITICAL_WINDOW_SECONDS = 0

    subject = Subject()
    method = listener_module.PumpFunCurveListener._enqueue_creator_funding_job

    # Default behaviour remains the existing creator-level cache hit.
    assert await method(subject, "creator", mint="cached-mint", migration_timestamp=None)
    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT count(*) FROM creator_funding_queue WHERE mint='cached-mint'"
    ).fetchone()[0] == 0
    connection.close()

    assert await method(
        subject, "creator", mint="fresh-mint", migration_timestamp="2026-08-11T00:00:00Z",
        source="reviewed_migration", observation_required=True,
    )
    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT observation_required FROM creator_funding_queue WHERE mint='fresh-mint'"
    ).fetchone()[0] == 1
    connection.close()


def test_marked_satisfied_row_is_not_reconciled_away(tmp_path, monkeypatch):
    path = tmp_path / "queue.db"
    connection = sqlite3.connect(path)
    _queue_schema(connection)
    connection.execute("INSERT INTO creator_funders VALUES ('creator','funder',1,1)")
    connection.execute(
        "INSERT INTO creator_funding_queue "
        "(creator_address,mint,status,next_attempt_at,locked_until,attempts,"
        "observation_required,created_at,updated_at) VALUES "
        "('creator','ordinary','pending',0,0,0,0,1,1),"
        "('creator','observed','pending',0,0,0,1,2,2)"
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(worker, "DB_PATH", str(path))

    assert worker._recover_stale_rows(10) == (1, 0)
    connection = sqlite3.connect(path)
    assert dict(connection.execute(
        "SELECT mint,status FROM creator_funding_queue"
    )) == {"ordinary": "complete", "observed": "pending"}
    connection.close()


@pytest.mark.asyncio
async def test_worker_passes_marker_to_extractor(tmp_path, monkeypatch):
    seen = []

    async def extract(*args, **kwargs):
        seen.append((args[3], kwargs["observation_required"]))
        return {"status": "fresh"}

    monkeypatch.setattr(extractor_module, "extract_funding_for_new_token", extract)
    monkeypatch.setattr(worker, "_funder_count", lambda _creator: 1)
    monkeypatch.setattr(worker, "_mark_complete", lambda *_args, **_kwargs: None)
    row = {
        "creator_address": "creator", "mint": "current-mint",
        "migration_timestamp": "2026-08-11T00:00:00Z", "create_tx_signature": "sig",
        "attempts": 0, "observation_required": 1,
        "job_priority": 0, "priority_reason": "reviewed_migration",
    }
    assert await worker._process_job(row) == "complete"
    assert seen == [("current-mint", True)]


@pytest.mark.asyncio
async def test_marked_extraction_bypasses_cache_with_current_mint_lineage(tmp_path, monkeypatch):
    path = tmp_path / "funding.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE creator_funders (creator_address TEXT, funder_address TEXT)")
    connection.execute("INSERT INTO creator_funders VALUES ('creator','funder')")
    connection.commit()
    connection.close()
    monkeypatch.setattr(extractor_module, "DB_PATH", str(path))

    class ReachedFreshAcquisition(RuntimeError):
        pass

    async def local_extractor():
        return object()

    seen_scopes = []

    @contextmanager
    def acquisition_scope(**kwargs):
        seen_scopes.append(kwargs)
        raise ReachedFreshAcquisition
        yield

    monkeypatch.setattr(extractor_module, "get_extractor", local_extractor)
    monkeypatch.setattr(extractor_module, "acquisition_scope", acquisition_scope)
    with pytest.raises(ReachedFreshAcquisition):
        await extractor_module.extract_funding_for_new_token(
            "creator", "2026-08-11T00:00:00Z", "sig", "current-mint",
            observation_required=True,
        )
    assert seen_scopes == [{
        "purpose": "creator_funding", "creator": "creator", "launch": "current-mint",
    }]
