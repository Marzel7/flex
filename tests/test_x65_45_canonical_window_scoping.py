from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


# X65.45 — Canonical WATCHTOWER and Walkback Evidence Coverage now scope
# to the selected Discovery window.
#
# This EXPLICITLY REVERSES the window-independence design established in
# X65.42/X65.43 earlier in this same session: those tasks made Canonical
# WATCHTOWER (43, all-time) and Walkback Evidence Coverage (22/43,
# all-time) deliberately ignore the 24h/All selector, on the reasoning
# that they represent "what the system already knows," not a windowed
# filter. Per explicit follow-up instruction, both the summary badges AND
# the address table beneath them now filter to launches whose resolved
# launch create_at falls within the selected window -- consistent with
# how the rest of Discovery (Topology/Behaviour, X65.35b) windows its
# population.
#
# x65_27ConfirmedWatchtowerRows() (the raw, unfiltered, all-time cohort)
# is UNCHANGED and still exists -- it's still used by the Detection
# Status panel's description text, which must keep citing the true
# all-time registry total regardless of what the Canonical WATCHTOWER
# section itself currently displays.


def _windowed_helper_body():
    start = HTML.index("function x65_45CanonicalRowsForWindow")
    end = HTML.index("\n  }", start)
    return HTML[start:end + len("\n  }")]


def test_new_windowed_helper_filters_by_create_at_not_confirmation_completed_at():
    helper = _windowed_helper_body()
    assert "X65_34_CONFIRMED_ROWS" in helper
    assert "dwWindowSeconds()" in helper
    assert "r.create_at" in helper
    assert "confirmation_completed_at" not in helper


def test_windowed_helper_excludes_rows_with_no_resolvable_create_at():
    helper = _windowed_helper_body()
    assert "typeof r.create_at" in helper
    assert "'number'" in helper


def test_windowed_helper_only_filters_never_mutates_canonical_rows():
    helper = _windowed_helper_body()
    assert "X65_34_CONFIRMED_ROWS=" not in helper
    assert ".filter(" in helper


def test_raw_canonical_helper_still_exists_unfiltered():
    # x65_27ConfirmedWatchtowerRows() must remain the raw, all-time,
    # unconditional cohort -- still needed by the Detection Status panel's
    # description text.
    start = HTML.index("function x65_27ConfirmedWatchtowerRows")
    end = HTML.index("\n  }", start)
    body = HTML[start:end + len("\n  }")]
    assert body.strip() == (
        "function x65_27ConfirmedWatchtowerRows(){\n"
        "    return X65_34_CONFIRMED_ROWS;\n"
        "  }"
    )


def test_population_section_uses_windowed_helper_for_display():
    # X65.58 -- renderKnownWatchtowerPopulation() was split into
    # renderCanonicalWatchtowerSection()/renderWalkbackCoverageSection();
    # the `confirmed` var (computed via x65_45CanonicalRowsForWindow()) is
    # now computed once in renderKnownWatchtowerBlock() and passed to both.
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    assert "x65_45CanonicalRowsForWindow()" in block
    canonical = _function("renderCanonicalWatchtowerSection", "renderWalkbackCoverageSection")
    assert "x65_27ConfirmedWatchtowerRows()" not in canonical


def test_canonical_copy_mentions_selected_window_not_all_time():
    canonical = _function("renderCanonicalWatchtowerSection", "renderWalkbackCoverageSection")
    canonical_card_start = canonical.index("Authoritative WATCHTOWER registry")
    canonical_card = canonical[canonical_card_start:canonical_card_start + 300]
    assert "selected" in canonical_card
    assert "dwWindowLabel()" in canonical_card
    assert "all-time, window-independent" not in canonical_card


def test_coverage_copy_references_the_windowed_set_not_all_time():
    coverage = _function("renderWalkbackCoverageSection", "renderKnownWatchtowerBlock")
    coverage_card_start = coverage.index("Walkback Evidence Coverage")
    coverage_card = coverage[coverage_card_start:coverage_card_start + 600]
    assert "selected" in coverage_card
    assert "does not change with the selected Discovery window" not in coverage_card


def test_walkback_coverage_computed_from_windowed_canonical_set():
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    assert "var confirmed=x65_45CanonicalRowsForWindow();" in block
    coverage = _function("renderWalkbackCoverageSection", "renderKnownWatchtowerBlock")
    assert "walkbackCoverage=confirmed.filter(" in coverage


def test_detection_status_description_cites_true_all_time_total_directly():
    # Since Canonical WATCHTOWER below no longer always shows the all-time
    # count, the Detection Status panel's own description must cite the
    # true all-time registry total directly (from the raw, unfiltered
    # X65_34_CONFIRMED_ROWS) rather than pointing at "Canonical WATCHTOWER
    # below" as if that section always shows it.
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    assert "X65_34_CONFIRMED_ROWS.length.toLocaleString()" in panel
    assert "launches total, across all time" in panel


def test_candidates_remain_window_scoped_unaffected():
    helper = _function("x65_27CandidateWatchtowerRows", "x65_27CandidateStatus")
    assert "!r.is_cascade_confirmed" in helper
    assert "x65_25WatchtowerRows()" in helper


def test_no_programwatcher_or_migration_dependency_introduced():
    helper = _function("x65_45CanonicalRowsForWindow", "x65_27CandidateWatchtowerRows")
    for excluded in ("ProgramWatcher", "FIRED_CREATE", "CANDIDATE_PROTECT", "migration_at"):
        assert excluded not in helper


def test_load_failure_still_shows_explicit_failure_state_not_a_false_zero():
    # X65.58 -- this guard moved to the assembly point,
    # renderKnownWatchtowerBlock(), since renderCanonicalWatchtowerSection()/
    # renderWalkbackCoverageSection() no longer have their own guards.
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    assert "X65_34_CONFIRMED_FAILED" in block
    assert "Unable to load confirmed population" in block
