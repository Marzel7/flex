"""X67.7 — Tests for direct promotion from candidate to Canonical WATCHTOWER.

TREASURY_VERIFIED is removed as a durable destination state; a successful
verification now runs the full canonical promotion predicate
(evaluate_candidate_for_canonical_promotion) and, only if every condition
holds, promotes through the existing canonical registry writer
(watchtower_registry_promotion.promote_walkback_confirmed_watchtower).

All tests use isolated in-memory SQLite databases -- never the production
database.
"""
import json
import sqlite3
import sys
import types

import pytest

from src.ops.provisioning_candidates_workflow import (
    ensure_schema,
    discover_candidate,
    verify_candidate,
    close_manually,
    sync_promoted_state,
    reconcile_legacy_treasury_verified,
    reevaluate_pending_candidates,
    list_candidates,
    evaluate_candidate_for_canonical_promotion,
    promote_eligible_candidate,
    is_confirmed_in_model1,
    SHARED_RELAY_SESSION_THRESHOLD,
)


def _install_fake_registry_writer(monkeypatch, conn):
    """Installs a minimal, real-behaving stand-in for
    watchtower_registry_promotion.promote_walkback_confirmed_watchtower into
    sys.modules, so promote_eligible_candidate's own `from src.core...import`
    resolves to test-controlled logic without touching the real module or
    any production table. Mirrors the real function's actual contract:
    idempotent on mint, writes wt_watchtower_launches, returns
    {"action": "promoted"|"already_present"|"not_eligible"|"failed", ...}."""
    from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID

    def promote_walkback_confirmed_watchtower(ops_conn, mint, *, outcome_type, operator_id,
                                                evidence, completed_at=None):
        if not (outcome_type == "CANONICAL_OPERATOR_REACHED" and operator_id == WATCHTOWER_OPERATOR_ID):
            return {"action": "not_eligible", "mint": mint, "error": None}
        existing = ops_conn.execute(
            "SELECT 1 FROM wt_watchtower_launches WHERE mint=?", (mint,)
        ).fetchone()
        if existing:
            return {"action": "already_present", "mint": mint, "error": None}
        creator = (evidence or {}).get("creator")
        if not creator:
            return {"action": "missing_evidence", "mint": mint, "error": "no creator available"}
        ops_conn.execute(
            "INSERT INTO wt_watchtower_launches (mint, creator_wallet) VALUES (?, ?)",
            (mint, creator),
        )
        ops_conn.commit()
        return {"action": "promoted", "mint": mint, "error": None}

    fake_module = types.ModuleType("src.core.watchtower_registry_promotion")
    fake_module.promote_walkback_confirmed_watchtower = promote_walkback_confirmed_watchtower
    monkeypatch.setitem(sys.modules, "src.core.watchtower_registry_promotion", fake_module)


@pytest.fixture
def conn(monkeypatch):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_schema(c)
    c.execute("CREATE TABLE wt_watchtower_launches (mint TEXT PRIMARY KEY, creator_wallet TEXT)")
    c.execute("""CREATE TABLE wt_confirmed_treasuries (
        treasury TEXT PRIMARY KEY, method TEXT, confidence TEXT, provenance TEXT)""")
    c.execute("""CREATE TABLE wt_active_subprov_sessions (
        id INTEGER PRIMARY KEY, subprov_wallet TEXT, treasury_wallet TEXT,
        funding_signature TEXT, funding_amount REAL, funding_time INTEGER)""")
    c.execute("""CREATE TABLE wt_walkback_queue (
        mint TEXT PRIMARY KEY, subprov TEXT, funding_mechanism TEXT)""")
    c.commit()
    _install_fake_registry_writer(monkeypatch, c)
    return c


def _fake_tx(*, treasury: str, subprov: str, amount_lamports: int = 1_000_000_000):
    return {
        "meta": {"preBalances": [amount_lamports + 5000, 0], "postBalances": [5000, amount_lamports]},
        "transaction": {"message": {"accountKeys": [treasury, subprov]}},
    }


