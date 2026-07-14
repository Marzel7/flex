"""X19.7 canonical attribution conclusions and strict X20 routing."""
from __future__ import annotations

import sqlite3

import pytest

from src.ops.attribution_outcome import (
    AMBIGUOUS_BRANCH,
    CANONICAL_OPERATOR_REACHED,
    INSUFFICIENT_EVIDENCE,
    KNOWN_BRIDGE_REACHED,
    KNOWN_CEX_REACHED,
    KNOWN_MULTI_TOKEN_CREATOR,
    KNOWN_RELAY_REACHED,
    LINEAGE_GAP,
    MAX_DEPTH,
    UNKNOWN_INFRASTRUCTURE,
    derive_outcome,
    ensure_schema,
    evaluate_launcher_profile,
    emerging_operator_seeds,
    persist_outcome,
)
from src.discovery.service import DiscoveryService


OPS_SCHEMA = """
CREATE TABLE wt_walkback_queue (
 mint TEXT PRIMARY KEY, creator TEXT, subprov TEXT, treasury TEXT,
 funder_wallet TEXT, walkback_class TEXT, attribution_source TEXT,
 intelligence_outcome TEXT, status TEXT, attempts INTEGER, rpc_used INTEGER, last_error TEXT,
 enqueued_at INTEGER, completed_at INTEGER, updated_at INTEGER
);
CREATE TABLE wt_watchtower_launches (
 mint TEXT PRIMARY KEY, creator_wallet TEXT, subprov_wallet TEXT, treasury_wallet TEXT
);
CREATE TABLE watchtower_token_attribution (
 mint TEXT PRIMARY KEY, creator TEXT, matched_subprov TEXT, matched_treasury TEXT
);
CREATE TABLE operators (
 operator_id TEXT PRIMARY KEY, display_name TEXT, status TEXT
);
CREATE TABLE operator_entities (operator_id TEXT, entity_address TEXT);
CREATE TABLE wt_treasury_review (
 treasury TEXT PRIMARY KEY, has_walkback_evidence INTEGER, detected_via TEXT,
 distinct_subprovs INTEGER
);
CREATE TABLE wt_discovered_subprovs (
 subprov TEXT PRIMARY KEY, creator_count INTEGER, wrap_close_count INTEGER
);
CREATE TABLE wt_wrap_close_candidates (creator TEXT, funded_at INTEGER, detected_at INTEGER);
CREATE TABLE wt_candidate_websocket_watches (candidate_wallet TEXT, wrap_close_time INTEGER, detected_at INTEGER);
CREATE TABLE wt_creator_birth_launch (creator TEXT, funded_at INTEGER, measured_at INTEGER);
"""

CORE_SCHEMA = """
CREATE TABLE token_analysis (
 mint TEXT PRIMARY KEY, pf_ws_creator TEXT, earliest_tx_creator TEXT, analyzed_at INTEGER
);
CREATE TABLE creator_funders (
 creator_address TEXT, funder_address TEXT, is_cex INTEGER, cex_exchange TEXT,
 cex_type TEXT, first_detected_at TEXT
);
CREATE TABLE address_labels (address TEXT PRIMARY KEY, label_name TEXT, category TEXT, tags TEXT);
"""


@pytest.fixture()
def databases():
    ops = sqlite3.connect(":memory:")
    core = sqlite3.connect(":memory:")
    ops.row_factory = core.row_factory = sqlite3.Row
    ops.executescript(OPS_SCHEMA)
    core.executescript(CORE_SCHEMA)
    ensure_schema(ops)
    yield ops, core
    ops.close()
    core.close()


