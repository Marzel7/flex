import json
import sqlite3
from pathlib import Path

import pytest

from src.ops.treasury_topology_classifier import (
    TopologyThresholds,
    classify_unknown_treasury,
    confirm_topology_candidate,
    replay_topology_candidate,
    surface_runtime_topology_candidate,
)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE wt_provisioning_edges (
          edge_id TEXT PRIMARY KEY, edge_type TEXT, from_wallet TEXT, to_wallet TEXT,
          first_observed_by_flex INTEGER, last_observed_by_flex INTEGER,
          observation_count INTEGER DEFAULT 1, funding_mechanism TEXT,
          funding_amount_sol REAL, funding_tx_signature TEXT,
          funding_block_time INTEGER, source_mint TEXT, provenance TEXT,
          UNIQUE(edge_type,from_wallet,to_wallet));
        CREATE TABLE wt_walkback_queue (
          mint TEXT PRIMARY KEY, creator TEXT, create_anchor_signature TEXT,
          create_anchor_audit_state TEXT);
        CREATE TABLE wt_walkback_transaction_roles (
          evidence_key TEXT PRIMARY KEY, mint TEXT, signature TEXT,
          signers_json TEXT, outer_shape_json TEXT);
        CREATE TABLE wt_confirmed_treasuries (
          treasury TEXT PRIMARY KEY, transfer_pct INTEGER, out_sol REAL,
          recipients INTEGER, micro_pings INTEGER, method TEXT, confidence TEXT,
          confirmed_at INTEGER, provenance TEXT, no_subscribe INTEGER DEFAULT 0);
        CREATE TABLE wt_known_spam_wallets (wallet TEXT PRIMARY KEY);
        CREATE TABLE wt_discovered_subprovs (subprov TEXT PRIMARY KEY, state TEXT);
        CREATE TABLE wt_treasury_fingerprint_decisions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, wallet TEXT, decision TEXT,
          signals_json TEXT, evidence_txs_json TEXT, source_migration TEXT,
          promoted_at INTEGER, webhook_status TEXT, decided_at INTEGER);
        CREATE TABLE wt_treasury_review (
          treasury TEXT PRIMARY KEY, detected_via TEXT, status TEXT DEFAULT 'PENDING_REVIEW',
          detected_at INTEGER, distinct_subprovs INTEGER, distinct_creators INTEGER,
          evidence_sigs TEXT, evidence_subprovs TEXT, evidence_creators TEXT,
          evidence_mints TEXT, has_walkback_evidence INTEGER, first_walkback_at INTEGER,
          last_walkback_at INTEGER);
    """)
    return conn


def _add_chain(conn, i, *, treasury="T", amount=1.112039, valid_anchor=True,
               fanout=True, shape=True, subprov=None, creator=None):
    subprov = subprov or f"S{i}"
    creator = creator or f"C{i}"
    mint, wrap_sig = f"M{i}", f"W{i}"
    conn.execute(
        "INSERT OR IGNORE INTO wt_provisioning_edges VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"t{i}", "TREASURY_TO_SUBPROV", treasury, subprov, 1, 1, 1,
         "PLAIN_XFER", 100.0, f"T{i}", 1, mint, "WALKBACK"),
    )
    conn.execute(
        "INSERT INTO wt_provisioning_edges VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"c{i}", "SUBPROV_TO_CREATOR", subprov, creator, 1, 1, 1,
         "WSOL_WRAP_CLOSE", amount, wrap_sig, 1, mint, "WALKBACK"),
    )
    conn.execute(
        "INSERT INTO wt_walkback_queue "
        "(mint,creator,create_anchor_signature,create_anchor_audit_state) VALUES (?,?,?,?)",
        (mint, creator, f"CREATE{i}" if valid_anchor else None,
         "VALID" if valid_anchor else "MISSING"),
    )
    signers = [subprov, f"F{i}"] if fanout else [subprov]
    instructions = [
        {"type": "createAccountWithSeed"}, {"type": "initializeAccount"},
        {"type": "closeAccount"},
    ] if shape else [{"type": "transfer"}]
    conn.execute(
        "INSERT INTO wt_walkback_transaction_roles VALUES (?,?,?,?,?)",
        (f"r{i}", mint, wrap_sig, json.dumps(signers), json.dumps(instructions)),
    )


def _classify(conn, wallet="T", **kwargs):
    return classify_unknown_treasury(
        conn, wallet, infrastructure_check=lambda _wallet: False, **kwargs,
    )


def test_repeated_complete_topology_qualifies_unknown_treasury():
    conn = _db()
    for i in range(5):
        _add_chain(conn, i)
    result = _classify(conn)
    assert result["verdict"] == "QUALIFIED_TOPOLOGY"
    assert result["distinct_mints"] == 5
    assert result["distinct_subprovs"] == 5
    assert result["distinct_creators"] == 5
    assert result["dominant_wrap_amount_sol"] == 1.112039
    assert result["amount_consistency"] == 1.0
    assert all(c["fanout_signers"] for c in result["chains"])


@pytest.mark.parametrize("failure", ["anchor", "fanout", "shape"])
def test_incomplete_route_evidence_cannot_establish_treasury(failure):
    conn = _db()
    for i in range(5):
        _add_chain(
            conn, i, valid_anchor=failure != "anchor",
            fanout=failure != "fanout", shape=failure != "shape",
        )
    result = _classify(conn)
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert result["distinct_mints"] == 0


def test_reused_subprov_and_inconsistent_amounts_fail_closed():
    conn = _db()
    for i, amount in enumerate((1.0, 2.0, 3.0, 4.0, 5.0)):
        _add_chain(conn, i, amount=amount)
    result = _classify(conn)
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert "inconsistent_wrap_amount" in result["reasons"]

    conn = _db()
    for i in range(5):
        _add_chain(conn, i, subprov="REUSED")
    result = _classify(conn)
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert result["rejected_evidence"]["subprov_not_single_use"] == 5


def test_repeated_multi_modal_amounts_qualify_but_singleton_noise_does_not():
    conn = _db()
    for i, amount in enumerate((1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 99.0)):
        _add_chain(conn, i, amount=amount)
    result = _classify(conn)
    assert result["verdict"] == "QUALIFIED_TOPOLOGY"
    assert result["amount_model"] == "MULTI_MODAL_REPEATED"
    assert result["repeated_amount_clusters"] == {1.0: 2, 2.0: 2, 3.0: 2}
    assert result["repeated_amount_consistency"] == pytest.approx(6 / 7, abs=1e-6)

    conn = _db()
    for i, amount in enumerate((1.0, 1.0, 2.0, 2.0, 3.0, 4.0)):
        _add_chain(conn, i, amount=amount)
    result = _classify(conn)
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert result["amount_model"] == "UNQUALIFIED"
    assert "inconsistent_wrap_amount" in result["reasons"]


def test_confirmed_spam_subprov_and_infrastructure_are_rejected():
    for setup, reason, infra in (
        ("confirmed", "already_confirmed", None),
        ("spam", "known_spam", None),
        ("subprov", "known_subprov", None),
        ("infra", "known_infrastructure", lambda wallet: wallet == "T"),
    ):
        conn = _db()
        if setup == "confirmed":
            conn.execute("INSERT INTO wt_confirmed_treasuries(treasury) VALUES ('T')")
        elif setup == "spam":
            conn.execute("INSERT INTO wt_known_spam_wallets VALUES ('T')")
        elif setup == "subprov":
            conn.execute("INSERT INTO wt_discovered_subprovs VALUES ('T','CONFIRMED')")
        result = classify_unknown_treasury(conn, "T", infrastructure_check=infra)
        assert result == {"wallet": "T", "verdict": "REJECTED", "reason": reason, "chains": []}


def test_missing_infrastructure_authority_fails_closed():
    conn = _db()
    result = classify_unknown_treasury(conn, "T")
    assert result["verdict"] == "EXCLUSION_CHECK_REQUIRED"


def test_runtime_classifier_disables_topology_confirmation(monkeypatch):
    conn = _db()
    for i in range(5):
        _add_chain(conn, i)
    candidate = _classify(conn)

    with pytest.raises(RuntimeError, match="review-only; confirmation is unavailable"):
        confirm_topology_candidate(
            conn, candidate, now=12345, infrastructure_check=lambda _wallet: False,
        )
    assert conn.execute("SELECT COUNT(*) FROM wt_confirmed_treasuries").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM wt_treasury_fingerprint_decisions").fetchone()[0] == 0


def test_unqualified_candidate_cannot_be_promoted():
    conn = _db()
    with pytest.raises(RuntimeError, match="review-only; confirmation is unavailable"):
        confirm_topology_candidate(conn, {"wallet": "T", "verdict": "INSUFFICIENT_EVIDENCE"})


def test_runtime_detector_surfaces_review_only_and_is_idempotent():
    conn = _db()
    for i in range(5):
        _add_chain(conn, i)
    first = surface_runtime_topology_candidate(
        conn, "T", now=100, infrastructure_check=lambda _wallet: False,
    )
    assert first["action"] == "inserted"
    row = conn.execute("SELECT * FROM wt_treasury_review WHERE treasury='T'").fetchone()
    assert row["detected_via"] == "topology_cohort_qualified"
    assert row["status"] == "PENDING_REVIEW"
    assert row["distinct_subprovs"] == row["distinct_creators"] == 5
    assert len(json.loads(row["evidence_mints"])) == 5
    assert conn.execute("SELECT COUNT(*) FROM wt_confirmed_treasuries").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM wt_treasury_fingerprint_decisions").fetchone()[0] == 0

    second = surface_runtime_topology_candidate(
        conn, "T", now=101, infrastructure_check=lambda _wallet: False,
    )
    assert second["action"] == "updated"
    row = conn.execute(
        "SELECT first_walkback_at,last_walkback_at FROM wt_treasury_review"
    ).fetchone()
    assert tuple(row) == (100, 101)


def test_runtime_detector_fails_closed_and_preserves_human_disposition():
    conn = _db()
    for i in range(4):
        _add_chain(conn, i)
    result = surface_runtime_topology_candidate(
        conn, "T", now=100, infrastructure_check=lambda _wallet: False,
    )
    assert result["action"] == "not_qualified"
    assert conn.execute("SELECT COUNT(*) FROM wt_treasury_review").fetchone()[0] == 0

    _add_chain(conn, 4)
    conn.execute(
        "INSERT INTO wt_treasury_review(treasury,status,detected_via) "
        "VALUES ('T','REJECTED','human_review')"
    )
    result = surface_runtime_topology_candidate(
        conn, "T", now=101, infrastructure_check=lambda _wallet: False,
    )
    assert result == {"action": "skipped_reviewed", "wallet": "T", "status": "REJECTED"}
    row = conn.execute("SELECT status,detected_via FROM wt_treasury_review").fetchone()
    assert tuple(row) == ("REJECTED", "human_review")


def test_runtime_classifier_disables_topology_replay(monkeypatch):
    conn = _db()
    conn.executescript("""
        ALTER TABLE wt_walkback_queue ADD COLUMN status TEXT DEFAULT 'complete';
        ALTER TABLE wt_walkback_queue ADD COLUMN intelligence_outcome TEXT DEFAULT 'LINEAGE_GAP';
        ALTER TABLE wt_walkback_queue ADD COLUMN subprov TEXT;
        ALTER TABLE wt_walkback_queue ADD COLUMN treasury TEXT;
        ALTER TABLE wt_walkback_queue ADD COLUMN funder_sig TEXT;
        ALTER TABLE wt_walkback_queue ADD COLUMN funding_mechanism TEXT;
        ALTER TABLE wt_walkback_queue ADD COLUMN attribution_source TEXT;
        ALTER TABLE wt_walkback_queue ADD COLUMN updated_at INTEGER;
        CREATE TABLE wt_provisioning_sessions (
          source_mint TEXT PRIMARY KEY, treasury TEXT, subprov TEXT, creator TEXT,
          subprov_to_creator_mechanism TEXT);
        CREATE TABLE watchtower_token_attribution (
          mint TEXT PRIMARY KEY, creator TEXT, matched_subprov TEXT, matched_treasury TEXT,
          score REAL, tier TEXT, reasons_json TEXT, scored_at INTEGER);
        CREATE TABLE wt_attribution_outcomes (
          mint TEXT PRIMARY KEY, outcome_type TEXT);
    """)
    for i in range(5):
        _add_chain(conn, i)
        conn.execute(
            "UPDATE wt_walkback_queue SET subprov=?,funder_sig=?,funding_mechanism='WSOL_WRAP_CLOSE' WHERE mint=?",
            (f"S{i}", f"W{i}", f"M{i}"),
        )
        conn.execute(
            "INSERT INTO wt_provisioning_sessions VALUES (?,?,?,?,?)",
            (f"M{i}", "T", f"S{i}", f"C{i}", "WSOL_WRAP_CLOSE"),
        )
    candidate = _classify(conn)
    with pytest.raises(RuntimeError, match="review-only; replay is unavailable"):
        replay_topology_candidate(
            conn, candidate, allowed_mints=["M0"], now=123,
            infrastructure_check=lambda _wallet: False,
        )
    assert conn.execute(
        "SELECT intelligence_outcome FROM wt_walkback_queue WHERE mint='M1'"
    ).fetchone()[0] == "LINEAGE_GAP"
    assert conn.execute("SELECT COUNT(*) FROM watchtower_token_attribution").fetchone()[0] == 0


def test_current_watchtower_cohort_read_only():
    live_path = Path("/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db")
    if not live_path.exists():
        pytest.skip("current WATCHTOWER cohort database unavailable")
    conn = sqlite3.connect(f"file:{live_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        gzaa = classify_unknown_treasury(
            conn, "GzaaMeT8osXc71tFhVZ8pgtDv9dy3nPK7xKMkDZm7zac",
            infrastructure_check=lambda _wallet: False,
            _allow_confirmed_topology=True,
        )
        assert gzaa["verdict"] == "QUALIFIED_TOPOLOGY"
        assert gzaa["distinct_mints"] >= 11
        assert gzaa["dominant_wrap_amount_sol"] == 0.203039
        assert gzaa["amount_consistency"] == 1.0

        azkp = classify_unknown_treasury(
            conn, "AzkPUXUXPQW8fagwzQuPfXAoEzTMaNNvigFMBx8zhLRM",
            infrastructure_check=lambda _wallet: False,
        )
        assert azkp["verdict"] == "QUALIFIED_TOPOLOGY"
        assert azkp["distinct_mints"] >= 38
        assert azkp["distinct_subprovs"] >= 38
        assert azkp["distinct_creators"] >= 38
        assert azkp["amount_model"] == "MULTI_MODAL_REPEATED"
        assert azkp["repeated_amount_count"] >= 37
        assert azkp["repeated_amount_consistency"] >= 0.97

        one_chain = classify_unknown_treasury(
            conn, "HHXRs6kGuJwnbC5bQbxCcXQBm8pgeDugHUjnrduZP5sc",
            infrastructure_check=lambda _wallet: False,
        )
        assert one_chain["verdict"] == "INSUFFICIENT_EVIDENCE"
        assert one_chain["distinct_mints"] <= 1
    finally:
        conn.close()
