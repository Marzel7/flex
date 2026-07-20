"""X26.2.1 — Fix Attribution Promotion (Discovery-side gate only).

X26.2 established that Discovery rendered a "WATCHTOWER ATTRIBUTION" node
for launches whose watchtower_token_attribution row existed purely because
a wallet was found in wt_discovered_subprovs (via walkback_worker.py's
`confirmed_subprov OR treasury` write gate) — with NO confirmed treasury,
NO Canonical Operator, and NO Operation Identity. This suite proves the
fix: Discovery's own rendering gate now requires `matched_treasury` to be
genuinely populated before creating this timeline node at all. The
underlying table/writer (walkback_worker.py) and every other consumer of
watchtower_token_attribution (operation_scheduler.py, walkback_queue.py,
operation_dashboard_routes.py, watchtower_funnel.py, attribution_outcome.py)
are untouched — this is a presentation-layer-only change confined to
src/discovery/service.py's own timeline-node construction.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA = """
CREATE TABLE migrated_tokens (mint TEXT PRIMARY KEY, creator TEXT, migration_tx TEXT,
 migration_time INTEGER, stored_at INTEGER);
CREATE TABLE wt_watchtower_launches (mint TEXT, creator_wallet TEXT, create_signature TEXT,
 create_time INTEGER, treasury_wallet TEXT, subprov_wallet TEXT, wrap_close_signature TEXT,
 funding_mechanism TEXT, creator_extraction_method TEXT, confidence TEXT, recorded_at INTEGER);
CREATE TABLE wt_wrap_close_candidates (creator TEXT PRIMARY KEY, funding_mechanism TEXT,
 creator_extraction_method TEXT, subprov_wallet TEXT, lineage_source_treasury TEXT,
 base_amount_sol REAL, tx_signature TEXT, funded_at INTEGER, confidence TEXT, detected_at INTEGER);
CREATE TABLE wt_discovered_subprovs (subprov TEXT PRIMARY KEY, creator_count INTEGER,
 treasury TEXT, immediate_funder TEXT, confidence REAL, state TEXT, wrap_close_count INTEGER,
 funding_mechanism TEXT, first_seen INTEGER, last_seen INTEGER);
CREATE TABLE wt_confirmed_treasuries (treasury TEXT PRIMARY KEY, confidence TEXT, method TEXT,
 out_sol REAL, recipients INTEGER, confirmed_at INTEGER);
CREATE TABLE wt_treasury_review (treasury TEXT PRIMARY KEY, status TEXT, confidence TEXT,
 detected_at INTEGER, reviewed_at INTEGER, detected_via TEXT, recipients INTEGER, out_sol REAL);
CREATE TABLE watchtower_token_attribution (mint TEXT PRIMARY KEY, creator TEXT, score REAL,
 tier TEXT, reasons_json TEXT, matched_treasury TEXT, matched_subprov TEXT,
 reviewed_status TEXT, scored_at INTEGER);
CREATE TABLE wt_token_lifecycle (mint TEXT PRIMARY KEY, treasury TEXT, subprov TEXT, creator TEXT,
 lifecycle_state TEXT, launched_at INTEGER, migrated_at INTEGER, recycled_at INTEGER,
 operation_uuid TEXT, updated_at INTEGER);
CREATE TABLE wt_walkback_queue (mint TEXT PRIMARY KEY, creator TEXT, subprov TEXT, treasury TEXT,
 status TEXT, completed_at INTEGER, intelligence_outcome TEXT);
CREATE TABLE wt_ops_v2_treasury_resolution (operation_uuid TEXT PRIMARY KEY,
 current_assigned_treasury TEXT, positional_root_candidate TEXT, confidence REAL,
 evidence_path TEXT, status TEXT, reason TEXT, resolved_at INTEGER);
CREATE TABLE operators (operator_id TEXT PRIMARY KEY, status TEXT, confidence TEXT,
 first_seen INTEGER, last_seen INTEGER, summary TEXT, review_state TEXT, display_name TEXT,
 created_at INTEGER, updated_at INTEGER);