def _queue(ops, mint: str, **values) -> None:
    data = {
        "creator": "CREATOR", "subprov": None, "treasury": None,
        "funder_wallet": None, "walkback_class": "FULL_WALKBACK",
        "attribution_source": "unknown", "intelligence_outcome": "NO_ATTRIBUTION_FOUND",
        "status": "complete", "attempts": 0, "rpc_used": 0, "last_error": None,
    }
    data.update(values)
    ops.execute(
        "INSERT INTO wt_walkback_queue VALUES (?,?,?,?,?,?,?,?,?,?,?,?,100,200,200)",
        (mint, data["creator"], data["subprov"], data["treasury"], data["funder_wallet"],
         data["walkback_class"], data["attribution_source"], data["intelligence_outcome"],
         data["status"], data["attempts"], data["rpc_used"], data["last_error"]),
    )


def test_all_terminal_stop_classes_are_typed(databases):
    ops, core = databases
    ops.execute("INSERT INTO operators VALUES ('OP','WATCHTOWER','CONFIRMED')")
    ops.execute("INSERT INTO operator_entities VALUES ('OP','TREASURY')")
    _queue(ops, "canonical", treasury="TREASURY", intelligence_outcome="WATCHTOWER_CONFIRMED")

    for i, timestamp in enumerate((1, 200000, 400000, 600000, 800000), 1):
        core.execute("INSERT INTO token_analysis VALUES (?,?,NULL,?)", (f"hist{i}", "REPEAT", timestamp))
    _queue(ops, "repeat", creator="REPEAT", attribution_source="known_multi_token_creator")

    core.execute("INSERT INTO creator_funders VALUES ('CEX_CREATOR','CEX_WALLET',1,'Coinbase',NULL,'2025-01-01')")
    _queue(ops, "cex", creator="CEX_CREATOR", attribution_source="cex_funded")
    _queue(ops, "bridge", funder_wallet="2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS")
    _queue(ops, "relay", funder_wallet="F7p3dFrjRTbtRp8FRF6qHLomXbKRBzpvBLjtQcfcgmNe")
    _queue(ops, "platform", funder_wallet="AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk")
    ops.execute("INSERT INTO wt_treasury_review VALUES ('UNKNOWN',1,'walkback_hop2',2)")
    _queue(ops, "unknown", treasury="UNKNOWN")
    _queue(ops, "gap", subprov="SUBPROV", intelligence_outcome="LINEAGE_GAP")
    _queue(ops, "depth", intelligence_outcome="MAX_DEPTH")
    _queue(ops, "insufficient")

    expected = {
        "canonical": CANONICAL_OPERATOR_REACHED,
        "repeat": KNOWN_MULTI_TOKEN_CREATOR,
        "cex": KNOWN_CEX_REACHED,
        "bridge": KNOWN_BRIDGE_REACHED,
        "relay": KNOWN_RELAY_REACHED,
        "platform": KNOWN_RELAY_REACHED,
        "unknown": UNKNOWN_INFRASTRUCTURE,
        "gap": LINEAGE_GAP,
        "depth": MAX_DEPTH,
        "insufficient": INSUFFICIENT_EVIDENCE,
    }
    for mint, outcome_type in expected.items():
        outcome = derive_outcome(ops, mint, core_conn=core, now=900000)
        assert outcome is not None
        assert outcome.outcome_type == outcome_type
        assert bool(outcome.should_seed_emerging_operator) == (outcome_type == UNKNOWN_INFRASTRUCTURE)
        if outcome_type == LINEAGE_GAP:
            assert outcome.should_retry
            assert outcome.evidence["retry_condition"] == "NEW_EVIDENCE_ONLY"


def test_conflicting_canonical_branches_are_ambiguous(databases):
    ops, core = databases
    ops.executemany("INSERT INTO operators VALUES (?,?, 'CONFIRMED')", [("A", "A"), ("B", "B")])
    ops.executemany("INSERT INTO operator_entities VALUES (?,?)", [("A", "TA"), ("B", "TB")])
    _queue(ops, "branch", treasury="TA")
    ops.execute("INSERT INTO wt_watchtower_launches VALUES ('branch','C','S','TB')")
    assert derive_outcome(ops, "branch", core_conn=core).outcome_type == AMBIGUOUS_BRANCH


