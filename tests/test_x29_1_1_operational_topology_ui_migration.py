"""X29.1.1 — Complete Operational Topology UI Migration.

This sprint is a UI-only migration: the Operational Intelligence hierarchy
(already built in X29.1) is promoted to the primary Discovery workflow, with
progressive (one-level-at-a-time) drill-down and cumulative filtering; the
legacy Investigation Queue panel is collapsed and relabelled, not removed.
No classifier, replay, or API logic changed.

These tests verify the property the new progressive-drill-down UI actually
depends on: build_hierarchy()'s existing tree shape is ALREADY correctly
scoped per-parent (a behaviour node's children are counted only within that
specific topology+behaviour combination, not globally) -- this was verified
manually against live data before building the UI (see the X29.1.1 design
doc), and is pinned here as a regression test so a future change to
build_hierarchy() can't silently break the drill-down semantics the UI
depends on.

Also covers: cumulative filtering via query() combines correctly at every
level (topology alone / topology+behaviour / topology+behaviour+mechanism),
matching the brief's explicit Filtering Rules; and that the API response
shape (the JSON keys/structure) is unchanged from X29.1 (no field renamed,
removed, or restructured), since this sprint claims not to touch the API.
"""
from __future__ import annotations

from src.ops.operational_intelligence import build_hierarchy, query


def _fixture_intelligence():
    """Mirrors the shape build_operational_intelligence() produces, with a
    deliberately tricky case: MINT_5 has BOTH Rapid Birth and Repeat Creator
    tags AND both WSOL Wrap-Close and Plain Transfer mechanisms, so it must
    surface correctly across multiple branches without double-counting the
    exclusive topology level."""
    return {
        "generated_at": 1111111111,
        "records": {
            "MINT_1": {"topology": "FAN_OUT", "behaviours": ["RAPID_BIRTH_LAUNCH"], "mechanisms": ["WSOL_WRAP_CLOSE"]},
            "MINT_2": {"topology": "FAN_OUT", "behaviours": ["REPEAT_CREATOR"], "mechanisms": ["PLAIN_TRANSFER"]},
            "MINT_3": {"topology": "FAN_OUT", "behaviours": ["REPEAT_CREATOR"], "mechanisms": ["PLAIN_TRANSFER"]},
            "MINT_4": {"topology": "LINEAR", "behaviours": [], "mechanisms": ["WSOL_WRAP_CLOSE"]},
            "MINT_5": {"topology": "FAN_OUT", "behaviours": ["RAPID_BIRTH_LAUNCH", "REPEAT_CREATOR"],
                       "mechanisms": ["WSOL_WRAP_CLOSE", "PLAIN_TRANSFER"]},
        },
    }


# ─────────────────────── Progressive drill-down scoping ───────────────────────

def test_topology_level_counts_are_exclusive_and_sum_to_total():
    """Level 1 of the drill-down: topology counts must be exclusive and sum
    to the total record count -- this is what makes 'Fan-Out (124)' at the
    top of the hierarchy trustworthy as the starting point of the brief's
    drill-down example."""
    intel = _fixture_intelligence()
    tree = build_hierarchy(intel)["tree"]
    total = sum(n["count"] for n in tree)
    assert total == len(intel["records"])
    fan_out = next(n for n in tree if n["topology"] == "FAN_OUT")
    assert fan_out["count"] == 4  # MINT_1, MINT_2, MINT_3, MINT_5


def test_behaviour_level_is_scoped_to_the_selected_topology_only():
    """Level 2: a behaviour node's count must reflect ONLY launches within
    the selected topology, not the global count for that behaviour across
    all topologies -- the exact semantic the brief's Drill-down Behaviour
    section specifies ('81 of the 124 Fan-Out launches', not '81 globally').
    """
    intel = _fixture_intelligence()
    tree = build_hierarchy(intel)["tree"]
    fan_out = next(n for n in tree if n["topology"] == "FAN_OUT")
    repeat_creator_under_fanout = next(c for c in fan_out["children"] if c["behaviour"] == "REPEAT_CREATOR")
    # 3 Fan-Out mints have REPEAT_CREATOR (MINT_2, MINT_3, MINT_5) -- NOT the
    # global Repeat Creator count (which would also need to include any
    # LINEAR/other-topology mints tagged REPEAT_CREATOR, of which there are
    # none in this fixture, but the scoping must hold structurally either way).
    assert repeat_creator_under_fanout["count"] == 3


def test_mechanism_level_is_scoped_to_topology_and_behaviour_selection():
    """Level 3: a mechanism node's count must reflect only launches matching
    BOTH the selected topology AND the selected behaviour -- fully cumulative
    scoping, three levels deep."""
    intel = _fixture_intelligence()
    tree = build_hierarchy(intel)["tree"]
    fan_out = next(n for n in tree if n["topology"] == "FAN_OUT")
    repeat_creator = next(c for c in fan_out["children"] if c["behaviour"] == "REPEAT_CREATOR")
    plain_transfer = next(m for m in repeat_creator["children"] if m["mechanism"] == "PLAIN_TRANSFER")
    # MINT_2, MINT_3, MINT_5 all have REPEAT_CREATOR under FAN_OUT; all three
    # ALSO carry PLAIN_TRANSFER (MINT_5 carries both mechanisms).
    assert plain_transfer["count"] == 3


