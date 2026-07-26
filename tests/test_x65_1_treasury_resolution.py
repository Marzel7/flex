"""X65.1 — Sub-Provider Treasury Resolution for Unassigned Quick-Birth Launches.

Tests src/ops/treasury_resolution.py's read-only creator -> subprov ->
treasury walkback against a minimal in-memory schema mirroring the real
wt_attribution_outcomes / wt_active_subprov_sessions / wt_confirmed_
treasuries / wt_ops_v2_wallets tables.

Must never: write to any table, promote a candidate to KNOWN_TREASURY
without an existing wt_confirmed_treasuries row, fabricate a wallet, or
silently guess when evidence is missing.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.ops.treasury_resolution import (
    MAX_WALKBACK_DEPTH,
    STATUS_KNOWN_TREASURY,
    STATUS_NO_SUBPROV,
    STATUS_UNKNOWN_TREASURY_CANDIDATE,
    STATUS_UNRESOLVED,
    SUBPROV_CONFIRMED,
    SUBPROV_DIRECT_TREASURY,
    SUBPROV_PROBABLE,
    SUBPROV_UNRESOLVED,
    classify_creator_funder,
    get_creator_funder,
    is_bridged_further_upstream,
    match_known_treasury,
    resolve_treasury_for_cohort,
    resolve_treasury_for_launch,
    treasury_scale_stats,
)


def _build_ops_db(tmp_path):
    db = tmp_path / "ops.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE wt_attribution_outcomes (
            mint TEXT PRIMARY KEY, outcome_type TEXT, terminal_entity TEXT, evidence_json TEXT
        );
        CREATE TABLE wt_active_subprov_sessions (
            subprov_wallet TEXT, treasury_wallet TEXT, funding_signature TEXT,
            funding_amount REAL, funding_time INTEGER, funding_mechanism TEXT,
            state TEXT, open_reason TEXT
        );
        CREATE TABLE wt_confirmed_treasuries (
            treasury TEXT PRIMARY KEY, method TEXT, confidence TEXT,
            confirmed_at INTEGER, provenance TEXT
        );
        CREATE TABLE wt_ops_v2_wallets (
            operation_uuid TEXT, wallet TEXT, role TEXT, last_seen INTEGER
        );
    """)
    conn.commit()
    return conn, db


VALID_SIG = "pokoBD8CxcaQCbcqMCyVwrVSvUmpYoSQAkRD5GzoQpf44MUgQQDdr1ccHfZyaNhrJMeZEXNLYePpihsyQwJMw4J"


def _insert_attribution(conn, mint, creator, funder, outcome_type="INSUFFICIENT_EVIDENCE"):
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES (?,?,?,?)",
        (mint, outcome_type, funder, json.dumps({"creator": creator})),
    )
    conn.commit()


def _insert_session(conn, subprov, treasury, sig=VALID_SIG, amount=650.0, funding_time=1000, mechanism="PLAIN_TRANSFER"):
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions VALUES (?,?,?,?,?,?,?,?)",
        (subprov, treasury, sig, amount, funding_time, mechanism, "EXPIRED", "PROVISION_CANDIDATE"),
    )
    conn.commit()


def _insert_confirmed_treasury(conn, treasury, method="3SIGNAL", confidence="CONFIRMED", confirmed_at=500):
    conn.execute(
        "INSERT INTO wt_confirmed_treasuries VALUES (?,?,?,?,?)",
        (treasury, method, confidence, confirmed_at, "TEST_PROVENANCE"),
    )
    conn.commit()


def _insert_operation_link(conn, treasury, operation_uuid):
    conn.execute(
        "INSERT INTO wt_ops_v2_wallets VALUES (?,?,?,?)",
        (operation_uuid, treasury, "TREASURY", 999),
    )
    conn.commit()


# ── get_creator_funder ───────────────────────────────────────────────────

