"""Presentation contracts for the X20.9 Discovery Operational Conclusion card.

Wording must stay factual: the engine reports matched evidence and a count,
never a probabilistic label ("possible"/"probable"/"likely"). The analyst
draws the conclusion from the "Analyst interpretation" section, which is
explicitly labelled as interpretation rather than an engine finding.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/discovery.html").read_text()


def test_treasury_expansion_card_never_claims_confirmed_attribution():
    assert "Treasury Review Lead" in HTML
    assert "Treasury ownership has not been confirmed" in HTML
    assert "Treasury Review Required" in HTML
    assert "function treasuryExpansion(t){" in HTML


def test_card_never_uses_probabilistic_labels():
    for banned in ("Possible Operation Expansion", "Probable Operation Expansion", "band"):
        assert banned not in HTML


def test_card_reports_matched_no_match_unavailable_and_interpretation_separately():
    assert "Observed similarities" in HTML
    assert "Available but not matched" in HTML
    assert "Evidence not currently available" in HTML
    assert "Analyst interpretation" in HTML


def test_treasury_expansion_composed_without_new_route_and_never_suppresses_x20():
    # Sourced entirely from the existing /api/discovery/entity/<mint> payload field,
    # not a separate fetch — so it can never race or override the emerging_candidate path.
    assert "treasuryExpansion(d.treasury_expansion)" in HTML
    assert "emergingCandidate(d.emerging_candidate)" in HTML
    assert HTML.index("treasuryExpansion(d.treasury_expansion)") < HTML.index("emergingCandidate(d.emerging_candidate)")


def test_card_only_renders_when_evidence_actually_matched():
    assert "if(!t||!t.matched_evidence||!t.matched_evidence.length)return ''" in HTML


def test_mission_control_surfaces_treasury_expansion_separately_from_watchtower():
    mc_html = (ROOT / "templates/ops_shell_index.html").read_text()
    assert "treasury_expansions" in mc_html
    assert "Treasury Review Lead" in mc_html
    assert "Review required" in mc_html
    for banned in ("Possible WATCHTOWER", "Probable WATCHTOWER", "Potential Operation Expansion"):
        assert banned not in mc_html
    # Must appear as its own card, never merged into the confirmed-WATCHTOWER card.
    assert "var expansionCard=" in mc_html
    assert "var watchCard" in mc_html
    assert mc_html.index("var watchCard") != mc_html.index("var expansionCard")
