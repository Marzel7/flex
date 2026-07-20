"""X29.1 — Operational Topology Intelligence Framework.

Validates the three-dimension model designed in X29.0 against synthetic
fixtures covering the exact cases that doc's historical walkthrough
identified (Part 3): a clean Fan-Out+Rapid-Birth+Wrap-Close launch, a
Repeat-Creator launch whose topology must NOT be forced by that behaviour
tag (the concrete defect X29.0 found in today's investigation_pipeline.py),
a Linear single-use chain, and an evidence-insufficient Unknown case.

Constraints exercised directly:
  - topology is exactly one value, Unknown is a valid, non-error result
  - behaviour and mechanism are additive (zero, one, or many tags)
  - behaviour never determines topology (X29.0 Part 1's identified defect)
  - storage is flat/dimension-based; the hierarchy is a derived view,
    never a second source of truth (rebuilding it from the same records
    must be deterministic and must not mutate the records)
  - cross-dimensional queries work independent of the hierarchy
"""
from __future__ import annotations

import sqlite3

import pytest

from src.ops.funding_topology import (
    classify_topology_for_launch, FAN_OUT, LINEAR, MULTI_LEVEL_FAN_OUT, MESH, UNKNOWN,
)
from src.ops.funding_mechanism import (
    classify_mechanisms_for_launch, WSOL_WRAP_CLOSE, PLAIN_TRANSFER, SEEDED_ACCOUNT_CLOSE, MIXED,
)
from src.ops.operational_intelligence import build_hierarchy, query


# ─────────────────────── Stage 1: Funding Topology ───────────────────────

def test_fan_out_requires_more_than_one_sibling():
    result = classify_topology_for_launch(
        None, subprov_wallet="SUBPROV_A", treasury_wallet="TREASURY_A",
        sibling_counts={"SUBPROV_A": 5},
    )
    assert result["topology"] == FAN_OUT


def test_linear_when_subprov_has_exactly_one_sibling():
    result = classify_topology_for_launch(
        None, subprov_wallet="SUBPROV_B", treasury_wallet="TREASURY_A",
        sibling_counts={"SUBPROV_B": 1},
    )
    assert result["topology"] == LINEAR


def test_linear_when_treasury_direct_no_subprov():
    result = classify_topology_for_launch(
        None, subprov_wallet=None, treasury_wallet="TREASURY_A",
    )
    assert result["topology"] == LINEAR
    assert result["derived_from"] == "treasury_direct_no_subprov"


def test_multi_level_fan_out_takes_priority_over_sibling_count():
    """A subprov that is itself a recorded child-subprov must classify as
    Multi-Level Fan-Out even if it also has multiple creator siblings --
    most-specific-first evaluation order (X29.0 Part 2's mutual-exclusivity
    rule)."""
    result = classify_topology_for_launch(
        None, subprov_wallet="CHILD_SUBPROV", treasury_wallet="TREASURY_A",
        sibling_counts={"CHILD_SUBPROV": 5},
        multi_level_subprovs={"CHILD_SUBPROV"},
    )
    assert result["topology"] == MULTI_LEVEL_FAN_OUT


def test_mesh_takes_priority_over_fan_out():
    result = classify_topology_for_launch(
        None, subprov_wallet="SUBPROV_C", treasury_wallet="MESH_TREASURY",
        sibling_counts={"SUBPROV_C": 5},
        mesh_treasuries={"MESH_TREASURY"},
    )
    assert result["topology"] == MESH


def test_unknown_when_no_lineage_evidence_at_all():
    result = classify_topology_for_launch(None, subprov_wallet=None, treasury_wallet=None)
    assert result["topology"] == UNKNOWN


def test_unknown_when_subprov_present_but_no_sibling_evidence():
    """A subprov IS recorded (e.g. from evidence_json) but we have no
    sibling-count data for it at all -- must fall to Unknown, never guess
    Fan-Out or Linear (X29.0: 'never infer topology without evidence')."""
    result = classify_topology_for_launch(
        None, subprov_wallet="UNSEEN_SUBPROV", treasury_wallet="TREASURY_A",
        sibling_counts={},
    )
    assert result["topology"] == UNKNOWN
    assert result["derived_from"] == "subprov_present_no_sibling_evidence"


