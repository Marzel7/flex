"""X65.54 — Progressive Discovery Startup.

The X65.54 audit found landing()'s five requests (recent discovery,
attribution-outcomes summary, emerging operators, promotions,
emerging-operator seeds) gated the entire first paint even though the
static shell (operationalIntelligencePanel()'s mounts, roleBrowsePanel())
carries none of their data, and even though the critical-path loaders
(originally loadOperationsPanel, loadWatchtowerCandidateQueue,
loadPipelineHealth, loadOperationalIntelligence) read none of it either --
the dependency was pure code placement, not a real one.

X65.58 follow-up: loadOperationsPanel()/loadWatchtowerCandidateQueue() and
their mounts were removed entirely (redundant with the Operation
Intelligence tab's own Canonical WATCHTOWER/Provisioning Candidates
sections, and rendered outside the tab structure in a way that read as
belonging to whichever tab was open). Only loadPipelineHealth() and
loadOperationalIntelligence() remain as the critical-path loaders.

This restructures landing() into: landingShell() (pure, static, no fetch)
written immediately, the four critical-path loaders started immediately
after, and the five-request batch moved into its own loadLandingIntel()
that populates its own mount independently. The one genuine dependency
chain (loadOperationalIntelligence -> renderTopoLevel ->
updateLaunchTableFilter) is untouched.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


def _landing_shell_body() -> str:
    start = HTML.index("function landingShell")
    return HTML[start:HTML.index("}\n", start) + 1]


def test_landing_shell_is_a_pure_function_with_no_fetch():
    shell = _landing_shell_body()
    assert "fetch(" not in shell
    assert "Promise.all" not in shell
    assert "operationalIntelligencePanel()" in shell
    assert "roleBrowsePanel()" in shell
    assert "loadingHealthPanel()" in shell


def test_landing_writes_shell_before_starting_any_loader():
    # X65.58 follow-up -- loadOperationsPanel()/loadWatchtowerCandidateQueue()
    # were removed entirely (redundant landing-page panels superseded by the
    # Operation Intelligence tab); the remaining critical-path loaders must
    # still start after the shell write.
    landing = _landing_body()
    shell_write_idx = landing.index("$('dw-content').innerHTML=landingShell()")
    for loader in (
        "loadPipelineHealth();", "loadOperationalIntelligence();", "loadLandingIntel();",
    ):
        assert landing.index(loader) > shell_write_idx


def _landing_body() -> str:
    start = HTML.index("function landing(){")
    return HTML[start:HTML.index("function search(){", start)]


def test_landing_does_not_gate_critical_loaders_behind_promise_all():
    landing = _landing_body()
    assert "Promise.all(" not in landing
    assert "fetch(" not in landing


def test_landing_intel_batch_moved_to_its_own_function():
    fn = _function("loadLandingIntel", "landing")
    assert "Promise.all" in fn
    assert "/api/discovery/recent?limit=20" in fn
    assert "/api/ops-v2/attribution-outcomes/summary?window=" in fn
    assert "/api/ops/emerging-operators?limit=50" in fn
    assert "/api/operators/promotions" in fn
    assert "/api/ops-v2/emerging-operator-seeds" in fn
    # Populates its own mount, not the whole dw-content area.
    assert "dw-landing-intel-mount" in fn


def test_landing_intel_mount_exists_in_the_shell_with_a_loading_placeholder():
    shell = _function("landingShell", "loadLandingIntel")
    assert 'id="dw-landing-intel-mount"' in shell
    assert "Loading today" in shell


def test_landing_intel_failure_shows_a_scoped_message_not_a_page_wide_one():
    fn = _function("loadLandingIntel", "landing")
    assert "temporarily unavailable" in fn
    # Must target its own mount, not replace the whole page content.
    assert "$('dw-content').innerHTML=" not in fn


def test_real_dependency_chain_unchanged():
    # loadOperationalIntelligence still triggers renderTopoLevel via its own
    # success callback, which still calls updateLaunchTableFilter -- this
    # chain must be completely untouched by the sequencing change.
    _oi_start = HTML.index("function loadOperationalIntelligence")
    oi_fn = HTML[_oi_start:_oi_start + 2000]
    assert "renderTopoLevel()" in oi_fn
    render_topo = _function("renderTopoLevel", "bindTopoLevelClicks")
    assert "updateLaunchTableFilter()" in render_topo


def test_watchtower_candidate_queue_panel_removed_not_just_deferred():
    # X65.58 follow-up -- the standalone WATCHTOWER Candidate Queue PANEL
    # (previously an "intentional exception" left as a plain, non-warming
    # fetch per X65.54) was removed entirely rather than fixed: it
    # duplicated Provisioning Candidates already shown in the Operation
    # Intelligence tab and rendered outside the tab structure, reading as
    # if it belonged to whichever tab happened to be open.
    #
    # X65.60 note: the /api/ops-v2/watchtower-candidates ENDPOINT is fetched
    # again, but only as a walkback-status ENRICHMENT source merged onto the
    # existing Provisioning Candidates table (loadX65_60WalkbackStatusEnrichment)
    # -- not as a reintroduction of the standalone panel this test guards
    # against. renderWatchtowerCandidateQueue()/loadWatchtowerCandidateQueue()
    # (the removed panel's own functions) must still not exist.
    assert "function renderWatchtowerCandidateQueue" not in HTML
    assert "function loadWatchtowerCandidateQueue" not in HTML


def test_no_new_polling_mechanism_introduced():
    assert "setInterval(" not in HTML


def test_landing_intel_response_composition_unchanged():
    # Same field reads / same event-priority logic / same metrics as before
    # the restructure -- only WHEN this fires changed, not what it computes.
    fn = _function("loadLandingIntel", "landing")
    assert "displayPriority" in fn
    assert "CANONICAL_OPERATOR_REACHED" in fn
    assert "todayMetric(" in fn
    assert "Emerging Operator Candidates" in fn
