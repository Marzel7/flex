from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


# X65.52 — Non-Blocking Discovery Cold Cache Handling (frontend half).
# Presentation/request-flow only: no detection/classification/evidence
# changes. Discovery-facing fetches opt into the backend's allow_warming=1
# contract (X65.52 backend) and poll every 5-10s on a 202/warming
# response, rendering a distinct "warming" placeholder per section rather
# than an indefinite "Loading…" state or a frozen page.


def test_warming_panel_communicates_availability_not_failure():
    panel_fn = _function("warmingPanel", "fetchWithWarmingPoll")
    assert "Detection intelligence is warming" in panel_fn
    assert "will update automatically" in panel_fn
    # Must not read as an error/failure state.
    assert "unavailable" not in panel_fn.lower()
    assert "error" not in panel_fn.lower()
    assert "failed" not in panel_fn.lower()


def test_fetch_with_warming_poll_never_blocks_on_202():
    fetch_once = _function("_fetchOnceMaybeWarming", "fetchWithWarmingPoll")
    assert "r.status===202" in fetch_once.replace(" ", "")
    poll_fn = _function("fetchWithWarmingPoll", "fetchWithWarmingPollAsync")
    assert "setTimeout(" in poll_fn
    # onData called with (null, true) so the caller can render its own
    # warming placeholder without waiting for the retry to land.
    assert "onData(null,true)" in poll_fn.replace(" ", "")


def test_poll_interval_uses_server_supplied_retry_after_within_5_to_10s_range():
    helper = _function("_fetchOnceMaybeWarming", "fetchWithWarmingPoll")
    assert "retry_after_seconds" in helper
    assert "||8" in helper.replace(" ", "")  # sane default within the 5-10s band if the server omits it


def test_poll_has_a_bounded_retry_cap_not_infinite():
    helper = _function("fetchWithWarmingPoll", "fetchWithWarmingPollAsync")
    assert "_attempt>=40" in helper.replace(" ", "")


def test_operational_intelligence_load_opts_into_allow_warming():
    fn = _function("loadOperationalIntelligence", "triageSummaryCard")
    assert "allow_warming=1" in fn
    assert "fetchWithWarmingPoll(" in fn
    assert "warmingPanel(" in fn


def test_pipeline_health_load_opts_into_allow_warming():
    fn = _function("loadPipelineHealth", "renderPipelineFor")\
        if "function renderPipelineFor" in HTML else _function("loadPipelineHealth", "loadOperationalIntelligence")
    assert "allow_warming=1" in fn
    assert "fetchWithWarmingPoll(" in fn
    assert "warmingPanel(" in fn


def test_launch_universe_fetch_opts_into_allow_warming_async_variant():
    fn = _function("updateLaunchTableFilter", "loadConfirmedWatchtowerRows")
    assert "allow_warming" in fn.replace("'", "").replace('"', "")
    assert "fetchWithWarmingPollAsync(" in fn


def test_canonical_watchtower_and_detection_status_show_warming_not_indefinite_loading():
    creator_identity_fn = _function("x60CreatorIdentityRows", "x60TopologyRows") \
        if "function x60CreatorIdentityRows" in HTML else None
    # X65.58 -- the shared cold-cache guard (and its warmingPanel(...) call)
    # moved from being duplicated inside renderWatchtowerDetectionStatus()
    # AND renderKnownWatchtowerPopulation() individually, to living once in
    # the assembly point, renderKnownWatchtowerBlock() (which now calls the
    # split renderCanonicalWatchtowerSection()/renderWalkbackCoverageSection()
    # only after confirming the fetch succeeded).
    detection_status_fn = _function("renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection")
    block_fn = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    assert "warmingPanel(" in block_fn
    # The OLD indefinite "Loading…" placeholders for these two sections
    # must be gone -- replaced by the distinct warming state.
    assert "Loading detection status" not in detection_status_fn
    assert "Loading launch universe" not in block_fn


def test_each_section_loads_independently_not_gated_on_a_single_master_flag():
    # Detection Status and Canonical WATCHTOWER both gate on
    # X60_UNIVERSE_LOADED/X65_34_CONFIRMED_LOADED, but the warming poll for
    # each backing fetch is independent (separate fetchWithWarmingPoll/
    # fetchWithWarmingPollAsync calls, separate URLs) -- a slow
    # investigation-pipeline warm must not block operational-intelligence
    # from rendering, and vice versa.
    oi_fn = _function("loadOperationalIntelligence", "triageSummaryCard")
    pipeline_fn = _function("loadPipelineHealth", "x60CreatorIdentityRows") \
        if "function x60CreatorIdentityRows" in HTML else _function("loadPipelineHealth", "loadOperationalIntelligence")
    assert "operational-intelligence" in oi_fn
    assert "investigation-pipeline" in pipeline_fn
    assert oi_fn != pipeline_fn


def test_no_new_polling_introduced_beyond_the_warming_retry_mechanism():
    # No setInterval anywhere -- polling must only happen via the bounded,
    # single-in-flight-retry warming mechanism, never an independent timer.
    assert "setInterval(" not in HTML


def test_warming_response_shape_matches_backend_contract():
    helper = _function("_fetchOnceMaybeWarming", "fetchWithWarmingPoll")
    assert "warming:true" in helper.replace(" ", "")
    assert "warming:false" in helper.replace(" ", "")
