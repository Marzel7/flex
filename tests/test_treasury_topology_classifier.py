import json
import sqlite3
from pathlib import Path

import pytest

from src.ops.treasury_topology_classifier import (
    TopologyThresholds,
    classify_unknown_treasury,
    confirm_topology_candidate,
    replay_topology_candidate,
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
        CREATE TABLE wt_treasury_review (treasury TEXT PRIMARY KEY);
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


def test_explicit_confirmation_uses_audited_topology_provenance(monkeypatch):
    conn = _db()
    for i in range(5):
        _add_chain(conn, i)
    candidate = _classify(conn)

    import src.core.treasury_bank as bank
    monkeypatch.setattr(bank, "_schema_ensured", True)
    result = confirm_topology_candidate(
        conn, candidate, now=12345, infrastructure_check=lambda _wallet: False,
    )

    assert result["verdict"] == "CONFIRMED"
    row = conn.execute(
        "SELECT method,confidence,provenance,confirmed_at FROM wt_confirmed_treasuries "
        "WHERE treasury='T'"
    ).fetchone()
    assert tuple(row) == ("TOPOLOGY_COHORT", "STRICT", "CONFIRMED_TOPOLOGY_COHORT", 12345)
    decision = conn.execute(
        "SELECT decision,signals_json,evidence_txs_json FROM wt_treasury_fingerprint_decisions"
    ).fetchone()
    assert decision["decision"] == "CONFIRMED"
    assert len(json.loads(decision["evidence_txs_json"])) == 5


def test_unqualified_candidate_cannot_be_promoted():
    conn = _db()
    with pytest.raises(ValueError, match="not topology-qualified"):
        confirm_topology_candidate(conn, {"wallet": "T", "verdict": "INSUFFICIENT_EVIDENCE"})


def test_replay_is_allowlisted_and_fails_closed(monkeypatch):
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
    monkeypatch.setattr("src.ops.attribution_outcome.materialize_outcome", lambda *_a, **_k: None)
    monkeypatch.setattr("src.ops.watchtower_candidates.sync_walkback_result", lambda *_a, **_k: None)
    result = replay_topology_candidate(
        conn, candidate, allowed_mints=["M0"], now=123,
        infrastructure_check=lambda _wallet: False,
    )
    assert result["replayed"] == ["M0"]
    assert tuple(conn.execute(
        "SELECT treasury,intelligence_outcome,attribution_source FROM wt_walkback_queue WHERE mint='M0'"
    ).fetchone()) == ("T", "WATCHTOWER_CONFIRMED", "topology_cohort_replay")
    assert conn.execute(
        "SELECT intelligence_outcome FROM wt_walkback_queue WHERE mint='M1'"
    ).fetchone()[0] == "LINEAGE_GAP"
    with pytest.raises(ValueError, match="not a subset"):
        replay_topology_candidate(
            conn, candidate, allowed_mints=["NOT_ALLOWED"], now=124,
            infrastructure_check=lambda _wallet: False,
        )


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

        one_chain = classify_unknown_treasury(
            conn, "HHXRs6kGuJwnbC5bQbxCcXQBm8pgeDugHUjnrduZP5sc",
            infrastructure_check=lambda _wallet: False,
        )
        assert one_chain["verdict"] == "INSUFFICIENT_EVIDENCE"
        assert one_chain["distinct_mints"] <= 1
    finally:
        conn.close()
