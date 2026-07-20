"""X26.8 — Reject-State-Aware Operational Behaviour.

X26.6's audit found (and X26.8 confirms live, using the real Axiom launch
mint 2GTswvgFNGucLwrUMvttVshy28C5bmjgsuQZ4eVcpump) that Operational
Behaviour narrated a REJECTED_INFRASTRUCTURE wt_discovered_subprovs row
(Axiom) as a valid sub-provisioner: "Sub-provisioner funded creator via
PLAIN_XFER", "Sub-provisioner has funded 2 creators", "First time this
exact sub-provisioner->creator funding path was observed" -- despite
Attribution Outcome and the Infrastructure Boundary correctly concluding
"Known infrastructure boundary: Axiom" on the very same page.

This suite proves the fix: OperationalBehaviourService now resolves the
funder's CURRENT canonical role (VALID_SUBPROVISIONER / REJECTED_
INFRASTRUCTURE / OTHER_REJECTED / UNRESOLVED_FUNDER) from
wt_discovered_subprovs.state (with the reviewed infrastructure registry as
an additional, registry-wins check) BEFORE building any wording, and every
section (Behaviour Summary, Infrastructure Pattern, Operational
Consistency, Missing Evidence) uses role-neutral language for anything
that isn't a genuine, non-rejected sub-provisioner -- while preserving
every underlying historical count, mechanism, and observation unchanged.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from src.ops.operational_behaviour import (
    OperationalBehaviourService,
    ROLE_OTHER_REJECTED,
    ROLE_REJECTED_INFRASTRUCTURE,
    ROLE_UNRESOLVED_FUNDER,
    ROLE_VALID_SUBPROVISIONER,
    _resolve_funder_role,
    assert_no_infrastructure_subprovisioner_conflict,
)

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
"""

CORE_SCHEMA = """
CREATE TABLE wt_known_operator_hubs (hub_wallet TEXT PRIMARY KEY, operator_identity TEXT);
CREATE TABLE wt_provisioning_hubs (hub_address TEXT PRIMARY KEY, status TEXT);
"""


@pytest.fixture()
def db_factory():
    paths = []

    def _make(ops_sql: str = "", core_sql: str = "") -> tuple[str, str]:
        fd1, ops_path = tempfile.mkstemp(suffix="_x26_8_ops.db")
        os.close(fd1)
        conn = sqlite3.connect(ops_path)
        conn.executescript(OPS_SCHEMA)
        if ops_sql:
            conn.executescript(ops_sql)
        conn.commit()
        conn.close()

        fd2, core_path = tempfile.mkstemp(suffix="_x26_8_core.db")
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


def _rejected_infra_fixture_sql():
    return f"""
    INSERT INTO wt_provisioning_edges VALUES
      ('edge1','SUBPROV_TO_CREATOR','{AXIOM}','{AXIOM_CREATOR}',
       1784161726,1784161726,1,'PLAIN_XFER',0.310183,'SIG1',1784161304,'{AXIOM_MINT}','WALKBACK');
    INSERT INTO wt_provisioning_sessions VALUES
      ('sess1','{AXIOM_MINT}',NULL,'{AXIOM}','{AXIOM_CREATOR}',
       NULL,1784161304,NULL,NULL,NULL,NULL,NULL,'PLAIN_XFER',NULL,0.310183,1784161726);
    INSERT INTO wt_discovered_subprovs VALUES
      ('{AXIOM}',2,NULL,0,'PLAIN_XFER',0,'REJECTED_INFRASTRUCTURE','KNOWN_INFRASTRUCTURE_REGISTRY_MATCH');
    """


# ---------------------------------------------------------------------------
# Role resolution
# ---------------------------------------------------------------------------

def test_role_resolution_rejected_infrastructure_state():
    role = _resolve_funder_role({"subprov": "X", "state": "REJECTED_INFRASTRUCTURE"}, "X_not_in_registry_placeholder")
    assert role == ROLE_REJECTED_INFRASTRUCTURE


def test_role_resolution_other_rejected_state():
    role = _resolve_funder_role({"subprov": "X", "state": "REJECTED_NON_PROVISIONING"}, "X_not_in_registry_placeholder")
    assert role == ROLE_OTHER_REJECTED


