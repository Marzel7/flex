"""X65.58A — Correct Topology Semantics in Ecosystem Intelligence.

The initial X65.58 implementation left one leak: renderTopologyDistribution()
(Ecosystem tab, Stage 2) special-cased a cascade-confirmed cohort by
rendering renderCanonicalWatchtowerTopology() — the WATCHTOWER operational
diagram — instead of the standard Multi-Level Fan-Out/Fan-Out/Linear/
Unknown distribution cards. This falsely implied "these Ecosystem-cohort
launches follow the WATCHTOWER operational model," even though Ecosystem's
population already excludes campaign==='WATCHTOWER' launches upstream.

Fix (presentation/semantic only, no backend/classification/query change):
  - renderCanonicalWatchtowerTopology() and its only other dependency,
    renderProvisioningWalletExplanation(), REMOVED entirely.
  - renderTopologyDistribution() always renders the standard distribution
    cards now, regardless of is_cascade_confirmed.
  - The Operation Intelligence tab's compact diagram
    (renderKnownWatchtowerTopology) is renamed "Operational Topology" and
    is the ONLY place this diagram renders anywhere in Discovery.
  - Ecosystem's Stage 2 heading renamed "Topology" -> "Topology
    Distribution" (stage nav + section heading) so the two concepts never
    share a label.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


def _code_only(fn: str) -> str:
    return "\n".join(line for line in fn.splitlines() if not line.strip().startswith("//"))


# ─────────────────── WATCHTOWER diagram removed from Ecosystem ───────────────────

def test_watchtower_operational_diagram_never_appears_in_ecosystem_stage_2():
    dist_fn = _function("renderTopologyDistribution", "renderTopologyDistributionRows")
    code = _code_only(dist_fn)
    assert "renderCanonicalWatchtowerTopology" not in code
    assert "is_cascade_confirmed" not in code


def test_topology_distribution_always_renders_standard_cards():
    dist_fn = _function("renderTopologyDistribution", "renderTopologyDistributionRows")
    code = _code_only(dist_fn)
    assert "return renderTopologyDistributionRows(rows)" in code
    # Only the entry-point gate remains; no branching on confirmation.
    assert code.count("return") == 2  # the '' gate + the distribution-rows call


def test_the_full_card_watchtower_topology_function_no_longer_exists():
    assert "function renderCanonicalWatchtowerTopology" not in HTML
    assert "function renderProvisioningWalletExplanation" not in HTML


def test_compact_operation_tab_diagram_is_the_only_surviving_diagram():
    assert "function renderKnownWatchtowerTopology" in HTML
    fn = _function("renderKnownWatchtowerTopology", "renderConfirmedWatchtowerTreasury")
    assert "Treasury" in fn and "Subprovider" in fn and "Creator" in fn and "Launch" in fn


# ─────────────────────────── Heading renames ───────────────────────────

def test_operation_tab_diagram_heading_is_operational_topology():
    fn = _function("renderKnownWatchtowerTopology", "renderConfirmedWatchtowerTreasury")
    assert '<div class="dw-op-topology-title">Operational Topology</div>' in fn
    # Never the old "WATCHTOWER Operational Topology" hardcoded title --
    # the operation name is data (DW_OPERATION), not baked into the label.
    assert '<div class="dw-op-topology-title">WATCHTOWER Operational Topology</div>' not in fn


def test_operation_tab_diagram_uses_dw_operation_not_hardcoded_watchtower():
    fn = _function("renderKnownWatchtowerTopology", "renderConfirmedWatchtowerTreasury")
    assert "DW_OPERATION" in fn


def test_ecosystem_stage_2_heading_renamed_topology_distribution():
    rows_fn = _function("renderTopologyDistributionRows", "renderProvisioningWalletExplanation") \
        if "function renderProvisioningWalletExplanation" in HTML \
        else _function("renderTopologyDistributionRows", "renderFundingOrigin")
    assert "2. Topology Distribution" in rows_fn
    assert "dw-x58-heading\">2. Topology<" not in rows_fn.replace(" ", "").replace(">2.", ">2.")


def test_stage_nav_label_renamed_topology_distribution():
    stage_nav_fn = _function("renderStageNav", "renderX58Mounts")
    assert "label:'Topology Distribution'" in stage_nav_fn
    assert "label:'Topology'," not in stage_nav_fn


# ───────────────────────── Data/behaviour preserved ─────────────────────────

def test_distribution_buckets_and_counts_unchanged():
    rows_fn = _function("renderTopologyDistributionRows", "renderFundingOrigin") \
        if "function renderProvisioningWalletExplanation" not in HTML \
        else _function("renderTopologyDistributionRows", "renderProvisioningWalletExplanation")
    for bucket in ("MULTI_LEVEL_FAN_OUT", "FAN_OUT", "LINEAR", "UNKNOWN"):
        assert bucket in rows_fn
    assert "x58Card('topology'" in rows_fn
    assert "counts[key]=(counts[key]||0)+1" in rows_fn.replace(" ", "")


def test_no_backend_query_or_classification_touched():
    dist_fn = _function("renderTopologyDistribution", "renderTopologyDistributionRows")
    for excluded in ("fetch(", "classify_topology_for_launch", "build_campaign_classification", "SELECT "):
        assert excluded not in dist_fn


def test_topology_source_breakdown_still_present():
    # X65.18/X65.20's evidence-source breakdown per bucket is unrelated to
    # this fix and must be completely unaffected.
    rows_fn = _function("renderTopologyDistributionRows", "renderFundingOrigin") \
        if "function renderProvisioningWalletExplanation" not in HTML \
        else _function("renderTopologyDistributionRows", "renderProvisioningWalletExplanation")
    assert "x60TopologySourceLabel" in rows_fn
    assert "dw-topo-source-row" in rows_fn


def test_ecosystem_tab_and_operation_tab_topology_are_visually_distinct_css():
    # The Operation tab's compact diagram uses dw-op-topology-* classes;
    # Ecosystem's distribution cards use dw-x56-cards/x58Card -- no shared
    # class name that could visually conflate the two concepts.
    op_fn = _function("renderKnownWatchtowerTopology", "renderConfirmedWatchtowerTreasury")
    eco_fn = _function("renderTopologyDistributionRows", "renderFundingOrigin") \
        if "function renderProvisioningWalletExplanation" not in HTML \
        else _function("renderTopologyDistributionRows", "renderProvisioningWalletExplanation")
    assert "dw-op-topology-compact" in op_fn
    assert "dw-op-topology-compact" not in eco_fn
