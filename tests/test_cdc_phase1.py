"""
CDC Phase 1 validation — 6 checkpoints (pure DB/store, no network).

Run:  python3 -m pytest tests/test_cdc_phase1.py -v
"""
import sqlite3
import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.core.ws_cascade_store as store

# ── fixtures ────────────────────────────────────────────────────────────────

TREASURY  = "TREASURY1111111111111111111111111111111111111"
CDC_W     = "CDC_WALLET_111111111111111111111111111111111111"
FUND_SIG  = "sig_fund_0000000000000000000000000000000000000000"
OUT_SIG   = "sig_out_0000000000000000000000000000000000000000"
RECIP1    = "RECIP11111111111111111111111111111111111111111"
RECIP2    = "RECIP21111111111111111111111111111111111111111"


def _mem_conn():
    """In-memory ops DB with CDC schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store.ensure_cascade_schema(conn)
    return conn


# ── checkpoint 1 — CDC created on ≥50 SOL transfer ─────────────────────────

def test_register_cdc_created():
    conn = _mem_conn()
    created = store.register_cdc(
        conn,
        wallet=CDC_W,
        source_treasury=TREASURY,
        funding_sig=FUND_SIG,
        funding_amount_sol=84.0,
        block_time=1_700_000_000,
    )
    assert created is True
    row = conn.execute(
        "SELECT wallet, observation_state, funding_amount_sol FROM wt_capital_distributor_candidates "
        "WHERE wallet=?", (CDC_W,)).fetchone()
    assert row is not None, "CDC row not created"
    assert row["observation_state"] == "OBSERVING"
    assert row["funding_amount_sol"] == 84.0


def test_register_cdc_idempotent():
    conn = _mem_conn()
    store.register_cdc(conn, wallet=CDC_W, source_treasury=TREASURY,
                       funding_sig=FUND_SIG, funding_amount_sol=84.0, block_time=0)
    created2 = store.register_cdc(conn, wallet=CDC_W, source_treasury=TREASURY,
                                   funding_sig=FUND_SIG, funding_amount_sol=84.0, block_time=0)
    assert created2 is False, "second register should return False (already exists)"
    count = conn.execute("SELECT COUNT(*) FROM wt_capital_distributor_candidates").fetchone()[0]
    assert count == 1


# ── checkpoint 2 — kind=cdc subscription opens (mark_subscribed) ───────────

def test_cdc_mark_subscribed():
    conn = _mem_conn()
    store.register_cdc(conn, wallet=CDC_W, source_treasury=TREASURY,
                       funding_sig=FUND_SIG, funding_amount_sol=84.0, block_time=0)
    store.cdc_mark_subscribed(conn, wallet=CDC_W)
    row = conn.execute(
        "SELECT observation_state, subscription_started FROM wt_capital_distributor_candidates "
        "WHERE wallet=?", (CDC_W,)).fetchone()
    assert row["observation_state"] == "SUBSCRIBED"
    assert row["subscription_started"] is not None


# ── checkpoint 3 — outbound events recorded ─────────────────────────────────

def test_record_cdc_outbound():
    conn = _mem_conn()
    store.register_cdc(conn, wallet=CDC_W, source_treasury=TREASURY,
                       funding_sig=FUND_SIG, funding_amount_sol=84.0, block_time=0)
    store.cdc_mark_subscribed(conn, wallet=CDC_W)
    recipients = [(RECIP1, 10.5), (RECIP2, 5.25)]
    store.record_cdc_outbound(conn, cdc_wallet=CDC_W, sig=OUT_SIG,
                               block_time=1_700_001_000, recipients=recipients)
    rows = conn.execute(
        "SELECT recipient, amount_sol FROM wt_cdc_outbound_events "
        "WHERE cdc_wallet=? ORDER BY recipient", (CDC_W,)).fetchall()
    assert len(rows) == 2
    addrs = {r["recipient"] for r in rows}
    assert RECIP1 in addrs and RECIP2 in addrs


# ── checkpoint 4 — last_activity updates ────────────────────────────────────

def test_last_activity_updated():
    conn = _mem_conn()
    btime = 1_700_001_000
    store.register_cdc(conn, wallet=CDC_W, source_treasury=TREASURY,
                       funding_sig=FUND_SIG, funding_amount_sol=84.0, block_time=0)
    store.cdc_mark_subscribed(conn, wallet=CDC_W)
    store.record_cdc_outbound(conn, cdc_wallet=CDC_W, sig=OUT_SIG,
                               block_time=btime, recipients=[(RECIP1, 1.0)])
    row = conn.execute(
        "SELECT last_activity FROM wt_capital_distributor_candidates WHERE wallet=?",
        (CDC_W,)).fetchone()
    assert row["last_activity"] == btime


# ── checkpoint 5 — 60m quiet → INACTIVE + unsubscribe ───────────────────────

def test_expire_inactive_cdcs():
    conn = _mem_conn()
    old_activity = int(time.time()) - 4000   # > 3600s ago
    store.register_cdc(conn, wallet=CDC_W, source_treasury=TREASURY,
                       funding_sig=FUND_SIG, funding_amount_sol=84.0, block_time=0)
    store.cdc_mark_subscribed(conn, wallet=CDC_W)
    # manually backdate last_activity to simulate quiet period
    conn.execute("UPDATE wt_capital_distributor_candidates SET last_activity=? WHERE wallet=?",
                 (old_activity, CDC_W))
    conn.commit()
    cutoff = int(time.time()) - 3600
    expired = store.expire_inactive_cdcs(conn, cutoff)
    assert CDC_W in expired
    row = conn.execute(
        "SELECT observation_state, subscription_ended FROM wt_capital_distributor_candidates "
        "WHERE wallet=?", (CDC_W,)).fetchone()
    assert row["observation_state"] == "INACTIVE"
    assert row["subscription_ended"] is not None


def test_expire_skips_recently_active():
    conn = _mem_conn()
    store.register_cdc(conn, wallet=CDC_W, source_treasury=TREASURY,
                       funding_sig=FUND_SIG, funding_amount_sol=84.0, block_time=0)
    store.cdc_mark_subscribed(conn, wallet=CDC_W)
    # activity just now
    conn.execute("UPDATE wt_capital_distributor_candidates SET last_activity=? WHERE wallet=?",
                 (int(time.time()), CDC_W))
    conn.commit()
    cutoff = int(time.time()) - 3600
    expired = store.expire_inactive_cdcs(conn, cutoff)
    assert CDC_W not in expired


# ── checkpoint 6 — no ProgramWatcher / candidate rows ───────────────────────

def test_no_candidate_rows_created():
    conn = _mem_conn()
    store.register_cdc(conn, wallet=CDC_W, source_treasury=TREASURY,
                       funding_sig=FUND_SIG, funding_amount_sol=84.0, block_time=0)
    store.cdc_mark_subscribed(conn, wallet=CDC_W)
    store.record_cdc_outbound(conn, cdc_wallet=CDC_W, sig=OUT_SIG,
                               block_time=1_700_001_000, recipients=[(RECIP1, 50.0)])
    cand_count = conn.execute(
        "SELECT COUNT(*) FROM wt_candidate_websocket_watches").fetchone()[0]
    assert cand_count == 0, f"Expected 0 candidates, got {cand_count}"


def test_is_cdc_wallet():
    conn = _mem_conn()
    assert store.is_cdc_wallet(conn, CDC_W) is False
    store.register_cdc(conn, wallet=CDC_W, source_treasury=TREASURY,
                       funding_sig=FUND_SIG, funding_amount_sol=84.0, block_time=0)
    assert store.is_cdc_wallet(conn, CDC_W) is True
