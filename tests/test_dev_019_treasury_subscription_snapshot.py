"""DEV-019 bounded in-memory treasury subscription telemetry.

The snapshot is deliberately a pure SubscriptionManager method: these fixtures
use only a fake websocket and never open the operations database or transport.
"""
from __future__ import annotations

import asyncio
import hashlib

import base58
import pytest

from src.core.ws_cascade import SUB_PRIORITY_TREASURY, SubscriptionManager


class _FakeWS:
    def __init__(self):
        self.sent: list[str] = []

    async def send(self, payload: str):
        self.sent.append(payload)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _wallet(number: int) -> str:
    return base58.b58encode(hashlib.sha256(f"treasury-{number}".encode()).digest()).decode()


def _selection(count: int, selected: int) -> list[dict]:
    return [
        {
            "address": _wallet(index),
            "selected": index < selected,
            "selection_priority": SUB_PRIORITY_TREASURY if index < selected else None,
            "selection_reason": (
                "CONFIRMED_TREASURY_ROOT_TIER" if index < selected else "TREASURY_SUBSCRIPTION_CAP"
            ),
            "skip_failure_reason": None if index < selected else "TREASURY_SUBSCRIPTION_CAP",
        }
        for index in range(count)
    ]


def test_snapshot_distinguishes_29_active_from_54_not_selected():
    manager = SubscriptionManager()
    manager.ws = _FakeWS()
    manager.set_treasury_selection(_selection(83, 29))
    for index in range(29):
        wallet = _wallet(index)
        _run(manager.subscribe(wallet, "treasury", priority=SUB_PRIORITY_TREASURY))
        request_id = next(key for key, value in manager.pending_req.items() if value[0] == wallet)
        manager.on_subscribe_confirmed(request_id, index + 1)

    snapshot = manager.treasury_subscription_snapshot()
    assert snapshot["record_count"] == 83
    assert snapshot["selected_count"] == 29
    assert snapshot["active_count"] == 29
    by_address = {record["address"]: record for record in snapshot["records"]}
    assert by_address[_wallet(0)] == {
        "address": _wallet(0), "selected": True, "requested": True,
        "acknowledged": True, "active": True,
        "selection_priority": SUB_PRIORITY_TREASURY,
        "selection_reason": "CONFIRMED_TREASURY_ROOT_TIER",
        "skip_failure_reason": None, "reconnect_generation": 0,
    }
    assert by_address[_wallet(82)]["selected"] is False
    assert by_address[_wallet(82)]["skip_failure_reason"] == "TREASURY_SUBSCRIPTION_CAP"
    assert snapshot["serialized_bytes"] <= 32 * 1024


def test_selected_wallet_without_runtime_state_is_explicit_not_silently_absent():
    manager = SubscriptionManager()
    manager.set_treasury_selection(_selection(1, 1))
    snapshot = manager.treasury_subscription_snapshot()
    record = snapshot["records"][0]
    assert record["selected"] is True
    assert record["requested"] is False
    assert record["acknowledged"] is False
    assert record["active"] is False
    assert record["skip_failure_reason"] == "NO_RUNTIME_SUBSCRIPTION_STATE"


def test_snapshot_records_send_failure_without_transport_retry():
    manager = SubscriptionManager()
    manager.set_treasury_selection(_selection(1, 1))
    _run(manager.subscribe(_wallet(0), "treasury", priority=SUB_PRIORITY_TREASURY))
    record = manager.treasury_subscription_snapshot()["records"][0]
    assert record["skip_failure_reason"] == "NO_WEBSOCKET_CONNECTION"
    assert record["requested"] is False


def test_snapshot_is_bounded_and_has_no_transport_side_effect():
    manager = SubscriptionManager()
    fake_ws = _FakeWS()
    manager.ws = fake_ws
    manager.set_treasury_selection(_selection(83, 0))
    before = list(fake_ws.sent)
    snapshot = manager.treasury_subscription_snapshot()
    assert fake_ws.sent == before
    assert snapshot["serialized_bytes"] <= 32 * 1024
    manager._treasury_snapshot_max_bytes = 1
    with pytest.raises(ValueError, match="TREASURY_SNAPSHOT_BYTE_LIMIT"):
        manager.treasury_subscription_snapshot()


def test_snapshot_rejects_over_limit_or_duplicate_selection_before_runtime_use():
    manager = SubscriptionManager()
    with pytest.raises(ValueError, match="TREASURY_SNAPSHOT_RECORD_LIMIT"):
        manager.set_treasury_selection(_selection(84, 0))
    duplicate = _selection(2, 1)
    duplicate[1]["address"] = duplicate[0]["address"]
    with pytest.raises(ValueError, match="TREASURY_SNAPSHOT_DUPLICATE_ADDRESS"):
        manager.set_treasury_selection(duplicate)
