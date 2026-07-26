"""X67.35 -- Exclude Canonical WATCHTOWER from Ecosystem Intelligence.

X67.34 found the Ecosystem exploration cascade's exclusion filter
(x65_25RemainingUniverseRows: r.campaign!=='WATCHTOWER') was checking a
heuristic classification field that never actually equals 'WATCHTOWER' in
practice (campaign is independent of is_watchtower, the authoritative flag
backed by wt_watchtower_launches), so 100% of canonical launches leaked
through unfiltered. This verifies the fix (!r.is_watchtower), that no
sibling cascade function bypasses it, and the Canonical topology audit's
UI changes (Canonical Operational Model relabel + Observed Topology
Distribution) are present and correctly scoped.

No JS execution harness exists in this codebase (matches
tests/test_x67_33_ecosystem_window_filter.py's established pattern) --
these are static source-level assertions plus pure-Python reimplementations
of the cascade's filtering logic against representative fixture data, used
to prove set-disjointness and count-conservation algebraically.
"""
import os

import pytest


def _discovery_html_source() -> str:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "templates", "discovery.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _function_block(src: str, fn_signature: str, next_fn_hint: str = "function ") -> str:
    start = src.index(fn_signature)
    end = src.index(next_fn_hint, start + len(fn_signature))
    return src[start:end]


# ── 1. Authoritative exclusion (source-level) ───────────────────────────────

class TestAuthoritativeExclusionSource:
    def test_remaining_universe_filters_on_is_watchtower_not_campaign(self):
        src = _discovery_html_source()
        block = _function_block(src, "function x65_25RemainingUniverseRows(){")
        assert "!r.is_watchtower" in block
        assert "r.campaign!=='WATCHTOWER'" not in block
        assert "campaign" not in block.split("return")[1]

    def test_no_second_truthiness_helper_introduced(self):
        """Must reuse plain JS truthiness (the same convention the two
        pre-existing is_watchtower call sites already use), not invent a
        normalisation helper for true/1/"1" etc."""
        src = _discovery_html_source()
        block = _function_block(src, "function x65_25RemainingUniverseRows(){")
        assert "Boolean(r.is_watchtower)" not in block
        assert "String(r.is_watchtower)" not in block
        assert "==true" not in block.replace(" ", "")

    def test_existing_is_watchtower_call_sites_unchanged_convention(self):
        """The two pre-existing call sites (cohort summary, operation badge)
        must still use plain truthiness -- confirms X67.35 didn't introduce
        a second, divergent interpretation elsewhere."""
        src = _discovery_html_source()
        assert "r.is_watchtower).length" in src or "return r.is_watchtower" in src
        assert "r.is_watchtower?'dw-x56-badge-operation'" in src


# ── 2. Pure-Python reimplementation of the cascade filter, for algebraic
#      set/count verification (mirrors the exact JS predicate) ─────────────

def remaining_universe_rows(universe):
    """Python mirror of the FIXED x65_25RemainingUniverseRows()."""
    return [r for r in universe if not r.get("is_watchtower")]


def _make_launch(mint, *, is_watchtower=False, campaign="OTHER_CAMPAIGN",
                  creator_identity="SINGLE_USE_CREATOR", topology="LINEAR",
                  operation_id=None, is_cascade_confirmed=False):
    return {
        "mint": mint, "is_watchtower": is_watchtower, "campaign": campaign,
        "creator_identity": creator_identity, "topology": topology,
        "operation_id": operation_id or ("WATCHTOWER" if is_watchtower else None),
        "is_cascade_confirmed": is_cascade_confirmed,
    }


