"""Provider-free integration tests for the disabled loopback telemetry seam."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os

import base58
import pytest

from src.core.ws_cascade import (
    SUB_PRIORITY_TREASURY, SubscriptionManager,
    _start_treasury_subscription_snapshot_server,
    treasury_subscription_snapshot_http_response,
)


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _wallet(number):
    return base58.b58encode(hashlib.sha256(f"telemetry-{number}".encode()).digest()).decode()


def _selection(total=83, selected=29):
    return [{
        "address": _wallet(index), "registry_member": True, "selection_eligible": True,
        "selected": index < selected,
        "selection_priority": SUB_PRIORITY_TREASURY if index < selected else None,
        "selection_reason": "ROOT" if index < selected else "TREASURY_SUBSCRIPTION_CAP",
        "skip_failure_reason": None if index < selected else "TREASURY_SUBSCRIPTION_CAP",
    } for index in range(total)]


def _records(snapshot):
    return {row[0]: dict(zip(snapshot["record_fields"], row)) for row in snapshot["records"]}


def test_read_only_endpoint_exposes_actual_manager_state_only_after_selection_ready():
    manager = SubscriptionManager()
    status, body = treasury_subscription_snapshot_http_response(
        manager, b"GET /internal/treasury-subscriptions HTTP/1.1\r\n")
    assert status == 503 and json.loads(body)["error"] == "TREASURY_SNAPSHOT_NOT_READY"

    manager.ws = _FakeWS()
    manager.set_treasury_selection(_selection())
    for index in range(29):
        wallet = _wallet(index)
        _run(manager.subscribe(wallet, "treasury", priority=SUB_PRIORITY_TREASURY))
        request_id = next(rid for rid, entry in manager.pending_req.items() if entry[0] == wallet)
        manager.on_subscribe_confirmed(request_id, index + 1)
    before = list(manager.ws.sent)
    status, body = treasury_subscription_snapshot_http_response(
        manager, b"GET /internal/treasury-subscriptions HTTP/1.1\r\n")
    snapshot = json.loads(body)
    assert status == 200
    assert manager.ws.sent == before
    assert (snapshot["registry_count"], snapshot["selected_count"], snapshot["requested_count"],
            snapshot["acknowledged_count"], snapshot["active_count"], snapshot["skipped_count"]) == (83, 29, 29, 29, 29, 54)
    rows = _records(snapshot)
    assert rows[_wallet(0)]["subscription_state"] == "ACKNOWLEDGED_ACTIVE"
    assert rows[_wallet(82)]["subscription_state"] == "EXPLICITLY_SUPPRESSED"
    assert snapshot["serialized_bytes"] <= 32 * 1024


def test_unacknowledged_failure_reconnect_and_overflow_fail_closed_without_actions():
    manager = SubscriptionManager()
    manager.ws = _FakeWS()
    manager.set_treasury_selection(_selection(3, 3))
    _run(manager.subscribe(_wallet(0), "treasury", priority=SUB_PRIORITY_TREASURY))
    _run(manager.subscribe(_wallet(1), "treasury", priority=SUB_PRIORITY_TREASURY))
    failed_rid = next(rid for rid, entry in manager.pending_req.items() if entry[0] == _wallet(0))
    manager.on_subscribe_failed(failed_rid)
    manager._reconnect_gen = 1
    rows = _records(manager.treasury_subscription_snapshot())
    assert rows[_wallet(0)]["subscription_state"] == "FAILED_SUBSCRIPTION"
    assert rows[_wallet(1)]["subscription_state"] == "REQUEST_SENT_UNACKNOWLEDGED"
    assert rows[_wallet(2)]["subscription_state"] == "RECONNECTING"
    with pytest.raises(ValueError, match="TREASURY_SNAPSHOT_RECORD_LIMIT"):
        manager.set_treasury_selection(_selection(84, 0))


def test_disabled_server_is_noop_and_endpoint_never_accepts_unrelated_paths(monkeypatch):
    manager = SubscriptionManager()
    monkeypatch.delenv("WS_TREASURY_TELEMETRY_PORT", raising=False)
    assert _run(_start_treasury_subscription_snapshot_server(manager)) is None
    status, body = treasury_subscription_snapshot_http_response(manager, b"GET /else HTTP/1.1\r\n")
    assert status == 404 and json.loads(body)["error"] == "NOT_FOUND"
