"""Presentation contracts for the X21C Discovery Triage Workspace."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/discovery.html").read_text()


def test_triage_workspace_only_activates_for_the_two_unresolved_outcome_types():
    assert "var TRIAGE_OUTCOME_TYPES = {INSUFFICIENT_EVIDENCE:1, LINEAGE_GAP:1};" in HTML
    assert "if(TRIAGE_OUTCOME_TYPES[type]){" in HTML
    # Every other outcome_type must still fall through to the original flat list.
    assert "renderTriageWorkspace(new URLSearchParams(window.location.search).get('filter'));" in HTML


def test_level1_shows_worth_monitoring_vs_low_information_split():
    assert "worth monitoring" in HTML
    assert "no current investigative path" in HTML


def test_pattern_summary_links_are_filter_driven_not_hardcoded_scores():
    assert "patternSummaryCard" in HTML
    assert "outcome_type=INSUFFICIENT_EVIDENCE&filter=" in HTML


def test_provisioning_activity_reports_dormant_state_honestly():
    assert "No provisioning observations have been captured yet." in HTML
    assert "function provisioningActivityCard(p){" in HTML


def test_investigation_rows_are_grouped_by_entity_not_by_token():
    assert "function investigationRow(e){" in HTML
    assert "e.launch_count" in HTML
    assert "Open Investigation" in HTML


def test_filters_are_derived_from_persisted_facts_only():
    assert "TRIAGE_FILTERS" in HTML
    for label in (
        "Creator identified", "Repeated creator", "Repeated treasury",
        "Treasury Review Lead", "Emerging Operator", "Low-information",
    ):
        assert label in HTML


def test_workspace_consumes_new_read_only_endpoints():
    assert "/api/ops-v2/discovery-triage/summary" in HTML
    assert "/api/ops-v2/discovery-triage/queue" in HTML
