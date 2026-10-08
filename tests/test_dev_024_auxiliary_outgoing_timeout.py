"""DEV-024: auxiliary outgoing observation must not replay core funding."""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

import src.core.creator_funding_worker as worker
import src.extractors.realtime_creator_funding_extractor as extractor_module


def _funding_db(tmp_path):
    path = str(tmp_path / "funding.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE creator_funders (creator_address TEXT, funder_address TEXT)"
    )
    conn.commit()
    conn.close()
    return path


@pytest.mark.asyncio
async def test_auxiliary_timeout_preserves_core_funding_and_cancels_outgoing_task(
    tmp_path, monkeypatch
):
    path = _funding_db(tmp_path)
    monkeypatch.setattr(extractor_module, "DB_PATH", path)
    monkeypatch.setattr(extractor_module, "AUXILIARY_OUTGOING_TIMEOUT_SECONDS", 0.01)
    cancelled = asyncio.Event()

    class FakeExtractor:
        async def process_new_token(self, creator, _migration):
            conn = sqlite3.connect(path)
            conn.execute("INSERT INTO creator_funders VALUES (?, ?)", (creator, "funder"))
            conn.commit()
            conn.close()
            return {"status": "success"}

        async def check_create_tx_for_jitotip(self, *_args):
            return None

        async def check_transfers_for_debridge(self, *_args):
            return None

        async def check_transfers_for_axiom(self, *_args):
            return None

        async def extract_outgoing_transfers(self, *_args):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

    async def get_fake_extractor():
        return FakeExtractor()

    monkeypatch.setattr(extractor_module, "get_extractor", get_fake_extractor)
    result = await extractor_module.extract_funding_for_new_token(
        "creator", "2026-10-08T00:00:00Z", "sig", "mint", observation_required=True
    )

    assert result["status"] == "success"
    assert result["auxiliary_observations"]["outgoing_transfer_scan"] == {
        "status": "incomplete_timeout",
        "reason": "auxiliary_deadline",
    }
    assert cancelled.is_set(), "the timed auxiliary task must be awaited through cancellation"
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM creator_funders WHERE creator_address='creator'").fetchone()[0] == 1
    conn.close()


@pytest.mark.asyncio
async def test_core_failure_still_propagates_before_auxiliary_observation(tmp_path, monkeypatch):
    monkeypatch.setattr(extractor_module, "DB_PATH", _funding_db(tmp_path))
    outgoing_called = False

    class FailingExtractor:
        async def process_new_token(self, *_args):
            raise RuntimeError("core provider failure")

        async def extract_outgoing_transfers(self, *_args):
            nonlocal outgoing_called
            outgoing_called = True

    async def get_failing_extractor():
        return FailingExtractor()

    monkeypatch.setattr(extractor_module, "get_extractor", get_failing_extractor)
    with pytest.raises(RuntimeError, match="core provider failure"):
        await extractor_module.extract_funding_for_new_token(
            "creator", "2026-10-08T00:00:00Z", "sig", "mint", observation_required=True
        )
    assert not outgoing_called


@pytest.mark.asyncio
async def test_worker_retries_a_genuine_core_failure(monkeypatch):
    async def core_failure(*_args, **_kwargs):
        raise RuntimeError("core provider failure")

    retried = []
    monkeypatch.setattr(worker, "record_event_fail_open", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(worker, "_funder_count", lambda _creator: 0)
    monkeypatch.setattr(worker, "_retry_on_nested_write", lambda fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(worker, "_mark_retry", lambda *args, **_kwargs: retried.append(args))
    monkeypatch.setattr(extractor_module, "extract_funding_for_new_token", core_failure)

    outcome = await worker._process_job(
        {
            "creator_address": "creator",
            "mint": "mint",
            "migration_timestamp": "2026-10-08T00:00:00Z",
            "create_tx_signature": "sig",
            "attempts": 0,
            "job_priority": 1,
            "priority_reason": "test",
        }
    )
    assert outcome == "retry"
    assert retried and retried[0][3] == "core provider failure"
