from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


# X65.40 — Restore Walkback-Time Semantics for WATCHTOWER Detection.
#
# Supersedes X65.39's x65_39WindowedConfirmedRows(), which windowed the
# canonical confirmed cohort by launch create_time -- answering "when was
# this launched," not "what did walkback confirm during this period."
# WATCHTOWER detection/confirmation authority now lives in the walkback
# pipeline, not the CREATE transaction, so the selected-window metric must
# use wt_attribution_outcomes.completed_at (exposed per-record as
# confirmation_completed_at by operational_intelligence.py, X65.40) --
# never launch create_time, migration time, or ProgramWatcher/CREATE-
# detection state. mint is wt_attribution_outcomes' PRIMARY KEY (schema-
# enforced), so completed_at is a single, non-retriable timestamp per mint
# -- reprocessing the same mint cannot produce a second, later
# confirmation to double-count.


def test_walkback_confirmed_helper_filters_by_confirmation_completed_at():
    helper = _function("x65_40WalkbackConfirmedRowsForWindow", "x65_27CandidateWatchtowerRows")
    assert "X65_34_CONFIRMED_ROWS" in helper
    assert "dwWindowSeconds()" in helper
    assert "r.confirmation_completed_at" in helper


def test_walkback_confirmed_helper_never_uses_launch_create_time():
    helper = _function("x65_40WalkbackConfirmedRowsForWindow", "x65_27CandidateWatchtowerRows")
    assert "create_at" not in helper
    assert "create_time" not in helper


def test_walkback_confirmed_helper_never_uses_migration_or_programwatcher_state():
    helper = _function("x65_40WalkbackConfirmedRowsForWindow", "x65_27CandidateWatchtowerRows")
    for excluded in ("migration_at", "migration_time", "ProgramWatcher", "FIRED_CREATE", "CANDIDATE_PROTECT"):
        assert excluded not in helper


def test_walkback_confirmed_helper_never_mutates_or_replaces_canonical_rows():
    helper = _function("x65_40WalkbackConfirmedRowsForWindow", "x65_27CandidateWatchtowerRows")
    assert "X65_34_CONFIRMED_ROWS=" not in helper  # no reassignment, filter() only
    assert ".filter(" in helper


def test_walkback_confirmed_helper_excludes_rows_with_no_resolvable_timestamp():
    # A row missing confirmation_completed_at must be excluded from every
    # window, never defaulted into it (same discipline as the backend's
    # launch_create_times_for_mints -- absence means "no evidence," not
    # "confirmed now").
    helper = _function("x65_40WalkbackConfirmedRowsForWindow", "x65_27CandidateWatchtowerRows")
    assert "typeof r.confirmation_completed_at" in helper
    assert "'number'" in helper


def test_canonical_helper_still_returns_full_cohort_unfiltered():
    start = HTML.index("function x65_27ConfirmedWatchtowerRows")
    end = HTML.index("\n  }", start)
    body = HTML[start:end + len("\n  }")]
    assert body.strip() == (
        "function x65_27ConfirmedWatchtowerRows(){\n"
        "    return X65_34_CONFIRMED_ROWS;\n"
        "  }"
    )


# X65.42 -- renderKnownWatchtowerPopulation no longer shows the windowed
# walkback-confirmation count itself (moved to the new operational
# renderWatchtowerDetectionStatus() panel, tested in
# test_x65_42_detection_status_and_window_simplification.py); it now shows
# ONLY the canonical (all-time) cohort + walkback coverage. The tests below
# are updated to assert against the new home of this logic.


def test_walkback_confirmed_used_in_detection_status_panel():
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    assert "x65_40WalkbackConfirmedRowsForWindow()" in panel


def test_old_x65_39_create_time_helper_no_longer_used():
    # X65.58 -- renderKnownWatchtowerPopulation() split into two functions.
    canonical = _function("renderCanonicalWatchtowerSection", "renderWalkbackCoverageSection")
    coverage = _function("renderWalkbackCoverageSection", "renderKnownWatchtowerBlock")
    assert "x65_39WindowedConfirmedRows" not in canonical
    assert "x65_39WindowedConfirmedRows" not in coverage


def test_detection_status_panel_shows_confirmed_and_candidate_counts():
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    assert "walkbackConfirmed.length" in panel
    assert "candidates.length" in panel
    assert "dwWindowLabel()" in panel


def test_detection_status_load_failure_shows_explicit_failure_state_not_a_false_zero():
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    assert "X65_34_CONFIRMED_FAILED" in panel
    assert "Unable to load confirmed population" in panel
    failure_pos = panel.index("X65_34_CONFIRMED_FAILED")
    badge_pos = panel.index("walkbackConfirmed.length")
    assert failure_pos < badge_pos


def test_treasury_and_topology_summaries_still_use_canonical_not_windowed():
    treasury = _function("renderConfirmedWatchtowerTreasury", "renderUnresolvedTreasuryAttribution")
    topology = _function("renderKnownWatchtowerTopology", "renderConfirmedWatchtowerTreasury")
    for fn in (treasury, topology):
        assert "x65_40WalkbackConfirmedRowsForWindow" not in fn


def test_candidate_rows_remain_window_scoped_unaffected():
    helper = _function("x65_27CandidateWatchtowerRows", "x65_27CandidateStatus")
    assert "!r.is_cascade_confirmed" in helper
    assert "x65_25WatchtowerRows()" in helper


def test_canonical_fetch_still_requests_window_all_unconditionally():
    loader = _function("loadConfirmedWatchtowerRows", "loadOperationalIntelligence")
    assert "window:'all'" in loader.replace(" ", "")
