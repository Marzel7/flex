"""X65.53A — Remove the final blocking `all` request from the default
Discovery workflow.

loadConfirmedWatchtowerRows() (X65.34/X65.35) always fetches window=all --
correctly, since confirmed WATCHTOWER evidence is window-independent -- but
did so via a plain blocking fetch(), unlike every sibling call in this file
(updateLaunchTableFilter, loadOperationalIntelligence's topology fetch,
loadPipelineHealth), which all opt into the X65.52 non-blocking warming
contract. On a cold all-window cache this call could block the DEFAULT
24h landing page for the full all-window build duration (measured ~173s
in the X65.53 profiling pass).

This fix keeps window=all and the returned data identical -- only the
request mechanics change: allow_warming=1 + fetchWithWarmingPollAsync()
instead of a direct fetch(), reusing the same warmingPanel() UI the
sibling sections already show while their own cache is cold.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


def _confirmed_fn() -> str:
    # X65.58 follow-up -- loadOperationsPanel()/operationsPanel() were
    # removed (redundant landing-page panel superseded by the Operation
    # Intelligence tab); roleBrowsePanel() is now the next function after
    # loadConfirmedWatchtowerRows().
    return _function("loadConfirmedWatchtowerRows", "roleBrowsePanel")


def test_window_all_is_preserved():
    fn = _confirmed_fn()
    assert "window:'all'" in fn.replace(" ", "")


def test_include_records_and_cascade_confirmed_filters_preserved():
    fn = _confirmed_fn()
    compact = fn.replace(" ", "")
    assert "include_records:'1'" in compact
    assert "is_cascade_confirmed:'1'" in compact


def test_opts_into_allow_warming():
    fn = _confirmed_fn()
    assert "allow_warming:'1'" in fn.replace(" ", "")


def test_uses_warming_poll_async_not_a_bare_fetch():
    fn = _confirmed_fn()
    assert "fetchWithWarmingPollAsync(" in fn
    # No direct fetch(...) call inside this function anymore -- the
    # only fetch happens inside fetchWithWarmingPollAsync/_fetchOnceMaybeWarming.
    assert "fetch('/api/ops-v2/operational-intelligence?'+params.toString())" not in fn


def test_warming_tick_repaints_via_render_x58_mounts():
    fn = _confirmed_fn()
    assert "fetchWithWarmingPollAsync(url,function(){renderX58Mounts()})" in fn.replace(" ", "")


def test_resolved_data_still_populates_confirmed_rows_and_loaded_flag():
    fn = _confirmed_fn()
    assert "X65_34_CONFIRMED_ROWS=(d.launches||[])" in fn.replace(" ", "")
    assert "X65_34_CONFIRMED_LOADED=true" in fn.replace(" ", "")
    assert "is_cascade_confirmed" in fn


def test_failure_path_unchanged():
    fn = _confirmed_fn()
    assert "X65_34_CONFIRMED_FAILED=true" in fn.replace(" ", "")
    assert "X65_34_CONFIRMED_ROWS=[]" in fn.replace(" ", "")


def test_duplicate_fetch_guard_still_present():
    fn = _confirmed_fn()
    assert "X65_34_CONFIRMED_FETCH_STARTED" in fn


def test_existing_warming_panel_reused_by_sibling_renderers_not_reimplemented():
    # X65.58 -- the section's own placeholder comes from
    # renderKnownWatchtowerBlock() (the assembly point, gating on
    # !X60_UNIVERSE_LOADED||!X65_34_CONFIRMED_LOADED -> warmingPanel(...));
    # this function itself must not define a new/duplicate placeholder
    # string.
    fn = _confirmed_fn()
    assert "=warmingPanel(" not in fn.replace(" ", "")
    assert "return warmingPanel(" not in fn
    block_fn = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    assert "warmingPanel(" in block_fn
