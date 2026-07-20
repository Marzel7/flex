"""X24.8 — Non-Acknowledging Standing-Watchlist Subscription Investigation.

Read-only diagnostic instrumentation only: SubscriptionManager.sub_kind_breakdown()
exposes per-kind (treasury/subprov/dust/candidate/...) lifetime sent/confirmed/
exhausted counts, so a future investigation can immediately tell "this whole
subscription tier never acks" apart from "one specific wallet within an
otherwise-healthy tier never acks" — the exact ambiguity this sprint had to
resolve by hand from raw log line-counts. No subscribe/retry behaviour changed.
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
    """X24.9 — subscribe() now rejects non-32-byte pubkeys; derive a valid one
    deterministically from a readable seed so fixtures stay self-documenting."""
    return base58.b58encode(hashlib.sha256(seed.encode()).digest()).decode()


@pytest.fixture
def mgr():
    m = SubscriptionManager()
    m.ws = _FakeWS()
    return m


def test_breakdown_starts_empty(mgr):
    assert mgr.sub_kind_breakdown() == {}


def test_sent_counted_by_kind(mgr):
    _run(mgr.subscribe(_pubkey("W1"), "dust"))
    _run(mgr.subscribe(_pubkey("W2"), "dust"))
    _run(mgr.subscribe(_pubkey("W3"), "treasury"))
    breakdown = mgr.sub_kind_breakdown()
    assert breakdown["dust"] == {"sent": 2, "confirmed": 0, "exhausted": 0}
    assert breakdown["treasury"] == {"sent": 1, "confirmed": 0, "exhausted": 0}


def test_confirmed_counted_by_kind(mgr):
    _run(mgr.subscribe(_pubkey("W1"), "dust"))
    rid = next(iter(mgr.pending_req))
    mgr.on_subscribe_confirmed(rid, sub_id=1)
    assert mgr.sub_kind_breakdown()["dust"] == {"sent": 1, "confirmed": 1, "exhausted": 0}


def test_distinguishes_whole_tier_failure_from_isolated_wallet_failure(mgr):
    """The exact ambiguity X24.8 needed to resolve: two dust wallets never ack while
    a third of the same kind does — the breakdown must show partial, not total,
    failure for that kind."""
    dust_ok = _pubkey("DUST_OK")
    dust_bad_1 = _pubkey("DUST_BAD_1")
    dust_bad_2 = _pubkey("DUST_BAD_2")
    _run(mgr.subscribe(dust_ok, "dust"))
    _run(mgr.subscribe(dust_bad_1, "dust"))
    _run(mgr.subscribe(dust_bad_2, "dust"))
    ok_rid = next(rid for rid, ent in mgr.pending_req.items() if ent[0] == dust_ok)
    mgr.on_subscribe_confirmed(ok_rid, sub_id=1)

    # Simulate the other two exhausting retries (mirrors Cascade.cleanup_pass()'s
    # exhaustion branch without needing the full async maintenance loop).
    for _ in (dust_bad_1, dust_bad_2):
        rid = next(iter(mgr.pending_req))
        wallet, kind = mgr.pending_req[rid][0], mgr.pending_req[rid][1]
        mgr.pending_req.pop(rid, None)
        mgr.wallet_kind.pop(wallet, None)
        mgr._exhausted_by_kind[kind] = mgr._exhausted_by_kind.get(kind, 0) + 1

    breakdown = mgr.sub_kind_breakdown()
    assert breakdown["dust"] == {"sent": 3, "confirmed": 1, "exhausted": 2}
    # A whole-tier failure (e.g. WS_PROMOTE_DISCOVERED-style outage) would instead
    # show confirmed == 0 for every sent wallet of that kind — distinguishable at a glance.
