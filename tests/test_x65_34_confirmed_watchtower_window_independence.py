from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


# X65.34 — Source Confirmed WATCHTOWER from Canonical Walkback Evidence.
#
# Root cause: is_cascade_confirmed was correctly computed, but the windowed
# cascade population (x65_25WatchtowerRows / X60_UNIVERSE_ROWS, seeded from
# wt_attribution_outcomes within the current DW_WINDOW) had zero overlap with
# the canonical wt_watchtower_launches confirmed set at the default 24h
# window -- so Confirmed WATCHTOWER always rendered "0 launches" even though
# 21+ confirmed launches existed. Fix: fetch the confirmed population once,
# independent of DW_WINDOW (window=all), and reuse it verbatim wherever
# Confirmed WATCHTOWER rows are needed. No new classifier, no schema change,
# no new evidence -- just an unwindowed read of the same existing field.


def test_confirmed_rows_helper_returns_window_independent_state():
    helper = _function("x65_27ConfirmedWatchtowerRows", "x65_27CandidateWatchtowerRows")
    assert "X65_34_CONFIRMED_ROWS" in helper
    # Must not filter the windowed cascade population directly anymore.
    assert "x65_25WatchtowerRows()" not in helper


def test_candidate_rows_remain_window_scoped_and_unchanged():
    helper = _function("x65_27CandidateWatchtowerRows", "x65_27CandidateStatus")
    assert "!r.is_cascade_confirmed" in helper
    assert "x65_25WatchtowerRows()" in helper
    assert "X65_34_CONFIRMED_ROWS" not in helper


def test_loader_fetches_independent_of_current_window():
    loader = _function("loadConfirmedWatchtowerRows", "loadOperationalIntelligence")
    assert "/api/ops-v2/operational-intelligence" in loader
    assert "window" in loader
    assert "'all'" in loader or '"all"' in loader
    assert "r.is_cascade_confirmed" in loader
    assert "X65_34_CONFIRMED_ROWS" in loader
    assert "X65_34_CONFIRMED_LOADED=true" in loader


def test_loader_guards_against_duplicate_inflight_fetch():
    loader = _function("loadConfirmedWatchtowerRows", "loadOperationalIntelligence")
    assert "X65_34_CONFIRMED_FETCH_STARTED" in loader
    assert "if(X65_34_CONFIRMED_FETCH_STARTED)return" in loader.replace(" ", "")


def test_loader_marks_loaded_on_failure_to_avoid_infinite_loading_state():
    loader = _function("loadConfirmedWatchtowerRows", "loadOperationalIntelligence")
    catch_block = loader[loader.index(".catch("):]
    assert "X65_34_CONFIRMED_LOADED=true" in catch_block


def test_loader_is_wired_into_operational_intelligence_load():
    start = HTML.index("function loadOperationalIntelligence")
    end = HTML.index("\n  }", start)
    dispatcher = HTML[start:end]
    assert "loadConfirmedWatchtowerRows()" in dispatcher


def test_population_render_waits_on_confirmed_loaded_flag():
    # X65.58 -- the cold-cache/failure guard (including this flag check)
    # moved from renderKnownWatchtowerPopulation() (now split into
    # renderCanonicalWatchtowerSection()/renderWalkbackCoverageSection())
    # up to the assembly point, renderKnownWatchtowerBlock().
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    assert "X65_34_CONFIRMED_LOADED" in block


def test_confirmed_treasury_section_waits_on_confirmed_loaded_flag():
    section = _function("renderConfirmedWatchtowerTreasury", "renderUnresolvedTreasuryAttribution")
    assert "X65_34_CONFIRMED_LOADED" in section


def test_unresolved_treasury_section_unchanged_still_window_scoped():
    section = _function("renderUnresolvedTreasuryAttribution", "renderKnownWatchtowerFunding")
    assert "if(!X60_UNIVERSE_LOADED||!candidates.length)return ''" in section


def test_topology_and_funding_unchanged_still_use_full_watchtower_population():
    topology = _function("renderKnownWatchtowerTopology", "renderConfirmedWatchtowerTreasury")
    funding = _function("renderKnownWatchtowerFunding", "renderKnownWatchtowerBlock")
    for fn in (topology, funding):
        assert "x65_25WatchtowerRows()" in fn
        assert "X65_34_CONFIRMED_ROWS" not in fn


def test_no_new_classifier_or_schema_touched():
    # Guard against scope creep: this fix must be presentation-only, reusing
    # the existing is_cascade_confirmed field via a differently-windowed
    # fetch of the same existing endpoint -- never a new route or field.
    loader = _function("loadConfirmedWatchtowerRows", "loadOperationalIntelligence")
    assert "/api/ops-v2/operational-intelligence" in loader
    assert "/api/ops-v2/watchtower" not in loader
    assert "new_classifier" not in HTML.lower()


def test_no_programwatcher_or_create_detection_state_touched():
    # Explicit exclusion from the brief: this task must not reach into
    # ProgramWatcher, CREATE detection, migration, or live-arm state.
    loader = _function("loadConfirmedWatchtowerRows", "loadOperationalIntelligence")
    for excluded in ("ProgramWatcher", "FIRED_CREATE", "CANDIDATE_PROTECT", "migration"):
        assert excluded not in loader
