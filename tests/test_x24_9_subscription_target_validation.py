"""X24.9 — Subscription Target Validation & Watchlist Integrity.

Proves invalid subscription targets (malformed pubkeys) are rejected at the
system boundary — before ever reaching pending_req, the retry cycle, or
Helius — rather than being discovered only after repeated websocket failures
(the exact investigation X24.8 had to run by hand).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from src.utils.pubkey_validation import is_valid_pubkey, invalid_reason
from src.core.ws_cascade import SubscriptionManager, ProgramCreateWatcher
from src.ops.subscription_target_audit import (
    audit_source, audit_all_sources, startup_validation_summary, recommend_remediation,
)


# ── Phase 1: canonical validator ────────────────────────────────────────────

VALID_WALLET = "DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK"        # 32 bytes
INVALID_33_BYTE = "EF11p7bnxFZMCktB73bBp55MnoLHFHMzxiTv5GhEFggq3"     # the real X24.8 wallet
INVALID_BASE58 = "not-valid-base58-!!!chars"


def test_valid_pubkey_accepted():
    assert is_valid_pubkey(VALID_WALLET) is True
    assert invalid_reason(VALID_WALLET) is None


def test_33_byte_pubkey_rejected():
    assert is_valid_pubkey(INVALID_33_BYTE) is False
    assert invalid_reason(INVALID_33_BYTE) == "WRONG_LENGTH_33_BYTES"


def test_malformed_base58_rejected():
    assert is_valid_pubkey(INVALID_BASE58) is False
    assert invalid_reason(INVALID_BASE58) == "BASE58_DECODE_ERROR"


def test_empty_and_none_rejected():
    assert is_valid_pubkey(None) is False
    assert is_valid_pubkey("") is False


def test_validator_is_deterministic():
    """Same input, same output, every time — no hidden state or randomness."""
    results = {is_valid_pubkey(INVALID_33_BYTE) for _ in range(20)}
    assert results == {False}


# ── Phase 4: runtime protection at SubscriptionManager.subscribe() ─────────

class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, payload: str):
        self.sent.append(json.loads(payload))


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def mgr():
    m = SubscriptionManager()
    m.ws = _FakeWS()
    return m


def test_invalid_wallet_never_enters_pending(mgr):
    _run(mgr.subscribe(INVALID_33_BYTE, "dust"))
    assert mgr.pending_req == {}
    assert INVALID_33_BYTE not in mgr.wallet_kind


def test_invalid_wallet_never_sends_to_provider(mgr):
    _run(mgr.subscribe(INVALID_33_BYTE, "dust"))
    assert mgr.ws.sent == []


def test_invalid_wallet_never_consumes_retry_budget(mgr):
    """Confirms invalid wallets can't reach sweep_stale_pending()'s retry path at all —
    there's nothing pending to retry."""
    _run(mgr.subscribe(INVALID_33_BYTE, "dust"))
    dropped, stale_hot = mgr.sweep_stale_pending()
    assert dropped == []
    assert stale_hot == []


def test_invalid_wallet_excluded_from_subscription_counts(mgr):
    _run(mgr.subscribe(INVALID_33_BYTE, "dust"))
    assert mgr._subs_sent_total == 0
    assert mgr._sent_by_kind == {}


def test_invalid_wallet_counted_as_rejected(mgr):
    _run(mgr.subscribe(INVALID_33_BYTE, "dust"))
    assert mgr._invalid_rejected_total == 1
    assert mgr._invalid_rejected_by_kind == {"dust": 1}


def test_valid_wallet_subscription_behaviour_unchanged(mgr):
    """No behaviour change for valid subscriptions — same send, same pending_req shape."""
    _run(mgr.subscribe(VALID_WALLET, "dust"))
    assert mgr._subs_sent_total == 1
    assert len(mgr.pending_req) == 1
    assert len(mgr.ws.sent) == 1
    assert mgr._invalid_rejected_total == 0


def test_add_candidates_rejects_invalid_wallet():
    pw = ProgramCreateWatcher()
    pw.add_candidates([{"candidate": INVALID_33_BYTE, "subprov": "x", "treasury": "y",
                         "wrap_sig": "sig", "wrap_time": 0, "amount": 1.0}], None)
    assert pw.active_candidates == {}
    assert pw._invalid_rejected_total == 1