CREATE TABLE operator_entities (operator_id TEXT, entity_address TEXT, entity_type TEXT,
 confidence TEXT, evidence_count INTEGER, first_seen INTEGER, last_seen INTEGER, added_at INTEGER);
CREATE TABLE operator_evidence (evidence_id TEXT PRIMARY KEY, operator_id TEXT, evidence_type TEXT,
 category TEXT, source TEXT, entity_a TEXT, entity_b TEXT, weight REAL, detail_json TEXT,
 recorded_at INTEGER);
CREATE TABLE operator_reviews (review_id TEXT PRIMARY KEY, operator_id TEXT, decision TEXT,
 reviewer TEXT, reviewed_at INTEGER, notes TEXT, superseded_by TEXT);
CREATE TABLE wt_attribution_outcomes (mint TEXT PRIMARY KEY, outcome_type TEXT, stop_reason TEXT,
 terminal_entity TEXT, terminal_entity_type TEXT, confidence TEXT, evidence_json TEXT,
 operator_id TEXT, should_seed_emerging_operator INTEGER, should_retry INTEGER,
 completed_at INTEGER, source_queue_updated_at INTEGER, materialized_at INTEGER);
"""


@pytest.fixture()
def db_factory():
    paths = []

    def _make(extra_sql: str = "") -> str:
        fd, path = tempfile.mkstemp(suffix="_x26_2_1.db")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMA)
        if extra_sql:
            conn.executescript(extra_sql)
        conn.commit()
        conn.close()
        paths.append(path)
        return path

    yield _make
    for p in paths:
        os.unlink(p)


def _service(db_path):
    from src.discovery.service import DiscoveryService
    return DiscoveryService(db_path, db_path)


def _kinds(data):
    return {n["kind"] for n in data["timeline"]}


# ---------------------------------------------------------------------------
# Phase 6 required test 1: matched_subprov only -> no attribution node
# ---------------------------------------------------------------------------

def test_matched_subprov_only_renders_no_attribution_node(db_factory):
    db = db_factory("""
    INSERT INTO watchtower_token_attribution VALUES
      ('MINT','CREATOR',80,'WALKBACK','[]',NULL,'SUBPROV','AUTO',160);
    """)
    data = _service(db).resolve("MINT", "token")
    assert "CONFIRMED_TREASURY_ATTRIBUTION" not in _kinds(data)
    assert "WATCHTOWER_ATTRIBUTION" not in _kinds(data)


# ---------------------------------------------------------------------------
# Phase 6 required test 2: LINEAGE_GAP -> no attribution node
# (reproduces the exact X26.2 case: EjxEK9QN... equivalent fixture)
# ---------------------------------------------------------------------------

def test_lineage_gap_with_subprov_only_renders_no_attribution_node(db_factory):
    db = db_factory("""
    INSERT INTO wt_walkback_queue VALUES
      ('MINT','CREATOR','SUBPROV',NULL,'complete',150,'LINEAGE_GAP');
    INSERT INTO wt_attribution_outcomes VALUES
      ('MINT','LINEAGE_GAP','Attribution boundary reached.','SUBPROV','INFRASTRUCTURE',
       'MEDIUM','{}',NULL,0,0,150,NULL,150);
    INSERT INTO watchtower_token_attribution VALUES
      ('MINT','CREATOR',80,'WALKBACK','[]',NULL,'SUBPROV','AUTO',160);
    """)
    data = _service(db).resolve("MINT", "token")
    kinds = _kinds(data)
    assert "CONFIRMED_TREASURY_ATTRIBUTION" not in kinds
    assert data["attribution_outcome"]["outcome_type"] == "LINEAGE_GAP"
    assert data["canonical_identity"] is None


# ---------------------------------------------------------------------------
# Phase 6 required test 3: matched_treasury -> attribution node still renders
# ---------------------------------------------------------------------------

def test_matched_treasury_still_renders_attribution_node(db_factory):
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY','HIGH','walkback',24.2,8,110);
    INSERT INTO watchtower_token_attribution VALUES
      ('MINT','CREATOR',95,'STRONG','["Known infrastructure ancestry"]','TREASURY','SUBPROV','AUTO',160);
    """)
    data = _service(db).resolve("MINT", "token")
    kinds = _kinds(data)
    assert "CONFIRMED_TREASURY_ATTRIBUTION" in kinds
    node = next(n for n in data["timeline"] if n["kind"] == "CONFIRMED_TREASURY_ATTRIBUTION")
    assert node["state"] == "CONFIRMED"
    assert node["connected_entity"]["id"] == "TREASURY"


