from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source():
    return (ROOT / "templates/operation_profile.html").read_text(encoding="utf-8")


def test_profile_has_exactly_five_single_purpose_tabs():
    page = source()
    assert page.count('<section class="rp-panel" data-panel=') == 5
    for tab in ("Summary", "Evidence", "Members", "Analysis", "History"):
        assert f"'{tab}'" in page
    for retired in ("'Overview'", "'Structure'", "'Intelligence'"):
        assert retired not in page


def test_hero_is_the_only_status_reason_and_promotion_presentation():
    page = source()
    hero = page.split('<header class="rp-hero">', 1)[1].split('</header>', 1)[0]
    for field in ("rp-kind", "rp-name", "rp-disposition", "rp-launches", "rp-promotion", "rp-reason"):
        assert field in hero
    assert "Why?" not in page
    assert "Promotion Readiness" not in page
    assert "Current disposition" not in page


def test_evidence_is_compact_and_semantics_are_progressively_disclosed():
    page = source()
    for group in ("Supporting", "Contradictory", "Missing"):
        assert f"disclosure('{group}'" in page
    assert "evidenceRows" in page
    assert "dependency_group" not in page
    assert "provenance_independence" not in page
    assert "rp-table" not in page


def test_valid_actions_and_state_colours_match_registry_language():
    page = source()
    assert "Review Evidence" in page
    assert "Govern Identity" in page
    assert "pres.confirmation_permitted&&disp==='OPERATOR_CANDIDATE'" in page
    assert "Promotion unavailable — additional independent evidence required." in page
    assert ".disp-CONFIRMED_OPERATION{--rp-color:#22c55e}" in page
    assert ".disp-REVIEW{--rp-color:#f97316}" in page
    assert ".disp-INFRASTRUCTURE{--rp-color:#60a5fa}" in page
    assert ".disp-UNRESOLVED{--rp-color:#64748b}" in page


def test_empty_states_and_content_audit_are_present():
    page = source()
    for message in (
        "No launch records are currently available for this Investigation Population.",
        "No walkback evidence.", "No topology reconstructed.",
        "No member identities have been identified yet.",
    ):
        assert message in page
    audit = (ROOT / "docs/audits/x73_6_investigation_profile_content_audit.md").read_text(encoding="utf-8")
    assert "## Before" in audit and "## After" in audit
    assert "reducing its visible explanatory content by more than half" in audit
