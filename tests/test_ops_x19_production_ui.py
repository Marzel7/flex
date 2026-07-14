"""Presentation contracts for Sprint X19's four production surfaces."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def template(name: str) -> str:
    return (ROOT / "templates" / name).read_text()


def test_shared_disclosure_primitive_is_defined_once_and_reused():
    css = (ROOT / "static/css/intel-platform.css").read_text()
    assert ".x19-disclosure" in css
    for name in (
        "ops_shell_index.html",
        "discovery.html",
        "entity_intelligence.html",
        "operator_intelligence.html",
    ):
        assert "x19-disclosure" in template(name)


def test_mission_control_level_one_is_analyst_attention_only():
    html = template("ops_shell_index.html")
    for label in (
        "Active Operators",
        "Promotion Candidates",
        "Active Investigations",
        "Inbox",
        "Recent Intelligence",
    ):
        assert label in html
    assert "What requires analyst attention today?" in html


def test_discovery_follows_the_approved_provenance_order():
    html = template("discovery.html")
    for label in (
        "Identity · Level 1",
        "Funding walkback",
        "Attribution chain",
        "Evidence groups",
        "Promotion lineage",
        "Raw provenance",
    ):
        assert label in html
    assert "top+flow+wb+attribution+evidence+lineage+raw" in html
    assert 'class="x19-disclosure x19-level3"' in html


def test_entity_level_one_owns_only_entity_answers():
    html = template("entity_intelligence.html")
    for label in (
        "Current role",
        "Known operator",
        "Operations observed",
        "Current status",
        "Confidence",
    ):
        assert label in html
    assert 'class="x19-disclosure x19-level3"' in html
    assert 'id="ei-raw-disclosure"' in html


def test_operator_separates_current_historical_assessment_and_forecast():
    html = template("operator_intelligence.html")
    ordered = [
        "Identity</div>",
        "Current situation",
        "Historical · Behaviour summary",
        "Assessment <span",
        "Forecast <span",
    ]
    positions = [html.index(label) for label in ordered]
    assert positions == sorted(positions)
    assert 'class="x19-time-section historical"' in html
    assert 'class="x19-time-section forecast"' in html
    assert 'class="x19-disclosure x19-level3"' in html
    assert '<details class="x19-disclosure x19-level3" open' not in html
