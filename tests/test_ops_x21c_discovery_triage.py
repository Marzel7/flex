"""X21C — Discovery Triage Workspace: read-only aggregation over terminal
attribution outcomes. Buckets/signals must only reflect real persisted data;
provisioning-activity sections must report dormant (not fabricated) when
wt_provisioning_edges/wt_provisioning_sessions are empty.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.ops.discovery_triage import build_investigation_queue, build_triage_summary


OPS_SCHEMA = """
CREATE TABLE wt_attribution_outcomes (
 mint TEXT PRIMARY KEY, outcome_type TEXT, stop_reason TEXT, terminal_entity TEXT,
 terminal_entity_type TEXT, confidence TEXT, evidence_json TEXT, operator_id TEXT,
 should_seed_emerging_operator INTEGER, should_retry INTEGER, completed_at INTEGER
);
CREATE TABLE wt_treasury_review (
 treasury TEXT PRIMARY KEY, status TEXT, distinct_subprovs INTEGER, distinct_creators INTEGER
);
CREATE TABLE wt_unknown_infrastructure_registry (terminal_entity TEXT PRIMARY KEY);
CREATE TABLE wt_provisioning_edges (
 edge_id TEXT PRIMARY KEY, edge_type TEXT, from_wallet TEXT, to_wallet TEXT,
 observation_count INTEGER
);
CREATE TABLE wt_provisioning_sessions (
 session_id TEXT PRIMARY KEY, source_mint TEXT, treasury TEXT, subprov TEXT, creator TEXT,
 recorded_at INTEGER
);
"""


def _outcome(mint, outcome_type, terminal_entity, creator=None, treasuries=None,
             subprovisioners=None, completed_at=1000):
    evidence = {"creator": creator, "treasuries": treasuries or [], "subprovisioners": subprovisioners or []}
    return (mint, outcome_type, "stop reason", terminal_entity, "UNKNOWN", "LOW",
            json.dumps(evidence), None, 0, 1, completed_at)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(OPS_SCHEMA)
    rows = [
        # 2 rows, no creator/treasury/subprov at all -> NO_LINEAGE, low information
        _outcome("mintA", "INSUFFICIENT_EVIDENCE", None),
        _outcome("mintB", "INSUFFICIENT_EVIDENCE", None),
        # 3 rows, same creator (repeated), no treasury -> CREATOR_IDENTIFIED, worth monitoring
        _outcome("mintC", "INSUFFICIENT_EVIDENCE", "CREATOR1", creator="CREATOR1"),
        _outcome("mintD", "LINEAGE_GAP", "CREATOR1", creator="CREATOR1"),
        _outcome("mintE", "LINEAGE_GAP", "CREATOR1", creator="CREATOR1"),
        # 1 row, creator only, unique -> CREATOR_IDENTIFIED, low information (no repeat)
        _outcome("mintF", "INSUFFICIENT_EVIDENCE", "CREATOR2", creator="CREATOR2"),
        # 1 row, subprov known, no treasury -> PARTIAL_FUNDING_TRAIL
        _outcome("mintG", "LINEAGE_GAP", "SUBPROV1", creator="CREATOR3", subprovisioners=["SUBPROV1"]),
        # 1 row, treasury resolved in evidence -> RECORDED_TREASURY_LEAD
        _outcome("mintH", "LINEAGE_GAP", "TREASURY1", creator="CREATOR4", treasuries=["TREASURY1"]),
        # 1 row, terminal_entity is a Treasury Review Lead (unconfirmed treasury candidate)
        _outcome("mintI", "LINEAGE_GAP", "REVIEWED_TREASURY", creator="CREATOR5"),
    ]
    c.executemany(
        "INSERT INTO wt_attribution_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    c.execute("INSERT INTO wt_treasury_review (treasury, status) VALUES ('REVIEWED_TREASURY', 'PENDING_REVIEW')")
    c.commit()
    yield c
    c.close()


def test_pattern_summary_matches_real_bucket_derivation(conn):
    summary = build_triage_summary(conn)
    assert summary["total_terminal_outcomes"] == 9
    buckets = {b["bucket"]: b["count"] for b in summary["pattern_summary"]}
    assert buckets["NO_LINEAGE"] == 2
    # mintI's terminal_entity ("REVIEWED_TREASURY") is not itself in evidence.treasuries[],
    # so it buckets as CREATOR_IDENTIFIED (has_creator=True, has_treasury=False) even
    # though it also carries a Treasury Review Lead signal in the investigation queue.
    assert buckets["CREATOR_IDENTIFIED"] == 5  # mintC, mintD, mintE, mintF, mintI
    assert buckets["PARTIAL_FUNDING_TRAIL"] == 1  # mintG
    assert buckets["RECORDED_TREASURY_LEAD"] == 1  # mintH only (evidence.treasuries populated)


def test_repeated_creator_counted_correctly(conn):
    summary = build_triage_summary(conn)
    assert summary["repeated_creator_entities"] == 1  # CREATOR1
    assert summary["repeated_creator_rows"] == 3  # mintC, mintD, mintE


def test_treasury_review_lead_matched_via_terminal_entity_not_evidence_treasuries(conn):
    """mintI's terminal_entity IS the reviewed treasury even though evidence.treasuries[]
    is empty for this row — this is the real production shape confirmed against live data."""
    summary = build_triage_summary(conn)
    assert summary["treasury_review_lead_rows"] == 1


def test_provisioning_activity_reports_dormant_when_empty(conn):
    summary = build_triage_summary(conn)
    assert summary["provisioning_activity"]["active"] is False
    assert summary["provisioning_activity"]["edges_captured"] == 0
    assert summary["provisioning_activity"]["sessions_captured"] == 0


def test_provisioning_activity_becomes_active_once_populated(conn):
    conn.execute(
        "INSERT INTO wt_provisioning_sessions VALUES ('s1','mintH','TREASURY1',NULL,'CREATOR4',2000)"
    )
    conn.commit()
    summary = build_triage_summary(conn)
    assert summary["provisioning_activity"]["active"] is True
    assert summary["provisioning_activity"]["sessions_captured"] == 1


def test_worth_monitoring_excludes_pure_no_lineage_and_singleton_creators(conn):
    summary = build_triage_summary(conn)
    # worth monitoring: mintC/D/E (repeated creator), mintH (treasury), mintI (review lead) = 5
    # low information: mintA, mintB (no lineage), mintF (singleton creator), mintG (partial, unique subprov) = 4
    assert summary["worth_monitoring"] == 5
    assert summary["low_information"] == 4


def test_investigation_queue_groups_by_creator_not_by_token(conn):
    queue = build_investigation_queue(conn, limit=50)
    creator1_entry = next(e for e in queue["entries"] if e["entity"] == "CREATOR1")
    assert creator1_entry["launch_count"] == 3
    assert "Repeated creator (3 launches)" in creator1_entry["signals"]


def test_investigation_queue_ranks_treasury_review_lead_highest(conn):
    queue = build_investigation_queue(conn, limit=50)
    top = queue["entries"][0]
    assert top["group_type"] == "treasury"
    assert "Treasury Review Lead" in top["signals"]


def test_emerging_operator_signal_only_fires_on_real_terminal_entity_match(conn):
    """Regression test for a real bug: the creator branch must check the GROUP's
    observed terminal_entity values against unknown_infra, never the creator
    address itself (which would produce false positives when a creator address
    coincidentally matches an unrelated registry entry)."""
    conn.execute("INSERT INTO wt_unknown_infrastructure_registry VALUES ('CREATOR2')")
    conn.commit()
    # CREATOR2's own row has terminal_entity='CREATOR2' too in this fixture (mintF),
    # so this SHOULD genuinely fire — confirms the positive case still works.
    queue = build_investigation_queue(conn, limit=50)
    creator2_entry = next(e for e in queue["entries"] if e["entity"] == "CREATOR2")
    assert "Emerging Operator candidate" in creator2_entry["signals"]

    # But CREATOR1's rows have terminal_entity='CREATOR1', which is NOT registered —
    # must not fire for CREATOR1 even though CREATOR2 is registered elsewhere.
    creator1_entry = next(e for e in queue["entries"] if e["entity"] == "CREATOR1")
    assert "Emerging Operator candidate" not in creator1_entry["signals"]


def test_filter_by_bucket_key(conn):
    queue = build_investigation_queue(conn, limit=50, filter_key="NO_LINEAGE")
    assert all(e["bucket"] == "NO_LINEAGE" for e in queue["entries"])
    assert len(queue["entries"]) > 0


def test_only_triage_outcome_types_are_considered(conn):
    """A CANONICAL_OPERATOR_REACHED or other confirmed outcome must never appear
    in this workspace — it belongs to a different analyst view entirely."""
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('mintZ','CANONICAL_OPERATOR_REACHED','done','OP1','CANONICAL_OPERATOR','HIGH','{}',NULL,0,0,999)"
    )
    conn.commit()
    summary = build_triage_summary(conn)
    assert summary["total_terminal_outcomes"] == 9  # unchanged
