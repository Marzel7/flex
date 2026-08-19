"""Provider-free tests for OF-DV34-P2 -- operation-identity separation review.

All tests operate on in-memory Python structures (no DB connection, no
network). They exercise the real functions in
src/analysis/dv34_p2_identity_review.py so the counterfactual checks are
genuine (able to fail on bad logic), not tautological JSON-shape assertions.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.analysis.dv34_p2_identity_review import (
    DV34_ADDRESS,
    DV34_UPSTREAM_HUB,
    check_no_confirmed_state_mutation,
    compute_cross_operation_provisioner_signal,
    compute_exact_amount_group_independence,
    compute_internal_subfamilies,
    compute_watchtower_boundary_counterfactual,
    compute_watchtower_membership_overlap,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TECH_ARTIFACT = REPO_ROOT / "docs/audits/of_dv34_p2_operation_identity_review.json"
HUMAN_ARTIFACT = REPO_ROOT / "docs/audits/of_dv34_p2_human_identity_review_packet.json"


# ---------------------------------------------------------------------------
# confirmed-vs-candidate Watchtower separation
# ---------------------------------------------------------------------------

def test_confirmed_and_candidate_hits_never_conflated():
    """A creator that is only in the CANDIDATE set must not leak into the
    confirmed_* keys, and vice versa."""
    dv34_mints = ["mintA", "mintB"]
    dv34_creators = ["creatorA", "creatorB"]
    result = compute_watchtower_membership_overlap(
        dv34_mints,
        dv34_creators,
        confirmed_treasury_addresses=["someone_else"],
        confirmed_launch_mints=[],
        candidate_mints=["mintA"],
        candidate_creators=["creatorA"],
    )
    assert result["confirmed_membership_count"] == 0
    assert result["candidate_overlap_count"] == 2
    assert result["candidate_mint_hits"] == ["mintA"]
    assert result["candidate_creator_hits"] == ["creatorA"]
    # confirmed keys must stay empty even though candidate keys hit
    assert result["confirmed_treasury_hits"] == []
    assert result["confirmed_launch_mint_hits"] == []


def test_confirmed_membership_detected_when_genuinely_present():
    """Sanity: the function CAN detect a real confirmed hit (not hardcoded
    to always return zero)."""
    result = compute_watchtower_membership_overlap(
        ["mintA"], ["creatorA"],
        confirmed_treasury_addresses=["creatorA"],
        confirmed_launch_mints=["mintA"],
        candidate_mints=[], candidate_creators=[],
    )
    assert result["confirmed_membership_count"] == 2
    assert "creatorA" in result["confirmed_treasury_hits"]
    assert "mintA" in result["confirmed_launch_mint_hits"]


# ---------------------------------------------------------------------------
# shared-source evidence is not double-counted
# ---------------------------------------------------------------------------

def test_dv34_and_upstream_hub_excluded_as_connecting_nodes_by_default():
    """Secondary edges routed through Dv34 itself or its own CEX upstream hub
    must NOT be usable to merge two creators -- that would double-count the
    same shared-source signal already used to build the family."""
    creators = ["c1", "c2"]
    edges = [("c1", DV34_ADDRESS), ("c2", DV34_ADDRESS)]  # only Dv34 links them
    result = compute_internal_subfamilies(creators, edges)
    assert result["cluster_count"] == 2  # NOT merged
    assert result["no_independent_clustering_found"] is True

    edges_hub = [("c1", DV34_UPSTREAM_HUB), ("c2", DV34_UPSTREAM_HUB)]
    result_hub = compute_internal_subfamilies(creators, edges_hub)
    assert result_hub["cluster_count"] == 2  # NOT merged via the CEX hub either


def test_genuine_non_hub_shared_funder_does_merge():
    """A shared funder that is NOT Dv34 or its upstream hub SHOULD merge two
    creators -- proves the function can produce real clustering, not just
    all-singletons by construction."""
    creators = ["c1", "c2", "c3"]
    edges = [("c1", "otherFunderX"), ("c2", "otherFunderX")]
    result = compute_internal_subfamilies(creators, edges)
    assert result["cluster_count"] == 2  # {c1,c2} merged, c3 alone
    assert ["c1", "c2"] in result["multi_member_clusters"]
    assert result["no_independent_clustering_found"] is False


def test_exact_amount_group_same_source_flagged_not_independent():
    result = compute_exact_amount_group_independence(
        2986500000, ["mintA", "mintB"],
        watchtower_historical_note_source="creator_funders",
        dv34_local_evidence_source="creator_funders",
    )
    assert result["classification"] == "SAME_SOURCE_DOUBLE_COUNT"


def test_exact_amount_group_genuinely_disjoint_source_flagged_independent():
    result = compute_exact_amount_group_independence(
        2986500000, ["mintA", "mintB"],
        watchtower_historical_note_source="raw_rpc_reverification_corpus",
        dv34_local_evidence_source="transfer_index",
    )
    assert result["classification"] == "INDEPENDENT_CORROBORATION"


# ---------------------------------------------------------------------------
# Dv34-funding-removed counterfactual is actually COMPUTED
# ---------------------------------------------------------------------------

def test_watchtower_boundary_counterfactual_computes_no_when_unlinked():
    dv34_creators = ["c1", "c2", "c3"]
    result = compute_watchtower_boundary_counterfactual(
        dv34_creators,
        watchtower_confirmed_creator_universe=set(),
        independent_link_count_to_confirmed_watchtower=0,
    )
    assert result["verdict"] == "NO"
    assert result["counterfactual_still_groups_with_watchtower"] is False


def test_watchtower_boundary_counterfactual_computes_yes_when_directly_linked():
    """The function must be sensitive to input -- if a creator genuinely
    overlaps confirmed Watchtower, the verdict flips to YES. Not hardcoded."""
    dv34_creators = ["c1", "c2", "c3"]
    result = compute_watchtower_boundary_counterfactual(
        dv34_creators,
        watchtower_confirmed_creator_universe={"c2"},
        independent_link_count_to_confirmed_watchtower=0,
    )
    assert result["verdict"] == "YES"
    assert result["direct_creator_identity_overlap_with_confirmed_watchtower"] == ["c2"]


def test_watchtower_boundary_counterfactual_computes_yes_via_independent_link_only():
    """Even with zero direct overlap, a nonzero independent-link count must
    flip the verdict -- proves the rule genuinely branches on both inputs."""
    result = compute_watchtower_boundary_counterfactual(
        ["c1"], watchtower_confirmed_creator_universe=set(),
        independent_link_count_to_confirmed_watchtower=1,
    )
    assert result["verdict"] == "YES"


# ---------------------------------------------------------------------------
# creator-role vs migration-signer separation is real
# ---------------------------------------------------------------------------

def test_creator_role_and_migration_signer_are_structurally_distinct_fields():
    """The technical artifact must keep dimension_3 (CREATE creator) and
    dimension_4 (migration signer) as separate keys with separate content --
    not the same value aliased under two names."""
    doc = json.loads(TECH_ARTIFACT.read_text())
    d3 = doc["dimension_3_create_creator_population_overlap"]
    d4 = doc["dimension_4_migration_signer_population_overlap"]
    assert d3 is not d4
    assert "pf_ws_creator" in json.dumps(d3)
    assert "migration_signer" in json.dumps(d4) or "migration-signer" in json.dumps(d4)
    # d4 must be reported as unavailable, not silently filled from d3's data
    assert d4["status"] == "NOT_LOCALLY_AVAILABLE_AT_SCALE"
    assert d4.get("kept_strictly_separate_from_dimension_3") is True


# ---------------------------------------------------------------------------
# CEX lineage preservation
# ---------------------------------------------------------------------------

def test_cex_infra_funder_never_used_as_sole_clustering_evidence():
    """A shared CEX/INFRA funder alone must never merge two creators -- the
    module hard-excludes Dv34's own known CEX upstream hub, and the pattern
    generalizes: only non-hub funders are eligible to merge clusters."""
    creators = ["c1", "c2"]
    # Both funded only by the known CEX hub -- must stay separate
    edges = [("c1", DV34_UPSTREAM_HUB), ("c2", DV34_UPSTREAM_HUB)]
    result = compute_internal_subfamilies(creators, edges)
    assert result["cluster_count"] == 2
    assert DV34_UPSTREAM_HUB in result["excluded_hub_addresses"]


def test_cross_operation_signal_distinguishes_cex_fanout_from_dv34_provisioning():
    """CEX-hub fanout to unrelated direct-funder roots must be labeled as
    CEX withdrawal fanout, not silently treated as Dv34-itself evidence."""
    result = compute_cross_operation_provisioner_signal(
        upstream_hub_direct_funder_roots=[DV34_ADDRESS, "otherRoot1", "otherRoot2"],
    )
    assert result["interpretation"] == "CEX_WITHDRAWAL_FANOUT_NOT_OPERATOR_EVIDENCE"
    assert DV34_ADDRESS not in result["other_direct_funder_roots_sharing_dv34_upstream_hub"]
    assert result["count_other_roots"] == 2


# ---------------------------------------------------------------------------
# internal-subfamily detection is a real partition
# ---------------------------------------------------------------------------

def test_internal_subfamily_not_rubber_stamped_all_one_family():
    """With zero independent secondary edges, the 23-style population must
    resolve to N singletons, NOT a fabricated single family -- proves the
    function doesn't just always report 'all one family'."""
    creators = [f"creator{i}" for i in range(23)]
    result = compute_internal_subfamilies(creators, secondary_funder_edges=[])
    assert result["cluster_count"] == 23
    assert result["all_resolve_to_one_family"] is False
    assert result["no_independent_clustering_found"] is True


