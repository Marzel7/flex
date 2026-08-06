from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_discovery_owns_surfacing_reason_and_navigation_only():
    html = (ROOT / "templates/discovery.html").read_text()
    assert "Why it surfaced" in html
    assert "Open Investigation →" in html
    assert "Open Treasury Review →" in html
    for governance_button in ("Approve Treasury", "Reject Treasury", "Create Investigation"):
        assert governance_button not in html


def test_investigation_owns_single_trigger_and_no_operator_comparison():
    html = (ROOT / "templates/operation_profile.html").read_text()
    assert html.count('>Investigation Trigger<') == 1
    assert "Operation Comparison" not in html
    assert "Recommended governance action" not in html


def test_treasury_review_owns_comparison_and_governance_not_discovery_reason():
    html = (ROOT / "templates/treasury_review.html").read_text()
    comparison = html.index("Operation Comparison")
    governance = html.index("Governance Decision")
    evidence = html.index("Supporting Evidence")
    assert comparison < governance < evidence
    assert "Why this surfaced" not in html
    assert "Investigation Trigger" not in html
    assert "Recommended governance action" in html
    for action in ("Approve Treasury", "Link to Existing Operator", "Create Operator Candidate",
                   "Create Investigation", "Needs More Evidence", "Reject Treasury"):
        assert action in html


def test_registry_remains_free_of_provisional_reasoning():
    html = (ROOT / "templates/operators_index.html").read_text()
    assert "Investigation Trigger" not in html
    assert "Operation Comparison" not in html
    assert "Why this surfaced" not in html
