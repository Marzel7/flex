"""X27.7 — Restore WATCHTOWER Lifecycle Capture.

Root cause (confirmed live, 2026-07-17): wt_watchtower_launches stopped receiving
new rows because the live detection path was starved, not dead. Direct evidence:

  - GET /api/ops-v2/intel/detection-path-health showed primary_live_path=0 across
    a 30-day window — every live-detected launch came from catch_up_path/
    retry_recovery_path (the sweep backstop), never the intended live WS
    notification for the subprov itself.
  - GET /api/ops-v2/intel/ws-cascade showed sub_avg_ack_ms=11660.5 and
    sub_p0_avg_ack_ms=32829.4 — Helius subscription-ack latency has drifted to
    11-33s, well past the old COLD_SUB_STALE_SEC=10s.
  - SubscriptionManager.sweep_stale_pending() unconditionally dropped any COLD
    pending subscribe request past COLD_SUB_STALE_SEC, popping it from
    wallet_kind/pending_req with NO retry (unlike hot_subprov, which already had
    a resubscribe path) — so a subprov's subscription was raced out and
    permanently lost before Helius's own ack ever arrived.
  - 393 "dropped cold pending subscription" log lines confirm this fired
    routinely, not as a rare edge case.

Fix: COLD_SUB_STALE_SEC raised past observed ack latency (10s -> 45s) AND
sweep_stale_pending()/cleanup_pass() now resubscribe cold drops up to
COLD_SUB_RETRY_MAX attempts (mirroring the existing hot_subprov retry), instead
of abandoning them. Confirmation clears the retry counter.

These tests exercise SubscriptionManager directly (no live daemon, no network)
to prove the specific defect is fixed without touching detection thresholds,
classification, attribution, or schema.
"""
from __future__ import annotations

import asyncio
import hashlib
import json

import base58
import pytest

from src.core import ws_cascade
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


def test_cold_stale_threshold_exceeds_observed_ack_latency():
    """Regression guard for the exact root cause: the timeout must stay above the
    live-measured ack latency (sub_p0_avg_ack_ms=32829.4ms), not just the old 10s."""
    assert ws_cascade.COLD_SUB_STALE_SEC >= 45


def test_sweep_stale_pending_returns_kind_and_attempts(mgr):
    """dropped entries must carry (wallet, kind, attempts) so callers can resubscribe —
    the old 2-tuple (dropped_wallets, stale_hot) shape had no way to retry cold drops."""
    wallet_key = _pubkey("SUBPROV_1")
    _run(mgr.subscribe(wallet_key, "subprov"))
    # Simulate the passage of time past COLD_SUB_STALE_SEC without an ack.
    rid = next(iter(mgr.pending_req))
    wallet, kind, queued_at, state, priority, qpos, sent_at, gen = mgr.pending_req[rid]
    mgr.pending_req[rid] = (wallet, kind, queued_at - (ws_cascade.COLD_SUB_STALE_SEC + 1),
                             state, priority, qpos, sent_at - (ws_cascade.COLD_SUB_STALE_SEC + 1), gen)
    dropped, stale_hot = mgr.sweep_stale_pending()
    assert dropped == [(wallet_key, "subprov", 0)]
    assert stale_hot == []


async def _resubscribe_cold_drops(mgr):
    """Mirrors the exact retry logic added to Cascade.cleanup_pass(), isolated from
    the surrounding DB housekeeping (session/candidate expiry, CDC TTL, etc.) that
    cleanup_pass also performs — this keeps the test focused on the one behaviour
    X27.7 changed, without needing a live schema-backed connection."""
    dropped, _stale_hot = mgr.sweep_stale_pending()
    for w, kind, attempts in dropped:
        if attempts < ws_cascade.COLD_SUB_RETRY_MAX:
            mgr._cold_retry_count[w] = attempts + 1
            await mgr.subscribe(w, kind)
        else:
            mgr._cold_retry_count.pop(w, None)


def test_cold_drop_within_retry_budget_resubscribes(mgr):
    """A cold-dropped wallet must be resubscribed instead of abandoned, as long as
    it hasn't exhausted COLD_SUB_RETRY_MAX attempts — the exact behaviour that was
    previously entirely missing for cold (non-hot) kinds."""
    wallet_key = _pubkey("SUBPROV_1")
    _run(mgr.subscribe(wallet_key, "subprov"))
    rid = next(iter(mgr.pending_req))
    ent = mgr.pending_req[rid]
    mgr.pending_req[rid] = ent[:2] + (ent[2] - (ws_cascade.COLD_SUB_STALE_SEC + 1),) + ent[3:]

    _run(_resubscribe_cold_drops(mgr))

    assert mgr._cold_retry_count.get(wallet_key) == 1
    # Two subscribe sends total: the original + the retry resubscribe.
    assert len(mgr.ws.sent) == 2
    assert mgr.ws.sent[-1]["method"] == "logsSubscribe"


def test_cold_drop_exhausting_retry_budget_gives_up(mgr):
    """Once COLD_SUB_RETRY_MAX attempts are exhausted, the wallet is abandoned (not an
    infinite retry loop against a wallet Helius will never ack)."""
    wallet_key = _pubkey("SUBPROV_1")
    mgr._cold_retry_count[wallet_key] = ws_cascade.COLD_SUB_RETRY_MAX
    _run(mgr.subscribe(wallet_key, "subprov"))
    rid = next(iter(mgr.pending_req))
    ent = mgr.pending_req[rid]
    mgr.pending_req[rid] = ent[:2] + (ent[2] - (ws_cascade.COLD_SUB_STALE_SEC + 1),) + ent[3:]

    _run(_resubscribe_cold_drops(mgr))

    assert wallet_key not in mgr._cold_retry_count
    assert len(mgr.ws.sent) == 1  # only the original send; no retry resubscribe


def test_confirmation_clears_retry_counter(mgr):
    """A wallet that eventually confirms must not carry a stale retry count forward —
    otherwise a wallet that flaked once and later recovered would silently start
    closer to the retry ceiling on its next legitimate stall."""
    wallet_key = _pubkey("SUBPROV_1")
    mgr._cold_retry_count[wallet_key] = 2
    _run(mgr.subscribe(wallet_key, "subprov"))
    rid = next(iter(mgr.pending_req))
    mgr.on_subscribe_confirmed(rid, sub_id=999)
    assert wallet_key not in mgr._cold_retry_count


def test_hot_subprov_retry_behaviour_unchanged(mgr):
    """Regression guard: hot_subprov's existing 2s stale/retry path must be untouched
    by the cold-path fix."""
    assert ws_cascade.HOT_SUB_STALE_SEC == 2
    wallet_key = _pubkey("HOT_1")
    _run(mgr.subscribe(wallet_key, "hot_subprov"))
    rid = next(iter(mgr.pending_req))
    ent = mgr.pending_req[rid]
    mgr.pending_req[rid] = ent[:2] + (ent[2] - (ws_cascade.HOT_SUB_STALE_SEC + 1),) + ent[3:]
    dropped, stale_hot = mgr.sweep_stale_pending()
    assert dropped == []
    assert stale_hot == [wallet_key]