def _seed_eligible(conn, *, mint, creator, subprov, treasury, wrap_close_sig="WRAP_SIG",
                    funding_sig="FUND_SIG", funding_time=1000, wrap_close_time=1100,
                    mechanism="WSOL_WRAP_CLOSE"):
    conn.execute("INSERT OR IGNORE INTO wt_confirmed_treasuries (treasury) VALUES (?)", (treasury,))
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions (subprov_wallet, treasury_wallet, funding_signature, "
        "funding_amount, funding_time) VALUES (?, ?, ?, ?, ?)",
        (subprov, treasury, funding_sig, 1000.0, funding_time))
    conn.execute(
        "INSERT INTO wt_walkback_queue (mint, subprov, funding_mechanism) VALUES (?, ?, ?)",
        (mint, subprov, mechanism))
    conn.commit()
    discover_candidate(conn, mint=mint, creator=creator, subprov_wallet=subprov, funding_mechanism=mechanism)
    return wrap_close_time


# ── 1. Eligible canonical candidate ─────────────────────────────────────────

def test_eligible_candidate_promotes_exactly_once(conn):
    wct = _seed_eligible(conn, mint="MINT_A", creator="CREATOR_A", subprov="SUB_A", treasury="TREASURY_A")
    tx = _fake_tx(treasury="TREASURY_A", subprov="SUB_A")
    result = verify_candidate(conn, mint="MINT_A", wrap_close_time=wct,
                               wrap_close_signature="WRAP_SIG", rpc_get_transaction=lambda sig: tx)
    assert result["action"] == "promoted"
    assert result["workflow_state"] == "PROMOTED_TO_MODEL_1"
    assert is_confirmed_in_model1(conn, "MINT_A")
    row = conn.execute("SELECT * FROM wt_provisioning_candidate_workflow WHERE mint='MINT_A'").fetchone()
    assert row["workflow_state"] == "PROMOTED_TO_MODEL_1"
    assert row["promoted_from_candidate"] == 1
    assert row["promotion_source"] == "X67_7_CANDIDATE_EVALUATOR"
    # exactly one canonical row
    assert conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches WHERE mint='MINT_A'").fetchone()["c"] == 1


# ── 2. Existing canonical row / retry ───────────────────────────────────────

def test_retry_promotion_is_already_canonical_and_reconciles(conn):
    wct = _seed_eligible(conn, mint="MINT_B", creator="CREATOR_B", subprov="SUB_B", treasury="TREASURY_B")
    tx = _fake_tx(treasury="TREASURY_B", subprov="SUB_B")
    verify_candidate(conn, mint="MINT_B", wrap_close_time=wct,
                      wrap_close_signature="WRAP_SIG", rpc_get_transaction=lambda sig: tx)
    # Simulate a retry of the promotion action directly against the evaluator.
    result = promote_eligible_candidate(
        conn, mint="MINT_B", treasury="TREASURY_B", subprov_wallet="SUB_B",
        wrap_close_signature="WRAP_SIG", lineage_gap_seconds=100, verification_evidence={},
    )
    assert result["action"] == "already_canonical"
    row = conn.execute("SELECT * FROM wt_provisioning_candidate_workflow WHERE mint='MINT_B'").fetchone()
    assert row["workflow_state"] == "PROMOTED_TO_MODEL_1"
    assert conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches WHERE mint='MINT_B'").fetchone()["c"] == 1


# ── 3. Treasury verified but relay topology ─────────────────────────────────

def test_relay_topology_does_not_hard_block_alone_but_mechanism_conflict_does(conn):
    """X67.4: session-count alone (even the 174-session case) is a SOFT
    signal -- Model 1 itself has confirmed subprovs up to 288 sessions. The
    actual hard gate this reproduces is the mechanism-evidence conflict that
    accompanied the known non-canonical candidate (9Pp8MeVxT5ku)."""
    wct = _seed_eligible(conn, mint="MINT_C", creator="CREATOR_C", subprov="RELAY_SUB",
                          treasury="TREASURY_C", mechanism="WSOL_WRAP_CLOSE")
    # Make the subprov look like a high-reuse relay (174-session pattern).
    for i in range(SHARED_RELAY_SESSION_THRESHOLD + 5):
        conn.execute(
            "INSERT INTO wt_active_subprov_sessions (subprov_wallet, treasury_wallet, funding_signature, "
            "funding_amount, funding_time) VALUES (?, ?, ?, ?, ?)",
            ("RELAY_SUB", "TREASURY_C", f"OTHER_SIG_{i}", 1.0, 200 + i))
    conn.commit()
    # Now also introduce the actual hard-gate condition: raw walkback
    # evidence disagrees with the recorded workflow mechanism (the real
    # signature of the known non-canonical candidate).
    conn.execute("UPDATE wt_walkback_queue SET funding_mechanism='PLAIN_XFER' WHERE mint='MINT_C'")
    conn.commit()
    tx = _fake_tx(treasury="TREASURY_C", subprov="RELAY_SUB")
    result = verify_candidate(conn, mint="MINT_C", wrap_close_time=wct,
                               wrap_close_signature="WRAP_SIG", rpc_get_transaction=lambda sig: tx)
    assert result["action"] == "not_eligible"
    assert result["decision"]["reason_code"] == "MECHANISM_EVIDENCE_CONFLICT"
    assert not is_confirmed_in_model1(conn, "MINT_C")


