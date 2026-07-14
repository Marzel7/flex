"""Presentation contracts for the shared X20.5 visual intelligence layer."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _template(name):
    return (ROOT / "templates" / name).read_text()


def test_shared_visual_primitives_are_defined_once():
    css = (ROOT / "static/css/intel-platform.css").read_text()
    for selector in (
        ".vi-card", ".vi-metric", ".vi-confidence", ".vi-chain",
        ".vi-chip", ".vi-status", ".vi-outcome-bars", ".vi-scorecard",
    ):
        assert selector in css


def test_mission_control_uses_aggregated_intelligence_cards():
    html = _template("ops_shell_index.html")
    for label in ("Promotion Queue", "Emerging Operator", "Walkbacks", "WATCHTOWER"):
        assert label in html
    assert "mc-intel-grid" in html
    assert "vi-outcome-bars" in html
    assert "/api/ops/emerging-operators?limit=6" in html


def test_discovery_uses_relationship_walkback_and_evidence_visuals():
    html = _template("discovery.html")
    assert "function visualChain" in html
    assert "vi-chain-node" in html
    assert "dw-evidence-groups" in html
    assert "Why the walk stopped" not in html
    assert "Raw provenance" in html


def test_entity_leads_with_who_what_why():
    html = _template("entity_intelligence.html")
    positions = [html.index(label) for label in (">Who<", ">What<", ">Why<")]
    assert positions == sorted(positions)
    assert "ei-l1-confidence-bar" in html
    assert "ei-l1-recent" in html


def test_operator_level_one_is_scorecard_led():
    html = _template("operator_intelligence.html")
    for element_id in (
        "oi-observation-count", "oi-beh-confidence-bar",
        "oi-assessment-evidence", "oi-assessment-contradictions",
        "oi-forecast-window", "oi-forecast-confidence",
    ):
        assert element_id in html
    assert html.index("Current situation") < html.index("Historical · Behaviour summary")
    assert html.index("Historical · Behaviour summary") < html.index("Assessment <span")
    assert html.index("Assessment <span") < html.index("Forecast <span")
    assert "Level 3</span> Evidence, history and engineering detail" in html