def test_get_creator_funder_returns_none_when_no_attribution_row(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    assert get_creator_funder(conn, "SomeMint") is None


def test_get_creator_funder_reads_terminal_entity_verbatim(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    _insert_attribution(conn, "MintA", "CreatorA", "FunderA")
    result = get_creator_funder(conn, "MintA")
    assert result["funder_wallet"] == "FunderA"
    assert result["creator"] == "CreatorA"
    assert result["outcome_type"] == "INSUFFICIENT_EVIDENCE"


# ── classify_creator_funder ──────────────────────────────────────────────

def test_classify_direct_treasury(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    _insert_confirmed_treasury(conn, "TreasuryDirect")
    result = classify_creator_funder(conn, "TreasuryDirect")
    assert result["classification"] == SUBPROV_DIRECT_TREASURY
    assert result["is_confirmed_treasury_directly"] is True


def test_classify_confirmed_subprov_requires_signature_and_treasury(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    _insert_session(conn, "SubprovA", "TreasuryA")
    result = classify_creator_funder(conn, "SubprovA")
    assert result["classification"] == SUBPROV_CONFIRMED
    assert result["session"]["funding_signature"] == VALID_SIG


def test_classify_probable_subprov_when_treasury_missing(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    _insert_session(conn, "SubprovB", treasury=None)
    result = classify_creator_funder(conn, "SubprovB")
    assert result["classification"] == SUBPROV_PROBABLE


def test_classify_probable_subprov_when_signature_missing(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    _insert_session(conn, "SubprovC", "TreasuryC", sig=None)
    result = classify_creator_funder(conn, "SubprovC")
    assert result["classification"] == SUBPROV_PROBABLE


def test_classify_unresolved_when_no_evidence_at_all(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    result = classify_creator_funder(conn, "NeverSeenWallet")
    assert result["classification"] == SUBPROV_UNRESOLVED


# ── treasury_scale_stats ─────────────────────────────────────────────────

def test_treasury_scale_stats_aggregates_across_multiple_subprovs(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    _insert_session(conn, "SubprovX", "TreasuryZ", amount=100.0)
    _insert_session(conn, "SubprovY", "TreasuryZ", amount=200.0)
    stats = treasury_scale_stats(conn, "TreasuryZ")
    assert stats["distinct_subprovs_funded"] == 2
    assert stats["total_funding_amount_sol"] == 300.0


def test_treasury_scale_stats_zero_for_unknown_treasury(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    stats = treasury_scale_stats(conn, "NeverFundedAnything")
    assert stats["distinct_subprovs_funded"] == 0
    assert stats["total_funding_amount_sol"] == 0.0


# ── is_bridged_further_upstream ──────────────────────────────────────────

def test_bridging_detected_when_treasury_is_itself_a_subprov(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    _insert_session(conn, "TreasuryBridged", "EvenHigherUpstream")
    assert is_bridged_further_upstream(conn, "TreasuryBridged") is True


def test_no_bridging_when_treasury_is_terminal(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    assert is_bridged_further_upstream(conn, "TerminalTreasury") is False


# ── match_known_treasury ─────────────────────────────────────────────────

def test_match_known_treasury_none_when_not_confirmed(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    assert match_known_treasury(conn, "UnconfirmedWallet") is None


def test_match_known_treasury_returns_operation_link(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    _insert_confirmed_treasury(conn, "TreasuryLinked", method="subprov_funder_trace")
    _insert_operation_link(conn, "TreasuryLinked", "op-uuid-123")
    match = match_known_treasury(conn, "TreasuryLinked")
    assert match is not None
    assert match["operation_id"] == "op-uuid-123"
    assert match["confirmation_method"] == "subprov_funder_trace"


def test_match_known_treasury_never_writes_to_confirmed_table(tmp_path):
    """Calling this repeatedly for an unconfirmed wallet must never
    result in it becoming confirmed -- this function is read-only."""
    conn, _ = _build_ops_db(tmp_path)
    for _ in range(5):
        match_known_treasury(conn, "NeverConfirmed")
    row = conn.execute("SELECT COUNT(*) FROM wt_confirmed_treasuries WHERE treasury='NeverConfirmed'").fetchone()
    assert row[0] == 0


# ── resolve_treasury_for_launch: full end-to-end scenarios ───────────────

def test_full_resolution_known_treasury_two_hops(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    _insert_attribution(conn, "MintKnown", "CreatorKnown", "SubprovKnown")
    _insert_session(conn, "SubprovKnown", "TreasuryKnown")
    _insert_confirmed_treasury(conn, "TreasuryKnown")
    _insert_operation_link(conn, "TreasuryKnown", "op-known")

    result = resolve_treasury_for_launch(conn, "MintKnown")["treasury_resolution"]
    assert result["status"] == STATUS_KNOWN_TREASURY
    assert result["creator_wallet"] == "CreatorKnown"
    assert result["subprov_wallet"] == "SubprovKnown"
    assert result["treasury_wallet"] == "TreasuryKnown"
    assert result["operation_id"] == "op-known"
    assert result["hop_depth"] == MAX_WALKBACK_DEPTH
    assert result["confidence"] > 0
    assert len(result["evidence"]) >= 3
    assert result["reason"]


def test_full_resolution_unknown_treasury_candidate_not_promoted(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    _insert_attribution(conn, "MintCandidate", "CreatorCandidate", "SubprovCandidate")
    _insert_session(conn, "SubprovCandidate", "UnconfirmedTreasury")
    # Deliberately NOT inserted into wt_confirmed_treasuries.

    result = resolve_treasury_for_launch(conn, "MintCandidate")["treasury_resolution"]
    assert result["status"] == STATUS_UNKNOWN_TREASURY_CANDIDATE
    assert result["treasury_wallet"] == "UnconfirmedTreasury"
    assert result["operation_id"] is None
    assert "not yet present in wt_confirmed_treasuries" in result["reason"]


def test_full_resolution_unresolved_when_no_evidence(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    _insert_attribution(conn, "MintCold", "CreatorCold", "NeverSeenFunder")

    result = resolve_treasury_for_launch(conn, "MintCold")["treasury_resolution"]
    assert result["status"] == STATUS_UNRESOLVED
    assert result["subprov_wallet"] is None
    assert result["treasury_wallet"] is None
    assert "never been observed" in result["reason"]


def test_full_resolution_unresolved_when_no_attribution_row_at_all(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    result = resolve_treasury_for_launch(conn, "NeverAttributedMint")["treasury_resolution"]
    assert result["status"] == STATUS_UNRESOLVED
    assert result["creator_wallet"] is None
    assert result["hop_depth"] == 0


def test_full_resolution_direct_treasury_one_hop(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    _insert_attribution(conn, "MintDirect", "CreatorDirect", "TreasuryDirectFund")
    _insert_confirmed_treasury(conn, "TreasuryDirectFund")
    _insert_operation_link(conn, "TreasuryDirectFund", "op-direct")

    result = resolve_treasury_for_launch(conn, "MintDirect")["treasury_resolution"]
    assert result["status"] == STATUS_KNOWN_TREASURY
    assert result["subprov_wallet"] is None
    assert result["treasury_wallet"] == "TreasuryDirectFund"
    assert result["hop_depth"] == 1


def test_evidence_path_never_empty_for_any_resolved_status(tmp_path):
    conn, _ = _build_ops_db(tmp_path)
    _insert_attribution(conn, "MintEv", "CreatorEv", "SubprovEv")
    _insert_session(conn, "SubprovEv", "TreasuryEv")
    _insert_confirmed_treasury(conn, "TreasuryEv")

    result = resolve_treasury_for_launch(conn, "MintEv")["treasury_resolution"]
    assert isinstance(result["evidence"], list)
    assert len(result["evidence"]) > 0
    for step in result["evidence"]:
        assert "wallet" in step
        assert "source" in step


def test_no_fabricated_wallet_when_unresolved(tmp_path):
    """UNRESOLVED must always carry null wallets, never a guessed value."""
    conn, _ = _build_ops_db(tmp_path)
    _insert_attribution(conn, "MintNull", "CreatorNull", "GhostFunder")
    result = resolve_treasury_for_launch(conn, "MintNull")["treasury_resolution"]
    assert result["status"] == STATUS_UNRESOLVED
    assert result["subprov_wallet"] is None
    assert result["treasury_wallet"] is None


# ── resolve_treasury_for_cohort: batch entry point ───────────────────────

def test_resolve_cohort_returns_one_object_per_mint(tmp_path):
    conn, db_path = _build_ops_db(tmp_path)
    _insert_attribution(conn, "MintOne", "C1", "F1")
    _insert_attribution(conn, "MintTwo", "C2", "F2")
    conn.close()

    results = resolve_treasury_for_cohort(str(db_path), ["MintOne", "MintTwo"])
    assert set(results.keys()) == {"MintOne", "MintTwo"}
    for mint, r in results.items():
        assert "treasury_resolution" in r


def test_resolve_cohort_is_read_only(tmp_path):
    """The batch function must never modify wt_confirmed_treasuries,
    wt_attribution_outcomes, or wt_active_subprov_sessions."""
    conn, db_path = _build_ops_db(tmp_path)
    _insert_attribution(conn, "MintRO", "CRO", "FRO")
    _insert_session(conn, "FRO", "TRO")
    _insert_confirmed_treasury(conn, "TRO")
    before_treasuries = conn.execute("SELECT COUNT(*) FROM wt_confirmed_treasuries").fetchone()[0]
    before_sessions = conn.execute("SELECT COUNT(*) FROM wt_active_subprov_sessions").fetchone()[0]
    before_outcomes = conn.execute("SELECT COUNT(*) FROM wt_attribution_outcomes").fetchone()[0]
    conn.close()

    resolve_treasury_for_cohort(str(db_path), ["MintRO"])

    conn2 = sqlite3.connect(db_path)
    after_treasuries = conn2.execute("SELECT COUNT(*) FROM wt_confirmed_treasuries").fetchone()[0]
    after_sessions = conn2.execute("SELECT COUNT(*) FROM wt_active_subprov_sessions").fetchone()[0]
    after_outcomes = conn2.execute("SELECT COUNT(*) FROM wt_attribution_outcomes").fetchone()[0]
    assert before_treasuries == after_treasuries
    assert before_sessions == after_sessions
    assert before_outcomes == after_outcomes