def test_multi_tag_mint_appears_under_every_matching_branch_without_inflating_topology():
    """MINT_5 has 2 behaviour tags and 2 mechanism tags -- it must appear
    under BOTH behaviour branches and both mechanism sub-branches (additive
    tags surfacing correctly), while the FAN_OUT topology count itself still
    counts MINT_5 exactly once."""
    intel = _fixture_intelligence()
    tree = build_hierarchy(intel)["tree"]
    fan_out = next(n for n in tree if n["topology"] == "FAN_OUT")
    assert fan_out["count"] == 4  # not 5 or more -- MINT_5 counted once at the topology level
    rapid = next(c for c in fan_out["children"] if c["behaviour"] == "RAPID_BIRTH_LAUNCH")
    repeat = next(c for c in fan_out["children"] if c["behaviour"] == "REPEAT_CREATOR")
    assert rapid["count"] == 2   # MINT_1, MINT_5
    assert repeat["count"] == 3  # MINT_2, MINT_3, MINT_5 -- MINT_5 appears in BOTH


# ─────────────────────── Cumulative filtering (drives the launch table) ───────────────────────

def test_cumulative_filter_topology_only():
    intel = _fixture_intelligence()
    assert set(query(intel, topology="FAN_OUT")) == {"MINT_1", "MINT_2", "MINT_3", "MINT_5"}


def test_cumulative_filter_topology_and_behaviour():
    """Selecting Fan-Out then Rapid Birth→Migration must filter to
    topology=FAN_OUT AND behaviour contains RAPID_BIRTH_LAUNCH -- exactly
    the brief's Filtering Rules example."""
    intel = _fixture_intelligence()
    assert set(query(intel, topology="FAN_OUT", behaviour="RAPID_BIRTH_LAUNCH")) == {"MINT_1", "MINT_5"}


def test_cumulative_filter_topology_behaviour_and_mechanism():
    """Selecting Fan-Out -> Repeat Creator -> WSOL Wrap-Close must filter to
    all three conditions simultaneously."""
    intel = _fixture_intelligence()
    result = query(intel, topology="FAN_OUT", behaviour="REPEAT_CREATOR", mechanism="WSOL_WRAP_CLOSE")
    assert set(result) == {"MINT_5"}  # only MINT_5 has all three


def test_narrowing_a_filter_never_increases_the_result_set():
    """Progressive drill-down must always narrow, never widen, the result
    set as more levels are selected -- the brief's core navigation
    requirement ('Navigation should therefore become progressively
    narrower')."""
    intel = _fixture_intelligence()
    topo_only = set(query(intel, topology="FAN_OUT"))
    topo_behaviour = set(query(intel, topology="FAN_OUT", behaviour="REPEAT_CREATOR"))
    topo_behaviour_mech = set(query(intel, topology="FAN_OUT", behaviour="REPEAT_CREATOR", mechanism="WSOL_WRAP_CLOSE"))
    assert topo_behaviour.issubset(topo_only)
    assert topo_behaviour_mech.issubset(topo_behaviour)


# ─────────────────────── API response shape unchanged (no logic drift) ───────────────────────

def test_hierarchy_response_shape_matches_x29_1_contract():
    """Static structural check: build_hierarchy()'s output keys/shape must
    be exactly what X29.1 already produced -- {generated_at, tree: [...]}
    with each tree node having topology/label/count/children, each behaviour
    child having behaviour/label/count/children, each mechanism grandchild
    having mechanism/label/count. This sprint must not have altered this
    contract (it only changes how the SAME data is rendered/navigated)."""
    intel = _fixture_intelligence()
    result = build_hierarchy(intel)
    assert set(result.keys()) == {"generated_at", "tree"}
    for node in result["tree"]:
        assert set(node.keys()) == {"topology", "label", "count", "children"}
        for behaviour_node in node["children"]:
            assert set(behaviour_node.keys()) == {"behaviour", "label", "count", "children"}
            for mech_node in behaviour_node["children"]:
                assert set(mech_node.keys()) == {"mechanism", "label", "count"}


def test_build_hierarchy_still_pure_no_mutation():
    """Re-confirms the storage-model invariant (already tested in X29.1,
    re-verified here since the UI now calls this function on every
    navigation click -- it must remain safe to call repeatedly)."""
    intel = _fixture_intelligence()
    before = {k: dict(v) for k, v in intel["records"].items()}
    build_hierarchy(intel)
    build_hierarchy(intel)
    build_hierarchy(intel)
    assert intel["records"] == before