def test_add_candidates_accepts_valid_wallet():
    pw = ProgramCreateWatcher()
    pw.add_candidates([{"candidate": VALID_WALLET, "subprov": "x", "treasury": "y",
                         "wrap_sig": "sig", "wrap_time": 0, "amount": 1.0}], None)
    assert VALID_WALLET in pw.active_candidates
    assert pw._invalid_rejected_total == 0


# ── Phase 2/3/6: subscription-source audit ──────────────────────────────────

@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE wt_confirmed_treasuries (treasury TEXT)")
    conn.execute("CREATE TABLE wt_active_subprov_sessions (subprov_wallet TEXT, state TEXT)")
    conn.execute("CREATE TABLE wt_discovered_subprovs (subprov TEXT, treasury_known INTEGER)")
    conn.execute("CREATE TABLE wt_dust_markers (wallet TEXT, active INTEGER)")
    conn.execute("CREATE TABLE wt_capital_distributor_candidates (wallet TEXT, observation_state TEXT)")
    conn.commit()
    return conn


def test_audit_source_detects_invalid_pubkey(db):
    db.execute("INSERT INTO wt_dust_markers VALUES (?, 1)", (INVALID_33_BYTE,))
    db.execute("INSERT INTO wt_dust_markers VALUES (?, 1)", (VALID_WALLET,))
    db.commit()
    audit = audit_source(db, "dust")
    assert audit["total_rows"] == 2
    assert audit["valid"] == 1
    assert audit["invalid"] == 1
    assert audit["invalid_wallets"][0]["wallet"] == INVALID_33_BYTE
    assert audit["invalid_wallets"][0]["reason"] == "WRONG_LENGTH_33_BYTES"


def test_audit_source_detects_duplicates(db):
    db.execute("INSERT INTO wt_dust_markers VALUES (?, 1)", (VALID_WALLET,))
    db.execute("INSERT INTO wt_dust_markers VALUES (?, 1)", (VALID_WALLET,))
    db.commit()
    audit = audit_source(db, "dust")
    assert audit["total_rows"] == 2
    assert audit["duplicates"] == 1


def test_audit_source_detects_disabled(db):
    db.execute("INSERT INTO wt_dust_markers VALUES (?, 0)", (VALID_WALLET,))
    db.commit()
    audit = audit_source(db, "dust")
    assert audit["disabled"] == 1


def test_audit_all_sources_covers_every_source(db):
    result = audit_all_sources(db)
    assert set(result.keys()) == {
        "treasury", "session_subprov", "promoted_subprov", "dust", "cdc",
    }


def test_startup_validation_summary_reports_by_source(db):
    db.execute("INSERT INTO wt_dust_markers VALUES (?, 1)", (INVALID_33_BYTE,))
    db.commit()
    summary = startup_validation_summary(db)
    assert summary["total_invalid"] == 1
    assert summary["invalid_by_source"]["dust"] == 1
    assert summary["invalid_by_source"]["treasury"] == 0
    assert "per_source" in summary


def test_startup_validation_never_silently_drops_a_failure(db):
    """Every source appears in the report, even ones with zero rows —
    absence of a source from the report would be a silent drop."""
    summary = startup_validation_summary(db)
    for source in ("treasury", "session_subprov", "promoted_subprov", "dust", "cdc"):
        assert source in summary["invalid_by_source"]


def test_recommend_remediation_never_mutates(db):
    db.execute("INSERT INTO wt_dust_markers VALUES (?, 1)", (INVALID_33_BYTE,))
    db.commit()
    before = db.execute("SELECT * FROM wt_dust_markers").fetchall()
    recs = recommend_remediation(db)
    after = db.execute("SELECT * FROM wt_dust_markers").fetchall()
    assert before == after
    assert len(recs) == 1
    assert recs[0]["wallet"] == INVALID_33_BYTE
    assert recs[0]["source"] == "dust"
    assert "recommendation" in recs[0]


def test_recommend_remediation_empty_when_all_valid(db):
    db.execute("INSERT INTO wt_dust_markers VALUES (?, 1)", (VALID_WALLET,))
    db.commit()
    assert recommend_remediation(db) == []
