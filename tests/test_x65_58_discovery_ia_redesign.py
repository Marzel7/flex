"""X65.58 — Discovery IA Redesign.

Splits Discovery's single mixed scroll into two tabs answering one
question each: "Operation Intelligence" (what do we know about the
currently selected operation?) and "Ecosystem Intelligence" (what else
exists outside it?). Presentation/IA-only — reuses every existing render
function, backend endpoint, and data source verbatim (see the section
mapping this task's design document produced).

Per explicit review feedback (four refinements applied before
implementation):
  1. Tabs are generically named ("Operation Intelligence" / "Ecosystem
     Intelligence") — never hard-coded to "WATCHTOWER Intelligence".
  2. Provisioning Candidates ranks ABOVE Walkback Coverage/Topology
     (actionable content before explanatory content).
  3. Treasury Intelligence is collapsed by default (<details>).
  4. The launch table is renamed "Matching Launches" (was "Launch Results").
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


def _function_body(name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index("\n  }", start) + len("\n  }")]


# ─────────────────────────── Operation selector ───────────────────────────

def test_operation_selector_exists_alongside_window_selector():
    assert 'id="dw-operation-select"' in HTML
    assert 'id="dw-window-select"' in HTML
    # Selector row order: window first, operation second.
    assert HTML.index('id="dw-window-select"') < HTML.index('id="dw-operation-select"')


def test_operation_selector_state_mirrors_window_selector_pattern():
    fn = _function("renderOperationSelector", "setOperation")
    assert "DW_OPERATIONS" in fn
    assert "DW_OPERATION" in fn
    set_op_fn = _function("setOperation", "renderTabBar" if "function renderTabBar" in HTML else "dwWindowLabel")
    assert "history.replaceState" in set_op_fn
    assert "landing()" in set_op_fn


def test_only_watchtower_exists_today_but_array_is_generic():
    start = HTML.index("var DW_OPERATIONS=")
    end = HTML.index(";", start)
    line = HTML[start:end]
    assert "WATCHTOWER" in line
    # Exactly one entry today -- not hard-coded elsewhere as the only
    # possible value (the array itself is the single source of truth).
    assert line.count("{v:") == 1


def test_operation_param_read_from_url_with_watchtower_default():
    start = HTML.index("var DW_OPERATION=")
    end = HTML.index(";", start)
    line = HTML[start:end]
    assert "get('operation')" in line
    assert "WATCHTOWER" in line


# ─────────────────────────────── Tab bar ───────────────────────────────

def test_tab_bar_uses_generic_labels_never_operation_specific():
    start = HTML.index("var DW_TABS=")
    end = HTML.index(";", start)
    line = HTML[start:end]
    assert "Operation Intelligence" in line
    assert "Ecosystem Intelligence" in line
    # The explicit reviewer correction: the tab bar's own generated LABELS
    # (DW_TABS array + the rendered button text) must never literally be
    # "WATCHTOWER Intelligence" -- doc comments explaining the decision may
    # still mention the phrase as a negative example.
    tab_bar_fn = _function("renderTabBar", "setTab")
    assert "'WATCHTOWER Intelligence'" not in tab_bar_fn
    assert '"WATCHTOWER Intelligence"' not in tab_bar_fn


def test_tab_panels_exist_and_toggle_via_hidden_attribute():
    panel_fn = _function("operationalIntelligencePanel", "loadOperationalIntelligence")
    assert 'id="dw-tab-panel-operation"' in panel_fn
    assert 'id="dw-tab-panel-ecosystem"' in panel_fn
    visibility_fn = _function_body("applyTabVisibility")
    assert "opPanel.hidden" in visibility_fn
    assert "ecoPanel.hidden" in visibility_fn


def test_tab_switch_does_not_gate_any_fetch():
    # X65.58's own design constraint: switching tabs must never trigger a
    # new cold fetch -- both tabs' mounts already populate unconditionally
    # on page load (see X65.54's progressive-startup loaders).
    tab_fns = [
        _function("setTab", "applyTabVisibility"),
        _function_body("applyTabVisibility"),
    ]
    for fn in tab_fns:
        assert "fetch(" not in fn


def test_tab_state_is_a_third_independent_url_param():
    # Window / Operation / Tab are three independent state dimensions, each
    # with its own URL param, per the design's explicit state-handling
    # section.
    assert "params.set('window'" in HTML
    assert "params.set('operation'" in HTML
    assert "params.set('tab'" in HTML


# ─────────────────── Operation Intelligence tab content/order ───────────────────

def test_operation_tab_header_is_data_driven_not_hardcoded():
    panel_fn = _function("operationalIntelligencePanel", "loadOperationalIntelligence")
    assert "Operation Intelligence" in panel_fn
    assert "esc(DW_OPERATION)" in panel_fn
    # Old hard-coded title must be gone.
    assert "Discovery Cohort Report" not in panel_fn


def test_provisioning_candidates_ranked_above_walkback_coverage_and_topology():
    # Reviewer's explicit workflow ordering: what exists -> what looks like
    # it (actionable) -> confirmed? (explanatory) -> why? (reference).
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    candidates_pos = block.index("renderWatchtowerProvisioningCandidates(candidates)")
    coverage_pos = block.index("renderWalkbackCoverageSection(confirmed)")
    topology_pos = block.index("renderKnownWatchtowerTopology()")
    assert candidates_pos < coverage_pos
    assert candidates_pos < topology_pos


def test_canonical_watchtower_ranked_before_provisioning_candidates():
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    canonical_pos = block.index("renderCanonicalWatchtowerSection(confirmed)")
    candidates_pos = block.index("renderWatchtowerProvisioningCandidates(candidates)")
    assert canonical_pos < candidates_pos


def test_treasury_intelligence_is_collapsed_by_default():
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    assert "<details class=\"dw-treasury-intel-details\">" in block
    assert "<summary class=\"dw-treasury-intel-summary\">Treasury Intelligence</summary>" in block
    # Native <details> with no `open` attribute = collapsed by default.
    assert "<details class=\"dw-treasury-intel-details\" open>" not in block


def test_treasury_intelligence_groups_all_three_existing_sections():
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    treasury_var_pos = block.index("var treasuryIntel=")
    treasury_var_line = block[treasury_var_pos:block.index(";", treasury_var_pos)]
    assert "renderConfirmedWatchtowerTreasury()" in treasury_var_line
    assert "renderUnresolvedTreasuryAttribution()" in treasury_var_line
    assert "renderKnownWatchtowerFunding()" in treasury_var_line


def test_topology_is_compacted_not_a_full_card():
    fn = _function("renderKnownWatchtowerTopology", "renderUnresolvedTreasuryAttribution") \
        if "function renderUnresolvedTreasuryAttribution" in HTML else _function("renderKnownWatchtowerTopology", "renderConfirmedWatchtowerTreasury")
    assert "dw-op-topology-compact" in fn
    # The old full-card badge/heading-numbered treatment must be gone from
    # THIS function specifically (renderCanonicalWatchtowerTopology, used by
    # the Ecosystem tab's Stage 2, is untouched and may still contain it).
    assert "✓ Canonical Operational Topology" not in fn
    assert "2. Topology" not in fn


def test_topology_explanation_preserved_as_tooltip_not_removed():
    fn = _function("renderKnownWatchtowerTopology", "renderUnresolvedTreasuryAttribution") \
        if "function renderUnresolvedTreasuryAttribution" in HTML else _function("renderKnownWatchtowerTopology", "renderConfirmedWatchtowerTreasury")
    assert "title=" in fn
    assert "42/42" in fn  # the original citation, preserved not deleted


def test_canonical_watchtower_topology_removed_from_ecosystem_entirely():
    # X65.58A supersedes X65.58's original design: renderTopologyDistribution()
    # (Ecosystem tab Stage 2) used to fall back to renderCanonicalWatchtowerTopology()
    # (the full WATCHTOWER operational diagram) for a cascade-confirmed
    # cohort -- falsely implying the explored cohort follows the WATCHTOWER
    # operational model. renderCanonicalWatchtowerTopology() was REMOVED
    # entirely; Ecosystem's Stage 2 now always renders the standard
    # Multi-Level Fan-Out/Fan-Out/Linear/Unknown distribution, regardless of
    # confirmation status.
    assert "function renderCanonicalWatchtowerTopology" not in HTML
    dist_fn = _function("renderTopologyDistribution", "renderTopologyDistributionRows")
    # No live call to the removed function -- "return renderCanonicalWatchtowerTopology()"
    # was the actual call site; a doc-comment mentioning the removed
    # function's name (explaining WHY it was removed) may still appear.
    assert "return renderCanonicalWatchtowerTopology()" not in dist_fn
    # No live branching on confirmation status either (also removed) --
    # check the actual code body, not any explanatory comment.
    code_only = "\n".join(
        line for line in dist_fn.splitlines() if not line.strip().startswith("//")
    )
    assert "is_cascade_confirmed" not in code_only


def test_explore_remaining_population_divider_removed():
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    assert "Explore Remaining Population" not in block
    assert "dw-x65-25-divider" not in block


# ─────────────────────── Ecosystem Intelligence tab ───────────────────────

def test_ecosystem_tab_contains_all_six_stages():
    panel_fn = _function("operationalIntelligencePanel", "loadOperationalIntelligence")
    eco_start = panel_fn.index('id="dw-tab-panel-ecosystem"')
    eco_panel = panel_fn[eco_start:]
    for mount in (
        "dw-x65-stage-nav-mount", "dw-x58-selection-mount",
        "dw-x64-creator-identity-mount", "dw-x58-topology-mount",
        "dw-x65-7-campaign-mount", "dw-topo-infra-mount",
        "dw-x65-1-treasury-resolution-mount", "dw-x58-attribution-mount",
        "dw-topo-level-mount",
    ):
        assert mount in eco_panel


def test_ecosystem_tab_does_not_contain_operation_only_mounts():
    panel_fn = _function("operationalIntelligencePanel", "loadOperationalIntelligence")
    op_start = panel_fn.index('id="dw-tab-panel-operation"')
    eco_start = panel_fn.index('id="dw-tab-panel-ecosystem"')
    op_panel = panel_fn[op_start:eco_start]
    # Operation tab mount only.
    assert "dw-x65-25-known-mount" in op_panel
    eco_panel = panel_fn[eco_start:]
    assert "dw-x65-25-known-mount" not in eco_panel


def test_launch_results_renamed_to_matching_launches():
    panel_fn = _function("operationalIntelligencePanel", "loadOperationalIntelligence")
    assert "Matching Launches" in panel_fn
    assert "Launch Results" not in panel_fn


def test_ecosystem_tab_header_states_its_own_question():
    panel_fn = _function("operationalIntelligencePanel", "loadOperationalIntelligence")
    assert "Ecosystem Intelligence" in panel_fn
    assert "what else exists outside" in panel_fn.lower()


# ───────────────────── No information lost / no behaviour change ─────────────────────

def test_every_original_render_function_still_exists():
    # Confirms nothing was deleted -- only reorganised/renamed/split.
    # renderCanonicalWatchtowerTopology() is deliberately EXCLUDED from this
    # list: X65.58A removed it entirely (see
    # test_canonical_watchtower_topology_removed_from_ecosystem_entirely).
    for fn_name in (
        "renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection",
        "renderWalkbackCoverageSection", "renderWatchtowerProvisioningCandidates",
        "renderConfirmedWatchtowerTreasury", "renderUnresolvedTreasuryAttribution",
        "renderKnownWatchtowerFunding", "renderKnownWatchtowerTopology",
        "renderStageNav", "topoBreadcrumb",
        "renderCreatorIdentity", "renderTopologyDistribution",
        "renderCampaignDistribution", "renderFundingOrigin",
        "renderTreasuryResolution", "renderOperationAttribution",
        "renderObservedPatterns",
    ):
        assert f"function {fn_name}" in HTML, f"{fn_name} missing"


def test_no_backend_api_or_query_changes():
    # Presentation-only guard: no new fetch endpoints introduced by this
    # task beyond what already existed.
    panel_fn = _function("operationalIntelligencePanel", "loadOperationalIntelligence")
    assert "fetch(" not in panel_fn


def test_no_new_classification_or_detection_logic():
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    for excluded in ("classify_topology_for_launch", "build_campaign_classification", "SELECT"):
        assert excluded not in block
