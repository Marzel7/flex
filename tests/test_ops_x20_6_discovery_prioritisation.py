"""Presentation contracts for X20.6 Discovery intelligence prioritisation."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/discovery.html").read_text()


def test_landing_separates_intelligence_from_attribution_health():
    for label in (
        "New Intelligence Today", "Intelligence", "Pipeline Health",
        "Attribution Health", "Knowledge changes and analyst actions first",
    ):
        assert label in HTML
    assert "dw-landing-layout" in HTML
    assert "dw-health-list" in HTML


def test_existing_canonical_apis_are_composed_without_new_route_contracts():
    assert "/api/discovery/recent?limit=20" in HTML
    assert "/api/ops-v2/attribution-outcomes?limit=500" in HTML
    assert "/api/ops-v2/attribution-outcomes?limit=500&outcome_type=" in HTML
    assert "/api/ops-v2/emerging-operator-seeds" in HTML


def test_unknown_infrastructure_remains_primary_x20_intelligence():
    assert "seeds.required_outcome_type" in HTML
    assert "Emerging Operator Candidates" in HTML
    assert "View Registry" in HTML
    assert "X20 intake" in HTML


def test_terminal_outcomes_are_aggregated_not_added_to_primary_feed():
    assert "summariseOutcomes" in HTML
    assert "healthPanel(summary,seedType)" in HTML
    assert "streams[stream]" in HTML
    assert "streams.walkbacks" not in HTML
    assert "displayPriority" in HTML


def test_aggregated_outcome_drills_into_filtered_cases():
    assert "FILTER_OUTCOME" in HTML
    assert 'href="/discovery?outcome_type=' in HTML
    assert "filteredCases(FILTER_OUTCOME)" in HTML
    assert "Attribution Health · Underlying cases" in HTML
    assert "href(x.mint,'token')" in HTML