def test_role_resolution_valid_subprov():
    role = _resolve_funder_role({"subprov": "X", "state": "PROVISIONAL_SUBPROV"}, "X_not_in_registry_placeholder")
    assert role == ROLE_VALID_SUBPROVISIONER


def test_role_resolution_unresolved():
    assert _resolve_funder_role(None, None) == ROLE_UNRESOLVED_FUNDER


def test_role_resolution_registry_wins_over_stale_non_rejected_state():
    """Even if wt_discovered_subprovs.state hasn't (yet) been marked REJECTED,
    a wallet in the reviewed infrastructure registry must never resolve to
    VALID_SUBPROVISIONER."""
    role = _resolve_funder_role({"subprov": AXIOM, "state": "PROVISION_CANDIDATE"}, AXIOM)
    assert role == ROLE_REJECTED_INFRASTRUCTURE


# ---------------------------------------------------------------------------
# Axiom live-fixture reproduction: the exact reported defect, now fixed
# ---------------------------------------------------------------------------

def test_axiom_never_produces_subprovisioner_funded_creator_wording(db_factory):
    ops_db, core_db = db_factory(_rejected_infra_fixture_sql())
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(source_mint=AXIOM_MINT, subprov=AXIOM, creator=AXIOM_CREATOR)
    all_text = " ".join(result["behaviour_summary"]) + " ".join(p["label"] for p in result["infrastructure_pattern"])
    assert "Sub-provisioner funded creator" not in all_text


def test_axiom_never_produces_subprovisioner_has_funded_n_creators(db_factory):
    ops_db, core_db = db_factory(_rejected_infra_fixture_sql())
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(source_mint=AXIOM_MINT, subprov=AXIOM, creator=AXIOM_CREATOR)
    all_text = " ".join(result["behaviour_summary"])
    assert "Sub-provisioner has funded" not in all_text


def test_axiom_never_produces_subprovisioner_funded_n_creators(db_factory):
    ops_db, core_db = db_factory(_rejected_infra_fixture_sql())
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(source_mint=AXIOM_MINT, subprov=AXIOM, creator=AXIOM_CREATOR)
    all_text = " ".join(p["label"] for p in result["infrastructure_pattern"])
    assert "Sub-provisioner funded 2 creator" not in all_text


def test_axiom_never_produces_subprov_to_creator_path_wording(db_factory):
    ops_db, core_db = db_factory(_rejected_infra_fixture_sql())
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(source_mint=AXIOM_MINT, subprov=AXIOM, creator=AXIOM_CREATOR)
    all_text = " ".join(p["label"] for p in result["infrastructure_pattern"])
    assert "sub-provisioner→creator" not in all_text
    assert "sub-provisioner→creator" not in all_text


def test_axiom_described_as_reviewed_infrastructure(db_factory):
    ops_db, core_db = db_factory(_rejected_infra_fixture_sql())
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(source_mint=AXIOM_MINT, subprov=AXIOM, creator=AXIOM_CREATOR)
    all_text = " ".join(result["behaviour_summary"]) + " ".join(p["label"] for p in result["infrastructure_pattern"])
    assert "Axiom" in all_text
    assert "infrastructure" in all_text.lower()


def test_axiom_funding_mechanism_still_visible(db_factory):
    ops_db, core_db = db_factory(_rejected_infra_fixture_sql())
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(source_mint=AXIOM_MINT, subprov=AXIOM, creator=AXIOM_CREATOR)
    all_text = " ".join(result["behaviour_summary"])
    assert "PLAIN_XFER" in all_text


def test_axiom_creator_funding_count_still_visible_with_neutral_wording(db_factory):
    # X26.9.1 — wt_discovered_subprovs.creator_count is no longer shown at all
    # for a rejected infrastructure funder (X26.9's audit found it to be a
    # frozen historical value, not a live count). This fixture has no
    # wt_attribution_outcomes/wt_walkback_queue creator rows, so
    # infrastructure_activity is None and no count is expected here — the
    # live-queryable replacement metrics are covered by their own dedicated
    # tests in test_x26_9_1_infrastructure_activity_metrics.py.
    ops_db, core_db = db_factory(_rejected_infra_fixture_sql())
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(source_mint=AXIOM_MINT, subprov=AXIOM, creator=AXIOM_CREATOR)
    assert result["infrastructure_activity"] is None
    all_text = " ".join(result["behaviour_summary"]) + " ".join(p["label"] for p in result["infrastructure_pattern"])
    assert "wt_discovered_subprovs" not in all_text


