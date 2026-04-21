"""
Tests for helius_watch.register_creator_address and the service-layer watch
lifecycle (_register_creator_watch, restore_creator_watches,
enqueue_stale_creator_reconciles).

Run with: python -m pytest tests/test_helius_watch.py -v
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest

import src.creators.helius_watch as hw
from src.creators.service import (
    _register_creator_watch,
    restore_creator_watches,
    enqueue_stale_creator_reconciles,
)
from src.creators.models import WatchStatus

CREATOR_A = "CreatorAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
CREATOR_B = "CreatorBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
WEBHOOK_ID = "wh-test-id-1234"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_session(current_addresses: list[str]):
    """Build a mock aiohttp session with GET → existing list, PUT → 200."""
    get_resp = AsyncMock()
    get_resp.status = 200
    get_resp.json = AsyncMock(return_value={
        "webhookURL": "https://example.com/hook",
        "webhookType": "enhanced",
        "transactionTypes": ["TRANSFER"],
        "accountAddresses": list(current_addresses),
    })
    get_resp.__aenter__ = AsyncMock(return_value=get_resp)
    get_resp.__aexit__ = AsyncMock(return_value=False)

    put_resp = AsyncMock()
    put_resp.status = 200
    put_resp.json = AsyncMock(return_value={"webhookId": WEBHOOK_ID})
    put_resp.__aenter__ = AsyncMock(return_value=put_resp)
    put_resp.__aexit__ = AsyncMock(return_value=False)

    session = AsyncMock()
    session.get = MagicMock(return_value=get_resp)
    session.put = MagicMock(return_value=put_resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session, put_resp


def _env(monkeypatch):
    monkeypatch.setenv("HELIUS_API_KEY", "test-api-key")
    monkeypatch.setenv("CREATOR_MOVEMENT_WEBHOOK_ID", WEBHOOK_ID)


# ---------------------------------------------------------------------------
# 1. register once when missing
# ---------------------------------------------------------------------------

def test_register_once_when_missing(monkeypatch):
    _env(monkeypatch)
    session, put_resp = _make_session([])

    with patch("src.creators.helius_watch.aiohttp.ClientSession", return_value=session):
        result = _run(hw.register_creator_address(CREATOR_A))

    assert result == WEBHOOK_ID
    # PUT was called once
    session.put.assert_called_once()
    # The payload sent to PUT contains CREATOR_A
    put_kwargs = session.put.call_args
    sent_addresses = put_kwargs[1]["json"]["accountAddresses"]
    assert CREATOR_A in sent_addresses


# ---------------------------------------------------------------------------
# 2. no-op when already present
# ---------------------------------------------------------------------------

def test_no_op_when_already_present(monkeypatch):
    _env(monkeypatch)
    session, put_resp = _make_session([CREATOR_A])

    with patch("src.creators.helius_watch.aiohttp.ClientSession", return_value=session):
        result = _run(hw.register_creator_address(CREATOR_A))

    assert result == WEBHOOK_ID
    # PUT must NOT be called — address is already present
    session.put.assert_not_called()


# ---------------------------------------------------------------------------
# 3. graceful None return when env vars absent
# ---------------------------------------------------------------------------

def test_returns_none_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("HELIUS_API_KEY", raising=False)
    monkeypatch.setenv("CREATOR_MOVEMENT_WEBHOOK_ID", WEBHOOK_ID)

    result = _run(hw.register_creator_address(CREATOR_A))
    assert result is None


def test_returns_none_when_webhook_id_missing(monkeypatch):
    monkeypatch.setenv("HELIUS_API_KEY", "test-api-key")
    monkeypatch.delenv("CREATOR_MOVEMENT_WEBHOOK_ID", raising=False)

    result = _run(hw.register_creator_address(CREATOR_A))
    assert result is None


# ---------------------------------------------------------------------------
# 4. concurrent calls in one process do not duplicate the address
# ---------------------------------------------------------------------------

def test_concurrent_calls_no_duplicate(monkeypatch):
    """
    Ten concurrent calls for the same address must result in exactly one PUT
    and the final address list must contain CREATOR_A exactly once.
    The module-level _webhook_lock serialises them; once the first call
    adds the address and returns, all subsequent callers see it in the list
    returned by GET (because our mock reflects the latest state) and skip PUT.
    """
    _env(monkeypatch)

    # Mutable state shared across all GET calls — simulates Helius responding
    # with the authoritative current list.
    state: dict = {"addresses": []}

    def make_get_resp():
        resp = AsyncMock()
        resp.status = 200
        # Capture current state at call time via a fresh async side_effect each time.
        async def _json():
            return {
                "webhookURL": "https://example.com/hook",
                "webhookType": "enhanced",
                "transactionTypes": ["TRANSFER"],
                "accountAddresses": list(state["addresses"]),
            }
        resp.json = AsyncMock(side_effect=_json)
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        return resp

    put_call_count = 0

    def make_put_resp(json_body):
        nonlocal put_call_count
        put_call_count += 1
        # Reflect the PUT in shared state
        state["addresses"] = list(json_body["accountAddresses"])
        resp = AsyncMock()
        resp.status = 200
        resp.json = AsyncMock(return_value={"webhookId": WEBHOOK_ID})
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        return resp

    session = AsyncMock()
    session.get = MagicMock(side_effect=lambda *a, **kw: make_get_resp())
    session.put = MagicMock(side_effect=lambda url, json, **kw: make_put_resp(json))
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    async def run_all():
        with patch("src.creators.helius_watch.aiohttp.ClientSession", return_value=session):
            results = await asyncio.gather(
                *[hw.register_creator_address(CREATOR_A) for _ in range(10)]
            )
        return results

    results = _run(run_all())

    # All calls return the webhook ID
    assert all(r == WEBHOOK_ID for r in results)
    # Exactly one PUT fired
    assert put_call_count == 1
    # Address appears exactly once in the final list
    assert state["addresses"].count(CREATOR_A) == 1


# ---------------------------------------------------------------------------
# 5. startup restore schedules re-registration without blocking startup
# ---------------------------------------------------------------------------

def test_restore_creator_watches_returns_immediately(monkeypatch):
    """
    restore_creator_watches must:
    - call mark_all_active_watches_stale
    - call get_creators_needing_watch
    - spawn tasks (not await them)
    - return count of creators immediately
    """
    repo = AsyncMock()
    repo.mark_all_active_watches_stale = AsyncMock()
    repo.get_creators_needing_watch = AsyncMock(return_value=[CREATOR_A, CREATOR_B])

    spawned_tasks = []

    async def run():
        async def fake_register(addr):
            return WEBHOOK_ID

        # Patch asyncio.create_task to capture tasks without running them
        with patch("src.creators.service.asyncio.create_task") as mock_create_task:
            mock_create_task.side_effect = lambda coro: spawned_tasks.append(coro) or coro
            count = await restore_creator_watches(
                repo=repo,
                register_webhook_fn=fake_register,
            )
        # Close captured coroutines so GC doesn't warn about unawaited coroutines.
        for coro in spawned_tasks:
            coro.close()
        return count

    count = _run(run())

    repo.mark_all_active_watches_stale.assert_awaited_once()
    repo.get_creators_needing_watch.assert_awaited_once()
    assert count == 2
    assert len(spawned_tasks) == 2


# ---------------------------------------------------------------------------
# 6. stale reconcile loop enqueues only quiet creators
# ---------------------------------------------------------------------------

def test_enqueue_stale_creator_reconciles_only_quiet(monkeypatch):
    """
    enqueue_stale_creator_reconciles must:
    - call get_stale_creators with the threshold
    - call enqueue_creator_activity_job for each stale creator
    - return count of jobs actually enqueued (job_id truthy)
    - not touch active/recent creators (they won't appear in get_stale_creators)
    """
    repo = AsyncMock()
    repo.get_stale_creators = AsyncMock(return_value=[CREATOR_A])
    # Return None for CREATOR_B (not in list) — simulate no-op for active creator

    enqueued = []

    async def fake_enqueue(creator_address, *, job_type, source_reason, priority, repo):
        enqueued.append(creator_address)
        return 42  # truthy job_id

    async def run():
        with patch("src.creators.service.enqueue_creator_activity_job", side_effect=fake_enqueue):
            return await enqueue_stale_creator_reconciles(
                repo=repo,
                stale_threshold_seconds=1200,
            )

    count = _run(run())

    repo.get_stale_creators.assert_awaited_once_with(stale_threshold_seconds=1200)
    assert count == 1
    assert enqueued == [CREATOR_A]
    # CREATOR_B was never touched
    assert CREATOR_B not in enqueued


# ---------------------------------------------------------------------------
# Bonus: _register_creator_watch idempotency under bursty resolution
# ---------------------------------------------------------------------------

def test_register_watch_skips_if_already_active():
    """
    _register_creator_watch must return early without calling register_webhook_fn
    when watch_status is already ACTIVE. Prevents redundant API calls during
    bursty creator resolution (many tokens from same creator in rapid succession).
    """
    from src.creators.models import CreatorActivityState

    active_state = CreatorActivityState(
        creator_address=CREATOR_A,
        watch_status=WatchStatus.ACTIVE,
        # all other fields default to None / False
    )
    repo = AsyncMock()
    repo.get_creator_activity_state = AsyncMock(return_value=active_state)

    register_fn = AsyncMock(return_value=WEBHOOK_ID)

    _run(_register_creator_watch(
        CREATOR_A,
        repo=repo,
        register_webhook_fn=register_fn,
    ))

    register_fn.assert_not_called()
    repo.upsert_creator_activity_state.assert_not_called()


def test_register_watch_marks_error_on_http_failure():
    """
    _register_creator_watch must catch register_webhook_fn exceptions and
    persist watch_status=error with the error message. Must not propagate.
    """
    repo = AsyncMock()
    repo.get_creator_activity_state = AsyncMock(return_value=None)

    async def failing_register(addr):
        raise RuntimeError("Helius 429 rate limit")

    _run(_register_creator_watch(
        CREATOR_A,
        repo=repo,
        register_webhook_fn=failing_register,
    ))

    calls = repo.upsert_creator_activity_state.call_args_list
    # First call: mark pending
    assert any(
        c.kwargs.get("watch_status") == WatchStatus.PENDING.value
        for c in calls
    )
    # Second call: mark error with message
    error_call = next(
        (c for c in calls if c.kwargs.get("watch_status") == WatchStatus.ERROR.value),
        None,
    )
    assert error_call is not None
    assert "429" in error_call.kwargs.get("watch_last_error", "")


if __name__ == "__main__":
    import traceback

    tests = [
        test_register_once_when_missing,
        test_no_op_when_already_present,
        test_returns_none_when_api_key_missing,
        test_returns_none_when_webhook_id_missing,
        test_concurrent_calls_no_duplicate,
        test_restore_creator_watches_returns_immediately,
        test_enqueue_stale_creator_reconciles_only_quiet,
        test_register_watch_skips_if_already_active,
        test_register_watch_marks_error_on_http_failure,
    ]

    # monkeypatch substitute for standalone __main__ runs
    class _MP:
        _saved = {}
        def setenv(self, k, v): self._saved[k] = os.environ.get(k); os.environ[k] = v
        def delenv(self, k, raising=True):
            self._saved[k] = os.environ.pop(k, None)
        def __del__(self):
            for k, v in self._saved.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v

    passed = failed = 0
    for t in tests:
        mp = _MP()
        try:
            import inspect
            sig = inspect.signature(t)
            args = [mp] if "monkeypatch" in sig.parameters else []
            t(*args)
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