def test_repeat_creator_requires_stable_profile_and_canonical_precedence(databases):
    ops, core = databases
    for i, timestamp in enumerate((1, 200000, 400000, 600000, 800000), 1):
        core.execute("INSERT INTO token_analysis VALUES (?,?,NULL,?)", (f"m{i}", "REPEAT", timestamp))
    assert evaluate_launcher_profile(ops, core, "REPEAT", now=900000)["established"]
    ops.execute("INSERT INTO wt_wrap_close_candidates VALUES ('REPEAT',899999,899999)")
    assert not evaluate_launcher_profile(ops, core, "REPEAT", now=900000)["established"]
    ops.execute("DELETE FROM wt_wrap_close_candidates")
    ops.execute("INSERT INTO operator_entities VALUES ('OP','REPEAT')")
    assert not evaluate_launcher_profile(ops, core, "REPEAT", now=900000)["established"]


def test_only_unknown_infrastructure_enters_x20_registry(databases):
    ops, core = databases
    ops.execute("INSERT INTO wt_treasury_review VALUES ('UNKNOWN',1,'walkback_hop2',2)")
    _queue(ops, "unknown", treasury="UNKNOWN")
    _queue(ops, "gap", subprov="GAP", intelligence_outcome="LINEAGE_GAP")
    for mint in ("unknown", "gap"):
        outcome = derive_outcome(ops, mint, core_conn=core)
        persist_outcome(ops, mint, outcome)
    rows = ops.execute(
        "SELECT terminal_entity FROM wt_unknown_infrastructure_registry WHERE eligible=1"
    ).fetchall()
    assert [row[0] for row in rows] == ["UNKNOWN"]
    assert [row["terminal_entity"] for row in emerging_operator_seeds(ops)] == ["UNKNOWN"]
    persist_outcome(ops, "unknown", derive_outcome(ops, "unknown", core_conn=core))
    assert ops.execute(
        "SELECT observation_count FROM wt_unknown_infrastructure_registry WHERE terminal_entity='UNKNOWN'"
    ).fetchone()[0] == 1


def test_failed_terminal_row_still_has_a_clear_conclusion(databases):
    ops, core = databases
    _queue(ops, "failed", status="failed", last_error="exhausted")
    outcome = derive_outcome(ops, "failed", core_conn=core)
    assert outcome.outcome_type == INSUFFICIENT_EVIDENCE
    assert outcome.completed_at == 200


def test_exhausted_pending_row_is_finalized_with_typed_outcome(databases):
    from src.core.walkback_worker import finalize_exhausted_pending

    ops, _core = databases
    _queue(ops, "exhausted", status="pending", attempts=3)
    assert finalize_exhausted_pending(ops, max_attempts=3) == 1
    row = ops.execute(
        "SELECT status,intelligence_outcome FROM wt_walkback_queue WHERE mint='exhausted'"
    ).fetchone()
    assert tuple(row) == ("failed", "NO_ATTRIBUTION_FOUND")
    assert ops.execute(
        "SELECT outcome_type FROM wt_attribution_outcomes WHERE mint='exhausted'"
    ).fetchone()[0] == INSUFFICIENT_EVIDENCE


def test_discovery_and_mission_stream_use_the_plain_typed_conclusion(tmp_path):
    path = tmp_path / "ops.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(OPS_SCHEMA)
    ensure_schema(conn)
    _queue(conn, "MINT")
    conn.execute("INSERT INTO wt_watchtower_launches VALUES ('MINT','CREATOR',NULL,NULL)")
    outcome = derive_outcome(conn, "MINT", core_conn=None)
    persist_outcome(conn, "MINT", outcome)
    conn.commit()
    conn.close()

    service = DiscoveryService(str(path), str(path))
    discovery = service.resolve("MINT", "token")
    assert discovery["attribution_outcome"]["outcome_type"] == INSUFFICIENT_EVIDENCE
    assert discovery["summary"] == outcome.stop_reason
    recent = service.recent()
    assert recent["streams"]["walkbacks"][0]["message"] == outcome.stop_reason
