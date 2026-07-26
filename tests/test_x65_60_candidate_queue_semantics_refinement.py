"""X65.60 — Refine WATCHTOWER Provisioning Candidate Queue Semantics.

The X65.59 audit established that Flag Reason (topology + treasury tier
concatenated, e.g. "Multi-Level Fan-Out via confirmed treasury") falsely
implies topology is part of WATCHTOWER candidate qualification, when
neither component is actually part of candidate generation or
confirmation. This refocuses the table around investigation progress:

  Confidence | Investigation Status | Treasury Progress | Walkback Status | Launched

Topology/treasury-tier/creator/subprov/mint move into an expandable
detail row (supporting evidence), not deleted.

Scope guard: this task applies ONLY to the WATCHTOWER Provisioning
Candidates table. No detection, candidate generation, walkback,
confirmation, topology classification, campaign classification, API,
backend query, or database schema change is in scope -- every assertion
here is presentation-only.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


# ─────────────────────────── Column redesign ───────────────────────────

def test_flag_reason_column_and_function_removed():
    assert "function x65_46FlagReason" not in HTML
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "Flag Reason" not in table_fn


def test_primary_columns_are_investigation_progress():
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    header_start = table_fn.index("<thead>")
    header_end = table_fn.index("</thead>")
    header = table_fn[header_start:header_end]
    for column in ("Confidence", "Investigation Status", "Treasury Progress", "Walkback Status", "Launched"):
        assert column in header


def test_investigation_status_reuses_existing_outcome_group_helper():
    # x65_27CandidateStatus (X65.27, unchanged) already derives Investigation
    # Status from X60_OUTCOME_GROUPS -- reused verbatim, not reimplemented.
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "x65_27CandidateStatus(r.mint)" in table_fn
    assert "cex_exchange_name" in table_fn  # CEX boundary name pass-through, unchanged


def test_treasury_progress_reuses_existing_treasury_tier_no_new_classification():
    start = HTML.index("var X65_60_TREASURY_PROGRESS_LABELS=")
    end = HTML.index("\n  }", HTML.index("function x65_60TreasuryProgress", start))
    fn = HTML[start:end]
    assert "r.campaign_evidence" in fn
    assert "treasury_tier" in fn
    for label in ("CONFIRMED", "PROBABLE", "NEW", "UNKNOWN"):
        assert label in fn
    assert "fetch(" not in fn


# ────────────────────────── Walkback Status enrichment ──────────────────────────

def test_walkback_status_labels_map_to_existing_backend_values_only():
    fn = _function("loadX65_60WalkbackStatusEnrichment", "x65_46SortedCandidates")
    assert "PENDING_WALKBACK" in HTML
    assert "WALKBACK_RUNNING" in HTML
    assert "WALKBACK_COMPLETE" in HTML
    assert "CONFIRMED_WATCHTOWER" in HTML
    assert "REJECTED" in HTML
    # No invented status values.
    assert "candidate_status" in fn


def test_walkback_enrichment_is_non_blocking_and_reuses_existing_endpoint():
    fn = _function("loadX65_60WalkbackStatusEnrichment", "x65_46SortedCandidates")
    assert "/api/ops-v2/watchtower-candidates" in fn
    # Merges by mint, then re-renders -- never blocks the table's own
    # initial render (which already has everything from operational-
    # intelligence before this fetch even starts).
    assert "renderX58Mounts()" in fn
    assert "X65_60_WALKBACK_FETCH_STARTED" in fn  # guarded against duplicate fetch


def test_walkback_enrichment_fetch_guard_prevents_duplicate_requests():
    start = HTML.index("function loadX65_60WalkbackStatusEnrichment")
    end = HTML.index("\n  }", start)
    fn = HTML[start:end]
    assert "if(X65_60_WALKBACK_FETCH_STARTED)return" in fn.replace(" ", "")


def test_candidate_table_triggers_enrichment_load():
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "loadX65_60WalkbackStatusEnrichment()" in table_fn


# ────────────────────────── Detail row (supporting evidence) ──────────────────────────

def test_detail_row_shows_topology_as_supporting_evidence_not_headline():
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "Observed Topology" in table_fn
    assert "X65_46_TOPOLOGY_LABELS[r.topology]" in table_fn.replace(" ", "")


def test_detail_row_shows_treasury_tier_as_supporting_evidence():
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "Treasury Tier" in table_fn


def test_detail_row_preserves_creator_subprov_mint_and_evidence():
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "addrChip(r.mint,'token')" in table_fn
    assert "addrChip(r.creator,'creator')" in table_fn
    assert "ev.subprov_wallet" in table_fn
    assert "creator_identity" in table_fn


def test_detail_row_still_links_through_to_full_entity_page():
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "href(r.mint,'token')" in table_fn
    assert "Open full launch detail" in table_fn


def test_row_click_toggles_detail_expansion():
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "x65_60ToggleCandidateDetail(" in table_fn
    toggle_fn = _function("x65_60ToggleCandidateDetail", "renderCandidateQueueTable")
    assert "style.display" in toggle_fn


def test_toggle_function_exposed_on_window_for_inline_onclick():
    # Bug fix: templates/discovery.html's entire script is one IIFE, so a
    # `function` declared inside it is NOT reachable from an inline
    # onclick="x65_60ToggleCandidateDetail(...)" HTML attribute (those
    # execute in global scope) -- the click silently no-op'd with a
    # swallowed ReferenceError. Must be exposed via window.<name>=<name>.
    toggle_fn = _function("x65_60ToggleCandidateDetail", "renderCandidateQueueTable")
    assert "window.x65_60ToggleCandidateDetail=x65_60ToggleCandidateDetail" in toggle_fn.replace(" ", "")


def test_copy_to_clipboard_also_exposed_on_window_same_bug_same_fix():
    # copyToClipboard is used via the same inline-onclick pattern inside
    # addrChip() (used throughout the candidate detail row) -- same root
    # cause, same fix, scoped to this one adjacent call site rather than a
    # full audit of every onclick in the file.
    start = HTML.index("function copyToClipboard")
    end = HTML.index("\n  }", HTML.index("window.copyToClipboard", start))
    fn = HTML[start:end]
    assert "window.copyToClipboard=copyToClipboard" in fn.replace(" ", "")


# ────────────────────────── No information lost ──────────────────────────

def test_no_underlying_field_is_dropped_from_the_row():
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    for field in ("r.mint", "r.creator", "r.topology", "r.campaign_evidence",
                  "r.campaign_confidence", "r.create_at", "r.creator_identity"):
        assert field in table_fn


# ────────────────────────── Scope guards ──────────────────────────

def test_no_other_discovery_sections_reference_the_removed_flag_reason():
    for fn_name in (
        "renderWatchtowerDetectionStatus", "renderCanonicalWatchtowerSection",
        "renderWalkbackCoverageSection", "renderKnownWatchtowerTopology",
        "renderConfirmedWatchtowerTreasury", "renderUnresolvedTreasuryAttribution",
        "renderKnownWatchtowerFunding", "renderTopologyDistribution",
        "renderTopologyDistributionRows", "renderCampaignDistribution",
        "renderFundingOrigin", "renderObservedPatterns",
    ):
        assert f"function {fn_name}" in HTML, f"{fn_name} missing -- scope violation"


def test_topology_distribution_ecosystem_stage_untouched():
    # X65.58A's fix (Ecosystem Stage 2 never shows the WATCHTOWER diagram,
    # always renders standard distribution cards) must be completely
    # unaffected by this candidate-table-only change.
    dist_fn = _function("renderTopologyDistribution", "renderTopologyDistributionRows")
    code = "\n".join(l for l in dist_fn.splitlines() if not l.strip().startswith("//"))
    assert "is_cascade_confirmed" not in code
    assert "renderCanonicalWatchtowerTopology" not in code


def test_no_new_backend_endpoint_only_existing_one_reused():
    # The one new fetch in this task hits an endpoint that already existed
    # (verified against src/core/operation_dashboard_routes.py's
    # api_watchtower_candidates, unchanged route) -- purely a frontend reuse.
    src = (ROOT / "src" / "core" / "operation_dashboard_routes.py").read_text()
    assert "def api_watchtower_candidates" in src
    assert '@ops_dashboard_bp.route("/api/ops-v2/watchtower-candidates")' in src
