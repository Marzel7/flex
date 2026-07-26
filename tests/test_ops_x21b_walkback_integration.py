"""X21B integration: walkback_worker._process_row captures provisioning facts on
success, without altering attribution, promotion, or existing walkback outcomes.

RPC is never called in these tests — _find_funder_via_rpc is monkeypatched to
return deterministic FunderInfo tuples, exactly as the real function would after
a successful RPC round-trip, so _process_row's own control flow (unchanged by
this sprint) drives the test rather than a reimplementation of it.
"""
from __future__ import annotations

import sqlite3

import pytest

import src.core.walkback_worker as walkback_worker
from src.ops.provisioning_edges import edges_for_wallet, sessions_for_wallet


OPS_SCHEMA = """
CREATE TABLE wt_walkback_queue (
 mint TEXT PRIMARY KEY, creator TEXT, subprov TEXT, treasury TEXT,
 walkback_class TEXT NOT NULL DEFAULT 'FULL_WALKBACK', attribution_source TEXT,
 status TEXT NOT NULL DEFAULT 'pending', rpc_used INTEGER NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
 enqueued_at INTEGER NOT NULL DEFAULT 0, started_at INTEGER, completed_at INTEGER,
 updated_at INTEGER NOT NULL DEFAULT 0, intelligence_outcome TEXT,
 funder_wallet TEXT, funding_mechanism TEXT, funder_amount_sol REAL,
 funder_sig TEXT, funder_slot INTEGER, funder_block_time INTEGER
);
CREATE TABLE wt_confirmed_treasuries (treasury TEXT PRIMARY KEY, confirmed_at INTEGER);
CREATE TABLE wt_discovered_subprovs (
 subprov TEXT PRIMARY KEY, first_creator TEXT, treasury TEXT, treasury_known INTEGER DEFAULT 0,
 first_seen INTEGER, last_seen INTEGER, creator_count INTEGER DEFAULT 1, wrap_close_count INTEGER DEFAULT 0,
 immediate_funder TEXT, funder_is_subprov INTEGER DEFAULT 0, confidence REAL DEFAULT 0.20,
 state TEXT DEFAULT 'PROVISION_CANDIDATE', topup_count INTEGER DEFAULT 0, rejected_reason TEXT,
 buy_swarm_count INTEGER DEFAULT 0, create_count INTEGER DEFAULT 0, buy_swarm_ratio REAL DEFAULT 0.0,
 subprov_type TEXT DEFAULT 'UNKNOWN', seeded_account_count INTEGER DEFAULT 0,
 discovery_source TEXT, funding_mechanism TEXT
);
CREATE TABLE watchtower_token_attribution (mint TEXT PRIMARY KEY, creator TEXT, matched_subprov TEXT, matched_treasury TEXT, score INTEGER, tier TEXT, scored_at INTEGER);
CREATE TABLE wt_treasury_review (
 treasury TEXT PRIMARY KEY, transfer_pct INTEGER, out_sol REAL, recipients INTEGER,
 micro_pings INTEGER, detected_via TEXT, status TEXT DEFAULT 'PENDING_REVIEW',
 reviewed_by TEXT, detected_at INTEGER, reviewed_at INTEGER,
 subprov_wallet TEXT, creator_wallet TEXT, token_mint TEXT,
 distinct_subprovs INTEGER, distinct_creators INTEGER,
 evidence_sigs TEXT, evidence_subprovs TEXT, evidence_creators TEXT, evidence_mints TEXT,
 has_walkback_evidence INTEGER, first_walkback_at INTEGER, last_walkback_at INTEGER
);
CREATE TABLE wt_attribution_outcomes (
 mint TEXT PRIMARY KEY, outcome_type TEXT, stop_reason TEXT, terminal_entity TEXT,
 terminal_entity_type TEXT, confidence TEXT, evidence_json TEXT, operator_id TEXT,
 should_seed_emerging_operator INTEGER, should_retry INTEGER, completed_at INTEGER,
 source_queue_updated_at INTEGER, materialized_at INTEGER
);
CREATE TABLE wt_unknown_infrastructure_registry (
 terminal_entity TEXT PRIMARY KEY, terminal_entity_type TEXT, first_source_mint TEXT,
 latest_source_mint TEXT, observation_count INTEGER DEFAULT 1, confidence TEXT,
 evidence_json TEXT, eligible INTEGER DEFAULT 1, first_seen_at INTEGER, last_seen_at INTEGER
);
CREATE TABLE wt_wrap_close_candidates (
 creator TEXT PRIMARY KEY, funding_mechanism TEXT, creator_extraction_method TEXT,
 subprov_wallet TEXT, close_destination TEXT, base_amount_sol REAL,
 tx_signature TEXT, funded_at INTEGER, confidence TEXT, state TEXT, detected_at INTEGER
);
"""


