"""X24.1 — mechanism-aware sub-provisioner observation.

Root cause (X24 reconciliation, confirmed against live production data): a
PLAIN_TRANSFER-funded sub-provisioner was subscribed via logsSubscribe(mentions:
[wallet]), which only fires on transactions whose program logs mention the
wallet. A plain system::transfer emits no such logs, so the subscription could
never observe the funding transaction — identical in kind to the reason
treasuries already use accountSubscribe instead of logsSubscribe.

These tests exercise SubscriptionManager.subscribe()/unsubscribe() directly
against a fake websocket (no real network, no live daemon) to prove:
  - PLAIN_TRANSFER selects accountSubscribe (the correct primitive)
  - WSOL_WRAP_CLOSE / SEEDED_ACCOUNT_CLOSE / unknown mechanisms are unchanged
    (still logsSubscribe) — existing behaviour preserved exactly
  - unsubscribe() sends the RPC method matching how the subscription was opened
  - duplicate subscribe() calls for the same wallet are no-ops (idempotent)
"""
from __future__ import annotations

import asyncio
import hashlib
import json

import base58
import pytest

from src.core.ws_cascade import SubscriptionManager


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, payload: str):
        self.sent.append(json.loads(payload))


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _pubkey(seed: str) -> str:
    """X24.9 — subscribe() now rejects non-32-byte pubkeys, so test fixtures need a
    real (deterministic, still-readable-by-seed) valid pubkey rather than a bare
    placeholder string like "TREASURY_WALLET_1"."""
    return base58.b58encode(hashlib.sha256(seed.encode()).digest()).decode()


@pytest.fixture
def mgr():
    m = SubscriptionManager()
    m.ws = _FakeWS()
    return m


def test_treasury_kind_uses_accountsubscribe(mgr):
    wallet = _pubkey("TREASURY_WALLET_1")
    _run(mgr.subscribe(wallet, "treasury"))
    msg = mgr.ws.sent[-1]
    assert msg["method"] == "accountSubscribe"
    assert msg["params"][0] == wallet


def test_subprov_account_kind_uses_accountsubscribe():
    """The new kind introduced by this fix: a PLAIN_TRANSFER-funded subprov must
    use accountSubscribe, exactly like the treasury tier, for the same reason."""
    m = SubscriptionManager()
    m.ws = _FakeWS()
    wallet = _pubkey("SUBPROV_PLAIN_1")
    _run(m.subscribe(wallet, "subprov_account"))
    msg = m.ws.sent[-1]
    assert msg["method"] == "accountSubscribe"
    assert msg["params"][0] == wallet


def test_ordinary_subprov_kind_still_uses_logssubscribe(mgr):
    """Regression guard: WSOL_WRAP_CLOSE / SEEDED_ACCOUNT_CLOSE sessions (kind="subprov")
    must keep the existing logsSubscribe behaviour unchanged."""
    wallet = _pubkey("SUBPROV_WRAP_CLOSE_1")
    _run(mgr.subscribe(wallet, "subprov"))
    msg = mgr.ws.sent[-1]
    assert msg["method"] == "logsSubscribe"
    assert msg["params"][0]["mentions"] == [wallet]


def test_hot_subprov_and_candidate_kinds_unaffected(mgr):
    _run(mgr.subscribe(_pubkey("HOT_1"), "hot_subprov"))
    assert mgr.ws.sent[-1]["method"] == "logsSubscribe"
    _run(mgr.subscribe(_pubkey("CAND_1"), "candidate"))
    assert mgr.ws.sent[-1]["method"] == "logsSubscribe"


def test_unsubscribe_sends_accountunsubscribe_for_account_based_kinds():
    """Regression: unsubscribe() previously always sent logsUnsubscribe regardless
    of kind, which was already wrong for the existing treasury tier (silently
    leaking the server-side subscription) and would have been wrong for the new
    subprov_account tier too. Both must now send the matching unsubscribe method."""
    m = SubscriptionManager()
    m.ws = _FakeWS()
    wallet = _pubkey("TREASURY_2")
    _run(m.subscribe(wallet, "treasury"))
    # simulate the subscription confirmation
    rid = next(r for r, ent in m.pending_req.items() if ent[0] == wallet)
    m.on_subscribe_confirmed(rid, 555)
    _run(m.unsubscribe(wallet))
    msg = m.ws.sent[-1]
    assert msg["method"] == "accountUnsubscribe"
    assert msg["params"] == [555]


def test_unsubscribe_sends_logsunsubscribe_for_logs_based_kinds():
    m = SubscriptionManager()
    m.ws = _FakeWS()
    wallet = _pubkey("SUBPROV_3")
    _run(m.subscribe(wallet, "subprov"))
    rid = next(r for r, ent in m.pending_req.items() if ent[0] == wallet)
    m.on_subscribe_confirmed(rid, 777)
    _run(m.unsubscribe(wallet))
    msg = m.ws.sent[-1]
    assert msg["method"] == "logsUnsubscribe"
    assert msg["params"] == [777]


def test_subprov_account_unsubscribe_uses_accountunsubscribe():
    m = SubscriptionManager()
    m.ws = _FakeWS()
    wallet = _pubkey("SUBPROV_ACCT_1")
    _run(m.subscribe(wallet, "subprov_account"))
    rid = next(r for r, ent in m.pending_req.items() if ent[0] == wallet)
    m.on_subscribe_confirmed(rid, 888)
    _run(m.unsubscribe(wallet))
    msg = m.ws.sent[-1]
    assert msg["method"] == "accountUnsubscribe"
    assert msg["params"] == [888]


def test_duplicate_subscribe_is_idempotent_no_second_send(mgr):
    """A wallet already subscribed (or pending) must not be re-subscribed —
    prevents duplicate candidate processing / duplicate WS subscriptions."""
    wallet = _pubkey("DUP_WALLET")
    _run(mgr.subscribe(wallet, "subprov_account"))
    sent_count_after_first = len(mgr.ws.sent)
    _run(mgr.subscribe(wallet, "subprov_account"))
    assert len(mgr.ws.sent) == sent_count_after_first  # no second send


def test_no_kind_is_ever_double_subscribed_via_multiple_primitives(mgr):
    """A single wallet must be subscribed through exactly ONE primitive at a
    time — subscribe() must never fire both accountSubscribe and logsSubscribe
    for the same wallet by default."""
    _run(mgr.subscribe(_pubkey("SINGLE_WALLET"), "subprov_account"))
    methods_used = {m["method"] for m in mgr.ws.sent}
    assert methods_used == {"accountSubscribe"}
