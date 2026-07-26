from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


# X65.25 — Discovery Refocus: WATCHTOWER is presented first as a known,
# validated reference model (Part A), then the existing discovery cascade
# (X65.24's Creator Identity -> Topology -> Campaign -> Funding Origin ->
# Operation -> Behaviour order, unchanged) investigates only the REMAINING
# population (Part B). Presentation-only: no backend/classifier/schema/API
# changes; every function this task touches only rewires which rows a JS
# render function reads, never src/ops/*.py or the Flask route.


def test_known_watchtower_block_renders_unconditionally():
    # X65.58 -- renderKnownWatchtowerPopulation() was split into
    # renderCanonicalWatchtowerSection()/renderWalkbackCoverageSection(),
    # Provisioning Candidates moved up, Treasury Intelligence is now a
    # collapsible grouping, and the "Explore Remaining Population" text
    # divider was REMOVED (superseded by the Operation/Ecosystem tab
    # boundary -- see test_x65_58_discovery_ia_redesign.py). Still
    # unconditional: no TOPO_SELECTION gate anywhere in this dispatcher.
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    assert "renderCanonicalWatchtowerSection(" in block
    assert "renderWalkbackCoverageSection(" in block
    assert "renderKnownWatchtowerTopology()" in block
    # X65.32 split renderKnownWatchtowerAttribution into two independent
    # sections -- see test_x65_32_treasury_classification_semantics.py.
    assert "renderConfirmedWatchtowerTreasury()" in block
    assert "renderUnresolvedTreasuryAttribution()" in block
    assert "renderKnownWatchtowerFunding()" in block
    assert "TOPO_SELECTION" not in block


def test_known_watchtower_population_is_not_gated_by_selection():
    # X65.58 -- split into two functions; neither reads TOPO_SELECTION.
    canonical = _function("renderCanonicalWatchtowerSection", "renderWalkbackCoverageSection")
    coverage = _function("renderWalkbackCoverageSection", "renderKnownWatchtowerBlock")
    assert "TOPO_SELECTION" not in canonical
    assert "TOPO_SELECTION" not in coverage


def test_known_watchtower_reuses_canonical_topology_render():
    # X65.58 -- renderKnownWatchtowerTopology() (the Operation Intelligence
    # tab's reference diagram) is a COMPACT presentation.
    # X65.58A -- renderCanonicalWatchtowerTopology() (the old full-card
    # version, formerly ALSO used by renderTopologyDistribution() on the
    # Ecosystem tab) was REMOVED entirely -- that second call site was
    # exactly the bug X65.58A fixed (showing the WATCHTOWER operational
    # diagram on Ecosystem Intelligence falsely implied the explored cohort
    # follows the WATCHTOWER operational model). The compact diagram is now
    # the only place this information renders anywhere in Discovery.
    topology = _function("renderKnownWatchtowerTopology", "renderUnresolvedTreasuryAttribution") \
        if "function renderUnresolvedTreasuryAttribution" in HTML else _function("renderKnownWatchtowerTopology", "renderConfirmedWatchtowerTreasury")
    assert "Treasury" in topology and "Subprovider" in topology and "Creator" in topology and "Launch" in topology
    assert "function renderCanonicalWatchtowerTopology" not in HTML


def test_attribution_completeness_is_not_a_filter():
    # X65.32 renamed/split this section; the non-interactive-row and
    # "not a filter" properties now live on the confirmed-only section.
    attribution = _function("renderConfirmedWatchtowerTreasury", "renderUnresolvedTreasuryAttribution")
    assert "dwStaticSegRow(" in attribution
    assert "dwCampaignSegRow(" not in attribution


def test_funding_provenance_is_descriptive_only():
    funding = _function("renderKnownWatchtowerFunding", "renderKnownWatchtowerBlock")
    assert "data-x56-dimension" not in funding
    assert "data-level" not in funding
    assert "descriptive only" in funding


def test_remaining_population_excludes_watchtower_campaign():
    helper = _function("x65_25RemainingUniverseRows", "x60CreatorIdentityRows")
    assert "r.campaign!=='WATCHTOWER'" in helper
    identity = _function("x60CreatorIdentityRows", "x60TopologyRows")
    assert "x65_25RemainingUniverseRows()" in identity


def test_current_rows_fallback_uses_remaining_population():
    current = _function("x60CurrentRows", "x60SanitizeSelection")
    assert "x65_25RemainingUniverseRows()" in current
    assert "X60_UNIVERSE_ROWS.slice()" not in current


def test_known_watchtower_mount_wired_before_cascade_stage_nav():
    panel = HTML[HTML.index("function operationalIntelligencePanel"):]
    known_pos = panel.index("dw-x65-25-known-mount")
    stage_nav_pos = panel.index("dw-x65-stage-nav-mount")
    identity_pos = panel.index("dw-x64-creator-identity-mount")
    assert known_pos < stage_nav_pos < identity_pos


def test_known_watchtower_rendered_before_cascade_in_dispatcher():
    dispatcher = _function("renderX58Mounts", "renderTopoLevel")
    known_render_pos = dispatcher.index("renderKnownWatchtowerBlock()")
    identity_render_pos = dispatcher.index("renderCreatorIdentity()")
    assert known_render_pos < identity_render_pos


def test_behaviour_still_terminal_per_x65_24():
    # Explicit product decision (X65.25 discussion): the "Remaining
    # Population" example list in this brief was illustrative, not a
    # request to move Behaviour before Campaign again. Behaviour stays
    # the terminal, additive-only stage from X65.24.
    assert "1. Creator Identity" in HTML
    assert "2. Topology" in HTML
    assert "3. Campaign" in HTML
    assert "4. Funding Origin" in HTML
    assert "5. Operation Attribution" in HTML
    assert "6. Behaviour" in HTML