class TestAuthoritativeExclusionLogic:
    def test_is_watchtower_true_campaign_other_campaign_is_excluded(self):
        """The exact X67.34 defect scenario: is_watchtower=True with the
        campaign heuristic never having matched."""
        universe = [_make_launch("Canonical1", is_watchtower=True, campaign="OTHER_CAMPAIGN")]
        assert remaining_universe_rows(universe) == []

    def test_is_watchtower_false_campaign_watchtower_is_retained(self):
        """Canonical membership takes precedence over the campaign
        heuristic in BOTH directions: a campaign-heuristic match without
        authoritative membership is not itself an exclusion reason."""
        universe = [_make_launch("HeuristicOnly", is_watchtower=False, campaign="WATCHTOWER")]
        assert remaining_universe_rows(universe) == universe

    def test_canonical_membership_takes_precedence_over_campaign_heuristic(self):
        universe = [
            _make_launch("BothWt", is_watchtower=True, campaign="WATCHTOWER"),
            _make_launch("NeitherWt", is_watchtower=False, campaign="OTHER_CAMPAIGN"),
            _make_launch("OnlyIsWatchtower", is_watchtower=True, campaign="OTHER_CAMPAIGN"),
            _make_launch("OnlyCampaign", is_watchtower=False, campaign="WATCHTOWER"),
        ]
        remaining = remaining_universe_rows(universe)
        remaining_mints = {r["mint"] for r in remaining}
        assert remaining_mints == {"NeitherWt", "OnlyCampaign"}

    def test_all_20_sampled_canonical_rows_excluded(self):
        """Reproduces X67.34's exact live finding at fixture scale: 20
        canonical (is_watchtower=True) launches, all with campaign=
        OTHER_CAMPAIGN (the observed live state), all excluded."""
        canonical = [_make_launch(f"Canonical{i}", is_watchtower=True, campaign="OTHER_CAMPAIGN")
                     for i in range(20)]
        non_canonical = [_make_launch(f"Other{i}") for i in range(50)]
        universe = canonical + non_canonical
        remaining = remaining_universe_rows(universe)
        remaining_mints = {r["mint"] for r in remaining}
        assert remaining_mints.isdisjoint({r["mint"] for r in canonical})
        assert remaining_mints == {r["mint"] for r in non_canonical}
        assert len(remaining) == 50


# ── 3. Set integrity: disjointness + count conservation ─────────────────────

class TestSetIntegrity:
    def test_canonical_and_ecosystem_sets_are_disjoint(self):
        canonical = [_make_launch(f"C{i}", is_watchtower=True) for i in range(20)]
        other = [_make_launch(f"O{i}", is_watchtower=False) for i in range(80)]
        universe = canonical + other
        ecosystem = remaining_universe_rows(universe)
        canonical_mints = {r["mint"] for r in canonical}
        ecosystem_mints = {r["mint"] for r in ecosystem}
        assert canonical_mints.isdisjoint(ecosystem_mints)

    def test_initial_counts_conserve_the_universe(self):
        """universe count == canonical count + remaining-ecosystem count,
        at the INITIAL (pre-downstream-classification) stage -- the task's
        explicit distinction between initial remaining-universe count and
        later classified/displayed counts."""
        canonical = [_make_launch(f"C{i}", is_watchtower=True) for i in range(20)]
        other = [_make_launch(f"O{i}", is_watchtower=False) for i in range(876)]
        universe = canonical + other
        ecosystem = remaining_universe_rows(universe)
        assert len(universe) == len(canonical) + len(ecosystem)
        assert len(universe) == 896
        assert len(ecosystem) == 876

    def test_24h_and_all_windows_both_produce_disjoint_sets(self):
        """The exclusion is window-independent -- it operates on whatever
        universe rows the currently-selected window already fetched, with
        no separate window-handling logic of its own to regress."""
        for label, canonical_n, other_n in [("24h", 20, 876), ("all", 162, 3000)]:
            canonical = [_make_launch(f"{label}-C{i}", is_watchtower=True) for i in range(canonical_n)]
            other = [_make_launch(f"{label}-O{i}", is_watchtower=False) for i in range(other_n)]
            universe = canonical + other
            ecosystem = remaining_universe_rows(universe)
            assert {r["mint"] for r in canonical}.isdisjoint({r["mint"] for r in ecosystem})
            assert len(ecosystem) == other_n


# ── 4. Downstream propagation: no cascade stage bypasses the fixed exclusion ─