def test_exactly_one_topology_value_always_assigned():
    """Every classify_topology_for_launch() call must return exactly one
    of the five defined topology constants -- never two, never a list."""
    from src.ops.funding_topology import TOPOLOGY_ORDER
    cases = [
        dict(subprov_wallet="S1", treasury_wallet="T1", sibling_counts={"S1": 3}),
        dict(subprov_wallet="S2", treasury_wallet="T1", sibling_counts={"S2": 1}),
        dict(subprov_wallet=None, treasury_wallet="T1"),
        dict(subprov_wallet=None, treasury_wallet=None),
        dict(subprov_wallet="S3", treasury_wallet="T2", multi_level_subprovs={"S3"}),
        dict(subprov_wallet="S4", treasury_wallet="MESH1", mesh_treasuries={"MESH1"}),
    ]
    for kwargs in cases:
        result = classify_topology_for_launch(None, **kwargs)
        assert result["topology"] in TOPOLOGY_ORDER
        assert isinstance(result["topology"], str)


# ───────────────── Stage 1 defect regression: behaviour must not leak into topology ─────────────────

def test_topology_classifier_has_no_behaviour_or_creator_history_parameter():
    """Static guard for the exact defect X29.0 Part 1 identified in today's
    investigation_pipeline.py (REPEAT_CREATOR force-overriding whatever
    topology bucket a launch would otherwise get): the topology classifier's
    signature must not accept any creator-history/behaviour-evidence
    argument at all, so it is structurally impossible for a future edit to
    let behaviour leak into this function without changing its signature
    first (a visible, reviewable change, not a silent one)."""
    import inspect
    sig = inspect.signature(classify_topology_for_launch)
    params = set(sig.parameters)
    behaviour_like = {"launch_count", "established", "behaviour", "archetype", "rapid_birth", "burst"}
    assert not (params & behaviour_like), (
        f"classify_topology_for_launch() must never accept behaviour/creator-history "
        f"evidence -- found overlapping params: {params & behaviour_like}"
    )


# ─────────────────────── Stage 3: Funding Mechanism (additive) ───────────────────────

def test_mechanism_is_additive_zero_one_or_many():
    assert classify_mechanisms_for_launch(launch_mechanism=None)["mechanisms"] == []
    assert classify_mechanisms_for_launch(launch_mechanism="WSOL_WRAP_CLOSE")["mechanisms"] == [WSOL_WRAP_CLOSE]
    result = classify_mechanisms_for_launch(
        launch_mechanism="WSOL_WRAP_CLOSE", edge_mechanisms=["PLAIN_XFER"],
    )
    assert set(result["mechanisms"]) == {WSOL_WRAP_CLOSE, PLAIN_TRANSFER, MIXED}


def test_plain_xfer_and_plain_transfer_normalize_to_same_canonical_tag():
    """Verified real data: wt_provisioning_edges persists 'PLAIN_XFER',
    other tables persist 'PLAIN_TRANSFER' -- both must normalize to the
    same canonical tag, not be treated as two different mechanisms (which
    would falsely trigger a MIXED tag for a launch using only one real
    mechanism under two spellings)."""
    a = classify_mechanisms_for_launch(launch_mechanism="PLAIN_TRANSFER", edge_mechanisms=["PLAIN_XFER"])
    assert a["mechanisms"] == [PLAIN_TRANSFER]
    assert MIXED not in a["mechanisms"]


def test_seeded_account_close_alone_no_mixed_tag():
    result = classify_mechanisms_for_launch(launch_mechanism="SEEDED_ACCOUNT_CLOSE")
    assert result["mechanisms"] == [SEEDED_ACCOUNT_CLOSE]
    assert MIXED not in result["mechanisms"]


def test_unrecognized_raw_value_is_silently_dropped_not_fabricated():
    """An unrecognized raw mechanism string must not be invented into a new
    tag -- it's simply excluded, since this module maps a known,
    already-persisted set, not an open vocabulary."""
    result = classify_mechanisms_for_launch(launch_mechanism="SOME_FUTURE_MECHANISM_NOT_YET_MAPPED")
    assert result["mechanisms"] == []


# ─────────────────────── Hierarchy is a derived view, not storage ───────────────────────

def _fake_intelligence():
    """A small, hand-built intelligence record covering every branch shape
    build_hierarchy() must handle: multi-behaviour, multi-mechanism,
    zero-behaviour, zero-mechanism, and every topology value."""
    return {
        "generated_at": 1234567890,
        "records": {
            "MINT_1": {"topology": FAN_OUT, "behaviours": ["RAPID_BIRTH_LAUNCH"], "mechanisms": ["WSOL_WRAP_CLOSE"]},
            "MINT_2": {"topology": FAN_OUT, "behaviours": ["RAPID_BIRTH_LAUNCH", "BURST_LAUNCH"], "mechanisms": ["WSOL_WRAP_CLOSE", "PLAIN_TRANSFER"]},
            "MINT_3": {"topology": LINEAR, "behaviours": [], "mechanisms": []},
            "MINT_4": {"topology": UNKNOWN, "behaviours": ["REPEAT_CREATOR"], "mechanisms": []},
            "MINT_5": {"topology": MULTI_LEVEL_FAN_OUT, "behaviours": [], "mechanisms": ["SEEDED_ACCOUNT_CLOSE"]},
        },
    }


