from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "templates/operator_intelligence.html").read_text(encoding="utf-8")


def test_confirmed_profile_uses_investigation_task_navigation():
    for tab in ("Summary", "Evidence", "Members", "Analysis", "History"):
        assert f'>{tab}</button>' in SOURCE
    for panel in ("summary", "evidence", "members", "analysis", "history"):
        assert f'data-panel="{panel}"' in SOURCE
    assert "setupProfileTabs" in SOURCE


def test_confirmed_summary_keeps_governance_as_progressive_disclosure():
    summary = SOURCE.split('data-panel="summary"', 1)[1].split('data-panel="evidence"', 1)[0]
    assert 'id="oi-operational-role"' in summary
    assert 'details class="oi-governance"' in summary
    assert "Quick Actions · confirmed-only workflow" in summary
    assert "summaryPanel.appendChild(inbox)" in SOURCE


def test_confirmed_profile_units_are_explicit():
    for label in (
        "Identity observations",
        "behaviour observations",
        "Assessment evidence",
        "Assessment contradictions",
        "relationship observations",
    ):
        assert label in SOURCE


def test_confirmed_operational_role_has_same_linked_examples_as_investigations():
    assert "role.observed_relationships" in SOURCE
    assert "https://solscan.io/account/" in SOURCE
    assert "https://solscan.io/tx/" in SOURCE
    assert "/token-intelligence?mint=" in SOURCE
    assert "View all ' + observations.length" in SOURCE
    assert "observations.slice(0,3)" in SOURCE


def test_confirmed_only_capabilities_remain_available():
    for capability in (
        "oi-governance-actions",
        "oi-behaviour-section",
        "oi-change-section",
        "oi-assess-section",
        "oi-forecast-section",
        "oi-identity-timeline",
    ):
        assert capability in SOURCE