def test_internal_subfamily_can_report_true_unification_when_warranted():
    """Conversely, when every creator DOES share a genuine non-hub funder,
    the function must report a single unified cluster -- proves it isn't
    hardcoded to always report singletons either."""
    creators = ["c1", "c2", "c3"]
    edges = [(c, "sharedRealFunder") for c in creators]
    result = compute_internal_subfamilies(creators, edges)
    assert result["all_resolve_to_one_family"] is True
    assert result["cluster_count"] == 1


def test_actual_dv34_population_produces_documented_zero_clustering_result():
    """Regression-pin: the technical artifact's dimension_10 claims 0 of 23
    cluster under independent evidence. Recompute with the module directly
    on the same 23-creator population size to confirm the artifact's claim
    is at minimum internally plausible (structural check, not a live DB
    re-query)."""
    doc = json.loads(TECH_ARTIFACT.read_text())
    d10 = doc["dimension_10_dv34_internal_boundary_counterfactual"]
    assert d10["result"]["total_creators"] == 23
    assert d10["result"]["multi_member_clusters"] == 0
    assert "0 of 23" in d10["how_many_remain_clustered"]

    # genuine re-derivation with a matching-shape synthetic input (no DB call)
    creators = [f"creator{i}" for i in range(23)]
    # simulate 3 CEX/hub-routed edges (excluded) + rest none, matching the
    # documented "0 non-hub links found" outcome
    edges = [("creator0", DV34_UPSTREAM_HUB), ("creator1", DV34_UPSTREAM_HUB)]
    result = compute_internal_subfamilies(creators, edges)
    assert result["multi_member_clusters"] == []
    assert result["singleton_count"] == 23


