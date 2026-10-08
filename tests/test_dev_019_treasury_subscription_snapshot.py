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


def _by_address(snapshot: dict) -> dict[str, dict]:
    return {
        row[0]: dict(zip(snapshot["record_fields"], row))
        for row in snapshot["records"]
    }


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
    assert snapshot["registry_count"] == 83
    assert snapshot["selected_count"] == 29
    assert snapshot["requested_count"] == 29
    assert snapshot["acknowledged_count"] == 29
    assert snapshot["active_count"] == 29
    assert snapshot["skipped_count"] == 54
    assert snapshot["failed_count"] == 0
    by_address = _by_address(snapshot)
    assert by_address[_wallet(0)] == {
        "address": _wallet(0), "registry_member": True, "selection_eligible": True,
        "selected": True, "requested": True,
        "acknowledged": True, "active": True,
        "selection_priority": SUB_PRIORITY_TREASURY,
        "selection_reason": "CONFIRMED_TREASURY_ROOT_TIER",
        "skip_failure_reason": None, "reconnect_generation": 0,
        "provider_subscription_id": 1, "subscription_state": "ACKNOWLEDGED_ACTIVE",
    }
    assert by_address[_wallet(82)]["selected"] is False
    assert by_address[_wallet(82)]["skip_failure_reason"] == "TREASURY_SUBSCRIPTION_CAP"
    assert by_address[_wallet(82)]["subscription_state"] == "EXPLICITLY_SUPPRESSED"
    assert snapshot["serialized_bytes"] <= 32 * 1024


def test_selected_wallet_without_runtime_state_is_explicit_not_silently_absent():
    manager = SubscriptionManager()
    manager.set_treasury_selection(_selection(1, 1))
    snapshot = manager.treasury_subscription_snapshot()
    record = _by_address(snapshot)[_wallet(0)]
    assert record["selected"] is True
    assert record["requested"] is False
    assert record["acknowledged"] is False
    assert record["active"] is False
    assert record["skip_failure_reason"] == "NO_RUNTIME_SUBSCRIPTION_STATE"
    assert record["subscription_state"] == "SELECTED_NOT_REQUESTED"


def test_snapshot_records_send_failure_without_transport_retry():
    manager = SubscriptionManager()
    manager.set_treasury_selection(_selection(1, 1))
    _run(manager.subscribe(_wallet(0), "treasury", priority=SUB_PRIORITY_TREASURY))
    record = _by_address(manager.treasury_subscription_snapshot())[_wallet(0)]
    assert record["skip_failure_reason"] == "NO_WEBSOCKET_CONNECTION"
    assert record["requested"] is False
    assert record["subscription_state"] == "DISCONNECTED"


def test_snapshot_distinguishes_sent_unacknowledged_provider_failure_and_reconnect():
    manager = SubscriptionManager()
    manager.ws = _FakeWS()
    manager.set_treasury_selection(_selection(3, 3))
    _run(manager.subscribe(_wallet(0), "treasury", priority=SUB_PRIORITY_TREASURY))
    _run(manager.subscribe(_wallet(1), "treasury", priority=SUB_PRIORITY_TREASURY))
    first_id = next(key for key, value in manager.pending_req.items() if value[0] == _wallet(0))
    manager.on_subscribe_failed(first_id)
    manager._reconnect_gen = 1
    snapshot = manager.treasury_subscription_snapshot()
    by_address = _by_address(snapshot)
    assert by_address[_wallet(0)]["subscription_state"] == "FAILED_SUBSCRIPTION"
    assert by_address[_wallet(1)]["subscription_state"] == "REQUEST_SENT_UNACKNOWLEDGED"
    assert by_address[_wallet(2)]["subscription_state"] == "RECONNECTING"
    assert snapshot["failed_count"] == 1


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
