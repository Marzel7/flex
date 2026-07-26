from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


# X65.24 — Discovery Flow Reorder: Creator Identity -> Topology -> Campaign
# -> Funding Origin -> Operation -> Behaviour (presentation/navigation only;
# see docs brief). Section numbering and mount order updated to match.
def test_discovery_report_uses_clear_section_terminology():
    for heading in (
        "1. Creator Identity",
        "2. Topology",
        "3. Campaign",
        "4. Funding Origin",
        "5. Operation Attribution",
        "6. Behaviour",
    ):
        assert heading in HTML


def test_report_mounts_follow_selection_to_results_order():
    panel = HTML[HTML.index("function operationalIntelligencePanel") :]
    mounts = (
        "dw-x58-selection-mount",
        "dw-x64-creator-identity-mount",
        "dw-x58-topology-mount",
        "dw-x65-7-campaign-mount",
        "dw-topo-infra-mount",
        "dw-x58-attribution-mount",
        "dw-topo-level-mount",
        "dw-x58-results-head-mount",
        "dw-topo-launch-table",
    )
    positions = [panel.index(mount) for mount in mounts]
    assert positions == sorted(positions)


def test_breadcrumb_names_progressive_investigation_dimensions():
    selection = HTML[HTML.index("function topoBreadcrumb") :]
    selection = selection[: selection.index("function renderIntelligenceSummary")]
    for dimension in ("behaviour", "creator_identity", "topology", "funding", "operation"):
        assert dimension in selection


def test_full_cohort_results_are_requested_when_filters_are_clear():
    assert "include_records:'1'" in HTML