def test_extreme_relay_session_count_alone_is_soft_not_hard(conn):
    """Direct evaluator test: a >=SHARED_RELAY_SESSION_THRESHOLD subprov with
    NO mechanism conflict is still ELIGIBLE (surfaced only as a soft flag in
    verified_evidence) -- matching X67.4's finding that Model 1 itself
    tolerates high-session-count subprovs when the mechanism is consistent."""
    conn.execute("INSERT INTO wt_confirmed_treasuries (treasury) VALUES ('TREASURY_D')")
    for i in range(SHARED_RELAY_SESSION_THRESHOLD + 5):
        conn.execute(
            "INSERT INTO wt_active_subprov_sessions (subprov_wallet, treasury_wallet, funding_signature, "
            "funding_amount, funding_time) VALUES (?, ?, ?, ?, ?)",
            ("HIGH_REUSE_SUB", "TREASURY_D", f"SIG_{i}", 1.0, 200 + i))
    conn.execute("INSERT INTO wt_walkback_queue (mint, subprov, funding_mechanism) VALUES "
                 "('MINT_SOFT', 'HIGH_REUSE_SUB', 'WSOL_WRAP_CLOSE')")
    conn.commit()
    discover_candidate(conn, mint="MINT_SOFT", creator="CREATOR_SOFT", subprov_wallet="HIGH_REUSE_SUB",
                        funding_mechanism="WSOL_WRAP_CLOSE")
    decision = evaluate_candidate_for_canonical_promotion(
        conn, mint="MINT_SOFT", treasury="TREASURY_D", subprov_wallet="HIGH_REUSE_SUB",
        wrap_close_signature="WRAP_SIG", lineage_gap_seconds=100,
        verification_evidence={"treasury_to_subprov_signature": "SIG_0", "wrap_close_signature": "WRAP_SIG"},
    )
    assert decision["eligible"] is True
    assert decision["verified_evidence"]["soft_relay_flag"] is True


# ── 4. Mechanism conflict ───────────────────────────────────────────────────

def test_mechanism_conflict_between_stored_and_raw_evidence(conn):
    wct = _seed_eligible(conn, mint="MINT_E", creator="CREATOR_E", subprov="SUB_E", treasury="TREASURY_E",
                          mechanism="WSOL_WRAP_CLOSE")
    conn.execute("UPDATE wt_walkback_queue SET funding_mechanism='PLAIN_XFER' WHERE mint='MINT_E'")
    conn.commit()
    tx = _fake_tx(treasury="TREASURY_E", subprov="SUB_E")
    result = verify_candidate(conn, mint="MINT_E", wrap_close_time=wct,
                               wrap_close_signature="WRAP_SIG", rpc_get_transaction=lambda sig: tx)
    assert result["action"] == "not_eligible"
    assert result["decision"]["reason_code"] == "MECHANISM_EVIDENCE_CONFLICT"
    assert not is_confirmed_in_model1(conn, "MINT_E")


# ── 5. Role collision ───────────────────────────────────────────────────────

def test_role_collision_treasury_equals_subprov(conn):
    conn.execute("INSERT INTO wt_confirmed_treasuries (treasury) VALUES ('SAME_WALLET')")
    conn.execute("INSERT INTO wt_walkback_queue (mint, subprov, funding_mechanism) VALUES "
                 "('MINT_F', 'SAME_WALLET', 'WSOL_WRAP_CLOSE')")
    conn.commit()
    discover_candidate(conn, mint="MINT_F", creator="CREATOR_F", subprov_wallet="SAME_WALLET",
                        funding_mechanism="WSOL_WRAP_CLOSE")
    decision = evaluate_candidate_for_canonical_promotion(
        conn, mint="MINT_F", treasury="SAME_WALLET", subprov_wallet="SAME_WALLET",
        wrap_close_signature="WRAP_SIG", lineage_gap_seconds=100,
        verification_evidence={"treasury_to_subprov_signature": "SIG", "wrap_close_signature": "WRAP_SIG"},
    )
    assert decision["eligible"] is False
    assert decision["reason_code"] == "ROLE_SEPARATION_FAILED"


