"""Sprint X16B populated-fixture tests for the read-only identity framework."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from src.ops.identity_framework import (
    EvidenceObservation,
    IdentityEvaluation,
    IdentityObservation,
    ContradictionObservation,
    PromotionDecisionEngine,
    INSUFFICIENT,
    PROMOTION_ELIGIBLE,
    REVIEW_CANDIDATE,
    REVIEW_REQUIRED,
)
from src.ops.operator_model import EVIDENCE_CONTEXT, EVIDENCE_IDENTITY, EVIDENCE_SUPPORTING
from src.ops.operator_resolver import IDENTITY_RULES, OperatorResolver


OPS_SCHEMA = """
CREATE TABLE wt_ops_v2 (
  operation_uuid TEXT PRIMARY KEY, treasury_root TEXT, status TEXT, first_seen INTEGER,
  last_seen INTEGER
);
CREATE TABLE wt_ops_v2_wallets (
  operation_uuid TEXT, wallet TEXT, role TEXT, first_seen INTEGER, last_seen INTEGER
);
CREATE TABLE wt_ops_v2_creators (
  operation_uuid TEXT, creator_wallet TEXT, token_mint TEXT, template_base REAL
);
CREATE TABLE wt_ops_v2_edges (
  operation_uuid TEXT, from_wallet TEXT, to_wallet TEXT
);
CREATE TABLE wt_vanity_families (
  family_label TEXT, family_prefixes_json TEXT, family_suffixes_json TEXT,
  confirmed_wallets_json TEXT, roles_json TEXT, confidence TEXT
);
CREATE TABLE wt_ops_v2_families (
  family_uuid TEXT, playbook_signature TEXT
);
CREATE TABLE wt_ops_v2_operation_family_links (
  family_uuid TEXT, operation_uuid TEXT
);
CREATE TABLE operators (operator_id TEXT PRIMARY KEY);
CREATE TABLE operator_entities (operator_id TEXT, entity_address TEXT);
CREATE TABLE operator_evidence (evidence_id TEXT PRIMARY KEY);
CREATE TABLE operator_reviews (review_id TEXT PRIMARY KEY);
"""


LIVE_SCHEMA = """
CREATE TABLE wt_known_operator_hubs (
  hub_wallet TEXT PRIMARY KEY, operator_identity TEXT, confidence REAL, evidence_json TEXT
);
CREATE TABLE wt_operations (
  operation_id INTEGER PRIMARY KEY, operator_identity TEXT, window_start INTEGER, window_end INTEGER
);
CREATE TABLE wt_operation_members (
  operation_id INTEGER, token_mint TEXT, creator_wallet TEXT
);
CREATE TABLE creator_funders (
  creator_address TEXT, funder_address TEXT, amount_sol REAL
);
CREATE TABLE wt_provisioning_hubs (
  hub_address TEXT PRIMARY KEY, treasury_amount REAL, status TEXT
);
CREATE TABLE operator_creator_edges (
  creator_a TEXT, creator_b TEXT, operator_anchor TEXT, edge_type TEXT, confidence REAL
);
"""


def _digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def populated_identity_dbs(tmp_path):
    ops_path = tmp_path / "ops.db"
    live_path = tmp_path / "live.db"
    ops = sqlite3.connect(ops_path)
    ops.executescript(OPS_SCHEMA)
    ops.executemany(
        "INSERT INTO wt_ops_v2 VALUES (?,?,?,?,?)",
        [
            ("A", "T1", "FORMING", 10, 20),
            ("B", "T1", "FORMING", 11, 21),
            ("SINGLE", "T_SINGLE", "FORMING", 12, 22),
        ],
    )
    wallet_rows = []
    for operation in ("A", "B"):
        wallet_rows.extend([
            (operation, "SP1", "SUB_PROV", 10, 20),
            (operation, "R1", "RELAY", 10, 20),
            (operation, "R2", "SIGNALLER", 10, 20),
            (operation, "R3", "COLLECTOR", 10, 20),
            (operation, "OVERLAP", "PASS_THROUGH" if operation == "A" else "TREASURY", 10, 20),
            (operation, "CREATOR_ONLY", "CREATOR", 10, 20),
        ])
    ops.executemany("INSERT INTO wt_ops_v2_wallets VALUES (?,?,?,?,?)", wallet_rows)
    ops.executemany(
        "INSERT INTO wt_ops_v2_creators VALUES (?,?,?,?)",
        [("A", "C1", "M1", 1.25), ("B", "C1", "M2", 1.25)],
    )
    ops.executemany(
        "INSERT INTO wt_ops_v2_edges VALUES (?,?,?)",
        [
            ("A", "T1", "SP1"), ("A", "SP1", "C1"),
            ("B", "T1", "SP1"), ("B", "SP1", "C1"),
        ],
    )
    ops.execute(
        "INSERT INTO wt_vanity_families VALUES (?,?,?,?,?,?)",
        (
            "WATCHTOWER_44OR", json.dumps(["44or", "44o1"]), json.dumps([]),
            json.dumps(["44orTREASURY", "44orSIGNALLER", "44o1SIGNALLER"]),
            json.dumps({
                "44orTREASURY": "TREASURY",
                "44orSIGNALLER": "SIGNALLER",
                "44o1SIGNALLER": "SIGNALLER_2",
            }),
            "CONFIRMED",
        ),
    )
    # A suffix-only family is deliberately not a valid X8 vanity-prefix rule.
    ops.execute(
        "INSERT INTO wt_vanity_families VALUES (?,?,?,?,?,?)",
        (
            "SUFFIX_ONLY", json.dumps([]), json.dumps(["same"]),
            json.dumps(["AAAAsame", "BBBBsame"]),
            json.dumps({"AAAAsame": "TREASURY", "BBBBsame": "SIGNALLER"}),
            "CONFIRMED",
        ),
    )
    ops.execute("INSERT INTO wt_ops_v2_families VALUES ('F1','shared-playbook')")
    ops.executemany(
        "INSERT INTO wt_ops_v2_operation_family_links VALUES (?,?)",
        [("F1", "A"), ("F1", "B")],
    )
    ops.commit()
    ops.close()

    live = sqlite3.connect(live_path)
    live.executescript(LIVE_SCHEMA)
    hubs = [
        ("HA", "OPERATION_ALPHA", 1.0, "{}"),
        ("HB", "OPERATION_ALPHA", 0.9, "{}"),
        ("H1", "OPERATOR_001", 1.0, "{}"),
        ("HWT1", "WATCHTOWER", 1.0, "{}"),
        ("HWT2", "WATCHTOWER", 1.0, "{}"),
    ]
    live.executemany("INSERT INTO wt_known_operator_hubs VALUES (?,?,?,?)", hubs)

    operations = []
    members = []
    funders = []
    next_id = 1

    def add_lineage(identity, hub, count, direct=False):
        nonlocal next_id
        for index in range(count):
            operation_id = next_id
            next_id += 1
            creator = f"{identity}-C{operation_id}"
            operations.append((operation_id, identity, operation_id * 100, operation_id * 100 + 50))
            members.append((operation_id, f"M{operation_id}", creator))
            if direct:
                funders.append((creator, hub, 1.0))
            else:
                mid1, mid2 = f"MID1-{operation_id}", f"MID2-{operation_id}"
                funders.extend([(creator, mid1, 1.0), (mid1, mid2, 1.0), (mid2, hub, 1.0)])

    add_lineage("OPERATION_ALPHA", "HA", 2)
    add_lineage("OPERATION_ALPHA", "HB", 2)
    add_lineage("OPERATOR_001", "H1", 3)
    add_lineage("WATCHTOWER", "HWT1", 1, direct=True)
    add_lineage("WATCHTOWER", "HWT2", 1, direct=True)
    for hub in ("HWT1", "HWT2"):
        funders.extend([
            (hub, "44orTREASURY", 700.0),
            (hub, "44orSIGNALLER", 0.001),
            (hub, "44o1SIGNALLER", 0.001),
        ])

    live.executemany("INSERT INTO wt_operations VALUES (?,?,?,?)", operations)
    live.executemany("INSERT INTO wt_operation_members VALUES (?,?,?)", members)
    live.executemany("INSERT INTO creator_funders VALUES (?,?,?)", funders)
    live.executemany(
        "INSERT INTO wt_provisioning_hubs VALUES (?,?,?)",
        [("HWT1", 700.0, "CONFIRMED"), ("HWT2", 700.0, "CONFIRMED")],
    )
    live.execute(
        "INSERT INTO operator_creator_edges VALUES (?,?,?,?,?)",
        ("P1", "P2", "PAYOUT", "shared_payout_wallet", 0.9),
    )
    live.commit()
    live.close()
    return ops_path, live_path


def _identity(candidate="candidate", evidence_type="CROSS_OPERATION_WALLET_OVERLAP"):
    return IdentityObservation(
        candidate_key=candidate,
        evidence_type=evidence_type,
        category=EVIDENCE_IDENTITY,
        confidence=0.8,
        reason="Populated deterministic identity fixture.",
        source_tables=("fixture_operations",),
        entities=("E1",),
        operations=("O1", "O2"),
    )


def test_every_documented_rule_is_reachable_on_populated_fixture(populated_identity_dbs):
    ops_path, live_path = populated_identity_dbs
    evaluation = OperatorResolver(None, str(ops_path), str(live_path)).evaluate()
    observed = {item.evidence_type for item in evaluation.identity}
    assert set(IDENTITY_RULES) <= observed


def test_rules_enforce_thresholds_and_roles(populated_identity_dbs):
    ops_path, live_path = populated_identity_dbs
    evaluation = OperatorResolver(None, str(ops_path), str(live_path)).evaluate()
    roots = [item for item in evaluation.identity if item.evidence_type == "SHARED_TREASURY_ROOT"]
    assert [item.entities for item in roots] == [("T1",)]
    overlaps = [item for item in evaluation.identity
                if item.evidence_type == "CROSS_OPERATION_WALLET_OVERLAP"]
    assert any(item.entities == ("OVERLAP",) for item in overlaps)
    assert not any("CREATOR_ONLY" in item.entities for item in overlaps)
    vanity = [item for item in evaluation.identity if item.evidence_type == "VANITY_ADDRESS_FAMILY"]
    assert len(vanity) == 1
    assert vanity[0].legacy_identifier == "WATCHTOWER_44OR"


def test_supporting_only_never_creates_identity_or_proposal(populated_identity_dbs):
    ops_path, live_path = populated_identity_dbs
    resolver = OperatorResolver(None, str(ops_path), str(live_path))
    evaluation = resolver.evaluate()
    payout = [item for item in evaluation.supporting if item.candidate_key == "payout:PAYOUT"]
    assert payout and not any(item.candidate_key == "payout:PAYOUT" for item in evaluation.identity)
    assert not any(item.candidate_key == "payout:PAYOUT"
                   for item in resolver.propose(evaluation))


def test_promotion_threshold_uses_distinct_identity_classes():
    first = _identity()
    repeated = IdentityObservation(
        candidate_key="candidate",
        evidence_type="CROSS_OPERATION_WALLET_OVERLAP",
        category=EVIDENCE_IDENTITY,
        confidence=0.8,
        reason="A second observation of the same evidence class.",
        source_tables=("fixture_operations",),
        entities=("E2",),
        operations=("O3", "O4"),
    )
    engine = PromotionDecisionEngine()
    one_class = engine.decide(IdentityEvaluation(identity=(first, repeated)))[0]
    assert one_class.decision == REVIEW_CANDIDATE
    assert one_class.identity_classes == ("CROSS_OPERATION_WALLET_OVERLAP",)

    second_class = _identity(evidence_type="VANITY_ADDRESS_FAMILY")
    eligible = engine.decide(IdentityEvaluation(identity=(first, second_class)))[0]
    assert eligible.decision == PROMOTION_ELIGIBLE


def test_contradiction_requires_review():
    contradiction = ContradictionObservation(
        candidate_key="candidate",
        reason="The same anchor has a reviewed incompatible attribution.",
        source_tables=("operator_reviews",),
        related_entities=("E1",),
    )
    proposal = PromotionDecisionEngine().decide(IdentityEvaluation(
        identity=(_identity(), _identity(evidence_type="VANITY_ADDRESS_FAMILY")),
        contradictions=(contradiction,),
    ))[0]
    assert proposal.decision == REVIEW_REQUIRED


def test_supporting_and_context_do_not_change_promotion_outcome():
    identity = _identity()
    supporting = EvidenceObservation(
        candidate_key="candidate", evidence_type="PLAYBOOK_SIGNATURE_MATCH",
        category=EVIDENCE_SUPPORTING, confidence=0.3,
        reason="Same playbook.", source_tables=("families",), operations=("O1", "O2"),
    )
    context = EvidenceObservation(
        candidate_key="candidate", evidence_type="CHAIN_ACTIVITY",
        category=EVIDENCE_CONTEXT, confidence=0.0,
        reason="Campaign count context.", source_tables=("operations",),
    )
    engine = PromotionDecisionEngine()
    baseline = engine.decide(IdentityEvaluation(identity=(identity,)))[0]
    enriched = engine.decide(IdentityEvaluation(
        identity=(identity,), supporting=(supporting,), context=(context,)
    ))[0]
    assert baseline.decision == enriched.decision == REVIEW_CANDIDATE
    assert enriched.review_confidence > baseline.review_confidence
    assert enriched.identity_confidence == baseline.identity_confidence


def test_evaluation_and_proposals_are_deterministic(populated_identity_dbs):
    ops_path, live_path = populated_identity_dbs
    resolver = OperatorResolver(None, str(ops_path), str(live_path))
    first_evaluation = resolver.evaluate()
    second_evaluation = resolver.evaluate()
    assert first_evaluation.to_dict() == second_evaluation.to_dict()
    assert [item.to_dict() for item in resolver.propose(first_evaluation)] == [
        item.to_dict() for item in resolver.propose(second_evaluation)
    ]


def test_x16a_expected_decisions(populated_identity_dbs):
    ops_path, live_path = populated_identity_dbs
    proposals = {
        item.candidate_key: item
        for item in OperatorResolver(None, str(ops_path), str(live_path)).propose()
    }
    assert proposals["legacy:WATCHTOWER"].decision == PROMOTION_ELIGIBLE
    assert proposals["legacy:OPERATION_ALPHA"].decision == REVIEW_CANDIDATE
    assert proposals["legacy:OPERATOR_001"].decision == REVIEW_CANDIDATE


def test_resolver_is_read_only_and_does_not_populate_canonical_tables(populated_identity_dbs):
    ops_path, live_path = populated_identity_dbs
    before = (_digest(ops_path), _digest(live_path))
    report = OperatorResolver(None, str(ops_path), str(live_path)).run()
    after = (_digest(ops_path), _digest(live_path))
    assert before == after
    assert report["mode"] == "READ_ONLY"
    assert report["write_performed"] is False
    assert report["operators_created"] == report["operators_promoted"] == 0
    conn = sqlite3.connect(ops_path)
    try:
        for table in ("operators", "operator_entities", "operator_evidence", "operator_reviews"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        conn.close()


def test_review_candidate_is_first_class_in_model_and_operator_workspace():
    from src.ops.operator_model import OPERATOR_STATES, REVIEW_CANDIDATE as MODEL_STATE

    assert MODEL_STATE in OPERATOR_STATES
    index = (Path(__file__).parents[1] / "templates" / "operators_index.html").read_text()
    detail = (Path(__file__).parents[1] / "templates" / "operator_intelligence.html").read_text()
    assert "REVIEW_CANDIDATE" in index
    assert "REVIEW_CANDIDATE" in detail
