"""X78.17 keeps X78.16 intelligence intact while compressing analyst triage."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "templates/treasury_review.html").read_text()
WORKSPACE = (ROOT / "src/ops/treasury_review_workspace.py").read_text()


def test_card_surfaces_best_comparison_and_compact_metrics():
    assert "orderedMatches(item.operation_matches)" in PAGE
    assert "Best Operation comparison" in PAGE
    assert "Partial resemblance" in PAGE
    assert "No comparable evidence yet" in PAGE
    assert "launches</span>" in PAGE
    assert "creators</span>" in PAGE
    assert "provisioners</span>" in PAGE
    assert "esc(t)+'</span>'+stateBadge" in PAGE
    assert "esc(short(t))+'</span>'+stateBadge" not in PAGE


def test_unknown_dimensions_are_secondary_not_full_grid_rows():
    assert ".filter(x=>x[1]!=='UNKNOWN')" in PAGE
    assert "+" in PAGE and "not evaluated" in PAGE
    assert "No comparable dimensions evaluated." in PAGE


def test_governance_is_one_primary_action_with_all_alternatives_preserved():
    assert "primaryAction" in PAGE
    assert "More actions ▾" in PAGE
    for action in (
        "APPROVE_TREASURY", "LINK_TO_OPERATOR", "CREATE_OPERATOR_CANDIDATE",
        "CREATE_INVESTIGATION", "NEEDS_MORE_EVIDENCE", "REJECT_TREASURY",
    ):
        assert action in PAGE
    assert "Comparison informs triage only. Governance remains analyst-controlled." in PAGE


def test_evidence_and_standard_topology_use_progressive_disclosure():
    assert '<details class="tr-support">' in PAGE
    assert "Supporting evidence · " in PAGE
    assert "standard&&primary&&primary.states&&primary.states.Topology==='MATCH'" in PAGE
    assert "examples(item.relationship_examples)" in PAGE


def test_x78_16_comparison_engine_is_not_changed_by_presentation_milestone():
    assert "def _operation_matches" in WORKSPACE
    assert "comparison_state" in WORKSPACE
    # The UX consumes, rather than recalculates, all X78.16 counters.
    for counter in (
        "evaluated_dimensions", "matched_dimensions", "partial_dimensions",
        "contradicted_dimensions", "unknown_dimensions",
    ):
        assert counter in PAGE


def test_actionable_global_sort_and_twenty_card_pagination_remain():
    assert "Actionable first · newest within group" in PAGE
    assert "limit=20&offset=" in PAGE
    assert "Load 20 more" in PAGE
    assert 'items.sort(key=lambda i:' in WORKSPACE
    assert 'i["comparison_triage"]["sort_rank"]' in WORKSPACE