# ── 6. Missing required field ───────────────────────────────────────────────

def test_missing_creator_field(conn):
    conn.execute("INSERT INTO wt_confirmed_treasuries (treasury) VALUES ('TREASURY_G')")
    conn.commit()
    # discover_candidate with creator=None -- row exists but creator is NULL
    discover_candidate(conn, mint="MINT_H2", creator=None, subprov_wallet="SUB_H2",
                        funding_mechanism="WSOL_WRAP_CLOSE")
    decision = evaluate_candidate_for_canonical_promotion(
        conn, mint="MINT_H2", treasury="TREASURY_G", subprov_wallet="SUB_H2",
        wrap_close_signature="WRAP_SIG", lineage_gap_seconds=100,
        verification_evidence={"treasury_to_subprov_signature": "SIG", "wrap_close_signature": "WRAP_SIG"},
    )
    assert decision["eligible"] is False
    assert decision["reason_code"] == "MISSING_REQUIRED_FIELD"
    assert "creator" in decision["missing_requirements"]


# ── 7. Partial write failure / reconciliation ───────────────────────────────

def test_reconciliation_after_external_canonical_write(conn):
    """Simulates a partial-failure scenario: the canonical registry already
    has the row (written by some other path) but this module's own workflow
    row hasn't caught up yet -- sync_promoted_state must reconcile without
    creating a duplicate."""
    wct = _seed_eligible(conn, mint="MINT_I2", creator="CREATOR_I2", subprov="SUB_I2", treasury="TREASURY_I2")
    conn.execute("INSERT INTO wt_watchtower_launches (mint, creator_wallet) VALUES ('MINT_I2', 'CREATOR_I2')")
    conn.commit()
    updated = sync_promoted_state(conn)
    assert updated == 1
    row = conn.execute("SELECT * FROM wt_provisioning_candidate_workflow WHERE mint='MINT_I2'").fetchone()
    assert row["workflow_state"] == "PROMOTED_TO_MODEL_1"
    assert conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches WHERE mint='MINT_I2'").fetchone()["c"] == 1


# ── 8. Legacy verified candidate ────────────────────────────────────────────

def test_legacy_treasury_verified_row_promotes_through_normal_evaluator(conn):
    """A pre-X67.7 TREASURY_VERIFIED row (simulating data left over from the
    old workflow) must pass through the SAME evaluator new candidates use."""
    conn.execute("INSERT INTO wt_confirmed_treasuries (treasury) VALUES ('LEGACY_TREASURY')")
    conn.execute("INSERT INTO wt_walkback_queue (mint, subprov, funding_mechanism) VALUES "
                 "('LEGACY_MINT', 'LEGACY_SUB', 'WSOL_WRAP_CLOSE')")
    conn.commit()
    now = 1000
    evidence = {
        "treasury": "LEGACY_TREASURY", "subprov_wallet": "LEGACY_SUB",
        "treasury_to_subprov_signature": "LEGACY_SIG", "wrap_close_signature": "LEGACY_WRAP_SIG",
        "funding_amount": 100.0, "lineage_gap_seconds": 90, "verified_at": now,
        "verification_method": "SESSION_HINT_RPC_VERIFIED",
    }
    conn.execute(
        "INSERT INTO wt_provisioning_candidate_workflow "
        "(mint, workflow_state, discovered_at, updated_at, creator, subprov_wallet, funding_mechanism, "
        " verified_treasury, lineage_gap_seconds, evidence_json, reconstructed) "
        "VALUES ('LEGACY_MINT', 'TREASURY_VERIFIED', ?, ?, 'LEGACY_CREATOR', 'LEGACY_SUB', "
        " 'WSOL_WRAP_CLOSE', 'LEGACY_TREASURY', 90, ?, 1)",
        (now, now, json.dumps(evidence)),
    )
    conn.commit()
    results = reconcile_legacy_treasury_verified(conn)
    assert len(results) == 1
    assert results[0]["action"] == "promoted"
    row = conn.execute("SELECT * FROM wt_provisioning_candidate_workflow WHERE mint='LEGACY_MINT'").fetchone()
    assert row["workflow_state"] == "PROMOTED_TO_MODEL_1"
    assert is_confirmed_in_model1(conn, "LEGACY_MINT")


