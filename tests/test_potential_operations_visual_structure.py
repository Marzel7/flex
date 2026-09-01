from pathlib import Path

from src.ops.potential_operations import _attach_current_evidence, _current_sort_key, _decorate, _presentation_name, activity_state, evolution_watch


ROOT = Path(__file__).resolve().parents[1]


def test_potential_page_uses_shared_cards_and_structured_sections():
    page = (ROOT / "templates/potential_operations.html").read_text()
    assert "intel-platform.css" in page
    for text in ("ip-strip", "ip-metric", "Focus Next", "po-focus-grid", "Living Potential Operations", "Actionable Unresolved Candidates", "Attention", "Evidence", "Action", "LIVE_CURRENT", "creator_quality_label", "activity_state"):
        assert text in page
    assert "Mechanism</th>" not in page
    assert "Discovery</th>" not in page

def test_living_overview_is_prominent_and_read_only_semantic():
    page = (ROOT / "templates/potential_operations.html").read_text()
    assert "Living Potential Operations" in page
    assert page.index("Living Potential Operations") < page.index("Actionable Unresolved Candidates")
    for text in ("Living Operations", "GENERIC LIVE · {{ living|length }} active", "V{{ item.version }} · Current", "PAUSED · No detector", "View Living history", "LIVE_CURRENT", "snapshot as of"):
        assert text in page
    assert "Living Behaviours" not in page


def test_candidate_names_are_mechanism_derived_and_relationship_free():
    assert _presentation_name({"key_mechanism": "hop-1 PLAIN_XFER | hop-2 PLAIN_XFER"}) == "2-hop Transfer Sequence"
    assert _presentation_name({"key_mechanism": "hop-1 PLAIN_XFER 10000 lamports"}) == "10K-lamport Direct Transfer"
    sentinel = _decorate({"candidate_id": "s", "workflow_status": "QUEUED", "latest_verdict": "POTENTIAL_VARIANT_OF_SENTINEL", "proposed_name": "Potential variant of Sentinel · 10 SOL Ladder"})
    assert sentinel["display_descriptor"] == "10 SOL Ladder Variant"
    assert not sentinel["display_descriptor"].startswith("Unresolved")


def test_evolution_projection_retains_two_rows_and_read_only_actions():
    variants = [
        {"candidate_id": "a", "latest_verdict": "POTENTIAL_VARIANT_OF_SENTINEL"},
        {"candidate_id": "b", "latest_verdict": "POTENTIAL_VARIANT_OF_SENTINEL"},
    ]
    watch = evolution_watch(variants)
    assert len(watch["sentinel_variants"]) == 2
    assert watch["harbinger"] == {"related_observations": 97, "qualifying_clusters": 0, "admitted_candidates": 0, "operator_id": watch["harbinger"]["operator_id"]}
    assert _decorate({"candidate_id": "p", "workflow_status": "ACTIVE_PROVISIONAL"})["action_label"] == "Review evidence →"


def _ranked(candidate_id, a24=0, a7=0, a30=0, matches=0, priority=1):
    return {"candidate_id": candidate_id, "priority_rank": priority,
            "current_evidence": {"metrics": {"last_1d": a24, "last_7d": a7, "last_30d": a30}, "matches": matches}}


def test_recency_first_comparator_and_state_are_deterministic():
    assert _current_sort_key(_ranked("more-24", 2, 0)) < _current_sort_key(_ranked("more-7", 1, 99))
    assert _current_sort_key(_ranked("more-7", 1, 3)) < _current_sort_key(_ranked("less-7", 1, 2, 99))
    assert _current_sort_key(_ranked("more-30", 1, 2, 3)) < _current_sort_key(_ranked("less-30", 1, 2, 2, 99))
    assert _current_sort_key(_ranked("more-evidence", 1, 2, 3, 4)) < _current_sort_key(_ranked("less-evidence", 1, 2, 3, 3))
    assert _current_sort_key(_ranked("stable", 1, 2, 3, 4, 1)) < _current_sort_key(_ranked("later", 1, 2, 3, 4, 2))
    assert [activity_state({"last_1d": a, "last_7d": b, "last_30d": c}) for a,b,c in ((3,3,3),(1,1,1),(0,0,1),(0,0,0))] == ["VERY_ACTIVE", "ACTIVE", "COOLING", "DORMANT"]


def test_activity_isolated_from_global_high_waters_and_evidence_is_not_rewritten():
    evidence={"candidate": {"matches": 7, "metrics": {"last_1d": 0, "last_7d": 0, "last_30d": 0}, "current_evidence_state": "QUIET", "global_high_water": 999999}}
    row=_attach_current_evidence({"candidate_id": "candidate", "priority_rank": 1}, evidence)
    assert row["current_evidence"]["activity_state"] == "UNKNOWN"
    assert row["current_evidence"]["matches"] == 7
    assert "999999" not in row["current_evidence"]["reason"]


def test_eight_hop_snapshot_is_not_presented_as_live_without_live_aggregation():
    evidence={"p3r-v2-dc4953db7adb853337c4": {"matches": 33, "metrics": {"last_1d": 1, "last_7d": 14, "last_30d": 30}, "technical_edge_metrics": {"last_1d": 8, "last_7d": 112, "last_30d": 240}, "current_evidence_state": "RECURRING"}}
    row=_attach_current_evidence({"candidate_id": "p3r-v2-dc4953db7adb853337c4", "priority_rank": 1}, evidence)
    assert row["current_evidence"]["metrics"] == {}
    assert row["current_evidence"]["activity_source"] == "UNKNOWN"
    assert row["current_evidence"]["technical_edge_metrics"]["last_1d"] == 8