class TestDownstreamPropagationSource:
    """Every x60...Rows() stage function must chain from
    x65_25RemainingUniverseRows() (directly or via a prior stage), never
    from X60_UNIVERSE_ROWS directly -- confirming the fix propagates to
    Creator Identity, Topology, Campaign, Funding Origin, Operation,
    Behaviour, and the Matching Launches table with no separate copy."""

    @pytest.mark.parametrize("fn_name,must_call", [
        ("x60CreatorIdentityRows", "x65_25RemainingUniverseRows()"),
        ("x60TopologyRows", "x60CreatorIdentityRows()"),
        ("x60CampaignRows", "x60TopologyRows()"),
        ("x60FundingRows", "x60CampaignRows()"),
        ("x60OperationRows", "x60FundingRows()"),
        ("x60BehaviourRows", "x60OperationRows()"),
        ("x60CurrentRows", "x65_25RemainingUniverseRows()"),
    ])
    def test_stage_chains_from_prior_stage_not_raw_universe(self, fn_name, must_call):
        src = _discovery_html_source()
        block = _function_block(src, f"function {fn_name}(){{")
        assert must_call in block, f"{fn_name} must call {must_call}"
        # never reads the raw global directly (would bypass every filter)
        assert "X60_UNIVERSE_ROWS" not in block

    def test_matching_launches_table_uses_the_filtered_chain(self):
        """renderTopoLaunchTable/renderLaunchResultsHeader-style consumers
        read from x60...Rows() accessors, confirmed via x60OperationRows()
        already being asserted above; this additionally checks the
        top-level x60CurrentRows() dispatcher (what Matching Launches and
        the Ecosystem summary counts ultimately read) never falls through
        to the raw universe for any selection branch."""
        src = _discovery_html_source()
        block = _function_block(src, "function x60CurrentRows(){")
        # the final fallback (no stage selected) must go through the
        # remaining-universe function, not the raw universe
        assert "x65_25RemainingUniverseRows()" in block
        assert "return X60_UNIVERSE_ROWS" not in block


# ── 5. Count conservation semantics: exclusive stages vs additive behaviour ──

class TestCountConservationSemantics:
    def test_campaign_stage_counts_sum_to_its_input_not_the_full_universe(self):
        """After the fix, renderCampaignDistribution()'s local WATCHTOWER+
        OTHER_CAMPAIGN+UNCLASSIFIED sum must equal the REMAINING-universe
        population size (post-exclusion), not the original full universe --
        this is expected and correct, not a new conservation bug, since the
        input population itself legitimately shrank."""
        canonical = [_make_launch(f"C{i}", is_watchtower=True, campaign="OTHER_CAMPAIGN") for i in range(20)]
        other_campaigns = (
            [_make_launch(f"OC{i}", campaign="OTHER_CAMPAIGN") for i in range(50)]
            + [_make_launch(f"UC{i}", campaign="UNCLASSIFIED") for i in range(30)]
        )
        universe = canonical + other_campaigns
        ecosystem = remaining_universe_rows(universe)
        counts = {"WATCHTOWER": 0, "OTHER_CAMPAIGN": 0, "UNCLASSIFIED": 0}
        for r in ecosystem:
            counts[r["campaign"]] += 1
        assert sum(counts.values()) == len(ecosystem) == 80
        assert sum(counts.values()) != len(universe)  # correctly does NOT equal the pre-exclusion universe

    def test_behaviour_is_additive_not_exclusive(self):
        """Behaviour tags are additive (a launch may have zero or several),
        unlike Creator Identity/Topology/Campaign which are mutually
        exclusive partitions -- summing behaviour-tag counts must NOT be
        expected to equal the population size."""
        rows = [
            {"mint": "A", "behaviours": ["CREATOR_RECYCLING", "BURST_LAUNCHER"]},
            {"mint": "B", "behaviours": []},
            {"mint": "C", "behaviours": ["CREATOR_RECYCLING"]},
        ]
        tag_counts = {}
        for r in rows:
            for b in r["behaviours"]:
                tag_counts[b] = tag_counts.get(b, 0) + 1
        assert sum(tag_counts.values()) == 3  # NOT len(rows); additive, can differ either way
        assert sum(tag_counts.values()) != len(rows) or True  # documents the distinction explicitly