# ── 9. Concurrent promotion attempts ────────────────────────────────────────

def test_concurrent_promotion_attempts_produce_one_canonical_row(conn):
    wct = _seed_eligible(conn, mint="MINT_J2", creator="CREATOR_J2", subprov="SUB_J2", treasury="TREASURY_J2")
    tx = _fake_tx(treasury="TREASURY_J2", subprov="SUB_J2")
    # First "worker" runs verify_candidate (which internally promotes).
    result1 = verify_candidate(conn, mint="MINT_J2", wrap_close_time=wct,
                                wrap_close_signature="WRAP_SIG", rpc_get_transaction=lambda sig: tx)
    assert result1["action"] == "promoted"
    # Second "worker" evaluates the same mint again (simulating a concurrent
    # or retried attempt) -- must not create a duplicate canonical row.
    result2 = promote_eligible_candidate(
        conn, mint="MINT_J2", treasury="TREASURY_J2", subprov_wallet="SUB_J2",
        wrap_close_signature="WRAP_SIG", lineage_gap_seconds=100, verification_evidence={},
    )
    assert result2["action"] == "already_canonical"
    assert conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches WHERE mint='MINT_J2'").fetchone()["c"] == 1
    row = conn.execute("SELECT * FROM wt_provisioning_candidate_workflow WHERE mint='MINT_J2'").fetchone()
    assert row["workflow_state"] == "PROMOTED_TO_MODEL_1"


# ── Regression: existing admission/idempotency/manual-close behaviour ──────

def test_admission_still_works(conn):
    result = discover_candidate(conn, mint="MINT_ADM", creator="C", subprov_wallet="S",
                                 funding_mechanism="WSOL_WRAP_CLOSE")
    assert result == "PENDING_VERIFICATION"


def test_plain_transfer_still_excluded_at_admission(conn):
    result = discover_candidate(conn, mint="MINT_PX", creator="C", subprov_wallet="S",
                                 funding_mechanism="PLAIN_XFER")
    assert result == "EXCLUDED_WRONG_MECHANISM"


def test_manual_close_unaffected(conn):
    discover_candidate(conn, mint="MINT_MC", creator="C", subprov_wallet="S", funding_mechanism="WSOL_WRAP_CLOSE")
    result = close_manually(conn, mint="MINT_MC", reason="OTHER_OPERATOR", actor="analyst")
    assert result["outcome"] == "CLOSED"


def test_reevaluate_pending_candidates_classifies_without_writing(conn):
    _seed_eligible(conn, mint="MINT_PENDING_1", creator="C1", subprov="SUB_P1", treasury="TREASURY_P1")
    for i in range(SHARED_RELAY_SESSION_THRESHOLD + 5):
        conn.execute(
            "INSERT INTO wt_active_subprov_sessions (subprov_wallet, treasury_wallet, funding_signature, "
            "funding_amount, funding_time) VALUES (?, ?, ?, ?, ?)",
            ("SUB_P1", "TREASURY_P1", f"EXTRA_SIG_{i}", 1.0, 300 + i))
    conn.commit()
    report = reevaluate_pending_candidates(conn)
    assert any(r["mint"] == "MINT_PENDING_1" for r in report)
    entry = next(r for r in report if r["mint"] == "MINT_PENDING_1")
    assert entry["classification"] == "SHARED_PROVISIONING_INTELLIGENCE"
    # never writes anything
    row = conn.execute("SELECT workflow_state FROM wt_provisioning_candidate_workflow WHERE mint='MINT_PENDING_1'").fetchone()
    assert row["workflow_state"] == "PENDING_VERIFICATION"


def test_list_candidates_filters_still_work(conn):
    discover_candidate(conn, mint="MINT_LC1", creator="C", subprov_wallet="S1", funding_mechanism="WSOL_WRAP_CLOSE")
    discover_candidate(conn, mint="MINT_LC2", creator="C", subprov_wallet="S2", funding_mechanism="SEEDED_ACCOUNT_CLOSE")
    wrap_only = list_candidates(conn, funding_mechanism="WSOL_WRAP_CLOSE")
    assert len(wrap_only) == 1
    assert wrap_only[0]["mint"] == "MINT_LC1"
