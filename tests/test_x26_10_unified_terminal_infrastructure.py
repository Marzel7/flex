"""X26.10 — Unify Terminal Infrastructure Behaviour.

X26.9.1 correctly gave Operational Behaviour a live-queryable evidence
model for reviewed infrastructure (Axiom-class automation wallets), but
that model only fired when `subprov` was already resolved by the time
OperationalBehaviourService.build() was called. This sprint found two
compounding gaps that meant a large class of reviewed terminal
infrastructure (CEX, bridge, custody wallets in particular) rendered an
EMPTY Operational Behaviour section despite Attribution Outcome correctly
identifying them as a terminal boundary:

  1. src/discovery/service.py's `_entity()` only ever derives its local
     `subprov` variable from wt_watchtower_launches.subprov_wallet /
     wt_token_lifecycle.subprov / watchtower_token_attribution
     .matched_subprov / wt_walkback_queue.subprov -- never from
     wt_walkback_queue.funder_wallet or .treasury, which is where many
     CEX/bridge/relay boundaries are actually recorded. So `subprov` was
     None and OperationalBehaviourService never received the address at
     all.
  2. Even when the caller DID pass the address as `subprov`,
     _build_behaviour_summary() and _build_infrastructure_pattern() each
     re-derived their own LOCAL `subprov` variable from
     `subprov_facts.get("subprov")` -- which is None whenever no
     wt_discovered_subprovs row exists for that wallet (true for many
     registry-only CEX/bridge/custody wallets that were never themselves a
     subprov candidate). This silently discarded the real address even
     when it had been correctly passed in.

This suite proves both gaps are closed and that every reviewed terminal
infrastructure class (CEX, automation, bridge, relay, custody) now
produces a structurally identical Operational Behaviour model, differing
only in the subtype phrase ("reviewed exchange" / "reviewed automation
infrastructure" / "reviewed bridge" / "reviewed relay" / "reviewed custody
infrastructure").
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile

import pytest

from src.ops.operational_behaviour import (
    OperationalBehaviourService,
    ROLE_REJECTED_INFRASTRUCTURE,
    ROLE_UNRESOLVED_FUNDER,
    ROLE_VALID_SUBPROVISIONER,
    _resolve_funder_role,
)

# Real registry addresses (src/utils/infra_mapping.py) used across this
# suite, one per reviewed terminal class.
AXIOM = "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk"           # category=automation
CEX_WALLET = "u6PJ8DtQuPFnfmwHbGFULQ4u4EgjDiyYKjVEsynXq2w"       # CEX_ACCOUNTS entry
BRIDGE_WALLET = "2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS"   # category=bridge
RELAY_WALLET = "F7p3dFrjRTbtRp8FRF6qHLomXbKRBzpvBLjtQcfcgmNe"    # category=relay
CUSTODY_WALLET = "2Hgx1GjKRuH9H21Fzz7uGiqh1Fcz3wMP5PmgpDbyDDYp"  # category=custody

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
        fd1, ops_path = tempfile.mkstemp(suffix="_x26_10_ops.db")
        os.close(fd1)
        conn = sqlite3.connect(ops_path)
        conn.executescript(OPS_SCHEMA)
        if ops_sql:
            conn.executescript(ops_sql)
        conn.commit()
        conn.close()

        fd2, core_path = tempfile.mkstemp(suffix="_x26_10_core.db")
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


def _reviewed_fixture_sql(wallet, n=2):
    """n mints attributed to `wallet` as terminal_entity, split across
    funder_wallet/subprov/treasury columns of wt_walkback_queue to prove
    the fix works regardless of which column actually holds the address."""
    lines = []
    for i in range(n):
        mint = f"MINT_{wallet[:6]}_{i}"
        creator = f"CREATOR_{wallet[:6]}_{i}"
        col = ["funder_wallet", "subprov", "treasury"][i % 3]
        cols = {"funder_wallet": "NULL", "subprov": "NULL", "treasury": "NULL"}
        cols[col] = f"'{wallet}'"
        lines.append(f"""
        INSERT INTO wt_walkback_queue VALUES
          ('{mint}','{creator}',{cols['subprov']},{cols['treasury']},{cols['funder_wallet']},'LINEAGE_GAP');
        INSERT INTO wt_attribution_outcomes VALUES
          ('{mint}','KNOWN_RELAY_REACHED','{wallet}','AUTOMATION');
        """)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Discovery-level fix: terminal_infrastructure fills the gap when subprov
# was never resolved through the normal chain (the funder_wallet/treasury
# column case).
# ---------------------------------------------------------------------------

def test_terminal_infrastructure_param_fills_gap_when_subprov_unresolved(db_factory):
    ops_db, core_db = db_factory(_reviewed_fixture_sql(CEX_WALLET, n=3))
    svc = OperationalBehaviourService(ops_db, core_db)
    # subprov=None (as _entity() would pass when the address is only in
    # funder_wallet/treasury) -- terminal_infrastructure fills the gap.
    result = svc.build(subprov=None, terminal_infrastructure=CEX_WALLET)
    assert result is not None
    assert result["infrastructure_activity"] is not None
    assert result["infrastructure_activity"]["attributed_launch_count"] == 3
    assert any("reviewed exchange" in s for s in result["behaviour_summary"])


def test_terminal_infrastructure_never_overrides_real_subprov(db_factory):
    """If subprov IS already resolved (a genuine sub-provisioner), passing
    a DIFFERENT terminal_infrastructure address must never override it."""
    ops_sql = """
    INSERT INTO wt_discovered_subprovs VALUES
      ('SUBPROV',5,'TREASURY',1,'WSOL_WRAP_CLOSE',3,'PROVISIONAL_SUBPROV',NULL);
    """
    ops_db, core_db = db_factory(ops_sql)
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov="SUBPROV", terminal_infrastructure=AXIOM)
    assert result["entities"]["subprov"] == "SUBPROV"
    assert any("Sub-provisioner" in s for s in result["behaviour_summary"])


# ---------------------------------------------------------------------------
# Cross-type consistency: every reviewed terminal class produces the same
# STRUCTURE, differing only in the subtype phrase.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wallet,subtype_phrase", [
    (AXIOM, "reviewed automation infrastructure"),
    (CEX_WALLET, "reviewed exchange"),
    (BRIDGE_WALLET, "reviewed bridge"),
    (RELAY_WALLET, "reviewed relay"),
    (CUSTODY_WALLET, "reviewed custody infrastructure"),
])
def test_cross_type_structural_consistency(db_factory, wallet, subtype_phrase):
    ops_db, core_db = db_factory(_reviewed_fixture_sql(wallet, n=2))
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(terminal_infrastructure=wallet)

    # Structure: same three-line evidence shape for every reviewed class.
    summary = result["behaviour_summary"]
    assert any(s.startswith("Funding source:") for s in summary)
    assert any(s.startswith("Launches attributed here:") for s in summary)
    assert any(s.startswith("Distinct creators observed:") for s in summary)
    assert any(subtype_phrase in s for s in summary)

    # Never leaks sub-provisioner language.
    assert not any("Sub-provisioner" in s for s in summary)
    assert not any("Sub-provisioner" in p["label"] for p in result["infrastructure_pattern"])

    # Consistency signals: same Not-applicable treatment for every class.
    by_signal = {row["signal"]: row["status"] for row in result["operational_consistency"]}
    assert by_signal["Repeated treasury"] == "Not applicable"
    assert by_signal["Full provisioning sequence recorded"] == "Not applicable"

    # Missing evidence: same not-applicable framing, never "missing".
    assert any("not applicable" in m.lower() for m in result["missing_evidence"])
    assert "Multiple launches from this sub-provisioner" not in result["missing_evidence"]

    # Metrics identical in shape (both present, both integers, same source columns).
    infra = result["infrastructure_activity"]
    assert infra["attributed_launch_count"] == 2
    assert infra["observed_creator_count"] == 2


def test_infrastructure_metrics_identical_shape_across_all_reviewed_types(db_factory):
    """All five reviewed classes must expose the exact same
    infrastructure_activity key set."""
    for wallet in (AXIOM, CEX_WALLET, BRIDGE_WALLET, RELAY_WALLET, CUSTODY_WALLET):
        ops_db, core_db = db_factory(_reviewed_fixture_sql(wallet, n=1))
        svc = OperationalBehaviourService(ops_db, core_db)
        result = svc.build(terminal_infrastructure=wallet)
        assert set(result["infrastructure_activity"].keys()) == {
            "attributed_launch_count", "observed_creator_count", "coverage_note",
        }


# ---------------------------------------------------------------------------
# creator_count never leaks back in for any reviewed class, even when a
# stale wt_discovered_subprovs row exists with a wildly wrong count.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wallet", [AXIOM, CEX_WALLET, BRIDGE_WALLET])
def test_creator_count_never_leaks_back_in(db_factory, wallet):
    ops_sql = _reviewed_fixture_sql(wallet, n=2) + f"""
    INSERT INTO wt_discovered_subprovs VALUES
      ('{wallet}',9999,NULL,0,'PLAIN_XFER',0,'REJECTED_INFRASTRUCTURE',NULL);
    """
    ops_db, core_db = db_factory(ops_sql)
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov=wallet)
    all_text = " ".join(result["behaviour_summary"]) + " ".join(p["label"] for p in result["infrastructure_pattern"])
    assert "9999" not in all_text
    assert result["infrastructure_activity"]["attributed_launch_count"] == 2


# ---------------------------------------------------------------------------
# Genuine sub-provisioner unaffected
# ---------------------------------------------------------------------------

def test_genuine_subprovisioner_unaffected(db_factory):
    ops_sql = """
    INSERT INTO wt_discovered_subprovs VALUES
      ('SUBPROV',7,'TREASURY',1,'WSOL_WRAP_CLOSE',3,'PROVISIONAL_SUBPROV',NULL);
    """
    ops_db, core_db = db_factory(ops_sql)
    svc = OperationalBehaviourService(ops_db, core_db)
    result = svc.build(subprov="SUBPROV")
    assert result["infrastructure_activity"] is None
    assert any("Sub-provisioner has funded 7 creator" in s for s in result["behaviour_summary"])


# ---------------------------------------------------------------------------
# Unknown infrastructure (not in any registry) / ordinary unresolved funder
# ---------------------------------------------------------------------------

def test_unresolved_funder_not_treated_as_infrastructure(db_factory):
    ops_db, core_db = db_factory()
    svc = OperationalBehaviourService(ops_db, core_db)
    role = _resolve_funder_role(None, "SOME_RANDOM_WALLET_NOT_IN_ANY_REGISTRY")
    assert role == ROLE_UNRESOLVED_FUNDER
    result = svc.build(subprov="SOME_RANDOM_WALLET_NOT_IN_ANY_REGISTRY")
    assert result["infrastructure_activity"] is None


def test_role_resolution_still_correct_for_registry_wallets():
    assert _resolve_funder_role(None, AXIOM) == ROLE_REJECTED_INFRASTRUCTURE
    assert _resolve_funder_role(None, CEX_WALLET) == ROLE_REJECTED_INFRASTRUCTURE
    assert _resolve_funder_role(None, BRIDGE_WALLET) == ROLE_REJECTED_INFRASTRUCTURE
    assert _resolve_funder_role({"state": "PROVISIONAL_SUBPROV"}, "SOME_GENUINE_SUBPROV") == ROLE_VALID_SUBPROVISIONER


# ---------------------------------------------------------------------------
# Attribution / operation identity / walkback / schema untouched
# ---------------------------------------------------------------------------

def test_discovery_attribution_outcome_field_unmodified(db_factory):
    """Confirm this sprint's fix only READS attribution_outcome.terminal_entity
    -- it must never mutate the dict or its fields."""
    from src.discovery.service import DiscoveryService
    import tempfile as _tf

    fd, db_path = _tf.mkstemp(suffix="_x26_10_e2e.db")
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
    CREATE TABLE wt_discovered_subprovs (subprov TEXT PRIMARY KEY, creator_count INTEGER, treasury TEXT, treasury_known INTEGER,
      immediate_funder TEXT, confidence REAL, state TEXT, wrap_close_count INTEGER, funding_mechanism TEXT, rejected_reason TEXT,
      first_seen INTEGER, last_seen INTEGER);
    CREATE TABLE wt_confirmed_treasuries (treasury TEXT PRIMARY KEY, confidence TEXT, method TEXT, out_sol REAL, recipients INTEGER, confirmed_at INTEGER);
    CREATE TABLE wt_treasury_review (treasury TEXT PRIMARY KEY, status TEXT, confidence TEXT, detected_at INTEGER, reviewed_at INTEGER, detected_via TEXT, recipients INTEGER, out_sol REAL);
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
    """)
    conn.execute("""
        INSERT INTO wt_walkback_queue VALUES
          ('MINT','CREATOR',NULL,NULL,?,'complete',150,'LINEAGE_GAP')
    """, (CEX_WALLET,))
    conn.execute("""
        INSERT INTO wt_attribution_outcomes VALUES
          ('MINT','KNOWN_CEX_REACHED','Attribution boundary reached.',?,'CEX','HIGH','{}',NULL,0,0,150,NULL,150)
    """, (CEX_WALLET,))
    conn.commit()

    before = hashlib.sha256(open(db_path, "rb").read()).digest()
    svc = DiscoveryService(db_path, db_path)
    data = svc.resolve("MINT", "token")
    after = hashlib.sha256(open(db_path, "rb").read()).digest()

    assert data["attribution_outcome"]["outcome_type"] == "KNOWN_CEX_REACHED"
    assert data["attribution_outcome"]["terminal_entity"] == CEX_WALLET
    assert data["operational_behaviour"]["infrastructure_activity"] is not None
    assert data["operational_behaviour"]["infrastructure_activity"]["attributed_launch_count"] == 1
    assert data["canonical_identity"] is None
    assert data["operation_identity"] is None
    assert before == after
    os.unlink(db_path)


def test_no_database_mutation(db_factory):
    ops_db, core_db = db_factory(_reviewed_fixture_sql(CEX_WALLET, n=2))
    before_ops = hashlib.sha256(open(ops_db, "rb").read()).digest()
    before_core = hashlib.sha256(open(core_db, "rb").read()).digest()
    svc = OperationalBehaviourService(ops_db, core_db)
    svc.build(terminal_infrastructure=CEX_WALLET)
    after_ops = hashlib.sha256(open(ops_db, "rb").read()).digest()
    after_core = hashlib.sha256(open(core_db, "rb").read()).digest()
    assert before_ops == after_ops
    assert before_core == after_core
