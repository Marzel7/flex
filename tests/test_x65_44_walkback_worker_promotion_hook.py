"""X65.44 — integration: _mark_complete's post-commit promotion hook.

Verifies the full path: walkback_worker._process_row reaches a genuine
CANONICAL_OPERATOR_REACHED outcome (treasury resolves to the canonical
WATCHTOWER operator via operator_entities/operators, matching production
schema exactly) -> materialize_outcome commits wt_attribution_outcomes ->
_promote_if_canonical_watchtower fires -> wt_watchtower_launches gets the
new row, with WALKBACK_RECOVERED provenance. Also proves the live-cascade
writer (ws_cascade_store.record_launch, called directly, unrelated to the
walkback worker) is unaffected by this hook.
"""
from __future__ import annotations

import sqlite3

import pytest

import src.core.walkback_worker as walkback_worker
from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID


OPS_SCHEMA = """
CREATE TABLE wt_walkback_queue (
 mint TEXT PRIMARY KEY, creator TEXT, subprov TEXT, treasury TEXT,
 walkback_class TEXT NOT NULL DEFAULT 'FULL_WALKBACK', attribution_source TEXT,
 status TEXT NOT NULL DEFAULT 'pending', rpc_used INTEGER NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
 enqueued_at INTEGER NOT NULL DEFAULT 0, started_at INTEGER, completed_at INTEGER,
 updated_at INTEGER NOT NULL DEFAULT 0, intelligence_outcome TEXT,
 funder_wallet TEXT, funding_mechanism TEXT, funder_amount_sol REAL,
 funder_sig TEXT, funder_slot INTEGER, funder_block_time INTEGER,
 create_anchor_signature TEXT, create_anchor_block_time INTEGER
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
CREATE TABLE operators (
 operator_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'CANDIDATE',
 confidence TEXT NOT NULL DEFAULT 'UNKNOWN', first_seen INTEGER, last_seen INTEGER,
 summary TEXT, review_state TEXT NOT NULL DEFAULT 'PENDING', display_name TEXT,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE operator_entities (
 operator_id TEXT NOT NULL, entity_address TEXT NOT NULL, entity_type TEXT NOT NULL DEFAULT 'UNKNOWN',
 confidence TEXT NOT NULL DEFAULT 'UNKNOWN', evidence_count INTEGER NOT NULL DEFAULT 0,
 first_seen INTEGER, last_seen INTEGER, added_at INTEGER NOT NULL,
 PRIMARY KEY (operator_id, entity_address)
);
CREATE TABLE wt_watchtower_launches (
 id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT, creator_wallet TEXT NOT NULL,
 create_signature TEXT, create_time INTEGER, create_slot INTEGER,
 treasury_wallet TEXT, subprov_wallet TEXT, subprov_funding_sol REAL,
 wrap_close_sol REAL, wrap_close_signature TEXT,
 birth_to_launch_seconds INTEGER, create_to_migration_secs INTEGER,
 detection_source TEXT, detection_delay_seconds INTEGER,
 funding_mechanism TEXT DEFAULT 'WSOL_WRAP_CLOSE',
 creator_extraction_method TEXT DEFAULT 'CLOSE_ACCOUNT_DESTINATION',
 confidence TEXT DEFAULT 'STRICT', state TEXT DEFAULT 'FIRED_CREATE',
 recorded_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
 UNIQUE(creator_wallet, create_signature)
);
CREATE TABLE wt_candidate_websocket_watches (
 candidate_wallet TEXT, subprov_wallet TEXT, state TEXT, close_reason TEXT, closed_at INTEGER
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
    # TREASURY_X is both a confirmed treasury AND a WATCHTOWER operator
    # entity -- the exact production shape (operator_entities join) that
    # derive_outcome() requires to actually reach CANONICAL_OPERATOR_REACHED
    # (not merely the legacy wt_walkback_queue.intelligence_outcome field).
    conn.execute("INSERT INTO wt_confirmed_treasuries (treasury, confirmed_at) VALUES ('TREASURY_X', 500)")
    conn.execute(
        "INSERT INTO operators (operator_id,status,confidence,created_at,updated_at,display_name) "
        "VALUES (?,'CONFIRMED','HIGH',500,500,'WATCHTOWER')", (WATCHTOWER_OPERATOR_ID,),
    )
    conn.execute(
        "INSERT INTO operator_entities (operator_id,entity_address,entity_type,added_at) "
        "VALUES (?,'TREASURY_X','treasury',500)", (WATCHTOWER_OPERATOR_ID,),
    )
    conn.commit()

    def fake_find_funder(wallet, rpc_counter, ops_conn=None, **_search_options):
        rpc_counter[0] += 1
        if wallet == "CREATOR_X":
            return ("SUBPROV_X", "sig_creator_funding", 100, 1000, 0.05, "WSOL_WRAP_CLOSE")
        if wallet == "SUBPROV_X":
            return ("TREASURY_X", "sig_subprov_funding", 90, 982, 0.203, "PLAIN_XFER")
        return (None, None, None, None, None, None)

    monkeypatch.setattr(walkback_worker, "_find_funder_via_rpc", fake_find_funder)
    monkeypatch.setattr(walkback_worker, "_surface_treasury_review_lead", lambda *a, **k: "recorded")
    return conn


def test_canonical_operator_reached_outcome_promotes_the_mint(ops):
    row = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    walkback_worker._process_row(ops, row)

    outcome = ops.execute("SELECT outcome_type, operator_id FROM wt_attribution_outcomes WHERE mint='mintX'").fetchone()
    assert outcome is not None
    assert outcome["outcome_type"] == "CANONICAL_OPERATOR_REACHED"
    assert outcome["operator_id"] == WATCHTOWER_OPERATOR_ID

    launch = ops.execute("SELECT * FROM wt_watchtower_launches WHERE mint='mintX'").fetchone()
    assert launch is not None
    assert launch["creator_wallet"] == "CREATOR_X"
    assert launch["confidence"] == "WALKBACK"
    assert launch["creator_extraction_method"] == "WALKBACK_RECOVERED"


def test_promotion_happens_after_outcome_commit_not_before(ops, monkeypatch):
    # Spy on promote_walkback_confirmed_watchtower to confirm it is called
    # with data already reflecting the durable, committed outcome (not
    # invoked speculatively before commit).
    calls = []
    import src.core.watchtower_registry_promotion as promo_module
    original = promo_module.promote_walkback_confirmed_watchtower

    def spy(conn, mint, **kwargs):
        # At call time, the outcome row must ALREADY be visible/committed.
        row = conn.execute("SELECT outcome_type FROM wt_attribution_outcomes WHERE mint=?", (mint,)).fetchone()
        calls.append(row["outcome_type"] if row else None)
        return original(conn, mint, **kwargs)

    monkeypatch.setattr(promo_module, "promote_walkback_confirmed_watchtower", spy)
    # walkback_worker imports the function locally inside _promote_if_canonical_watchtower,
    # so patch the module it re-imports from.
    monkeypatch.setattr(
        "src.core.watchtower_registry_promotion.promote_walkback_confirmed_watchtower", spy,
    )

    row = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    walkback_worker._process_row(ops, row)

    assert calls == ["CANONICAL_OPERATOR_REACHED"]


def test_reprocessing_the_same_row_does_not_duplicate_the_registry_row(ops):
    row = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    walkback_worker._process_row(ops, row)
    row2 = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    walkback_worker._process_row(ops, row2)

    count = ops.execute("SELECT COUNT(*) FROM wt_watchtower_launches WHERE mint='mintX'").fetchone()[0]
    assert count == 1


def test_non_watchtower_outcome_does_not_trigger_promotion(ops):
    # Remove the operator_entities link so this resolves to LINEAGE_GAP
    # instead of CANONICAL_OPERATOR_REACHED.
    ops.execute("DELETE FROM operator_entities")
    ops.commit()

    row = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    walkback_worker._process_row(ops, row)

    outcome = ops.execute("SELECT outcome_type FROM wt_attribution_outcomes WHERE mint='mintX'").fetchone()
    assert outcome["outcome_type"] != "CANONICAL_OPERATOR_REACHED"
    assert ops.execute("SELECT * FROM wt_watchtower_launches WHERE mint='mintX'").fetchone() is None


def test_promotion_failure_does_not_break_walkback_completion(ops, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("simulated registry failure")
    monkeypatch.setattr(
        "src.core.watchtower_registry_promotion.promote_walkback_confirmed_watchtower", boom,
    )

    row = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    # Must not raise -- the walkback commit already succeeded and must be preserved.
    walkback_worker._process_row(ops, row)

    outcome = ops.execute("SELECT outcome_type FROM wt_attribution_outcomes WHERE mint='mintX'").fetchone()
    assert outcome["outcome_type"] == "CANONICAL_OPERATOR_REACHED"
    queue_row = ops.execute("SELECT status FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    assert queue_row["status"] == "complete"


def test_live_cascade_writer_unaffected_by_promotion_hook(ops):
    # ws_cascade_store.record_launch is called directly by the live cascade
    # daemon, entirely independent of the walkback worker -- this hook must
    # not interfere with that path at all.
    from src.core import ws_cascade_store as store
    newly = store.record_launch(
        ops, mint="live_mint_1", creator="LiveCreator", create_sig="livesig1",
        create_time=1000, treasury="LiveTreasury", subprov="LiveSubprov",
        wrap_close_sig="livewrapsig", birth_to_launch_s=5,
    )
    assert newly is True
    row = ops.execute("SELECT * FROM wt_watchtower_launches WHERE mint='live_mint_1'").fetchone()
    assert row["confidence"] == "STRICT"
    assert row["creator_extraction_method"] == "CLOSE_ACCOUNT_DESTINATION"