def test_matched_treasury_with_rejected_status_still_gates_correctly(db_factory):
    """A REJECTED reviewed_status must still be honoured for the confirmed
    branch (analyst rejection of a real treasury match is a real state)."""
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY','HIGH','walkback',24.2,8,110);
    INSERT INTO watchtower_token_attribution VALUES
      ('MINT','CREATOR',95,'STRONG','[]','TREASURY','SUBPROV','REJECTED',160);
    """)
    data = _service(db).resolve("MINT", "token")
    node = next(n for n in data["timeline"] if n["kind"] == "CONFIRMED_TREASURY_ATTRIBUTION")
    assert node["state"] == "REJECTED"


# ---------------------------------------------------------------------------
# Phase 6 required test 4: Canonical Operator remains independent
# ---------------------------------------------------------------------------

def test_canonical_operator_independent_of_attribution_node(db_factory):
    """Canonical Operator can be genuinely resolved (via a real
    wt_watchtower_launches treasury_wallet, independent of
    watchtower_token_attribution) at the same time a bare
    matched_subprov-only attribution row exists -- the node must still not
    render, and Canonical Operator must still resolve on its own evidence."""
    from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID
    db = db_factory(f"""
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY','SUBPROV','WRAPTX',
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY','HIGH','walkback',24.2,8,110);
    INSERT INTO operators VALUES
      ('{WATCHTOWER_OPERATOR_ID}','CONFIRMED','CERTAIN',100,170,'Known operation','REVIEWED','WATCHTOWER',90,170);
    INSERT INTO operator_entities VALUES
      ('{WATCHTOWER_OPERATOR_ID}','TREASURY','TREASURY','HIGH',2,100,170,165);
    INSERT INTO watchtower_token_attribution VALUES
      ('MINT','CREATOR',80,'WALKBACK','[]',NULL,'SUBPROV','AUTO',160);
    """)
    data = _service(db).resolve("MINT", "token")
    assert "CONFIRMED_TREASURY_ATTRIBUTION" not in _kinds(data)
    assert data["canonical_identity"] is not None
    assert data["canonical_identity"]["operator_name"] == "WATCHTOWER"


# ---------------------------------------------------------------------------
# Phase 6 required test 5: Axiom/infrastructure cases still render correctly
# ---------------------------------------------------------------------------

def test_infrastructure_attribution_unaffected_by_gate_fix(db_factory):
    db = db_factory("""
    INSERT INTO wt_attribution_outcomes VALUES
      ('MINT','KNOWN_RELAY_REACHED','Attribution boundary reached. Known infrastructure boundary: Axiom.',
       'AxiomRXZAq1J','AUTOMATION','HIGH','{}',NULL,0,0,150,NULL,150);
    INSERT INTO watchtower_token_attribution VALUES
      ('MINT','CREATOR',80,'WALKBACK','[]',NULL,'SUBPROV','AUTO',160);
    """)
    data = _service(db).resolve("MINT", "token")
    assert "CONFIRMED_TREASURY_ATTRIBUTION" not in _kinds(data)
    assert data["attribution_outcome"]["outcome_type"] == "KNOWN_RELAY_REACHED"
    assert data["attribution_outcome"]["terminal_entity"] == "AxiomRXZAq1J"


# ---------------------------------------------------------------------------
# Phase 6 required test 6: Funding Walkback remains unchanged
# ---------------------------------------------------------------------------

def test_funding_walkback_unaffected_by_gate_fix(db_factory):
    db = db_factory("""
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,NULL,'SUBPROV','WRAPTX',
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    INSERT INTO wt_discovered_subprovs VALUES
      ('SUBPROV',4,NULL,NULL,0.9,'PROVISIONAL_SUBPROV',4,'WSOL_WRAP_CLOSE',120,145);
    INSERT INTO watchtower_token_attribution VALUES
      ('MINT','CREATOR',80,'WALKBACK','[]',NULL,'SUBPROV','AUTO',160);
    """)
    data = _service(db).resolve("MINT", "token")
    assert "CONFIRMED_TREASURY_ATTRIBUTION" not in _kinds(data)
    # walkback hop for the subprov must still be present/unaffected
    assert any(h.get("address") == "SUBPROV" for h in data["walkback"]["hops"])


# ---------------------------------------------------------------------------
# Phase 6 required test 8: existing confirmed cases still pass
# (covered by test_discovery_workspace.py::test_token_chain_is_explainable_
# and_chronological, updated for the renamed kind — verified separately)
# ---------------------------------------------------------------------------

def test_confirmed_case_from_x15_fixture_still_passes(db_factory):
    """Mirrors the long-standing discovery_workspace fixture's confirmed
    scenario to guard against regressions in the primary confirmed path."""
    db = db_factory("""
    INSERT INTO migrated_tokens VALUES ('MINT','CREATOR','MIGTX',150,151);
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATETX',140,'TREASURY','SUBPROV','WRAPTX',
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',141);
    INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY','HIGH','walkback',24.2,8,110);
    INSERT INTO watchtower_token_attribution VALUES
      ('MINT','CREATOR',95,'STRONG','["Known infrastructure ancestry"]','TREASURY','SUBPROV','AUTO',160);
    """)
    data = _service(db).resolve("MINT", "token")
    kinds = _kinds(data)
    assert "CONFIRMED_TREASURY_ATTRIBUTION" in kinds
    assert data["state"] == "CONFIRMED"


# ---------------------------------------------------------------------------
# Phase 6 required test 9: no database mutation
# ---------------------------------------------------------------------------

def test_no_database_mutation(db_factory):
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY','HIGH','walkback',24.2,8,110);
    INSERT INTO watchtower_token_attribution VALUES
      ('MINT','CREATOR',95,'STRONG','[]','TREASURY','SUBPROV','AUTO',160);
    """)
    before = hashlib.sha256(open(db, "rb").read()).digest()
    _service(db).resolve("MINT", "token")
    after = hashlib.sha256(open(db, "rb").read()).digest()
    assert before == after


# ---------------------------------------------------------------------------
# Wording audit (Phase 4): the surviving node never asserts WATCHTOWER
# unconditionally -- kind is now operator-neutral, "CONFIRMED_TREASURY_ATTRIBUTION"
# ---------------------------------------------------------------------------

def test_attribution_node_kind_is_operator_neutral(db_factory):
    db = db_factory("""
    INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY','HIGH','walkback',24.2,8,110);
    INSERT INTO watchtower_token_attribution VALUES
      ('MINT','CREATOR',95,'STRONG','[]','TREASURY','SUBPROV','AUTO',160);
    """)
    data = _service(db).resolve("MINT", "token")
    node = next(n for n in data["timeline"] if n["kind"] == "CONFIRMED_TREASURY_ATTRIBUTION")
    assert "WATCHTOWER" not in node["kind"]
    assert "WATCHTOWER" not in (node.get("detector") or "")
    assert "WATCHTOWER" not in (node.get("rule") or "")
