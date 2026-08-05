"""X72.1 compact Operational Intelligence dashboard contracts."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source() -> str:
    return (ROOT / "templates/emerging_operators.html").read_text()


def test_cards_have_one_shared_compact_structure():
    source = _source()
    assert "box-sizing:border-box" in source
    assert source.count("function card(f)") == 1
    for field in (
        "or-name", "or-badge", "Launches", "Creators", "or-reason",
        "Supporting ", "Missing ", "View Operation", "View Investigation",
    ):
        assert field in source
    assert "signals(r,disp)" in source
    assert ".slice(0,4)" in source


def test_dashboard_cards_do_not_render_evidence_reports():
    source = _source()
    card_source = source.split("function card(f)", 1)[1].split("function counts", 1)[0]
    assert "<p>" not in card_source
    assert "<details" not in card_source
    assert "analyst_explanation" not in source
    assert "reasoning_summary" not in source
    assert "promotion_readiness" not in source
    assert "evidenceDetails" not in source


def test_kpis_and_sections_expose_scan_priority():
    source = _source()
    for label in ("Confirmed", "Candidates", "Review", "Infrastructure", "Launches", "Unknown"):
        assert "'" + label + "'" in source
    for cue in (
        "Stable identity · no action required",
        "Confirmation eligible · highest analyst priority",
        "Contradictory evidence · resolution required",
        "Shared services · attribution caution",
    ):
        assert cue in source


def test_compact_height_and_grid_targets_are_explicit():
    source = _source()
    assert "min-height:176px" in source
    assert "minmax(300px,1fr)" in source
    assert "padding:14px 15px 12px" in source
    assert "gap:10px" in source


def test_profile_routes_and_evidence_workspace_remain_intact():
    dashboard = _source()
    profile = (ROOT / "templates/operation_profile.html").read_text()
    assert "p.profile_href||f.profile_href" in dashboard
    for detail in (
        "promotionFor", "Supporting", "Missing", "Legacy Context",
        "Promotion Blockers",
    ):
        assert detail in profile
