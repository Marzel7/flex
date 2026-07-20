"""X26.6.1 — Reject-State-Aware Discovery Provenance.

X26.3 introduced state='REJECTED_INFRASTRUCTURE' for wt_discovered_subprovs
rows that were incorrectly promoted from known infrastructure wallets
(Axiom, CEX hot wallets, etc.) funding creators. X26.6's Behaviour audit
was separate/upstream; this sprint closes a related gap X26.3 itself did
not touch: Discovery's own SUBPROVISIONER_RESOLVED node still rendered for
ANY wt_discovered_subprovs row regardless of state, so a REJECTED_
INFRASTRUCTURE row (Axiom) still produced a "creator-funding observation(s)
support the role" timeline node — exactly the same class of unsupported-
promotion defect X26.2/X26.3 fixed elsewhere, just in the one place those
sprints didn't check.

This suite proves the fix in src/discovery/service.py:
  - a REJECTED* wt_discovered_subprovs row never produces a
    SUBPROVISIONER_RESOLVED node;
  - _subprov_reason() can never produce supportive "support the role"
    wording for a rejected row even if called directly;
  - genuine (non-rejected) sub-provisioners are completely unaffected;
  - Infrastructure Boundary / attribution_outcome rendering, which is
    independent of wt_discovered_subprovs, is unaffected;
  - raw funding evidence (wt_wrap_close_candidates / launch records) is
    still rendered — only the SUBPROVISIONER_RESOLVED node itself is
    suppressed;
  - no database row is mutated by any of this (read-only Discovery).
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
 funding_mechanism TEXT, rejected_reason TEXT, first_seen INTEGER, last_seen INTEGER);
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
        fd, path = tempfile.mkstemp(suffix="_x26_6_1.db")
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


def _node(data, kind):
    return next((n for n in data["timeline"] if n["kind"] == kind), None)


AXIOM = "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk"


# ---------------------------------------------------------------------------
# Rejected infrastructure omitted
# ---------------------------------------------------------------------------

def test_rejected_infrastructure_subprov_omits_node(db_factory):
    db = db_factory(f"""
    INSERT INTO wt_wrap_close_candidates VALUES
      ('CREATOR','WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','{AXIOM}',NULL,
       1.0,'SIG1',100,'STRICT',100);
    INSERT INTO wt_discovered_subprovs VALUES
      ('{AXIOM}',2,NULL,NULL,0.4,'REJECTED_INFRASTRUCTURE',0,NULL,
       'known infrastructure wallet',100,200);
    """)
    data = _service(db).resolve("CREATOR", "creator")
    kinds = _kinds(data)
    assert "SUBPROVISIONER_RESOLVED" not in kinds
    # raw creator-identification evidence is still shown
    assert "CREATOR_IDENTIFIED" in kinds


# ---------------------------------------------------------------------------
# All REJECTED_* variants omitted, not just REJECTED_INFRASTRUCTURE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", ["REJECTED_INFRASTRUCTURE", "REJECTED_NON_PROVISIONING", "REJECTED", "rejected_infrastructure"])
def test_all_rejected_states_omit_node(db_factory, state):
    db = db_factory(f"""
    INSERT INTO wt_wrap_close_candidates VALUES
      ('CREATOR','WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','SUBPROV',NULL,
       1.0,'SIG1',100,'STRICT',100);
    INSERT INTO wt_discovered_subprovs VALUES
      ('SUBPROV',2,NULL,NULL,0.4,'{state}',0,NULL,'x',100,200);
    """)
    data = _service(db).resolve("CREATOR", "creator")
    assert "SUBPROVISIONER_RESOLVED" not in _kinds(data)


# ---------------------------------------------------------------------------
# No "support the sub-provisioner role" text for rejected rows, even if
# _subprov_reason() were called directly (defensive hardening)
# ---------------------------------------------------------------------------

def test_subprov_reason_never_supportive_for_rejected_row():
    from src.discovery.service import DiscoveryService
    row = {
        "state": "REJECTED_INFRASTRUCTURE", "rejected_reason": "known infrastructure wallet",
        "wrap_close_count": 2, "creator_count": 2, "treasury": None,
    }
    reason = DiscoveryService._subprov_reason(row)
    assert "observation(s) support the role" not in reason
    assert "support the sub-provisioner role" not in reason
    assert "do not support the role" in reason
    assert "rejected" in reason.lower()


def test_subprov_reason_still_supportive_for_genuine_row():
    from src.discovery.service import DiscoveryService
    row = {"state": "PROVISIONAL_SUBPROV", "wrap_close_count": 3, "creator_count": 3, "treasury": "TREASURY"}
    reason = DiscoveryService._subprov_reason(row)
    assert "support the role" in reason


# ---------------------------------------------------------------------------
# Genuine candidates / provisional subprovs still render exactly as before
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", ["PROVISIONAL_SUBPROV", "PROVISION_CANDIDATE", "CONFIRMED_AUTO", None])
def test_genuine_subprov_states_still_render(db_factory, state):
    state_sql = "NULL" if state is None else f"'{state}'"
    db = db_factory(f"""
    INSERT INTO wt_wrap_close_candidates VALUES
      ('CREATOR','WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','SUBPROV',NULL,
       1.0,'SIG1',100,'STRICT',100);
    INSERT INTO wt_discovered_subprovs VALUES
      ('SUBPROV',3,'TREASURY',NULL,0.6,{state_sql},2,'WSOL_WRAP_CLOSE',NULL,100,200);
    """)
    data = _service(db).resolve("CREATOR", "creator")
    assert "SUBPROVISIONER_RESOLVED" in _kinds(data)
    node = _node(data, "SUBPROVISIONER_RESOLVED")
    assert "support the role" in node["reason"]


# ---------------------------------------------------------------------------
# Infrastructure Boundary (attribution_outcome) remains visible independent
# of the rejected sub-provisioner node being suppressed
# ---------------------------------------------------------------------------

def test_infrastructure_attribution_outcome_unaffected(db_factory):
    db = db_factory(f"""
    INSERT INTO wt_wrap_close_candidates VALUES
      ('CREATOR','WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','{AXIOM}',NULL,
       1.0,'SIG1',100,'STRICT',100);
    INSERT INTO wt_discovered_subprovs VALUES
      ('{AXIOM}',2,NULL,NULL,0.4,'REJECTED_INFRASTRUCTURE',0,NULL,
       'known infrastructure wallet',100,200);
    INSERT INTO wt_attribution_outcomes VALUES
      ('MINT','KNOWN_RELAY_REACHED','Attribution boundary reached. Known infrastructure boundary: Axiom.',
       '{AXIOM}','INFRASTRUCTURE','MEDIUM','{{}}',NULL,0,0,150,NULL,150);
    """)
    data = _service(db).resolve("MINT", "token")
    assert data["attribution_outcome"]["outcome_type"] == "KNOWN_RELAY_REACHED"
    assert data["attribution_outcome"]["terminal_entity"] == AXIOM
    assert "SUBPROVISIONER_RESOLVED" not in _kinds(data)


# ---------------------------------------------------------------------------
# Raw funding evidence (creator identification) preserved
# ---------------------------------------------------------------------------

def test_raw_creator_identification_preserved_for_rejected_subprov(db_factory):
    db = db_factory(f"""
    INSERT INTO wt_wrap_close_candidates VALUES
      ('CREATOR','WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','{AXIOM}',NULL,
       2.5,'SIG_XYZ',100,'STRICT',100);
    INSERT INTO wt_discovered_subprovs VALUES
      ('{AXIOM}',2,NULL,NULL,0.4,'REJECTED_INFRASTRUCTURE',0,NULL,
       'known infrastructure wallet',100,200);
    """)
    data = _service(db).resolve("CREATOR", "creator")
    node = _node(data, "CREATOR_IDENTIFIED")
    assert node is not None
    assert any(e.get("value") == "SIG_XYZ" for e in node["evidence"])


# ---------------------------------------------------------------------------
# No database mutation from resolving a rejected-subprov entity
# ---------------------------------------------------------------------------

def test_no_database_mutation(db_factory):
    db = db_factory(f"""
    INSERT INTO wt_discovered_subprovs VALUES
      ('{AXIOM}',2,NULL,NULL,0.4,'REJECTED_INFRASTRUCTURE',0,NULL,
       'known infrastructure wallet',100,200);
    """)
    before = hashlib.sha256(open(db, "rb").read()).digest()
    _service(db).resolve(AXIOM, "sub_provisioner")
    after = hashlib.sha256(open(db, "rb").read()).digest()
    assert before == after