# ---------------------------------------------------------------------------
# cross-operation-provisioner handling doesn't mutate anything
# ---------------------------------------------------------------------------

def test_cross_operation_signal_is_pure_no_db_handles_accepted():
    """compute_cross_operation_provisioner_signal must accept plain lists
    only -- it has no DB connection parameter, so it structurally cannot
    write anywhere."""
    import inspect
    sig = inspect.signature(compute_cross_operation_provisioner_signal)
    for param in sig.parameters.values():
        ann = str(param.annotation)
        assert ann in ("<class 'inspect._empty'>", "str") or "Sequence" in ann


# ---------------------------------------------------------------------------
# candidate/canonical authority respected -- no membership changed anywhere
# ---------------------------------------------------------------------------

def test_no_module_function_contains_write_verbs_in_source():
    import src.analysis.dv34_p2_identity_review as mod
    src_text = Path(mod.__file__).read_text().lower()
    forbidden = ["insert into", "update ", "delete from", " drop table", "execute(", "cursor.execute", "conn.commit"]
    hits = [f for f in forbidden if f in src_text]
    assert hits == [], f"module contains apparent write operations: {hits}"


def test_no_automatic_watchtower_promotion_structural_guard():
    clean = check_no_confirmed_state_mutation([
        "SELECT * FROM wt_confirmed_treasuries",
        "SELECT creator FROM watchtower_token_attribution",
    ])
    assert clean["clean"] is True

    dirty = check_no_confirmed_state_mutation([
        "INSERT INTO wt_confirmed_treasuries (treasury) VALUES ('x')",
    ])
    assert dirty["clean"] is False
    assert len(dirty["violations"]) == 1


