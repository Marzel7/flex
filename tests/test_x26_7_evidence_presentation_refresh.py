"""X26.7 — Discovery Evidence Presentation Refresh.

X26.7's audit found that Discovery's whole-page "No historical discovery
evidence is available" fallback (templates/discovery.html, `if
(!d.timeline||!d.timeline.length)`) fired for real, live launches that
actually possess substantial persisted evidence: every sampled
KNOWN_CEX_REACHED/KNOWN_RELAY_REACHED/KNOWN_BRIDGE_REACHED attribution
outcome had a genuine wt_walkback_queue row (creator, funding_mechanism,
funder_wallet) that never became a timeline node, because _entity() only
ever built TOKEN_LAUNCH/CREATOR_IDENTIFIED nodes from
wt_watchtower_launches/wt_wrap_close_candidates/migrated_tokens/
wt_token_lifecycle -- never from wt_walkback_queue, even when it was the
only record of the launch that existed. This meant real, persisted
evidence (attribution_outcome, launch_profile, the creator itself) was
silently withheld from the page's normal render branch.

This suite proves the fix: a wt_walkback_queue row with a creator now
produces TOKEN_LAUNCH and CREATOR_IDENTIFIED timeline nodes (using only
fields that were already persisted -- no new evidence is invented), which
is enough to route the page to its normal (non-empty) render branch so
Launch Profile / Funding Walkback / Evidence Groups continue to render
instead of vanishing. It also proves the fix does not affect any launch
that already had a real launch/migration/lifecycle row, does not
resurrect any rejected sub-provisioner or leak operator identity, and
performs no database mutation.
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
 walkback_class TEXT, attribution_source TEXT, status TEXT, rpc_used INTEGER, attempts INTEGER,
 last_error TEXT, enqueued_at INTEGER, started_at INTEGER, completed_at INTEGER, updated_at INTEGER,
 intelligence_outcome TEXT, funder_wallet TEXT, funding_mechanism TEXT, funder_amount_sol REAL,
 funder_sig TEXT, funder_slot INTEGER, funder_block_time INTEGER);
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
        fd, path = tempfile.mkstemp(suffix="_x26_7.db")
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


AXIOM = "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk"


# ---------------------------------------------------------------------------
# Rejected-infrastructure launches: walkback-only evidence surfaces, but the
# rejected sub-provisioner conclusion itself never reappears (X26.6.1's fix
# must remain intact).
# ---------------------------------------------------------------------------

def test_rejected_infrastructure_launch_surfaces_walkback_evidence_not_empty(db_factory):
    db = db_factory(f"""
    INSERT INTO wt_walkback_queue VALUES
      ('MINT','CREATOR',NULL,NULL,'FULL_WALKBACK',NULL,'complete',1,1,NULL,
       90,95,150,150,'KNOWN_RELAY_REACHED','{AXIOM}','PLAIN_XFER',1.5,'FUNDSIG',NULL,140);
    INSERT INTO wt_attribution_outcomes VALUES
      ('MINT','KNOWN_RELAY_REACHED','Attribution boundary reached. Known infrastructure boundary: Axiom.',
       '{AXIOM}','INFRASTRUCTURE','HIGH','{{}}',NULL,0,0,150,NULL,150);
    INSERT INTO wt_discovered_subprovs VALUES
      ('{AXIOM}',2,NULL,NULL,0.4,'REJECTED_INFRASTRUCTURE',0,NULL,
       'known infrastructure wallet',100,200);
    """)
    data = _service(db).resolve("MINT", "token")
    kinds = _kinds(data)
    # Real evidence now surfaces instead of the whole page going empty.
    assert "TOKEN_LAUNCH" in kinds
    assert "CREATOR_IDENTIFIED" in kinds
    assert data["attribution_outcome"]["outcome_type"] == "KNOWN_RELAY_REACHED"
    assert data["launch_profile"]["classification"] == "OBSERVED_ONLY"
    # X26.6.1's fix must still hold: no rejected sub-provisioner conclusion.
    assert "SUBPROVISIONER_RESOLVED" not in kinds


# ---------------------------------------------------------------------------
# Walkback-only launches (no rejected subprov involved at all)
# ---------------------------------------------------------------------------

def test_walkback_only_launch_no_longer_empty(db_factory):
    db = db_factory("""
    INSERT INTO wt_walkback_queue VALUES
      ('MINT','CREATOR',NULL,NULL,'FULL_WALKBACK',NULL,'complete',1,1,NULL,
       90,95,150,150,'KNOWN_CEX_REACHED','CEXWALLET','PLAIN_XFER',2.0,'FUNDSIG2',NULL,140);
    """)
    data = _service(db).resolve("MINT", "token")
    assert data["timeline"], "timeline must not be empty when a real walkback row exists"
    kinds = _kinds(data)
    assert "TOKEN_LAUNCH" in kinds
    assert "CREATOR_IDENTIFIED" in kinds


# ---------------------------------------------------------------------------
# Provisioned launches (genuine wrap-close + launch row) render exactly as
# before -- the fallback path must never override real evidence.
# ---------------------------------------------------------------------------

def test_provisioned_launch_unaffected(db_factory):
    db = db_factory("""
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATESIG',100,'TREASURY','SUBPROV','WRAPSIG',
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',100);
    INSERT INTO wt_wrap_close_candidates VALUES
      ('CREATOR','WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','SUBPROV',NULL,
       1.0,'WRAPSIG',95,'STRICT',95);
    INSERT INTO wt_walkback_queue VALUES
      ('MINT','CREATOR','SUBPROV','TREASURY','FULL_WALKBACK',NULL,'complete',0,1,NULL,
       90,95,150,150,'WATCHTOWER_CONFIRMED',NULL,NULL,NULL,NULL,NULL,NULL);
    """)
    data = _service(db).resolve("MINT", "token")
    kinds = _kinds(data)
    assert "TOKEN_LAUNCH" in kinds
    assert "CREATOR_IDENTIFIED" in kinds
    node = next(n for n in data["timeline"] if n["kind"] == "CREATOR_IDENTIFIED")
    # Still uses the wrap-close-derived reason, not the walkback-queue fallback.
    assert node["detector"] == "Wrap-Close Detector"


# ---------------------------------------------------------------------------
# Treasury-confirmed launches unaffected
# ---------------------------------------------------------------------------

def test_treasury_confirmed_launch_unaffected(db_factory):
    db = db_factory("""
    INSERT INTO wt_watchtower_launches VALUES
      ('MINT','CREATOR','CREATESIG',100,'TREASURY','SUBPROV','WRAPSIG',
       'WSOL_WRAP_CLOSE','CLOSE_ACCOUNT_DESTINATION','STRICT',100);
    INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY','HIGH','manual_review',500.0,20,100);
    """)
    data = _service(db).resolve("MINT", "token")
    assert data["timeline"]
    # Treasury resolution still works exactly as before.
    kinds = _kinds(data)
    assert "TREASURY_RESOLVED" in kinds


# ---------------------------------------------------------------------------
# No operator leakage introduced by the new walkback-derived nodes
# ---------------------------------------------------------------------------

def test_no_operator_leakage_from_walkback_only_node(db_factory):
    db = db_factory("""
    INSERT INTO wt_walkback_queue VALUES
      ('MINT','CREATOR',NULL,NULL,'FULL_WALKBACK',NULL,'complete',1,1,NULL,
       90,95,150,150,'LINEAGE_GAP','FUNDER','PLAIN_XFER',1.0,'SIG',NULL,140);
    """)
    data = _service(db).resolve("MINT", "token")
    assert data["canonical_identity"] is None
    for n in data["timeline"]:
        assert "operator" not in str(n.get("reason", "")).lower()


# ---------------------------------------------------------------------------
# No operator confirmed / no treasury cases still behave correctly
# ---------------------------------------------------------------------------

def test_no_treasury_no_operator_case(db_factory):
    db = db_factory("""
    INSERT INTO wt_walkback_queue VALUES
      ('MINT','CREATOR',NULL,NULL,'FULL_WALKBACK',NULL,'complete',1,1,NULL,
       90,95,150,150,'INSUFFICIENT_EVIDENCE',NULL,NULL,NULL,NULL,NULL,NULL);
    """)
    data = _service(db).resolve("MINT", "token")
    assert data["operation_identity"] is None
    assert data["canonical_identity"] is None
    # Still surfaces the creator even with no funder/mechanism data at all.
    assert "CREATOR_IDENTIFIED" in _kinds(data)


# ---------------------------------------------------------------------------
# No database mutation
# ---------------------------------------------------------------------------

def test_no_database_mutation(db_factory):
    db = db_factory(f"""
    INSERT INTO wt_walkback_queue VALUES
      ('MINT','CREATOR',NULL,NULL,'FULL_WALKBACK',NULL,'complete',1,1,NULL,
       90,95,150,150,'KNOWN_RELAY_REACHED','{AXIOM}','PLAIN_XFER',1.5,'SIG',NULL,140);
    """)
    before = hashlib.sha256(open(db, "rb").read()).digest()
    _service(db).resolve("MINT", "token")
    after = hashlib.sha256(open(db, "rb").read()).digest()
    assert before == after


# ---------------------------------------------------------------------------
# Wording fix: "terminated at infrastructure infrastructure" no longer
# possible for terminal_entity_type == 'INFRASTRUCTURE'
# ---------------------------------------------------------------------------

def test_infrastructure_infrastructure_wording_fixed():
    """The old unconditional `+' infrastructure.'` concatenation is gone; the
    fix must be conditional on the noun already being 'infrastructure'."""
    root = Path(__file__).resolve().parents[1]
    html = (root / "templates/discovery.html").read_text()
    assert "terminalnoun==='infrastructure'?'':' infrastructure'" in html.replace(" ", "").lower() \
        or "terminalNoun==='infrastructure'?'':' infrastructure'" in html
