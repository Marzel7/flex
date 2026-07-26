from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


def test_selected_quick_cohort_is_not_repeated_as_observed_pattern():
    observed = _function("renderObservedPatterns", "renderTopologyDistribution")
    assert "QUICK_BIRTH_MIGRATION" not in observed
    assert "Quick Birth → Migration" not in observed


def test_behaviour_labels_distinguish_birth_create_from_migration_speed():
    observed = _function("renderObservedPatterns", "renderTopologyDistribution")
    assert "Rapid Birth → CREATE" in observed
    assert "Migration <5m" in observed
    assert "Migration 5–15m" in observed
    assert "Migration >15m" in observed


def test_current_cohort_is_compact_and_does_not_repeat_filter_name():
    cohort = _function("renderCohortSummary", "renderLaunchResultsHeader")
    assert "Quick Birth → Migration" not in cohort
    assert "repeat creators" in cohort
    assert "creator recycling" in cohort
    assert "dw-x58-cohort-item" in cohort


def test_exchange_funding_sources_are_not_rendered_at_discovery_level():
    funding = _function("renderFundingEvidence", "renderIntelligenceHighlights")
    assert "renderFundingIntelligence()" not in funding
    assert "fundingMount.innerHTML=''" in funding
    assert "infraMount.innerHTML=''" in funding


def test_topology_hides_zero_count_branches():
    topology = _function("renderTopologyDistribution", "renderCohortSummary")
    assert "filter(function(v){return counts[v]>0})" in topology


def test_launch_badges_keep_distinct_operation_behaviour_topology_colours():
    assert "dw-x56-badge-operation" in HTML
    assert "dw-x56-badge-behaviour" in HTML
    assert "dw-x56-badge-topology" in HTML


def test_legacy_pipeline_does_not_block_initial_discovery_render():
    # X65.54 — landing() itself no longer contains the landingIntel()
    # Promise.all directly (that batch moved to its own function, started
    # independently); the shell (landingShell(), including
    # loadingHealthPanel()'s static placeholder) and loadPipelineHealth()'s
    # own independent fetch are what must be free of any investigation
    # -pipeline call inside the unrelated five-request batch.
    landing = _function("landing", "search")
    assert "investigation-pipeline" not in landing
    assert "loadingHealthPanel()" in _function("landingShell", "loadLandingIntel")
    assert "loadPipelineHealth();" in landing