@pytest.fixture
def ops(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(OPS_SCHEMA)
    conn.execute(
        "INSERT INTO wt_walkback_queue (mint, creator, walkback_class, status, attempts, enqueued_at) "
        "VALUES ('mintX', 'CREATOR_X', 'FULL_WALKBACK', 'running', 1, 900)"
    )
    conn.commit()

    # Deterministic funder chain: CREATOR_X funded by SUBPROV_X (bt=1000), SUBPROV_X
    # funded by TREASURY_X (bt=982) — TREASURY_X is unknown, so this lands as a
    # LINEAGE_GAP hop-2 lead, exactly the case where X21B capture must still fire
    # even though no attribution is written.
    calls = {"n": 0}

    def fake_find_funder(wallet, rpc_counter, ops_conn=None, **_search_options):
        calls["n"] += 1
        rpc_counter[0] += 1
        if wallet == "CREATOR_X":
            return ("SUBPROV_X", "sig_creator_funding", 100, 1000, 0.05, "PLAIN_XFER")
        if wallet == "SUBPROV_X":
            return ("TREASURY_X", "sig_subprov_funding", 90, 982, 0.203, "WSOL_WRAP_CLOSE")
        return (None, None, None, None, None, None)

    monkeypatch.setattr(walkback_worker, "_find_funder_via_rpc", fake_find_funder)
    monkeypatch.setattr(walkback_worker, "_surface_treasury_review_lead", lambda *a, **k: "recorded")
    return conn


def test_lineage_gap_hop2_still_captures_provisioning_facts(ops):
    row = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    walkback_worker._process_row(ops, row)

    queue_row = ops.execute("SELECT status, intelligence_outcome, treasury FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    assert queue_row["status"] == "complete"
    assert queue_row["intelligence_outcome"] == "LINEAGE_GAP"
    # TREASURY_X must NEVER appear as confirmed attribution — it's an unverified hop.
    assert queue_row["treasury"] is None

    attribution_row = ops.execute("SELECT * FROM watchtower_token_attribution WHERE mint='mintX'").fetchone()
    assert attribution_row is None  # no confirmed attribution was written

    # But the provisioning facts ARE captured, independent of attribution:
    treasury_edges = edges_for_wallet(ops, "TREASURY_X")
    assert len(treasury_edges["outgoing"]) == 1
    edge = treasury_edges["outgoing"][0]
    assert edge["to_wallet"] == "SUBPROV_X"
    assert edge["funding_amount_sol"] == 0.203
    assert edge["funding_mechanism"] == "WSOL_WRAP_CLOSE"

    subprov_edges = edges_for_wallet(ops, "SUBPROV_X")
    assert len(subprov_edges["incoming"]) == 1  # from TREASURY_X
    assert len(subprov_edges["outgoing"]) == 1  # to CREATOR_X
    assert subprov_edges["outgoing"][0]["to_wallet"] == "CREATOR_X"

    sessions = sessions_for_wallet(ops, "TREASURY_X")
    assert len(sessions) == 1
    session = sessions[0]
    assert session["source_mint"] == "mintX"
    assert session["treasury_to_subprov_latency_seconds"] == 18  # 1000 - 982


def test_confirmed_watchtower_path_also_captures_facts(ops):
    ops.execute("INSERT INTO wt_confirmed_treasuries (treasury, confirmed_at) VALUES ('TREASURY_X', 500)")
    ops.commit()

    row = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    walkback_worker._process_row(ops, row)

    queue_row = ops.execute("SELECT status, intelligence_outcome, treasury, subprov FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    assert queue_row["intelligence_outcome"] == "WATCHTOWER_CONFIRMED"
    assert queue_row["treasury"] == "TREASURY_X"

    attribution_row = ops.execute("SELECT * FROM watchtower_token_attribution WHERE mint='mintX'").fetchone()
    assert attribution_row is not None  # confirmed path DOES write attribution — unrelated to X21B

    # Facts are captured on the confirmed path too — capture is orthogonal to outcome.
    edges = edges_for_wallet(ops, "TREASURY_X")
    assert len(edges["outgoing"]) == 1
    assert edges["outgoing"][0]["to_wallet"] == "SUBPROV_X"


def test_capture_failure_never_breaks_walkback_completion(ops, monkeypatch):
    """If the provisioning-edge module raises for any reason, walkback must still
    complete and mark the row — capture is best-effort, never load-bearing."""
    def boom(*a, **k):
        raise RuntimeError("simulated capture failure")

    monkeypatch.setattr("src.ops.provisioning_edges.capture_provisioning_relationship", boom)

    row = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    walkback_worker._process_row(ops, row)

    queue_row = ops.execute("SELECT status, intelligence_outcome FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    assert queue_row["status"] == "complete"
    assert queue_row["intelligence_outcome"] == "LINEAGE_GAP"


def test_full_walkback_anchors_creator_and_upstream_searches(ops, monkeypatch):
    calls = []

    def anchored_find(wallet, rpc_counter, ops_conn=None, **options):
        calls.append((wallet, options))
        rpc_counter[0] += 1
        if wallet == "CREATOR_X":
            return ("SUBPROV_X", "creator_funding_sig", 200, 2000, 0.123039,
                    "WSOL_WRAP_CLOSE")
        if wallet == "SUBPROV_X":
            return ("TREASURY_X", "capital_funding_sig", 100, 1900, 720.0,
                    "PLAIN_XFER")
        return (None, None, None, None, None, None)

    close_tx = {
        "transaction": {"message": {"instructions": [{
            "parsed": {"type": "closeAccount", "info": {"destination": "CREATOR_X"}}
        }]}},
        "meta": {},
    }
    monkeypatch.setattr(walkback_worker, "_find_funder_via_rpc", anchored_find)
    monkeypatch.setattr(walkback_worker, "_recover_create_signature_from_db",
                        lambda _mint: "create_sig")
    monkeypatch.setattr(walkback_worker, "_get_tx", lambda _sig: close_tx)
    ops.execute("INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY_X', 1)")

    row = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    walkback_worker._process_row(ops, row)

    assert calls == [
        ("CREATOR_X", {"before_signature": "create_sig"}),
        ("SUBPROV_X", {
            "before_signature": "creator_funding_sig", "prefer_oldest": True}),
    ]
    result = ops.execute(
        "SELECT intelligence_outcome,subprov,treasury FROM wt_walkback_queue WHERE mint='mintX'"
    ).fetchone()
    assert tuple(result) == ("WATCHTOWER_CONFIRMED", "SUBPROV_X", "TREASURY_X")
    close = ops.execute(
        "SELECT close_destination,state FROM wt_wrap_close_candidates WHERE creator='CREATOR_X'"
    ).fetchone()
    assert tuple(close) == ("CREATOR_X", "WALKBACK_EVIDENCE")


def test_signature_window_paginates_before_downstream_signature(monkeypatch):
    calls = []

    def fake_get_sigs(wallet, limit, before=None):
        calls.append((wallet, limit, before))
        if before == "downstream":
            return [{"signature": f"recent-{i}", "slot": 300 - i}
                    for i in range(walkback_worker.SIG_PAGE_LIMIT)]
        if before == f"recent-{walkback_worker.SIG_PAGE_LIMIT - 1}":
            return [{"signature": "capital", "slot": 1}]
        return []

    monkeypatch.setattr(walkback_worker, "_get_sigs", fake_get_sigs)
    rpc = [0]
    rows = walkback_worker._collect_signature_window(
        "SUBPROV_X", rpc, before_signature="downstream")

    assert rows[-1]["signature"] == "capital"
    assert calls[0][2] == "downstream"
    assert calls[1][2] == f"recent-{walkback_worker.SIG_PAGE_LIMIT - 1}"
    assert rpc[0] == 2
