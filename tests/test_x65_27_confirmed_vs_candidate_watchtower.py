from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


# X65.27 — Split Confirmed WATCHTOWER (Section A) from WATCHTOWER
# Provisioning Candidates (Section B). Presentation-only: reuses the
# existing is_cascade_confirmed backend field and X60_OUTCOME_GROUPS (an
# already-fetched response field), introduces no new classifier, schema,
# or API surface.


def test_confirmed_rows_use_existing_is_cascade_confirmed_field():
    # X65.34 — Confirmed WATCHTOWER is now sourced from a window-independent
    # fetch (X65_34_CONFIRMED_ROWS), because the windowed cascade population
    # (x65_25WatchtowerRows, built from wt_attribution_outcomes within the
    # current window) can have zero overlap with is_cascade_confirmed rows,
    # which are backed by the canonical wt_watchtower_launches table with no
    # window bound. The filtering by is_cascade_confirmed still happens --
    # just once, in loadConfirmedWatchtowerRows(), against the full ("all"
    # window) population, rather than per-render against the windowed one.
    helper = _function("x65_27ConfirmedWatchtowerRows", "x65_27CandidateWatchtowerRows")
    assert "X65_34_CONFIRMED_ROWS" in helper

    loader = _function("loadConfirmedWatchtowerRows", "loadOperationalIntelligence")
    assert "r.is_cascade_confirmed" in loader
    assert "window" in loader and "all" in loader


def test_candidate_rows_are_the_unconfirmed_complement():
    helper = _function("x65_27CandidateWatchtowerRows", "x65_27CandidateStatus")
    assert "!r.is_cascade_confirmed" in helper
    assert "x65_25WatchtowerRows()" in helper


def test_candidate_status_reuses_already_fetched_outcome_groups():
    status_fn = _function("x65_27CandidateStatus", "dwStaticSegRow")
    # X60_OUTCOME_GROUPS is populated by the SAME request that fetches
    # X60_UNIVERSE_ROWS (group_by=outcome, already wired before this task) --
    # this must not introduce a new fetch.
    assert "X60_OUTCOME_GROUPS" in status_fn
    assert "fetch(" not in status_fn


def test_population_section_splits_confirmed_from_candidates():
    # X65.45 -- Canonical WATCHTOWER's rows now come from
    # x65_45CanonicalRowsForWindow() (windowed by launch create_at), not
    # the raw x65_27ConfirmedWatchtowerRows() directly -- see
    # test_x65_45_canonical_window_scoping.py for the dedicated coverage.
    # X65.58 -- renderKnownWatchtowerPopulation() was split into
    # renderCanonicalWatchtowerSection()/renderWalkbackCoverageSection();
    # the assembly (including the candidates call) now lives in
    # renderKnownWatchtowerBlock().
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    assert "x65_45CanonicalRowsForWindow()" in block
    assert "x65_27CandidateWatchtowerRows()" in block
    assert "renderWatchtowerProvisioningCandidates(candidates)" in block
    canonical = _function("renderCanonicalWatchtowerSection", "renderWalkbackCoverageSection")
    assert "Canonical WATCHTOWER" in canonical


def test_candidate_section_has_its_own_heading_and_explanation():
    candidates_fn = _function("renderWatchtowerProvisioningCandidates", "renderKnownWatchtowerAddressTable")
    assert "WATCHTOWER Provisioning Candidates" in candidates_fn
    assert "not yet been operationally confirmed" in candidates_fn
    assert "dw-wt-candidate-section" in candidates_fn


def test_candidate_section_omitted_when_zero_candidates():
    candidates_fn = _function("renderWatchtowerProvisioningCandidates", "renderKnownWatchtowerAddressTable")
    assert "if(!candidates.length)return ''" in candidates_fn


def test_address_table_shows_status_column_for_candidates_not_treasury():
    # X65.46 -- candidates now render via their own dedicated operational
    # queue table (renderCandidateQueueTable), not a shared isCandidate
    # branch of renderKnownWatchtowerAddressTable (Section A/Canonical
    # WATCHTOWER only, post-X65.46).
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "Status" in table_fn
    assert "x65_27CandidateStatus(r.mint)" in table_fn
    confirmed_table_fn = _function("renderKnownWatchtowerAddressTable", "renderKnownWatchtowerTopology")
    assert "isCandidate" not in confirmed_table_fn
    # Section A (confirmed) still shows the Treasury column/value, unchanged
    # from X65.26.
    assert "r.treasury_wallet" in confirmed_table_fn


def test_confirmed_section_visually_distinct_from_candidate_section():
    # Different CSS classes -- amber/warning for candidates vs the existing
    # cyan-tinted known-WATCHTOWER treatment -- so a user cannot mistake a
    # candidate row for confirmed WATCHTOWER (the brief's explicit
    # validation requirement).
    assert "dw-wt-known-section" in HTML
    assert "dw-wt-candidate-section" in HTML
    assert HTML.index(".dw-wt-known-section {") < HTML.index(".dw-wt-candidate-section {")


def test_topology_and_funding_unchanged_still_use_full_watchtower_population():
    # X65.27's original "Keep unchanged" list covered attribution too, but
    # X65.32 deliberately supersedes that for attribution specifically
    # (mixing confirmed+candidate treasury data was exactly the semantics
    # bug X65.32 fixes -- see test_x65_32_treasury_classification_semantics.py).
    # Topology and funding provenance remain unchanged: still the FULL
    # campaign==='WATCHTOWER' population (confirmed + candidates combined).
    topology = _function("renderKnownWatchtowerTopology", "renderConfirmedWatchtowerTreasury")
    funding = _function("renderKnownWatchtowerFunding", "renderKnownWatchtowerBlock")
    for fn in (topology, funding):
        assert "x65_25WatchtowerRows()" in fn
        assert "x65_27ConfirmedWatchtowerRows" not in fn
        assert "x65_27CandidateWatchtowerRows" not in fn


def test_discovery_cascade_dispatcher_reflects_x65_58_reorder():
    # X65.58 — Discovery IA Redesign superseded this dispatcher's shape:
    # renderKnownWatchtowerPopulation() was split (see
    # test_x65_42/test_x65_45's own updated coverage), Provisioning
    # Candidates moved up (actionable content before explanatory content),
    # Treasury Intelligence is now grouped and collapsible, and the
    # "Explore Remaining Population" text divider was REMOVED — that job
    # is now done by the Operation/Ecosystem tab boundary itself, not an
    # inline sentence (see test_x65_58_discovery_ia_redesign.py for the
    # dedicated new-behaviour coverage).
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    assert "renderCanonicalWatchtowerSection(" in block
    assert "renderWalkbackCoverageSection(" in block
    assert "renderWatchtowerProvisioningCandidates(" in block
    assert "renderKnownWatchtowerTopology()" in block
    assert "renderConfirmedWatchtowerTreasury()" in block
    assert "renderUnresolvedTreasuryAttribution()" in block
    assert "renderKnownWatchtowerFunding()" in block
    assert "Explore Remaining Population" not in block
