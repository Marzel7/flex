from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


# X65.46 — Reduce address clutter / make the Provisioning Candidates queue
# investigation-focused. Presentation-only: no new backend query, no new
# classification.
#
# X65.60 supersedes this file's original "Flag Reason" column and the
# X65.58-follow-up's "row navigates to entity page" decision: the X65.59
# audit established that Flag Reason (topology + treasury tier
# concatenated) falsely implied topology is part of WATCHTOWER candidate
# qualification. The table now leads with investigation PROGRESS
# (Confidence / Investigation Status / Treasury Progress / Walkback Status
# / Launched); topology moves into an expandable detail row alongside
# creator/subprov/mint, restoring the expand-in-place interaction (with a
# link through to the full entity page still available inside that row).


def test_addr_chip_truncates_and_preserves_full_value():
    start = HTML.index("function addrChip")
    end = HTML.index("\n  }", start)
    body = HTML[start:end + len("\n  }")]
    assert "full.slice(0,4)" in body
    assert "full.slice(-4)" in body
    assert "title=" in body  # full value on hover
    assert "copyToClipboard(" in body  # click to copy


def test_copy_to_clipboard_helper_uses_navigator_clipboard():
    start = HTML.index("function copyToClipboard")
    end = HTML.index("\n  }", start)
    body = HTML[start:end + len("\n  }")]
    assert "navigator.clipboard" in body
    assert "writeText" in body


def test_canonical_table_no_longer_has_isCandidate_branch():
    table_fn = _function("renderKnownWatchtowerAddressTable", "renderKnownWatchtowerTopology")
    assert "isCandidate" not in table_fn
    assert "addrChip(r.mint,'token')" in table_fn
    assert "addrChip(r.creator,'creator')" in table_fn
    assert "addrChip(r.treasury_wallet,'treasury')" in table_fn


def test_flag_reason_function_removed():
    # X65.60 -- superseded entirely; topology + treasury tier are no longer
    # concatenated into one label anywhere.
    assert "function x65_46FlagReason" not in HTML


def test_treasury_progress_reuses_existing_treasury_tier_field():
    helper = _function("x65_60TreasuryProgress", "x65_60WalkbackStatusLabel") \
        if "function x65_60WalkbackStatusLabel" in HTML else _function("x65_60TreasuryProgress", "loadX65_60WalkbackStatusEnrichment")
    assert "campaign_evidence" in helper
    assert "treasury_tier" in helper
    assert "fetch(" not in helper


def test_default_sort_is_confidence_then_recency():
    helper = _function("x65_46SortedCandidates", "x65_60ToggleCandidateDetail") \
        if "function x65_60ToggleCandidateDetail" in HTML else _function("x65_46SortedCandidates", "renderCandidateQueueTable")
    assert "campaign_confidence" in helper
    assert "create_at" in helper
    assert "X65_46_CONFIDENCE_RANK" in helper


def test_confidence_rank_orders_high_before_medium_before_low():
    start = HTML.index("var X65_46_CONFIDENCE_RANK=")
    end = HTML.index(";", start)
    line = HTML[start:end]
    assert "HIGH:0" in line.replace(" ", "")
    assert "MEDIUM:1" in line.replace(" ", "")
    assert "LOW:2" in line.replace(" ", "")


def test_candidate_queue_table_leads_with_investigation_progress_columns():
    # X65.60 -- Flag Reason column removed; replaced with columns describing
    # how close a candidate is to confirmation, not its structural shape.
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    header_start = table_fn.index("<thead>")
    header_end = table_fn.index("</thead>")
    header = table_fn[header_start:header_end]
    assert "Confidence" in header
    assert "Investigation Status" in header
    assert "Treasury Progress" in header
    assert "Walkback Status" in header
    assert "Launched" in header
    assert "Flag Reason" not in header
    # Raw blockchain identifiers must NOT be primary/header-level columns --
    # still relocated to the expandable detail row, not the header.
    assert "Token Mint" not in header
    assert "<th>Creator</th>" not in header
    assert "<th>Treasury</th>" not in header


# X65.60 -- row-click reverts to expand-in-place (superseding the X65.58
# follow-up's navigate-to-entity-page behaviour): the detail row now needs
# to show topology/treasury-tier/creator/subprov/mint TOGETHER with the
# investigation-progress columns already visible in the same table, which
# navigating away would lose at a glance. A link to the full entity page
# is still available from inside the expanded row.


def test_row_click_expands_detail_row():
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "x65_60ToggleCandidateDetail(" in table_fn
    assert "dw-cq-detail" in table_fn
    assert "onclick=\"location.href=" not in table_fn


def test_detail_row_contains_topology_and_treasury_tier_as_supporting_evidence():
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "Observed Topology" in table_fn
    assert "Treasury Tier" in table_fn
    assert "X65_46_TOPOLOGY_LABELS" in table_fn


def test_detail_row_preserves_full_address_detail_and_entity_link():
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "addrChip(r.mint,'token')" in table_fn
    assert "addrChip(r.creator,'creator')" in table_fn
    assert "ev.subprov_wallet" in table_fn
    assert "href(r.mint,'token')" in table_fn  # link through to the entity page still present


def test_no_underlying_data_is_dropped_from_candidate_rows():
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "r.mint" in table_fn
    assert "x65_27CandidateStatus(r.mint)" in table_fn
    assert "r.campaign_confidence" in table_fn
    assert "r.create_at" in table_fn
    assert "r.topology" in table_fn
    assert "r.campaign_evidence" in table_fn


def test_provisioning_candidates_dispatcher_uses_new_table():
    section_fn = _function("renderWatchtowerProvisioningCandidates", "renderKnownWatchtowerAddressTable")
    assert "renderCandidateQueueTable(candidates)" in section_fn
    assert "renderKnownWatchtowerAddressTable" not in section_fn


def test_no_new_backend_query_shape_introduced_by_the_core_table_logic():
    # The table's own sort/render logic introduces no fetch of its own;
    # the ONE fetch (walkback-status enrichment) hits an EXISTING endpoint
    # (see test_walkback_status_enrichment_reuses_existing_endpoint below),
    # not a new backend route or query.
    for fn_name, next_name in [
        ("x65_46SortedCandidates", "x65_60ToggleCandidateDetail"),
    ]:
        fn = _function(fn_name, next_name)
        assert "fetch(" not in fn


def test_walkback_status_enrichment_reuses_existing_endpoint():
    # X65.60 -- Walkback Status is not present on the operational-
    # intelligence payload; this enrichment fetch reuses the EXISTING
    # /api/ops-v2/watchtower-candidates endpoint (unchanged route, no new
    # backend code) purely to merge candidate_status/walkback_result onto
    # already-rendered rows by mint. Non-blocking: the table renders fully
    # from operational-intelligence data first; this only re-renders once
    # the enrichment lands.
    fn = _function("loadX65_60WalkbackStatusEnrichment", "x65_46SortedCandidates")
    assert "/api/ops-v2/watchtower-candidates" in fn
    assert "candidate_status" in fn
    assert "walkback_result" in fn