def test_hierarchy_top_level_conserves_exactly():
    """Topology remains exclusive: every mint appears under exactly one
    top-level node, and the sum of top-level counts equals total mints."""
    intel = _fake_intelligence()
    tree = build_hierarchy(intel)["tree"]
    total_top = sum(node["count"] for node in tree)
    assert total_top == len(intel["records"])


def test_hierarchy_is_deterministic_and_does_not_mutate_records():
    """Rebuilding the hierarchy twice from the same flat records must
    produce an identical tree, and must never mutate the records dict --
    the brief's explicit 'do not store the hierarchy' requirement means it
    must be a pure, repeatable computation."""
    intel = _fake_intelligence()
    records_before = {k: dict(v) for k, v in intel["records"].items()}
    tree1 = build_hierarchy(intel)
    tree2 = build_hierarchy(intel)
    assert tree1 == tree2
    assert intel["records"] == records_before


def test_hierarchy_additive_behaviour_can_exceed_topology_total():
    """A mint with 2 behaviour tags contributes to BOTH behaviour branches
    under its topology node -- the sum of behaviour-node counts under one
    topology can exceed that topology's own total. This is expected
    (additive tags), not a conservation bug."""
    intel = _fake_intelligence()
    tree = build_hierarchy(intel)["tree"]
    fan_out_node = next(n for n in tree if n["topology"] == FAN_OUT)
    behaviour_sum = sum(c["count"] for c in fan_out_node["children"])
    assert behaviour_sum > fan_out_node["count"]  # MINT_2 counted under 2 behaviour branches


def test_hierarchy_handles_zero_behaviour_and_zero_mechanism_gracefully():
    intel = _fake_intelligence()
    tree = build_hierarchy(intel)["tree"]
    linear_node = next(n for n in tree if n["topology"] == LINEAR)
    assert linear_node["count"] == 1
    none_behaviour = next(c for c in linear_node["children"] if c["behaviour"] is None)
    assert none_behaviour["count"] == 1
    none_mechanism = next(m for m in none_behaviour["children"] if m["mechanism"] is None)
    assert none_mechanism["count"] == 1


# ─────────────────────── Cross-dimensional query independent of hierarchy ───────────────────────

def test_query_by_topology_alone():
    intel = _fake_intelligence()
    assert set(query(intel, topology=FAN_OUT)) == {"MINT_1", "MINT_2"}


def test_query_by_behaviour_regardless_of_topology():
    intel = _fake_intelligence()
    # RAPID_BIRTH_LAUNCH appears only under FAN_OUT in this fixture, but the
    # query must not care about topology unless topology is also specified.
    assert set(query(intel, behaviour="RAPID_BIRTH_LAUNCH")) == {"MINT_1", "MINT_2"}


def test_query_by_mechanism_regardless_of_topology():
    intel = _fake_intelligence()
    assert set(query(intel, mechanism="WSOL_WRAP_CLOSE")) == {"MINT_1", "MINT_2"}


def test_query_combines_topology_and_mechanism():
    """'Show Fan-Out launches using Plain Transfer' -- one of the brief's
    explicit example queries."""
    intel = _fake_intelligence()
    assert set(query(intel, topology=FAN_OUT, mechanism="PLAIN_TRANSFER")) == {"MINT_2"}


def test_query_combines_topology_and_behaviour():
    intel = _fake_intelligence()
    assert set(query(intel, topology=FAN_OUT, behaviour="BURST_LAUNCH")) == {"MINT_2"}


def test_query_mesh_and_burst_launcher_returns_empty_when_none_match():
    """'Show Mesh operations exhibiting Burst Launcher behaviour' -- the
    brief's example query; must return an empty list, not an error, when
    the fixture has no Mesh-topology mints at all."""
    intel = _fake_intelligence()
    assert query(intel, topology=MESH, behaviour="BURST_LAUNCH") == []


def test_query_with_no_filters_returns_everything():
    intel = _fake_intelligence()
    assert set(query(intel)) == set(intel["records"])
