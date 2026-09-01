"""X77.1: FULL_WALKBACK's write work (funder record, hop1 evidence capture,
provisioning facts) must now fire only AFTER both hop RPC calls resolve, never
interleaved between them. These tests prove: (1) call ORDER — every RPC call
happens before the first write, (2) EQUIVALENCE — final DB state is identical
to what test_ops_x21b_walkback_integration.py already proves for the
pre-existing code paths, and (3) FAILURE SEMANTICS — a hop2 RPC failure now
leaves NO partial hop1 evidence committed (a strict improvement over the
pre-X77.1 code, which had no rollback and would leave hop1's write committed
on a hop2 exception).
"""
from __future__ import annotations

import sqlite3

import pytest

import src.core.walkback_worker as walkback_worker
from src.ops.provisioning_edges import edges_for_wallet


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
CREATE TABLE wt_watchtower_launches (
 mint TEXT PRIMARY KEY, creator_wallet TEXT, create_signature TEXT,
 create_time INTEGER, treasury_wallet TEXT, subprov_wallet TEXT,
 wrap_close_signature TEXT, funding_mechanism TEXT, recorded_at INTEGER DEFAULT 0,
 wrap_close_sol REAL, subprov_funding_sol REAL
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
    monkeypatch.setattr(walkback_worker, "_surface_treasury_review_lead", lambda *a, **k: "recorded")
    return conn


def _wrap_close_tx(destination: str) -> dict:
    return {
        "transaction": {"message": {"instructions": [{
            "parsed": {"type": "closeAccount", "info": {"destination": destination}}
        }]}},
        "meta": {},
    }


def test_all_rpc_calls_precede_first_write(ops, monkeypatch):
    """Order proof: log every RPC call and every write; assert the split point
    (last RPC) occurs strictly before the first write into wt_walkback_queue's
    funder columns or the provisioning-edge tables."""
    events = []

    def fake_find_funder(wallet, rpc_counter, ops_conn=None, **_opts):
        events.append(("rpc_find_funder", wallet))
        rpc_counter[0] += 1
        if wallet == "CREATOR_X":
            return ("SUBPROV_X", "sig_creator_funding", 100, 1000, 0.123039, "WSOL_WRAP_CLOSE")
        if wallet == "SUBPROV_X":
            return ("TREASURY_X", "sig_subprov_funding", 90, 982, 720.0, "PLAIN_XFER")
        return (None, None, None, None, None, None)

    def fake_get_tx(sig):
        events.append(("rpc_get_tx", sig))
        return _wrap_close_tx("CREATOR_X")

    real_store_funder = walkback_worker._store_funder
    real_persist_hop1 = walkback_worker._persist_hop1_evidence
    real_capture_facts = walkback_worker._capture_provisioning_facts

    def logged_store_funder(*a, **k):
        events.append(("write_store_funder",))
        return real_store_funder(*a, **k)

    def logged_persist_hop1(*a, **k):
        events.append(("write_persist_hop1",))
        return real_persist_hop1(*a, **k)

    def logged_capture_facts(*a, **k):
        events.append(("write_capture_facts",))
        return real_capture_facts(*a, **k)

    monkeypatch.setattr(walkback_worker, "_find_funder_via_rpc", fake_find_funder)
    monkeypatch.setattr(walkback_worker, "_get_tx", fake_get_tx)
    monkeypatch.setattr(walkback_worker, "_store_funder", logged_store_funder)
    monkeypatch.setattr(walkback_worker, "_persist_hop1_evidence", logged_persist_hop1)
    monkeypatch.setattr(walkback_worker, "_capture_provisioning_facts", logged_capture_facts)
    ops.execute("INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY_X', 1)")

    row = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    walkback_worker._process_row(ops, row)

    kinds = [e[0] for e in events]
    last_rpc_idx = max(i for i, k in enumerate(kinds) if k.startswith("rpc_"))
    first_write_idx = min(i for i, k in enumerate(kinds) if k.startswith("write_"))
    assert last_rpc_idx < first_write_idx, f"a write preceded the final RPC call: {kinds}"
    # Both hops and the mech1 tx fetch must all have actually fired.
    assert ("rpc_find_funder", "CREATOR_X") in events
    assert ("rpc_find_funder", "SUBPROV_X") in events
    assert ("rpc_get_tx", "sig_creator_funding") in events


def test_reordered_full_walkback_matches_pre_x77_1_final_state(ops, monkeypatch):
    """Equivalence proof: same fixture/mocks as
    test_full_walkback_anchors_creator_and_upstream_searches in the X21B
    suite -- final DB state must be identical, only write TIMING changed."""
    def anchored_find(wallet, rpc_counter, ops_conn=None, **options):
        rpc_counter[0] += 1
        if wallet == "CREATOR_X":
            return ("SUBPROV_X", "creator_funding_sig", 200, 2000, 0.123039, "WSOL_WRAP_CLOSE")
        if wallet == "SUBPROV_X":
            return ("TREASURY_X", "capital_funding_sig", 100, 1900, 720.0, "PLAIN_XFER")
        return (None, None, None, None, None, None)

    close_tx = _wrap_close_tx("CREATOR_X")
    monkeypatch.setattr(walkback_worker, "_find_funder_via_rpc", anchored_find)
    monkeypatch.setattr(walkback_worker, "_recover_create_signature_from_db", lambda _mint: "create_sig")
    monkeypatch.setattr(walkback_worker, "_get_tx", lambda _sig: close_tx)
    ops.execute("INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY_X', 1)")

    row = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    walkback_worker._process_row(ops, row)

    result = ops.execute(
        "SELECT intelligence_outcome,subprov,treasury,funder_wallet,funding_mechanism FROM wt_walkback_queue WHERE mint='mintX'"
    ).fetchone()
    assert tuple(result) == ("WATCHTOWER_CONFIRMED", "SUBPROV_X", "TREASURY_X", "SUBPROV_X", "WSOL_WRAP_CLOSE")

    close = ops.execute(
        "SELECT close_destination,state FROM wt_wrap_close_candidates WHERE creator='CREATOR_X'"
    ).fetchone()
    assert tuple(close) == ("CREATOR_X", "WALKBACK_EVIDENCE")

    edges = edges_for_wallet(ops, "SUBPROV_X")
    assert len(edges["incoming"]) == 1
    assert edges["incoming"][0]["from_wallet"] == "TREASURY_X"
    assert len(edges["outgoing"]) == 1
    assert edges["outgoing"][0]["to_wallet"] == "CREATOR_X"


def test_hop2_rpc_failure_leaves_no_partial_hop1_evidence(ops, monkeypatch):
    """Failure-semantics proof: pre-X77.1 code wrote hop1's funder record and
    close-destination evidence BEFORE attempting hop2's RPC call, so a hop2
    exception left that hop1 write committed (a partial-write bug). Post-X77.1,
    hop1 evidence collection is deferred until after hop2 resolves, so a hop2
    failure must leave the funder_wallet column and the wrap-close-candidate
    row entirely absent -- nothing partial survives."""
    def failing_find_funder(wallet, rpc_counter, ops_conn=None, **_opts):
        rpc_counter[0] += 1
        if wallet == "CREATOR_X":
            return ("SUBPROV_X", "sig_creator_funding", 100, 1000, 0.123039, "WSOL_WRAP_CLOSE")
        if wallet == "SUBPROV_X":
            raise RuntimeError("simulated hop2 RPC failure")
        return (None, None, None, None, None, None)

    monkeypatch.setattr(walkback_worker, "_find_funder_via_rpc", failing_find_funder)
    monkeypatch.setattr(walkback_worker, "_get_tx", lambda _sig: _wrap_close_tx("CREATOR_X"))

    row = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    walkback_worker._process_row(ops, row)

    queue_row = ops.execute(
        "SELECT status, funder_wallet, funding_mechanism FROM wt_walkback_queue WHERE mint='mintX'"
    ).fetchone()
    # attempts=1 < MAX_ATTEMPTS, so the exception handler resets to 'pending'
    # for retry rather than terminal 'failed' -- either way, no hop1 write survives.
    assert queue_row["status"] == "pending"
    assert queue_row["funder_wallet"] is None
    assert queue_row["funding_mechanism"] is None

    close = ops.execute(
        "SELECT COUNT(*) AS n FROM wt_wrap_close_candidates WHERE creator='CREATOR_X'"
    ).fetchone()
    assert close["n"] == 0

    edges = edges_for_wallet(ops, "SUBPROV_X")
    assert len(edges["incoming"]) == 0
    assert len(edges["outgoing"]) == 0


def test_idempotent_retry_produces_no_duplicate_rows(ops, monkeypatch):
    """Idempotency proof: running the same FULL_WALKBACK row through
    _process_row twice (simulating a retry after a crash between commit and
    queue-status update) must not create duplicate provisioning-edge rows or
    wrap-close-candidate rows -- _store_funder/_persist_hop1_evidence/
    _capture_provisioning_facts are all overwrite-semantics, not INSERT-only."""
    def fake_find_funder(wallet, rpc_counter, ops_conn=None, **_opts):
        rpc_counter[0] += 1
        if wallet == "CREATOR_X":
            return ("SUBPROV_X", "creator_funding_sig", 200, 2000, 0.123039, "WSOL_WRAP_CLOSE")
        if wallet == "SUBPROV_X":
            return ("TREASURY_X", "capital_funding_sig", 100, 1900, 720.0, "PLAIN_XFER")
        return (None, None, None, None, None, None)

    monkeypatch.setattr(walkback_worker, "_find_funder_via_rpc", fake_find_funder)
    monkeypatch.setattr(walkback_worker, "_get_tx", lambda _sig: _wrap_close_tx("CREATOR_X"))
    ops.execute("INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY_X', 1)")

    row = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    walkback_worker._process_row(ops, row)

    # Simulate retry: reset status back to running, re-fetch fresh row, re-run.
    ops.execute("UPDATE wt_walkback_queue SET status='running' WHERE mint='mintX'")
    ops.commit()
    row2 = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    walkback_worker._process_row(ops, row2)

    close_count = ops.execute(
        "SELECT COUNT(*) AS n FROM wt_wrap_close_candidates WHERE creator='CREATOR_X'"
    ).fetchone()["n"]
    assert close_count == 1

    edges = edges_for_wallet(ops, "SUBPROV_X")
    assert len(edges["incoming"]) == 1
    assert len(edges["outgoing"]) == 1


def test_living_callback_runs_only_after_walkback_commit(ops, monkeypatch):
    """The real terminal writer commits before invoking the bounded bridge."""
    events = []
    monkeypatch.setattr(walkback_worker, "materialize_outcome", lambda *_: {}) if hasattr(walkback_worker, "materialize_outcome") else None
    import src.ops.attribution_outcome as attribution_outcome
    import src.ops.watchtower_candidates as candidates
    monkeypatch.setattr(attribution_outcome, "materialize_outcome", lambda *_: {})
    monkeypatch.setattr(candidates, "sync_walkback_result", lambda *_: None)
    monkeypatch.setattr(walkback_worker, "_promote_if_canonical_watchtower", lambda *_: None)

    def callback(conn, mint):
        events.append(("callback", conn.in_transaction, conn.execute(
            "SELECT status FROM wt_walkback_queue WHERE mint=?", (mint,)).fetchone()[0]))
        return {"status": "DISPATCHED"}

    monkeypatch.setattr(walkback_worker, "_notify_living_after_walkback_commit", callback)
    walkback_worker._mark_complete(ops, "mintX", "NO_ATTRIBUTION_FOUND", None, None, 0)
    assert events == [("callback", False, "complete")]


def test_living_callback_failure_cannot_rollback_committed_walkback(ops, monkeypatch):
    """A post-commit Living exception is isolated from durable source evidence."""
    import src.ops.attribution_outcome as attribution_outcome
    import src.ops.watchtower_candidates as candidates
    monkeypatch.setattr(attribution_outcome, "materialize_outcome", lambda *_: {})
    monkeypatch.setattr(candidates, "sync_walkback_result", lambda *_: None)
    monkeypatch.setattr(walkback_worker, "_promote_if_canonical_watchtower", lambda *_: None)
    monkeypatch.setattr(walkback_worker, "_notify_living_after_walkback_commit", lambda *_: (_ for _ in ()).throw(RuntimeError("living boom")))
    with pytest.raises(RuntimeError, match="living boom"):
        walkback_worker._mark_complete(ops, "mintX", "NO_ATTRIBUTION_FOUND", None, None, 0)
    # This test uses an injected replacement to prove the source commit occurs
    # before the post-commit hook. The production hook catches its own failures.
    assert ops.execute("SELECT status FROM wt_walkback_queue WHERE mint='mintX'").fetchone()[0] == "complete"


def test_production_living_callback_catches_failure(ops, monkeypatch):
    """The production callback reports its own error instead of raising it."""
    import src.ops.living_potential_operations as living
    monkeypatch.setattr(living, "handle_walkback_evidence_update", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("publisher failed")))
    assert walkback_worker._notify_living_after_walkback_commit(ops, "mintX")["status"] == "FAILED"
