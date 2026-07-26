from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


# X65.42 — Simplify Discovery Time Windows & Add Operational Detection
# Status.
#
# Two changes, both presentation-only (no attribution/classification/
# schema change):
#
# 1. Discovery's window selector is narrowed from 24h/7d/30d/all to just
#    24h/all -- an operational view ("what's happening today") and a
#    reference view ("what do we know about WATCHTOWER, all-time").
#    src/ops/discovery_window.py's WINDOW_ORDER/_WINDOW_SECONDS are
#    UNCHANGED (7d/30d still exist server-side for any other caller); only
#    the Discovery UI's own DW_WINDOWS array is narrowed.
#
# 2. The old "WATCHTOWER confirmed by walkback" card (which conflated one
#    pipeline metric with the whole page) is replaced by a factual
#    operational panel (renderWatchtowerDetectionStatus) with no invented
#    Healthy/Warning heuristic -- there is no well-defined operational
#    health model yet, and a fabricated status risks false alarms.
#    Canonical WATCHTOWER (all-time, window-independent) and Walkback
#    Evidence Coverage (informational, also window-independent) get their
#    own separate cards below it.


def test_window_selector_has_exactly_two_options():
    start = HTML.index("var DW_WINDOWS=")
    end = HTML.index(";", start)
    line = HTML[start:end]
    assert "24h" in line
    assert "all" in line
    assert "7d" not in line
    assert "30d" not in line


def test_backend_window_module_unchanged_still_supports_all_four():
    # X65.42 is a UI-only narrowing -- discovery_window.py's own
    # WINDOW_ORDER must still define all four values for any other caller.
    src = (ROOT / "src" / "ops" / "discovery_window.py").read_text()
    assert 'WINDOW_24H = "24h"' in src
    assert 'WINDOW_7D = "7d"' in src
    assert 'WINDOW_30D = "30d"' in src
    assert 'WINDOW_ALL = "all"' in src


def test_detection_status_panel_exists_and_is_purely_factual():
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    assert "WATCHTOWER Detection Status" in panel
    # No invented health heuristic -- explicitly rejected per this task's
    # own clarification.
    for excluded in ("Healthy", "Warning", "pipeline_status", "Pipeline status"):
        assert excluded not in panel


def test_detection_status_panel_no_new_backend_query():
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    assert "fetch(" not in panel


def test_detection_status_panel_no_walkback_processed_metric():
    # Explicitly rejected: a generic wt_attribution_outcomes-wide "processed"
    # count would dilute the WATCHTOWER-specific signal by mixing in
    # unrelated (non-WATCHTOWER) attribution throughput.
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    assert "Walkback processed" not in panel
    assert "walkback_processed" not in panel


def test_detection_status_panel_shows_latest_confirmation_and_candidate_timestamps():
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    assert "Latest WATCHTOWER confirmation" in panel
    assert "Latest candidate" in panel
    assert "confirmation_completed_at" in panel
    assert "r.create_at" in panel


def test_detection_status_panel_no_programwatcher_or_create_detection_dependency():
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    for excluded in ("ProgramWatcher", "FIRED_CREATE", "CANDIDATE_PROTECT"):
        assert excluded not in panel


def test_detection_status_wired_into_dispatcher_first():
    # X65.58 -- renderKnownWatchtowerPopulation() was split into
    # renderCanonicalWatchtowerSection()/renderWalkbackCoverageSection();
    # renderKnownWatchtowerBlock() is now the assembly point that calls
    # renderWatchtowerDetectionStatus() before either of them.
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    detection_pos = block.index("return renderWatchtowerDetectionStatus()")
    canonical_pos = block.index("renderCanonicalWatchtowerSection(confirmed)")
    assert detection_pos < canonical_pos


def test_canonical_and_coverage_are_separate_cards():
    # X65.58 -- these are now two independently-callable functions rather
    # than one function producing both cards; still verify Canonical
    # WATCHTOWER precedes Walkback Coverage in the assembled block.
    canonical = _function("renderCanonicalWatchtowerSection", "renderWalkbackCoverageSection")
    coverage = _function("renderWalkbackCoverageSection", "renderCandidateQueueTable") \
        if "function renderCandidateQueueTable" in HTML else _function("renderWalkbackCoverageSection", "renderKnownWatchtowerBlock")
    assert "Canonical WATCHTOWER" in canonical
    assert "Walkback Evidence Coverage" in coverage
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    canonical_call_pos = block.index("renderCanonicalWatchtowerSection(confirmed)")
    coverage_call_pos = block.index("renderWalkbackCoverageSection(confirmed)")
    assert canonical_call_pos < coverage_call_pos


# X65.45 -- superseded: Canonical WATCHTOWER (and Walkback Coverage) are no
# longer window-independent by design; they now scope to the selected
# Discovery window (test_x65_45_canonical_window_scoping.py). This test is
# retained under its historical name but updated to assert the NEW
# behaviour, so the test file's own narrative stays traceable across the
# X65.42 -> X65.45 semantics change.
def test_canonical_count_now_scopes_to_the_selected_window():
    canonical = _function("renderCanonicalWatchtowerSection", "renderWalkbackCoverageSection")
    assert "selected" in canonical
    assert "dwWindowLabel()" in canonical
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    assert "x65_45CanonicalRowsForWindow()" in block


def test_walkback_coverage_is_a_fraction_of_canonical_not_a_new_fetch():
    coverage = _function("renderWalkbackCoverageSection", "x65_60TreasuryProgress")
    assert "confirmed.filter(" in coverage
    assert "confirmation_completed_at" in coverage
    assert "fetch(" not in coverage
    assert "does not define WATCHTOWER membership" in coverage


# X65.45 -- superseded: Walkback Coverage now DOES vary with the selected
# window (it's a fraction of x65_45CanonicalRowsForWindow(), which is
# itself windowed). Retained under its historical name with updated
# assertions so the file's narrative stays traceable across the semantics
# change; see test_x65_45_canonical_window_scoping.py for full coverage.
def test_walkback_coverage_is_a_fraction_of_the_windowed_canonical_set():
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    assert "x65_45CanonicalRowsForWindow()" in block
    coverage = _function("renderWalkbackCoverageSection", "renderKnownWatchtowerBlock")
    assert "walkbackCoverage=confirmed.filter(" in coverage


def test_candidates_remain_window_scoped():
    helper = _function("x65_27CandidateWatchtowerRows", "x65_27CandidateStatus")
    assert "!r.is_cascade_confirmed" in helper
    assert "x65_25WatchtowerRows()" in helper  # window-scoped by DW_WINDOW


def test_no_attribution_or_classification_logic_touched():
    # This task is presentation-only -- guard against scope creep into the
    # actual classifiers/registry source.
    canonical = _function("renderCanonicalWatchtowerSection", "renderWalkbackCoverageSection")
    coverage = _function("renderWalkbackCoverageSection", "renderKnownWatchtowerBlock")
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    for fn in (canonical, coverage, panel):
        assert "classify_topology_for_launch" not in fn
        assert "build_campaign_classification" not in fn
