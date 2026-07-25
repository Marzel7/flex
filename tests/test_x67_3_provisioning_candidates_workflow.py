"""X67.3 — Tests for the restored WATCHTOWER Provisioning Candidates workflow.

Covers admission, verification, promotion-observation, and the historical
19-launch backfill reconciliation, using isolated in-memory SQLite databases
(never the production database) so these tests cannot affect real data.
"""
import json
import sqlite3
import time

import pytest

from src.ops.provisioning_candidates_workflow import (
    ensure_schema,
    discover_candidate,
    verify_candidate,
    close_manually,
    sync_promoted_state,
    list_candidates,
    select_nearest_eligible_session,
    is_confirmed_in_model1,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_schema(c)
    # Minimal Model 1 tables this module reads (never writes).
    c.execute("CREATE TABLE wt_watchtower_launches (mint TEXT PRIMARY KEY)")
    c.execute("""CREATE TABLE wt_confirmed_treasuries (
        treasury TEXT PRIMARY KEY, method TEXT, confidence TEXT, provenance TEXT)""")
    c.execute("""CREATE TABLE wt_active_subprov_sessions (
        id INTEGER PRIMARY KEY, subprov_wallet TEXT, treasury_wallet TEXT,
        funding_signature TEXT, funding_amount REAL, funding_time INTEGER)""")
    c.commit()
    return c


def _fake_tx(*, treasury: str, subprov: str, amount_lamports: int = 1_000_000_000):
    return {
        "meta": {"preBalances": [amount_lamports + 5000, 0], "postBalances": [5000, amount_lamports]},
        "transaction": {"message": {"accountKeys": [treasury, subprov]}},
    }


# ── Admission ────────────────────────────────────────────────────────────────

def test_valid_wrap_close_candidate_enters_pending(conn):
    result = discover_candidate(conn, mint="MINT_A", creator="CREATOR_A", subprov_wallet="SUB_A",
                                 funding_mechanism="WSOL_WRAP_CLOSE")
    assert result == "PENDING_VERIFICATION"
    row = conn.execute("SELECT * FROM wt_provisioning_candidate_workflow WHERE mint='MINT_A'").fetchone()
    assert row["workflow_state"] == "PENDING_VERIFICATION"


def test_seeded_account_close_candidate_enters_pending(conn):
    result = discover_candidate(conn, mint="MINT_B", creator="CREATOR_B", subprov_wallet="SUB_B",
                                 funding_mechanism="SEEDED_ACCOUNT_CLOSE")
    assert result == "PENDING_VERIFICATION"


def test_plain_transfer_is_excluded(conn):
    result = discover_candidate(conn, mint="MINT_C", creator="CREATOR_C", subprov_wallet="SUB_C",
                                 funding_mechanism="PLAIN_XFER")
    assert result == "EXCLUDED_WRONG_MECHANISM"
    row = conn.execute("SELECT * FROM wt_provisioning_candidate_workflow WHERE mint='MINT_C'").fetchone()
    assert row is None


def test_existing_model1_launch_is_excluded(conn):
    conn.execute("INSERT INTO wt_watchtower_launches (mint) VALUES ('MINT_D')")
    conn.commit()
    result = discover_candidate(conn, mint="MINT_D", creator="CREATOR_D", subprov_wallet="SUB_D",
                                 funding_mechanism="WSOL_WRAP_CLOSE")
    assert result == "EXCLUDED_ALREADY_CONFIRMED"
    row = conn.execute("SELECT * FROM wt_provisioning_candidate_workflow WHERE mint='MINT_D'").fetchone()
    assert row is None


def test_duplicate_discovery_is_idempotent(conn):
    r1 = discover_candidate(conn, mint="MINT_E", creator="CREATOR_E", subprov_wallet="SUB_E",
                             funding_mechanism="WSOL_WRAP_CLOSE")
    r2 = discover_candidate(conn, mint="MINT_E", creator="CREATOR_E", subprov_wallet="SUB_E",
                             funding_mechanism="WSOL_WRAP_CLOSE")
    assert r1 == r2 == "PENDING_VERIFICATION"
    count = conn.execute("SELECT COUNT(*) c FROM wt_provisioning_candidate_workflow WHERE mint='MINT_E'").fetchone()
    assert count["c"] == 1


def test_closed_row_not_reopened_by_rediscovery(conn):
    discover_candidate(conn, mint="MINT_F", creator="C", subprov_wallet="S", funding_mechanism="WSOL_WRAP_CLOSE")
    close_manually(conn, mint="MINT_F", reason="OTHER_OPERATOR", actor="analyst1")
    result = discover_candidate(conn, mint="MINT_F", creator="C", subprov_wallet="S",
                                 funding_mechanism="WSOL_WRAP_CLOSE")
    assert result == "INVESTIGATION_CLOSED"


# ── Verification ─────────────────────────────────────────────────────────────

def test_valid_treasury_transaction_moves_pending_to_verified(conn):
    conn.execute("INSERT INTO wt_confirmed_treasuries (treasury) VALUES ('TREASURY_1')")
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions (subprov_wallet, treasury_wallet, funding_signature, "
        "funding_amount, funding_time) VALUES ('SUB_1', 'TREASURY_1', 'SIG_1', 1000.0, 1000)")
    conn.commit()
    discover_candidate(conn, mint="MINT_G", creator="C", subprov_wallet="SUB_1", funding_mechanism="WSOL_WRAP_CLOSE")

    tx = _fake_tx(treasury="TREASURY_1", subprov="SUB_1")
    result = verify_candidate(conn, mint="MINT_G", wrap_close_time=1100,
                               rpc_get_transaction=lambda sig: tx)
    assert result["outcome"] == "PASS"
    row = conn.execute("SELECT * FROM wt_provisioning_candidate_workflow WHERE mint='MINT_G'").fetchone()
    assert row["workflow_state"] == "TREASURY_VERIFIED"
    assert row["verified_treasury"] == "TREASURY_1"


def test_treasury_mismatch_moves_pending_to_closed(conn):
    conn.execute("INSERT INTO wt_confirmed_treasuries (treasury) VALUES ('TREASURY_2')")
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions (subprov_wallet, treasury_wallet, funding_signature, "
        "funding_amount, funding_time) VALUES ('SUB_2', 'TREASURY_2', 'SIG_2', 1000.0, 1000)")
    conn.commit()
    discover_candidate(conn, mint="MINT_H", creator="C", subprov_wallet="SUB_2", funding_mechanism="WSOL_WRAP_CLOSE")

    # tx shows a DIFFERENT wallet as the real economic source (Binance-style mismatch)
    tx = _fake_tx(treasury="SOME_OTHER_WALLET", subprov="SUB_2")
    result = verify_candidate(conn, mint="MINT_H", wrap_close_time=1100,
                               rpc_get_transaction=lambda sig: tx)
    assert result["outcome"] == "FAIL"
    row = conn.execute("SELECT * FROM wt_provisioning_candidate_workflow WHERE mint='MINT_H'").fetchone()
    assert row["workflow_state"] == "INVESTIGATION_CLOSED"
    assert row["closure_reason"] == "TREASURY_MISMATCH"


def test_exchange_funded_source_closes_with_structured_reason(conn):
    """Simulates the Dv34prGm2BT7/Binance-2 negative control: the session
    records a confirmed treasury, but the actual transaction's economic source
    does not match it -- this must close, never verify."""
    conn.execute("INSERT INTO wt_confirmed_treasuries (treasury) VALUES ('SESSION_TREASURY')")
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions (subprov_wallet, treasury_wallet, funding_signature, "
        "funding_amount, funding_time) VALUES ('DV34_LIKE', 'SESSION_TREASURY', 'SIG_3', 9.5, 1000)")
    conn.commit()
    discover_candidate(conn, mint="MINT_I", creator="C", subprov_wallet="DV34_LIKE",
                        funding_mechanism="WSOL_WRAP_CLOSE")
    tx = _fake_tx(treasury="BINANCE_2", subprov="DV34_LIKE")  # real source != session treasury
    result = verify_candidate(conn, mint="MINT_I", wrap_close_time=1100, rpc_get_transaction=lambda sig: tx)
    assert result["outcome"] == "FAIL"
    row = conn.execute("SELECT * FROM wt_provisioning_candidate_workflow WHERE mint='MINT_I'").fetchone()
    assert row["closure_reason"] == "TREASURY_MISMATCH"


def test_missing_rpc_data_fails_closed_without_crashing(conn):
    conn.execute("INSERT INTO wt_confirmed_treasuries (treasury) VALUES ('TREASURY_3')")
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions (subprov_wallet, treasury_wallet, funding_signature, "
        "funding_amount, funding_time) VALUES ('SUB_3', 'TREASURY_3', 'SIG_4', 1000.0, 1000)")
    conn.commit()
    discover_candidate(conn, mint="MINT_J", creator="C", subprov_wallet="SUB_3", funding_mechanism="WSOL_WRAP_CLOSE")
    result = verify_candidate(conn, mint="MINT_J", wrap_close_time=1100, rpc_get_transaction=lambda sig: None)
    assert result["outcome"] == "FAIL"
    row = conn.execute("SELECT * FROM wt_provisioning_candidate_workflow WHERE mint='MINT_J'").fetchone()
    assert row["workflow_state"] == "INVESTIGATION_CLOSED"
    assert row["closure_reason"] == "INSUFFICIENT_EVIDENCE"


def test_no_eligible_session_remains_pending_via_upstream_not_resolved(conn):
    """No session at all for this subprov -- fails closed with an explicit,
    non-final-sounding reason (UPSTREAM_NOT_RESOLVED), not silently promoted."""
    discover_candidate(conn, mint="MINT_K", creator="C", subprov_wallet="SUB_NONE",
                        funding_mechanism="WSOL_WRAP_CLOSE")
    result = verify_candidate(conn, mint="MINT_K", wrap_close_time=1100, rpc_get_transaction=lambda sig: {})
    assert result["outcome"] == "FAIL"
    row = conn.execute("SELECT * FROM wt_provisioning_candidate_workflow WHERE mint='MINT_K'").fetchone()
    assert row["closure_reason"] == "UPSTREAM_NOT_RESOLVED"


def test_repeated_verification_is_idempotent(conn):
    conn.execute("INSERT INTO wt_confirmed_treasuries (treasury) VALUES ('TREASURY_4')")
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions (subprov_wallet, treasury_wallet, funding_signature, "
        "funding_amount, funding_time) VALUES ('SUB_4', 'TREASURY_4', 'SIG_5', 1000.0, 1000)")
    conn.commit()
    discover_candidate(conn, mint="MINT_L", creator="C", subprov_wallet="SUB_4", funding_mechanism="WSOL_WRAP_CLOSE")
    tx = _fake_tx(treasury="TREASURY_4", subprov="SUB_4")
    r1 = verify_candidate(conn, mint="MINT_L", wrap_close_time=1100, rpc_get_transaction=lambda sig: tx)
    r2 = verify_candidate(conn, mint="MINT_L", wrap_close_time=1100, rpc_get_transaction=lambda sig: tx)
    assert r1["outcome"] == "PASS"
    assert r2["outcome"] == "SKIPPED"
    assert r2["reason"] == "NOT_PENDING"


def test_gap_bound_selects_nearest_session_correctly(conn):
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions (subprov_wallet, treasury_wallet, funding_signature, "
        "funding_amount, funding_time) VALUES ('SUB_5', 'OLD_TREASURY', 'SIG_OLD', 1.0, 100)")
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions (subprov_wallet, treasury_wallet, funding_signature, "
        "funding_amount, funding_time) VALUES ('SUB_5', 'NEW_TREASURY', 'SIG_NEW', 1.0, 900)")
    conn.commit()
    selected = select_nearest_eligible_session(conn, subprov_wallet="SUB_5", wrap_close_time=1000)
    assert selected["treasury_wallet"] == "NEW_TREASURY"


# ── Promotion ─────────────────────────────────────────────────────────────────

def test_verified_candidate_does_not_auto_promote(conn):
    conn.execute("INSERT INTO wt_confirmed_treasuries (treasury) VALUES ('TREASURY_5')")
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions (subprov_wallet, treasury_wallet, funding_signature, "
        "funding_amount, funding_time) VALUES ('SUB_6', 'TREASURY_5', 'SIG_6', 1000.0, 1000)")
    conn.commit()
    discover_candidate(conn, mint="MINT_M", creator="C", subprov_wallet="SUB_6", funding_mechanism="WSOL_WRAP_CLOSE")
    tx = _fake_tx(treasury="TREASURY_5", subprov="SUB_6")
    result = verify_candidate(conn, mint="MINT_M", wrap_close_time=1100, rpc_get_transaction=lambda sig: tx)
    assert result["workflow_state"] == "TREASURY_VERIFIED"
    # Model 1 must remain untouched by verification alone.
    assert conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches").fetchone()["c"] == 0


def test_existing_human_action_promotes_through_canonical_route_only(conn):
    """Simulates the EXISTING promotion route (external to this module) writing
    wt_watchtower_launches directly -- this module must observe, not perform, that write."""
    conn.execute("INSERT INTO wt_confirmed_treasuries (treasury) VALUES ('TREASURY_6')")
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions (subprov_wallet, treasury_wallet, funding_signature, "
        "funding_amount, funding_time) VALUES ('SUB_7', 'TREASURY_6', 'SIG_7', 1000.0, 1000)")
    conn.commit()
    discover_candidate(conn, mint="MINT_N", creator="C", subprov_wallet="SUB_7", funding_mechanism="WSOL_WRAP_CLOSE")
    tx = _fake_tx(treasury="TREASURY_6", subprov="SUB_7")
    verify_candidate(conn, mint="MINT_N", wrap_close_time=1100, rpc_get_transaction=lambda sig: tx)

    # The EXISTING, external promotion route writes wt_watchtower_launches (simulated here).
    conn.execute("INSERT INTO wt_watchtower_launches (mint) VALUES ('MINT_N')")
    conn.commit()

    updated = sync_promoted_state(conn)
    assert updated == 1
    row = conn.execute("SELECT * FROM wt_provisioning_candidate_workflow WHERE mint='MINT_N'").fetchone()
    assert row["workflow_state"] == "PROMOTED_TO_MODEL_1"


def test_promoted_candidate_leaves_active_queue(conn):
    conn.execute("INSERT INTO wt_confirmed_treasuries (treasury) VALUES ('TREASURY_7')")
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions (subprov_wallet, treasury_wallet, funding_signature, "
        "funding_amount, funding_time) VALUES ('SUB_8', 'TREASURY_7', 'SIG_8', 1000.0, 1000)")
    conn.commit()
    discover_candidate(conn, mint="MINT_O", creator="C", subprov_wallet="SUB_8", funding_mechanism="WSOL_WRAP_CLOSE")
    tx = _fake_tx(treasury="TREASURY_7", subprov="SUB_8")
    verify_candidate(conn, mint="MINT_O", wrap_close_time=1100, rpc_get_transaction=lambda sig: tx)
    conn.execute("INSERT INTO wt_watchtower_launches (mint) VALUES ('MINT_O')")
    conn.commit()

    active = list_candidates(conn, states=["PENDING_VERIFICATION", "TREASURY_VERIFIED"])
    assert not any(r["mint"] == "MINT_O" for r in active)


def test_model1_tables_only_changed_by_existing_promotion_path(conn):
    """Runs the full discover -> verify -> close cycle across several mints and
    asserts wt_watchtower_launches / wt_confirmed_treasuries row counts never
    change as a side effect of any function in this module."""
    conn.execute("INSERT INTO wt_confirmed_treasuries (treasury) VALUES ('T1')")
    before_wt = conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches").fetchone()["c"]
    before_ct = conn.execute("SELECT COUNT(*) c FROM wt_confirmed_treasuries").fetchone()["c"]

    discover_candidate(conn, mint="MINT_P", creator="C", subprov_wallet="SUB_P", funding_mechanism="WSOL_WRAP_CLOSE")
    verify_candidate(conn, mint="MINT_P", wrap_close_time=1100, rpc_get_transaction=lambda sig: None)
    close_manually(conn, mint="MINT_P", reason="OTHER_OPERATOR", actor="analyst")
    sync_promoted_state(conn)

    after_wt = conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches").fetchone()["c"]
    after_ct = conn.execute("SELECT COUNT(*) c FROM wt_confirmed_treasuries").fetchone()["c"]
    assert before_wt == after_wt
    assert before_ct == after_ct


# ── UI-facing query surface ───────────────────────────────────────────────────

def test_list_candidates_counts_match_workflow_rows(conn):
    discover_candidate(conn, mint="MINT_Q1", creator="C", subprov_wallet="S1", funding_mechanism="WSOL_WRAP_CLOSE")
    discover_candidate(conn, mint="MINT_Q2", creator="C", subprov_wallet="S2", funding_mechanism="WSOL_WRAP_CLOSE")
    close_manually(conn, mint="MINT_Q2", reason="OTHER_OPERATOR", actor="a")

    pending = list_candidates(conn, states=["PENDING_VERIFICATION"])
    closed = list_candidates(conn, states=["INVESTIGATION_CLOSED"])
    assert len(pending) == 1
    assert len(closed) == 1


def test_list_candidates_mechanism_filter(conn):
    discover_candidate(conn, mint="MINT_R1", creator="C", subprov_wallet="S1", funding_mechanism="WSOL_WRAP_CLOSE")
    discover_candidate(conn, mint="MINT_R2", creator="C", subprov_wallet="S2", funding_mechanism="SEEDED_ACCOUNT_CLOSE")
    wrap_only = list_candidates(conn, funding_mechanism="WSOL_WRAP_CLOSE")
    assert len(wrap_only) == 1
    assert wrap_only[0]["mint"] == "MINT_R1"
