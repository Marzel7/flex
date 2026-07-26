from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


# X65.24 — Discovery Flow Reorder: Creator Identity -> Topology -> Campaign ->
# Funding Origin -> Operation -> Behaviour. This test file's contract is
# updated to match the new stage order/gating (presentation-only change;
# see docs brief). Behaviour is now the terminal, additive-only stage
# (renderObservedPatterns), replacing the old exclusive Behaviour Cohort
# entry point (renderBehaviourCohorts, removed).
def test_stages_are_gated_by_the_previous_selection():
    topology = _function("renderTopologyDistribution", "renderTopologyDistributionRows")
    campaign = _function("renderCampaignDistribution", "renderCampaignTreasuryBreakdown")
    funding = _function("renderFundingOrigin", "renderCohortSummary")
    operation = _function("renderOperationAttribution", "renderCreatorIdentity")
    behaviour = _function("renderObservedPatterns", "x60TopologySourceLabel")
    results = _function("renderLaunchResultsHeader", "topoFindNode")
    assert "if(!TOPO_SELECTION.creator_identity)return ''" in topology
    assert "if(!TOPO_SELECTION.topology)return ''" in campaign
    assert "if(!TOPO_SELECTION.campaign)return ''" in funding
    assert "if(!TOPO_SELECTION.funding)return ''" in operation
    assert "if(!TOPO_SELECTION.operation)return ''" in behaviour
    assert "if(!TOPO_SELECTION.operation)return ''" in results


def test_creator_identity_is_the_entry_point():
    identity = _function("renderCreatorIdentity", "renderCampaignDistribution")
    assert "X60_UNIVERSE_ROWS" in identity
    assert "1. Creator Identity" in identity


def test_upstream_changes_clear_every_downstream_selection():
    clicks = _function("bindTopoLevelClicks", "renderGroupedLaunches")
    assert "dimension==='creator_identity'" in clicks
    assert "dimension==='topology'" in clicks
    assert "dimension==='campaign'" in clicks
    assert "dimension==='funding'" in clicks
    assert "dimension==='operation'" in clicks
    assert "TOPO_SELECTION.behaviour=null" in clicks


def test_counts_are_derived_from_progressively_scoped_rows():
    assert "function x60BehaviourRows" in HTML
    assert "function x60CreatorIdentityRows" in HTML
    assert "function x60TopologyRows" in HTML
    assert "function x60FundingRows" in HTML
    assert "function x60OperationRows" in HTML


def test_zero_count_branches_are_not_rendered():
    assert "filter(function(v){return counts[v]>0})" in HTML
    assert "filter(function(k){return counts[k]>0})" in HTML


def test_watchtower_zero_is_explanatory_not_a_zero_card():
    operation = _function("renderOperationAttribution", "renderCreatorIdentity")
    assert "No launches in this cohort are currently attributed to WATCHTOWER." in operation
    assert "Object.keys(counts).filter" in operation


def test_funding_origin_uses_existing_cex_evidence():
    funding = _function("x60MatchesFunding", "x60FundingRows")
    assert "CEX_MINT_CACHE" in funding
    assert "info.treasuries" in funding
    assert "originCounts[info.origin]>1" in funding


def test_stale_urls_cannot_retain_an_empty_branch():
    guard = _function("x60SanitizeSelection", "x58CurrentRows")
    assert "!x60BehaviourRows().length" in guard
    assert "!x60CreatorIdentityRows().length" in guard
    assert "!x60TopologyRows().length" in guard
    assert "!x60FundingRows().length" in guard
    assert "!x60OperationRows().length" in guard


def test_behaviour_is_additive_not_an_entry_point():
    behaviour = _function("renderObservedPatterns", "x60TopologySourceLabel")
    assert "r.canonical_behaviour" not in behaviour
    assert "(r.behaviours||[])" in behaviour