def test_axiom_historical_funding_edge_remains_visible(db_factory):
    ops_db, core_db = db_factory(_rejected_infra_fixture_sql())
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(source_mint=AXIOM_MINT, subprov=AXIOM, creator=AXIOM_CREATOR)
    all_text = " ".join(p["label"] for p in result["infrastructure_pattern"])
    assert "observation of this exact funding relationship" in all_text or "observed" in all_text.lower()


# ---------------------------------------------------------------------------
# Genuine sub-provisioners retain existing wording
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", ["PROVISIONAL_SUBPROV", "PROVISION_CANDIDATE"])
def test_genuine_subprov_states_retain_role_specific_wording(db_factory, state):
    ops_sql = f"""
    INSERT INTO wt_provisioning_edges VALUES
      ('edge1','SUBPROV_TO_CREATOR','SUBPROV','CREATOR',
       100,100,3,'WSOL_WRAP_CLOSE',1.0,'SIG1',95,'MINT','WALKBACK');
    INSERT INTO wt_discovered_subprovs VALUES
      ('SUBPROV',3,'TREASURY',1,'WSOL_WRAP_CLOSE',2,'{state}',NULL);
    """
    ops_db, core_db = db_factory(ops_sql)
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov="SUBPROV", creator="CREATOR")
    all_text = " ".join(result["behaviour_summary"]) + " ".join(p["label"] for p in result["infrastructure_pattern"])
    assert "Sub-provisioner funded creator via WSOL_WRAP_CLOSE" in all_text
    assert "Sub-provisioner has funded 3 creator" in all_text or "Sub-provisioner funded 3 creator" in all_text


def test_rejected_non_provisioning_also_receives_neutral_wording(db_factory):
    ops_sql = """
    INSERT INTO wt_provisioning_edges VALUES
      ('edge1','SUBPROV_TO_CREATOR','FUNDER','CREATOR',
       100,100,1,'PLAIN_XFER',1.0,'SIG1',95,'MINT','WALKBACK');
    INSERT INTO wt_discovered_subprovs VALUES
      ('FUNDER',2,NULL,0,'PLAIN_XFER',0,'REJECTED_NON_PROVISIONING','buy-swarm fan-out');
    """
    ops_db, core_db = db_factory(ops_sql)
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov="FUNDER", creator="CREATOR")
    all_text = " ".join(result["behaviour_summary"]) + " ".join(p["label"] for p in result["infrastructure_pattern"])
    assert "Sub-provisioner funded creator" not in all_text
    assert "excluded from sub-provisioner classification" in all_text


# ---------------------------------------------------------------------------
# Operational Consistency: Not applicable for provisioning-specific rows
# ---------------------------------------------------------------------------

def test_consistency_provisioning_specific_rows_not_applicable_for_rejected(db_factory):
    ops_db, core_db = db_factory(_rejected_infra_fixture_sql())
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(source_mint=AXIOM_MINT, subprov=AXIOM, creator=AXIOM_CREATOR)
    by_signal = {row["signal"]: row["status"] for row in result["operational_consistency"]}
    assert by_signal["Repeated treasury"] == "Not applicable"
    assert by_signal["Full provisioning sequence recorded"] == "Not applicable"