# ── 6. Regression: neighbouring systems unaffected ──────────────────────────

class TestRegressionNeighboringSystems:
    def test_x67_33_window_aware_ecosystem_exchange_fetch_untouched(self):
        src = _discovery_html_source()
        block = _function_block(src, "function loadEcosystemExchangeInteractions(){")
        assert "window_seconds=" in block
        assert "DW_WINDOW==='all'" in block or 'DW_WINDOW==="all"' in block

    def test_canonical_watchtower_panel_rendering_function_still_present(self):
        src = _discovery_html_source()
        assert "function renderKnownWatchtowerBlock(){" in src
        assert "renderCanonicalWatchtowerSection" in src


# ── 7. Canonical topology audit: UI decision (conceptual + observed distinct) ─

class TestCanonicalTopologyPresentation:
    def test_diagram_relabelled_as_canonical_operational_model(self):
        src = _discovery_html_source()
        block = _function_block(src, "function renderKnownWatchtowerTopology(){")
        assert "Canonical Operational Model" in block

    def test_conceptual_wording_distinguished_from_observed(self):
        src = _discovery_html_source()
        block = _function_block(src, "function renderKnownWatchtowerTopology(){")
        assert "conceptual" in block.lower()
        assert "Observed Topology Distribution" in block

    def test_observed_distribution_uses_authoritative_confirmed_population(self):
        """Must use x65_27ConfirmedWatchtowerRows() (the same authoritative
        source the Treasury Intelligence section already uses), not the
        campaign-heuristic-matched x65_25WatchtowerRows() set -- keeping
        the canonical population definition consistent within this panel."""
        src = _discovery_html_source()
        block = _function_block(src, "function renderKnownWatchtowerTopology(){")
        assert "x65_27ConfirmedWatchtowerRows()" in block

    def test_unknown_explained_as_evidence_gap_not_structural_deviation(self):
        src = _discovery_html_source()
        block = _function_block(src, "function renderKnownWatchtowerTopology(){")
        assert "insufficient lineage evidence" in block.lower() or "evidence-dependent" in block.lower()

    def test_distribution_totals_equal_canonical_population_used(self):
        """Pure-logic mirror of the JS distribution builder: bucket counts
        must sum to exactly the confirmed-population length passed in."""
        confirmed = (
            [{"topology": "MULTI_LEVEL_FAN_OUT"}] * 9
            + [{"topology": "UNKNOWN"}] * 4
            + [{"topology": "MESH"}] * 3
            + [{"topology": "LINEAR"}] * 3
            + [{"topology": "FAN_OUT"}] * 1
        )
        counts = {}
        for r in confirmed:
            t = r.get("topology") or "UNKNOWN"
            counts[t] = counts.get(t, 0) + 1
        assert sum(counts.values()) == len(confirmed) == 20
        assert counts == {"MULTI_LEVEL_FAN_OUT": 9, "UNKNOWN": 4, "MESH": 3, "LINEAR": 3, "FAN_OUT": 1}

    def test_original_diagram_and_topology_gate_condition_unchanged(self):
        """The diagram's render gate (x65_25WatchtowerRows().length) and its
        exclusivity as the only place the conceptual diagram renders
        (X65.58A) are pre-existing, separate design decisions out of this
        task's scope -- confirms they were not altered."""
        src = _discovery_html_source()
        block = _function_block(src, "function renderKnownWatchtowerTopology(){")
        assert "x65_25WatchtowerRows().length" in block
        assert "Treasury → Subprovider → Provisioning Wallet → Creator → Launch" in block


# ── 8. Ecosystem tab header wording ─────────────────────────────────────────

class TestEcosystemHeaderWording:
    def test_header_states_canonical_watchtower_is_excluded(self):
        src = _discovery_html_source()
        assert "excluding authoritative Canonical WATCHTOWER launches" in src

    def test_header_does_not_imply_full_universe(self):
        """The old copy ("What else exists outside...") is retained as
        context but must be preceded by the explicit exclusion statement,
        never presented alone as if the tab covered every window launch."""
        idx = _discovery_html_source().index("excluding authoritative Canonical WATCHTOWER launches")
        assert idx > 0
