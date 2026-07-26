from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


# X65.43 — Clarify "Walkback Confirmed" vs "Canonical WATCHTOWER".
#
# The Detection Status panel's first metric was labelled "Confirmed
# WATCHTOWER" -- which reads as "is WATCHTOWER" (membership) to a
# first-time reader, when it actually measures NEW walkback-pipeline
# confirmations landing within the selected window. A 0 there does not
# mean "there are no WATCHTOWER launches" -- the canonical registry count
# (43, all-time, X65.41/X65.42) is entirely separate and never moves with
# this number. Renamed to "New Walkback Confirmations" with an explicit
# description distinguishing the two. Wording-only: no logic, registry
# source, attribution, candidate, topology, treasury, or behaviour change.


def test_old_ambiguous_label_removed():
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    assert "'Confirmed WATCHTOWER'" not in panel


def test_new_label_communicates_walkback_provenance():
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    assert "New Walkback Confirmations" in panel


def test_description_explicitly_disclaims_membership_definition():
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    assert "does not define WATCHTOWER membership" in panel
    assert "independently reconstructed and confirmed by the walkback attribution pipeline" in panel


def test_description_points_to_canonical_count_as_the_membership_source():
    # X65.45 -- Canonical WATCHTOWER below is no longer always all-time
    # (it now scopes to the selected window too), so the Detection Status
    # description was reworded to cite the true all-time registry total
    # directly rather than pointing at "Canonical WATCHTOWER below" (which
    # may show a smaller, windowed number).
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    # The canonical count referenced in the description must be the live,
    # already-fetched all-time cohort length -- never a hardcoded number
    # that could silently drift from the real registry size.
    assert "X65_34_CONFIRMED_ROWS.length.toLocaleString()+' launches total, across all time" in panel
    assert ", always 43 launches" not in panel


def test_description_names_the_selected_window():
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    assert panel.count("dwWindowLabel()") >= 2  # heading copy + description copy


def test_walkback_confirmed_helper_and_metric_source_unchanged():
    # Wording-only task -- the underlying computation must be untouched.
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    assert "x65_40WalkbackConfirmedRowsForWindow()" in panel
    assert "walkbackConfirmed.length" in panel


def test_canonical_and_coverage_sections_unaffected():
    # X65.45 changed these sections' data source (now window-scoped via
    # x65_45CanonicalRowsForWindow()) but their headings/labels are
    # unaffected by THIS task (X65.43, wording-only). X65.58 split the
    # single renderKnownWatchtowerPopulation() into two functions; check
    # each independently.
    canonical = _function("renderCanonicalWatchtowerSection", "renderWalkbackCoverageSection")
    coverage = _function("renderWalkbackCoverageSection", "renderKnownWatchtowerBlock")
    assert "Canonical WATCHTOWER" in canonical
    assert "Walkback Evidence Coverage" in coverage


def test_no_attribution_registry_or_classification_logic_touched():
    panel = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    canonical = _function("renderCanonicalWatchtowerSection", "renderWalkbackCoverageSection")
    coverage = _function("renderWalkbackCoverageSection", "renderKnownWatchtowerBlock")
    for fn in (panel, canonical, coverage):
        assert "classify_topology_for_launch" not in fn
        assert "build_campaign_classification" not in fn
        assert "SELECT" not in fn  # no new direct SQL/table query in JS
