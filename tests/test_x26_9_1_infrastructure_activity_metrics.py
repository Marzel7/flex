"""X26.9.1 — Correct Infrastructure Activity Metrics in Discovery.

X26.9's audit found that Operational Behaviour surfaced
wt_discovered_subprovs.creator_count for reviewed infrastructure wallets
(e.g. Axiom) as if it were a meaningful activity metric, when it is
actually a frozen historical value from a since-superseded promotion path
(WALKBACK_RECURRING_FUNDER / NO_ATTRIBUTION_FOUND) -- not a live count, not
an all-time count, and not the metric an analyst is actually asking for
when they view a known-infrastructure wallet.

This suite proves the fix: for a REJECTED_INFRASTRUCTURE/OTHER_REJECTED
funder, Operational Behaviour now surfaces two new, explicitly-named,
live-queryable fields instead --
  attributed_launch_count: COUNT(DISTINCT mint) FROM wt_attribution_outcomes
                           WHERE terminal_entity = funder_wallet
  observed_creator_count:  COUNT(DISTINCT creator) FROM wt_walkback_queue
                           WHERE funder_wallet = funder_wallet
                              OR subprov = funder_wallet
-- and never reads/displays wt_discovered_subprovs.creator_count for this
role. Genuine VALID_SUBPROVISIONER wallets are completely unaffected.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile

import pytest

from src.ops.operational_behaviour import OperationalBehaviourService

AXIOM = "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk"
AXIOM_MINT = "2GTswvgFNGucLwrUMvttVshy28C5bmjgsuQZ4eVcpump"
AXIOM_CREATOR = "GdRSPexhxbQz5H2zFQrNN2BAZUqEjAULBigTPvQ6oDMP"

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
    mint TEXT PRIMARY KEY, creator TEXT, subprov TEXT, funder_wallet TEXT, intelligence_outcome TEXT
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
        fd1, ops_path = tempfile.mkstemp(suffix="_x26_9_1_ops.db")
        os.close(fd1)
        conn = sqlite3.connect(ops_path)
        conn.executescript(OPS_SCHEMA)
        if ops_sql:
            conn.executescript(ops_sql)
        conn.commit()
        conn.close()

        fd2, core_path = tempfile.mkstemp(suffix="_x26_9_1_core.db")
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


def _axiom_fixture_sql(with_stale_creator_count=2):
    """46 distinct mints across wt_attribution_outcomes (terminal_entity=Axiom),
    23 distinct creators across wt_walkback_queue (funder_wallet=Axiom OR
    subprov=Axiom), and a stale wt_discovered_subprovs.creator_count that
    must never be displayed."""
    lines = [f"""
    INSERT INTO wt_discovered_subprovs VALUES
      ('{AXIOM}',{with_stale_creator_count},NULL,0,'PLAIN_XFER',0,'REJECTED_INFRASTRUCTURE','KNOWN_INFRASTRUCTURE_REGISTRY_MATCH');
    """]
    # 23 distinct creators, each funding exactly 2 mints (46 mints total),
    # split across funder_wallet and subprov columns to prove the OR-union.
    for i in range(23):
        creator = f"CREATOR{i:03d}"
        mint_a = f"MINT{i:03d}A"
        mint_b = f"MINT{i:03d}B"
        if i % 2 == 0:
            lines.append(f"""
            INSERT INTO wt_walkback_queue VALUES ('{mint_a}','{creator}',NULL,'{AXIOM}','LINEAGE_GAP');
            INSERT INTO wt_walkback_queue VALUES ('{mint_b}','{creator}','{AXIOM}',NULL,'LINEAGE_GAP');
            """)
        else:
            lines.append(f"""
            INSERT INTO wt_walkback_queue VALUES ('{mint_a}','{creator}','{AXIOM}',NULL,'NO_ATTRIBUTION_FOUND');
            INSERT INTO wt_walkback_queue VALUES ('{mint_b}','{creator}',NULL,'{AXIOM}','LINEAGE_GAP');
            """)
        lines.append(f"""
        INSERT INTO wt_attribution_outcomes VALUES ('{mint_a}','KNOWN_RELAY_REACHED','{AXIOM}','AUTOMATION');
        INSERT INTO wt_attribution_outcomes VALUES ('{mint_b}','KNOWN_RELAY_REACHED','{AXIOM}','AUTOMATION');
        """)
    return "\n".join(lines)


def test_axiom_displays_46_attributed_launches_and_23_observed_creators(db_factory):
    ops_db, core_db = db_factory(_axiom_fixture_sql())
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov=AXIOM, creator=AXIOM_CREATOR)
    infra = result["infrastructure_activity"]
    assert infra is not None
    assert infra["attributed_launch_count"] == 46
    assert infra["observed_creator_count"] == 23


def test_axiom_never_displays_stale_creator_count_of_2(db_factory):
    ops_db, core_db = db_factory(_axiom_fixture_sql(with_stale_creator_count=2))
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov=AXIOM, creator=AXIOM_CREATOR)
    all_text = " ".join(result["behaviour_summary"]) + " ".join(p["label"] for p in result["infrastructure_pattern"])
    # "2" must not appear as a standalone displayed count -- the correct
    # figures (46, 23) must be shown instead.
    assert "funded 2" not in all_text
    assert "2 creator-funding" not in all_text
    assert "46" in all_text
    assert "23" in all_text


def test_behaviour_summary_renders_required_lines(db_factory):
    # X26.10 — the generic "reviewed infrastructure" phrase was replaced by
    # a subtype-specific one ("reviewed automation infrastructure" for
    # Axiom); see test_x26_10_unified_terminal_infrastructure.py for the
    # full subtype-label coverage.
    ops_db, core_db = db_factory(_axiom_fixture_sql())
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov=AXIOM, creator=AXIOM_CREATOR)
    summary = result["behaviour_summary"]
    assert "Funding source: Axiom · reviewed automation infrastructure" in summary
    assert "Launches attributed here: 46" in summary
    assert "Distinct creators observed: 23" in summary


def test_infrastructure_metrics_not_read_from_discovered_subprovs_creator_count(db_factory):
    """Prove the new metrics are independent of wt_discovered_subprovs
    .creator_count by making that column wildly wrong (999) and confirming
    the displayed figures are still the correct 46/23 from the other tables."""
    ops_db, core_db = db_factory(_axiom_fixture_sql(with_stale_creator_count=999))
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov=AXIOM, creator=AXIOM_CREATOR)
    infra = result["infrastructure_activity"]
    assert infra["attributed_launch_count"] == 46
    assert infra["observed_creator_count"] == 23
    all_text = " ".join(result["behaviour_summary"])
    assert "999" not in all_text


def test_genuine_subprovisioner_still_displays_creator_count_normally(db_factory):
    ops_sql = """
    INSERT INTO wt_discovered_subprovs VALUES
      ('SUBPROV',16,'TREASURY',1,'WSOL_WRAP_CLOSE',2,'PROVISIONAL_SUBPROV',NULL);
    """
    ops_db, core_db = db_factory(ops_sql)
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov="SUBPROV", creator="CREATOR")
    assert result["infrastructure_activity"] is None
    all_text = " ".join(result["behaviour_summary"]) + " ".join(p["label"] for p in result["infrastructure_pattern"])
    assert "Sub-provisioner has funded 16 creator" in all_text or "Sub-provisioner funded 16 creator" in all_text


@pytest.mark.parametrize("terminal_type,name", [
    ("CEX", "SomeCEX"), ("BRIDGE", "SomeBridge"), ("RELAY", "SomeRelay"),
])
def test_cex_bridge_relay_wallets_use_same_infrastructure_aggregation(db_factory, terminal_type, name):
    """A CEX/bridge/relay wallet (not literally Axiom) must use the exact
    same attributed_launch_count/observed_creator_count aggregation."""
    wallet = f"WALLET_{terminal_type}"
    ops_sql = f"""
    INSERT INTO wt_discovered_subprovs VALUES
      ('{wallet}',7,NULL,0,'PLAIN_XFER',0,'REJECTED_INFRASTRUCTURE','KNOWN_INFRASTRUCTURE_REGISTRY_MATCH');
    INSERT INTO wt_attribution_outcomes VALUES ('MINT_A','KNOWN_{terminal_type}_REACHED','{wallet}','{terminal_type}');
    INSERT INTO wt_attribution_outcomes VALUES ('MINT_B','KNOWN_{terminal_type}_REACHED','{wallet}','{terminal_type}');
    INSERT INTO wt_walkback_queue VALUES ('MINT_A','CREATOR_A','{wallet}',NULL,'LINEAGE_GAP');
    INSERT INTO wt_walkback_queue VALUES ('MINT_B','CREATOR_B',NULL,'{wallet}','LINEAGE_GAP');
    """
    ops_db, core_db = db_factory(ops_sql)
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov=wallet, creator="CREATOR_A")
    infra = result["infrastructure_activity"]
    assert infra["attributed_launch_count"] == 2
    assert infra["observed_creator_count"] == 2


def test_duplicate_walkback_rows_do_not_double_count_creators(db_factory):
    """The same creator appearing in multiple wt_walkback_queue rows (e.g.
    multiple launches) must be counted once, not once per row."""
    ops_sql = f"""
    INSERT INTO wt_discovered_subprovs VALUES
      ('{AXIOM}',2,NULL,0,'PLAIN_XFER',0,'REJECTED_INFRASTRUCTURE',NULL);
    INSERT INTO wt_walkback_queue VALUES ('MINT_A','SAME_CREATOR','{AXIOM}',NULL,'LINEAGE_GAP');
    INSERT INTO wt_walkback_queue VALUES ('MINT_B','SAME_CREATOR',NULL,'{AXIOM}','LINEAGE_GAP');
    INSERT INTO wt_walkback_queue VALUES ('MINT_C','SAME_CREATOR','{AXIOM}',NULL,'NO_ATTRIBUTION_FOUND');
    INSERT INTO wt_attribution_outcomes VALUES ('MINT_A','KNOWN_RELAY_REACHED','{AXIOM}','AUTOMATION');
    INSERT INTO wt_attribution_outcomes VALUES ('MINT_B','KNOWN_RELAY_REACHED','{AXIOM}','AUTOMATION');
    INSERT INTO wt_attribution_outcomes VALUES ('MINT_C','KNOWN_RELAY_REACHED','{AXIOM}','AUTOMATION');
    """
    ops_db, core_db = db_factory(ops_sql)
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov=AXIOM, creator="SAME_CREATOR")
    infra = result["infrastructure_activity"]
    assert infra["observed_creator_count"] == 1  # one distinct creator, not 3
    assert infra["attributed_launch_count"] == 3  # three distinct mints


def test_duplicate_attribution_rows_cannot_double_count_mints(db_factory):
    """wt_attribution_outcomes.mint is a PRIMARY KEY, so a duplicate insert
    for the same mint is structurally impossible -- verify COUNT(DISTINCT
    mint) reflects that even if a naive COUNT(*) would not distinguish a
    re-materialized row from a new one."""
    ops_sql = f"""
    INSERT INTO wt_discovered_subprovs VALUES
      ('{AXIOM}',2,NULL,0,'PLAIN_XFER',0,'REJECTED_INFRASTRUCTURE',NULL);
    INSERT INTO wt_attribution_outcomes VALUES ('MINT_A','KNOWN_RELAY_REACHED','{AXIOM}','AUTOMATION');
    INSERT INTO wt_walkback_queue VALUES ('MINT_A','CREATOR_A','{AXIOM}',NULL,'LINEAGE_GAP');
    """
    ops_db, core_db = db_factory(ops_sql)
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov=AXIOM, creator="CREATOR_A")
    assert result["infrastructure_activity"]["attributed_launch_count"] == 1


def test_no_database_mutation(db_factory):
    ops_db, core_db = db_factory(_axiom_fixture_sql())
    before_ops = hashlib.sha256(open(ops_db, "rb").read()).digest()
    before_core = hashlib.sha256(open(core_db, "rb").read()).digest()
    svc = OperationalBehaviourService(ops_db, core_db)
    svc.build(subprov=AXIOM, creator=AXIOM_CREATOR)
    after_ops = hashlib.sha256(open(ops_db, "rb").read()).digest()
    after_core = hashlib.sha256(open(core_db, "rb").read()).digest()
    assert before_ops == after_ops
    assert before_core == after_core


def test_role_resolution_unchanged(db_factory):
    """X26.9.1 must not alter funder_role resolution -- only what's
    displayed once a role is known."""
    from src.ops.operational_behaviour import _resolve_funder_role, ROLE_REJECTED_INFRASTRUCTURE
    role = _resolve_funder_role({"subprov": AXIOM, "state": "REJECTED_INFRASTRUCTURE"}, AXIOM)
    assert role == ROLE_REJECTED_INFRASTRUCTURE


def test_coverage_note_present_and_explicit(db_factory):
    ops_db, core_db = db_factory(_axiom_fixture_sql())
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov=AXIOM, creator=AXIOM_CREATOR)
    assert "coverage_note" in result["infrastructure_activity"]
    assert "not an exhaustive" in result["infrastructure_activity"]["coverage_note"]


def test_no_implementation_source_text_in_behaviour_summary(db_factory):
    ops_db, core_db = db_factory(_axiom_fixture_sql())
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov=AXIOM, creator=AXIOM_CREATOR)
    all_text = " ".join(result["behaviour_summary"])
    assert "(per wt_discovered_subprovs)" not in all_text
    assert "(wt_discovered_subprovs.creator_count)" not in all_text
    assert "(wt_provisioning_edges.observation_count)" not in all_text