def test_consistency_provisioning_specific_rows_normal_for_valid_subprov(db_factory):
    ops_sql = """
    INSERT INTO wt_provisioning_edges VALUES
      ('edge1','SUBPROV_TO_CREATOR','SUBPROV','CREATOR',
       100,100,1,'WSOL_WRAP_CLOSE',1.0,'SIG1',95,'MINT','WALKBACK');
    INSERT INTO wt_discovered_subprovs VALUES
      ('SUBPROV',3,'TREASURY',1,'WSOL_WRAP_CLOSE',2,'PROVISIONAL_SUBPROV',NULL);
    """
    ops_db, core_db = db_factory(ops_sql)
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov="SUBPROV", creator="CREATOR")
    by_signal = {row["signal"]: row["status"] for row in result["operational_consistency"]}
    assert by_signal["Repeated treasury"] != "Not applicable"
    assert by_signal["Full provisioning sequence recorded"] != "Not applicable"


# ---------------------------------------------------------------------------
# Missing Evidence: not-applicable sub-provisioner evidence is never
# reported as MISSING for a rejected funder
# ---------------------------------------------------------------------------

def test_missing_evidence_does_not_report_not_applicable_as_missing(db_factory):
    ops_db, core_db = db_factory(_rejected_infra_fixture_sql())
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(source_mint=AXIOM_MINT, subprov=AXIOM, creator=AXIOM_CREATOR)
    missing = result["missing_evidence"]
    assert "Multiple launches from this sub-provisioner" not in missing
    assert "Repeated treasury (multiple creators funded by the same treasury)" not in missing
    assert "Provisioning hub reuse" not in missing
    assert any("not applicable" in m.lower() for m in missing)


def test_missing_evidence_normal_for_valid_subprov(db_factory):
    ops_sql = """
    INSERT INTO wt_discovered_subprovs VALUES
      ('SUBPROV',1,NULL,0,'WSOL_WRAP_CLOSE',1,'PROVISIONAL_SUBPROV',NULL);
    """
    ops_db, core_db = db_factory(ops_sql)
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov="SUBPROV", creator="CREATOR")
    assert "Multiple launches from this sub-provisioner" in result["missing_evidence"]


# ---------------------------------------------------------------------------
# Response-level invariant (Phase 8)
# ---------------------------------------------------------------------------

def test_known_infrastructure_boundary_and_valid_subprov_never_coexist():
    attribution_outcome = {"terminal_entity_type": "AUTOMATION", "terminal_entity": AXIOM}
    behaviour_ok = {
        "entities": {"subprov": AXIOM},
        "behaviour_summary": ["Funding source: Axiom · reviewed infrastructure"],
        "infrastructure_pattern": [{"label": "Infrastructure wallet (Axiom) funded 2 observed creators"}],
    }
    assert_no_infrastructure_subprovisioner_conflict(attribution_outcome, behaviour_ok)  # must not raise

    behaviour_bad = {
        "entities": {"subprov": AXIOM},
        "behaviour_summary": ["Sub-provisioner funded creator via PLAIN_XFER"],
        "infrastructure_pattern": [],
    }
    with pytest.raises(AssertionError):
        assert_no_infrastructure_subprovisioner_conflict(attribution_outcome, behaviour_bad)


def test_invariant_no_conflict_for_unrelated_wallet():
    attribution_outcome = {"terminal_entity_type": "AUTOMATION", "terminal_entity": AXIOM}
    behaviour = {
        "entities": {"subprov": "SOME_OTHER_WALLET"},
        "behaviour_summary": ["Sub-provisioner funded creator via WSOL_WRAP_CLOSE"],
        "infrastructure_pattern": [],
    }
    assert_no_infrastructure_subprovisioner_conflict(attribution_outcome, behaviour)  # must not raise


# ---------------------------------------------------------------------------
# No database mutation
# ---------------------------------------------------------------------------

def test_no_database_mutation(db_factory):
    import hashlib
    ops_db, core_db = db_factory(_rejected_infra_fixture_sql())
    before_ops = hashlib.sha256(open(ops_db, "rb").read()).digest()
    before_core = hashlib.sha256(open(core_db, "rb").read()).digest()
    svc = OperationalBehaviourService(ops_db, core_db)
    svc.build(source_mint=AXIOM_MINT, subprov=AXIOM, creator=AXIOM_CREATOR)
    after_ops = hashlib.sha256(open(ops_db, "rb").read()).digest()
    after_core = hashlib.sha256(open(core_db, "rb").read()).digest()
    assert before_ops == after_ops
    assert before_core == after_core