def test_no_automatic_promotion_flag_in_technical_artifact():
    doc = json.loads(TECH_ARTIFACT.read_text())
    safety = doc["operation_safety"]
    assert safety["watchtower_mutated"] is False
    assert safety["three_sw2_mutated"] is False
    assert safety["canonical_operations_mutated"] is False
    assert safety["confirmed_state_tables_written"] == []
    assert safety["provider_calls_made"] == 0


# ---------------------------------------------------------------------------
# artifact internal consistency: evidence matrix vs hypothesis citations
# ---------------------------------------------------------------------------

def test_hypothesis_support_never_cites_a_shared_source_dimension_as_independent():
    """If a hypothesis's 'source_independence' text claims a dimension is
    independent while the evidence matrix marks that same dimension
    SHARED_SOURCE, that is an internal contradiction the artifact must not
    contain."""
    doc = json.loads(TECH_ARTIFACT.read_text())
    matrix = doc["dimension_13_evidence_independence_matrix"]
    shared_source_dims = {k for k, v in matrix.items() if v == "SHARED_SOURCE"}

    # H1's only supporting evidence must not be misdescribed as independent
    h1 = doc["hypotheses"]["H1_part_of_watchtower"]
    assert "shared" in h1["source_independence"].lower() or "SHARED_SOURCE" in h1["source_independence"]

    # exact-amount-group dimension is SHARED_SOURCE in the matrix
    assert "internal_dv34_coherence_amount_groups" in shared_source_dims
    assert "exact_amount_group_deep_dive" in shared_source_dims
    # dimension_11's own conclusion must agree it is not independent corroboration
    d11 = doc["dimension_11_exact_amount_group_deep_dive"]
    assert "NOT independent" in d11["overall_conclusion"] or "SAME_SOURCE" in json.dumps(d11)


def test_watchtower_boundary_verdict_matches_dimension_9():
    doc = json.loads(TECH_ARTIFACT.read_text())
    assert doc["dimension_9_watchtower_boundary_counterfactual"]["verdict"] == "NO"
    human = json.loads(HUMAN_ARTIFACT.read_text())
    assert "NOT SUPPORTED" in human["five_hypotheses"]["H1_part_of_watchtower"]


def test_human_packet_recommended_disposition_is_a_valid_enum_value():
    valid = {
        "DV34_SEPARATE_OPERATION_CANDIDATE",
        "DV34_WATCHTOWER_RELATED_CANDIDATE",
        "DV34_PRIVATE_PROVISIONING_NETWORK_CANDIDATE",
        "DV34_CROSS_OPERATION_PROVISIONER_CANDIDATE",
        "DV34_OPERATION_IDENTITY_UNRESOLVED",
    }
    human = json.loads(HUMAN_ARTIFACT.read_text())
    assert human["recommended_disposition"] in valid


def test_escalation_note_present_only_alongside_primary_not_as_primary():
    human = json.loads(HUMAN_ARTIFACT.read_text())
    assert human["recommended_disposition"] != "DV34_WATCHTOWER_MEMBERSHIP_REVIEW"
    assert "escalation_note" in human
    assert "DV34_WATCHTOWER_MEMBERSHIP_REVIEW" in human["escalation_note"]


def test_technical_artifact_digest_matches_recomputation():
    import hashlib
    doc = json.loads(TECH_ARTIFACT.read_text())
    digest_in_file = doc.pop("deterministic_digest")
    recomputed = hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()
    assert digest_in_file == recomputed


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
