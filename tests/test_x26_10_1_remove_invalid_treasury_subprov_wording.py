"""X26.10.1 — Remove Invalid Treasury/Sub-Provisioner Wording from Terminal
Infrastructure Behaviour.

X26.10 unified the evidence model across reviewed terminal-infrastructure
classes (CEX, automation, bridge, relay, custody), but one line survived
unfixed: `_build_behaviour_summary()`'s "Treasury funded sub-provisioner
via {mechanism}" statement was emitted unconditionally whenever a
wt_provisioning_edges row with edge_type='TREASURY_TO_SUBPROV' existed --
with no check on funder_role at all. Reproduced live: a real Binance-
attributed mint showed "Treasury funded sub-provisioner via PLAIN_XFER"
even though the "treasury" address here is just another wallet that
happened to send ~59,000 SOL to Binance -- a genuine treasury/sub-
provisioner relationship was never established, only a historical
pipeline edge shaped like one.

This suite proves: the "Treasury funded sub-provisioner" line only ever
renders when funder_role is VALID_SUBPROVISIONER (both roles genuinely,
independently established); it never appears for any reviewed terminal
class; the historical-session line no longer exposes internal table/
column names or implementation conditions; and genuine provisioning
paths (WSOL_WRAP_CLOSE, SEEDED_ACCOUNT_CLOSE, two-hop treasury->subprov->
creator chains) are completely unaffected.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile

import pytest

from src.ops.operational_behaviour import OperationalBehaviourService

AXIOM = "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk"
CEX_WALLET = "u6PJ8DtQuPFnfmwHbGFULQ4u4EgjDiyYKjVEsynXq2w"
BRIDGE_WALLET = "2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS"
RELAY_WALLET = "F7p3dFrjRTbtRp8FRF6qHLomXbKRBzpvBLjtQcfcgmNe"
CUSTODY_WALLET = "2Hgx1GjKRuH9H21Fzz7uGiqh1Fcz3wMP5PmgpDbyDDYp"

OPS_SCHEMA = """
CREATE TABLE wt_provisioning_sessions (
    session_id TEXT PRIMARY KEY, source_mint TEXT, treasury TEXT, subprov TEXT, creator TEXT,
    treasury_to_subprov_block_time INTEGER, subprov_to_creator_block_time INTEGER,
    creator_launch_time INTEGER, treasury_to_subprov_latency_seconds INTEGER,
    subprov_to_creator_latency_seconds INTEGER, creator_to_launch_latency_seconds INTEGER,
    treasury_to_subprov_mechanism TEXT, subprov_to_creator_mechanism TEXT,
    treasury_to_subprov_amount_sol REAL, subprov_to_creator_amount_sol REAL, recorded_at INTEGER
);
CREATE TABLE wt_provisioning_edges (
    edge_id TEXT PRIMARY KEY, edge_type TEXT, from_wallet TEXT, to_wallet TEXT,
    first_observed_by_flex INTEGER, last_observed_by_flex INTEGER, observation_count INTEGER,
    funding_mechanism TEXT, funding_amount_sol REAL, funding_tx_signature TEXT,
    funding_block_time INTEGER, source_mint TEXT, provenance TEXT
);
CREATE TABLE wt_discovered_subprovs (
    subprov TEXT PRIMARY KEY, creator_count INTEGER, treasury TEXT, treasury_known INTEGER,
    funding_mechanism TEXT, wrap_close_count INTEGER, state TEXT, rejected_reason TEXT
);
CREATE TABLE wt_treasury_review (
    treasury TEXT PRIMARY KEY, distinct_subprovs INTEGER, distinct_creators INTEGER, status TEXT
);
CREATE TABLE wt_attribution_outcomes (
    mint TEXT PRIMARY KEY, outcome_type TEXT, terminal_entity TEXT, terminal_entity_type TEXT
);
CREATE TABLE wt_walkback_queue (
    mint TEXT PRIMARY KEY, creator TEXT, subprov TEXT, treasury TEXT, funder_wallet TEXT, intelligence_outcome TEXT
);
"""

CORE_SCHEMA = """
CREATE TABLE wt_known_operator_hubs (hub_wallet TEXT PRIMARY KEY, operator_identity TEXT);
CREATE TABLE wt_provisioning_hubs (hub_address TEXT PRIMARY KEY, status TEXT);
"""


@pytest.fixture()
def db_factory():
    paths = []

    def _make(ops_sql: str = "", core_sql: str = "") -> tuple[str, str]:
        fd1, ops_path = tempfile.mkstemp(suffix="_x26_10_1_ops.db")
        os.close(fd1)
        conn = sqlite3.connect(ops_path)
        conn.executescript(OPS_SCHEMA)
        if ops_sql:
            conn.executescript(ops_sql)
        conn.commit()
        conn.close()

        fd2, core_path = tempfile.mkstemp(suffix="_x26_10_1_core.db")
        os.close(fd2)
        conn = sqlite3.connect(core_path)
        conn.executescript(CORE_SCHEMA)
        if core_sql:
            conn.executescript(core_sql)
        conn.commit()
        conn.close()

        paths.extend([ops_path, core_path])
        return ops_path, core_path

    yield _make
    for p in paths:
        os.unlink(p)


def _terminal_infra_fixture_sql(wallet, upstream="UPSTREAM_WALLET", creator="CREATOR", mint="MINT", mechanism="PLAIN_XFER"):
    """A wt_provisioning_edges TREASURY_TO_SUBPROV-shaped edge into `wallet`
    (the reviewed terminal infrastructure address), plus a SUBPROV_TO_CREATOR
    edge out of it, plus a session -- reproducing the exact live scenario
    that produced the invalid "Treasury funded sub-provisioner" wording."""
    return f"""
    INSERT INTO wt_discovered_subprovs VALUES
      ('{wallet}',2,NULL,0,'{mechanism}',0,'REJECTED_INFRASTRUCTURE',NULL);
    INSERT INTO wt_provisioning_edges VALUES
      ('edge1','TREASURY_TO_SUBPROV','{upstream}','{wallet}',100,100,1,'{mechanism}',175.0,'SIG_TS',95,'{mint}','WALKBACK');
    INSERT INTO wt_provisioning_edges VALUES
      ('edge2','SUBPROV_TO_CREATOR','{wallet}','{creator}',100,100,1,'{mechanism}',5.0,'SIG_SC',96,'{mint}','WALKBACK');
    INSERT INTO wt_provisioning_sessions VALUES
      ('sess1','{mint}','{upstream}','{wallet}','{creator}',95,96,NULL,NULL,NULL,NULL,'{mechanism}','{mechanism}',175.0,5.0,150);
    INSERT INTO wt_attribution_outcomes VALUES
      ('{mint}','KNOWN_CEX_REACHED','{wallet}','CEX');
    INSERT INTO wt_walkback_queue VALUES
      ('{mint}','{creator}',NULL,'{upstream}','{wallet}','LINEAGE_GAP');
    """


# ---------------------------------------------------------------------------
# The core defect, reproduced and fixed
# ---------------------------------------------------------------------------

def test_cex_never_renders_treasury_funded_subprovisioner(db_factory):
    ops_db, core_db = db_factory(_terminal_infra_fixture_sql(CEX_WALLET))
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(treasury="UPSTREAM_WALLET", subprov=CEX_WALLET, creator="CREATOR")
    all_text = " ".join(result["behaviour_summary"])
    assert "Treasury funded sub-provisioner" not in all_text


def test_sub_provisioner_funded_creator_never_appears_for_terminal_infra(db_factory):
    ops_db, core_db = db_factory(_terminal_infra_fixture_sql(CEX_WALLET))
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(treasury="UPSTREAM_WALLET", subprov=CEX_WALLET, creator="CREATOR")
    all_text = " ".join(result["behaviour_summary"])
    assert "Sub-provisioner funded creator" not in all_text


@pytest.mark.parametrize("wallet,subtype_phrase", [
    (AXIOM, "reviewed automation infrastructure"),
    (BRIDGE_WALLET, "reviewed bridge"),
    (RELAY_WALLET, "reviewed relay"),
    (CUSTODY_WALLET, "reviewed custody infrastructure"),
])
def test_all_reviewed_types_follow_same_neutral_wording(db_factory, wallet, subtype_phrase):
    ops_db, core_db = db_factory(_terminal_infra_fixture_sql(wallet))
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(treasury="UPSTREAM_WALLET", subprov=wallet, creator="CREATOR")
    all_text = " ".join(result["behaviour_summary"])
    assert "Treasury funded sub-provisioner" not in all_text
    assert "Sub-provisioner funded creator" not in all_text
    assert subtype_phrase in all_text


def test_plain_xfer_remains_visible(db_factory):
    ops_db, core_db = db_factory(_terminal_infra_fixture_sql(CEX_WALLET))
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(treasury="UPSTREAM_WALLET", subprov=CEX_WALLET, creator="CREATOR")
    assert any("PLAIN_XFER" in s for s in result["behaviour_summary"])


def test_funding_source_label_remains_visible(db_factory):
    ops_db, core_db = db_factory(_terminal_infra_fixture_sql(CEX_WALLET))
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(treasury="UPSTREAM_WALLET", subprov=CEX_WALLET, creator="CREATOR")
    assert any(s.startswith("Funding source:") for s in result["behaviour_summary"])


def test_attributed_launch_and_creator_metrics_unchanged(db_factory):
    ops_db, core_db = db_factory(_terminal_infra_fixture_sql(CEX_WALLET))
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(treasury="UPSTREAM_WALLET", subprov=CEX_WALLET, creator="CREATOR")
    assert result["infrastructure_activity"]["attributed_launch_count"] == 1
    assert result["infrastructure_activity"]["observed_creator_count"] == 1


def test_historical_wording_has_no_implementation_detail(db_factory):
    ops_db, core_db = db_factory(_terminal_infra_fixture_sql(CEX_WALLET))
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(treasury="UPSTREAM_WALLET", subprov=CEX_WALLET, creator="CREATOR")
    all_text = " ".join(result["behaviour_summary"])
    assert "provisioning session exists" not in all_text
    assert "funder is not a valid sub-provisioner" not in all_text
    assert "Funding relationship reconstructed from historical chain data" in all_text


def test_no_internal_table_or_column_names_in_prose(db_factory):
    ops_db, core_db = db_factory(_terminal_infra_fixture_sql(CEX_WALLET))
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(treasury="UPSTREAM_WALLET", subprov=CEX_WALLET, creator="CREATOR")
    all_text = " ".join(result["behaviour_summary"])
    for internal in ("wt_provisioning_sessions", "wt_provisioning_edges", "wt_discovered_subprovs", "wt_walkback_queue"):
        assert internal not in all_text


def test_duplicate_mechanism_wording_consolidated(db_factory):
    """Only ONE PLAIN_XFER-mechanism statement should describe the creator-
    funding relationship for a terminal-infrastructure funder -- not a
    separate, role-labelled duplicate for the treasury-shaped edge."""
    ops_db, core_db = db_factory(_terminal_infra_fixture_sql(CEX_WALLET))
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(treasury="UPSTREAM_WALLET", subprov=CEX_WALLET, creator="CREATOR")
    mechanism_lines = [s for s in result["behaviour_summary"] if "PLAIN_XFER" in s]
    assert len(mechanism_lines) == 1
    assert mechanism_lines[0] == "Creator funding observed via PLAIN_XFER"


# ---------------------------------------------------------------------------
# Genuine provisioning paths retain existing role-specific wording
# ---------------------------------------------------------------------------

def test_genuine_treasury_subprov_creator_chain_retains_role_wording(db_factory):
    ops_sql = """
    INSERT INTO wt_discovered_subprovs VALUES
      ('SUBPROV',5,'TREASURY',1,'WSOL_WRAP_CLOSE',3,'PROVISIONAL_SUBPROV',NULL);
    INSERT INTO wt_provisioning_edges VALUES
      ('edge1','TREASURY_TO_SUBPROV','TREASURY','SUBPROV',100,100,1,'WSOL_WRAP_CLOSE',175.0,'SIG1',95,'MINT','WALKBACK');
    INSERT INTO wt_provisioning_edges VALUES
      ('edge2','SUBPROV_TO_CREATOR','SUBPROV','CREATOR',100,100,1,'WSOL_WRAP_CLOSE',5.0,'SIG2',96,'MINT','WALKBACK');
    INSERT INTO wt_provisioning_sessions VALUES
      ('sess1','MINT','TREASURY','SUBPROV','CREATOR',95,96,NULL,NULL,NULL,NULL,'WSOL_WRAP_CLOSE','WSOL_WRAP_CLOSE',175.0,5.0,150);
    """
    ops_db, core_db = db_factory(ops_sql)
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(treasury="TREASURY", subprov="SUBPROV", creator="CREATOR")
    all_text = " ".join(result["behaviour_summary"])
    assert "Treasury funded sub-provisioner via WSOL_WRAP_CLOSE" in all_text
    assert "Sub-provisioner funded creator via WSOL_WRAP_CLOSE" in all_text
    assert "Walkback completed successfully (provisioning session recorded)" in all_text


def test_genuine_seeded_account_close_path_retains_role_wording(db_factory):
    ops_sql = """
    INSERT INTO wt_discovered_subprovs VALUES
      ('SUBPROV2',4,'TREASURY2',1,'SEEDED_ACCOUNT_CLOSE',2,'PROVISIONAL_SUBPROV',NULL);
    INSERT INTO wt_provisioning_edges VALUES
      ('edge1','SUBPROV_TO_CREATOR','SUBPROV2','CREATOR2',100,100,1,'SEEDED_ACCOUNT_CLOSE',5.0,'SIG3',96,'MINT2','WALKBACK');
    """
    ops_db, core_db = db_factory(ops_sql)
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov="SUBPROV2", creator="CREATOR2")
    all_text = " ".join(result["behaviour_summary"])
    assert "Sub-provisioner funded creator via SEEDED_ACCOUNT_CLOSE" in all_text


def test_genuine_subprov_without_edges_still_shows_creator_count(db_factory):
    ops_sql = """
    INSERT INTO wt_discovered_subprovs VALUES
      ('SUBPROV3',9,NULL,0,'WSOL_WRAP_CLOSE',5,'PROVISIONAL_SUBPROV',NULL);
    """
    ops_db, core_db = db_factory(ops_sql)
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov="SUBPROV3")
    assert "Sub-provisioner has funded 9 creators" in result["behaviour_summary"]


# ---------------------------------------------------------------------------
# X26.10 subtype labels unchanged
# ---------------------------------------------------------------------------

def test_x26_10_subtype_labels_unchanged(db_factory):
    ops_db, core_db = db_factory(_terminal_infra_fixture_sql(AXIOM))
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(treasury="UPSTREAM_WALLET", subprov=AXIOM, creator="CREATOR")
    assert "Funding source: Axiom · reviewed automation infrastructure" in result["behaviour_summary"]


# ---------------------------------------------------------------------------
# Attribution outcome / operator / operation identity untouched at the
# Discovery level (end-to-end)
# ---------------------------------------------------------------------------

def test_discovery_e2e_no_side_effects(db_factory):
    from src.discovery.service import DiscoveryService

    fd, db_path = tempfile.mkstemp(suffix="_x26_10_1_e2e.db")
    os.close(fd)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
    CREATE TABLE migrated_tokens (mint TEXT PRIMARY KEY, creator TEXT, migration_tx TEXT, migration_time INTEGER, stored_at INTEGER);
    CREATE TABLE wt_watchtower_launches (mint TEXT, creator_wallet TEXT, create_signature TEXT, create_time INTEGER,
      treasury_wallet TEXT, subprov_wallet TEXT, wrap_close_signature TEXT, funding_mechanism TEXT,
      creator_extraction_method TEXT, confidence TEXT, recorded_at INTEGER);
    CREATE TABLE wt_wrap_close_candidates (creator TEXT PRIMARY KEY, funding_mechanism TEXT, creator_extraction_method TEXT,
      subprov_wallet TEXT, lineage_source_treasury TEXT, base_amount_sol REAL, tx_signature TEXT, funded_at INTEGER,
      confidence TEXT, detected_at INTEGER);
    CREATE TABLE wt_discovered_subprovs (subprov TEXT PRIMARY KEY, creator_count INTEGER, treasury TEXT, immediate_funder TEXT,
      confidence REAL, state TEXT, wrap_close_count INTEGER, funding_mechanism TEXT, rejected_reason TEXT, first_seen INTEGER, last_seen INTEGER,
      treasury_known INTEGER);
    CREATE TABLE wt_confirmed_treasuries (treasury TEXT PRIMARY KEY, confidence TEXT, method TEXT, out_sol REAL, recipients INTEGER, confirmed_at INTEGER);
    CREATE TABLE wt_treasury_review (treasury TEXT PRIMARY KEY, status TEXT, confidence TEXT, detected_at INTEGER, reviewed_at INTEGER, detected_via TEXT, recipients INTEGER, out_sol REAL, distinct_subprovs INTEGER, distinct_creators INTEGER);
    CREATE TABLE watchtower_token_attribution (mint TEXT PRIMARY KEY, creator TEXT, score REAL, tier TEXT, reasons_json TEXT,
      matched_treasury TEXT, matched_subprov TEXT, reviewed_status TEXT, scored_at INTEGER);
    CREATE TABLE wt_token_lifecycle (mint TEXT PRIMARY KEY, treasury TEXT, subprov TEXT, creator TEXT, lifecycle_state TEXT,
      launched_at INTEGER, migrated_at INTEGER, recycled_at INTEGER, operation_uuid TEXT, updated_at INTEGER);
    CREATE TABLE wt_walkback_queue (mint TEXT PRIMARY KEY, creator TEXT, subprov TEXT, treasury TEXT, funder_wallet TEXT,
      status TEXT, completed_at INTEGER, intelligence_outcome TEXT);
    CREATE TABLE wt_ops_v2_treasury_resolution (operation_uuid TEXT PRIMARY KEY, current_assigned_treasury TEXT,
      positional_root_candidate TEXT, confidence REAL, evidence_path TEXT, status TEXT, reason TEXT, resolved_at INTEGER);
    CREATE TABLE operators (operator_id TEXT PRIMARY KEY, status TEXT, confidence TEXT, first_seen INTEGER, last_seen INTEGER,
      summary TEXT, review_state TEXT, display_name TEXT, created_at INTEGER, updated_at INTEGER);
    CREATE TABLE operator_entities (operator_id TEXT, entity_address TEXT, entity_type TEXT, confidence TEXT,
      evidence_count INTEGER, first_seen INTEGER, last_seen INTEGER, added_at INTEGER);
    CREATE TABLE operator_evidence (evidence_id TEXT PRIMARY KEY, operator_id TEXT, evidence_type TEXT, category TEXT,
      source TEXT, entity_a TEXT, entity_b TEXT, weight REAL, detail_json TEXT, recorded_at INTEGER);
    CREATE TABLE operator_reviews (review_id TEXT PRIMARY KEY, operator_id TEXT, decision TEXT, reviewer TEXT,
      reviewed_at INTEGER, notes TEXT, superseded_by TEXT);
    CREATE TABLE wt_attribution_outcomes (mint TEXT PRIMARY KEY, outcome_type TEXT, stop_reason TEXT, terminal_entity TEXT,
      terminal_entity_type TEXT, confidence TEXT, evidence_json TEXT, operator_id TEXT, should_seed_emerging_operator INTEGER,
      should_retry INTEGER, completed_at INTEGER, source_queue_updated_at INTEGER, materialized_at INTEGER);
    CREATE TABLE wt_provisioning_sessions (session_id TEXT PRIMARY KEY, source_mint TEXT, treasury TEXT, subprov TEXT, creator TEXT,
      treasury_to_subprov_block_time INTEGER, subprov_to_creator_block_time INTEGER, creator_launch_time INTEGER,
      treasury_to_subprov_latency_seconds INTEGER, subprov_to_creator_latency_seconds INTEGER, creator_to_launch_latency_seconds INTEGER,
      treasury_to_subprov_mechanism TEXT, subprov_to_creator_mechanism TEXT, treasury_to_subprov_amount_sol REAL,
      subprov_to_creator_amount_sol REAL, recorded_at INTEGER);
    CREATE TABLE wt_provisioning_edges (edge_id TEXT PRIMARY KEY, edge_type TEXT, from_wallet TEXT, to_wallet TEXT,
      first_observed_by_flex INTEGER, last_observed_by_flex INTEGER, observation_count INTEGER, funding_mechanism TEXT,
      funding_amount_sol REAL, funding_tx_signature TEXT, funding_block_time INTEGER, source_mint TEXT, provenance TEXT);
    """)
    conn.execute("""INSERT INTO wt_discovered_subprovs VALUES
      (?,2,NULL,NULL,0.4,'REJECTED_INFRASTRUCTURE',0,'PLAIN_XFER',NULL,100,200,0)""", (CEX_WALLET,))
    conn.execute("INSERT INTO wt_provisioning_edges VALUES ('e1','TREASURY_TO_SUBPROV','UPSTREAM',?,100,100,1,'PLAIN_XFER',175.0,'SIGTS',95,'MINT','WALKBACK')", (CEX_WALLET,))
    conn.execute("INSERT INTO wt_provisioning_edges VALUES ('e2','SUBPROV_TO_CREATOR',?,'CREATOR',100,100,1,'PLAIN_XFER',5.0,'SIGSC',96,'MINT','WALKBACK')", (CEX_WALLET,))
    conn.execute("INSERT INTO wt_walkback_queue VALUES ('MINT','CREATOR',NULL,'UPSTREAM',?,'complete',150,'LINEAGE_GAP')", (CEX_WALLET,))
    conn.execute("INSERT INTO wt_attribution_outcomes VALUES ('MINT','KNOWN_CEX_REACHED','x',?,'CEX','HIGH','{}',NULL,0,0,150,NULL,150)", (CEX_WALLET,))
    conn.commit()
    before = hashlib.sha256(open(db_path, "rb").read()).digest()

    svc = DiscoveryService(db_path, db_path)
    data = svc.resolve("MINT", "token")

    after = hashlib.sha256(open(db_path, "rb").read()).digest()

    all_text = " ".join(data["operational_behaviour"]["behaviour_summary"])
    assert "Treasury funded sub-provisioner" not in all_text
    assert data["attribution_outcome"]["outcome_type"] == "KNOWN_CEX_REACHED"
    assert data["canonical_identity"] is None
    assert data["operation_identity"] is None
    assert before == after
    os.unlink(db_path)


# ---------------------------------------------------------------------------
# No database mutation
# ---------------------------------------------------------------------------

def test_no_database_mutation(db_factory):
    ops_db, core_db = db_factory(_terminal_infra_fixture_sql(CEX_WALLET))
    before_ops = hashlib.sha256(open(ops_db, "rb").read()).digest()
    before_core = hashlib.sha256(open(core_db, "rb").read()).digest()
    svc = OperationalBehaviourService(ops_db, core_db)
    svc.build(treasury="UPSTREAM_WALLET", subprov=CEX_WALLET, creator="CREATOR")
    after_ops = hashlib.sha256(open(ops_db, "rb").read()).digest()
    after_core = hashlib.sha256(open(core_db, "rb").read()).digest()
    assert before_ops == after_ops
    assert before_core == after_core
