"""OF-DV34-P3: provider-free tests for the main-DB (123-member) vs
new-discovery (23-member) Dv34 reconciliation.

No provider calls. No production writes. Tests execute against the real
local databases read-only (mode=ro) where practical, and against the
committed reconciliation artifacts otherwise.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DV34 = "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM"

MAIN_ARTIFACT = ROOT / "docs/audits/of_dv34_p3_main_db_70_vs_new_23_reconciliation.json"
MATRIX_ARTIFACT = ROOT / "docs/audits/of_dv34_p3_70_member_reconciliation_matrix.json"
FUNNEL_ARTIFACT = ROOT / "docs/audits/of_dv34_p3_discovery_coverage_funnel.json"

VALID_REASONS = {
    "HIGH_CONFIDENCE_QUALIFIED",
    "FUNDING_GAP_GT_3600S",
    "DIRECT_EDGE_NOT_PROJECTED_LOST_RECENCY_TIEBREAK",
    "OUTSIDE_DISCOVERY_INPUT_POPULATION",
    "CREATOR_FUNDERS_ONLY_NO_TRANSFER_INDEX_ROW",
}


@pytest.fixture(scope="module")
def main_doc():
    return json.loads(MAIN_ARTIFACT.read_text())


@pytest.fixture(scope="module")
def matrix_doc():
    return json.loads(MATRIX_ARTIFACT.read_text())


@pytest.fixture(scope="module")
def funnel_doc():
    return json.loads(FUNNEL_ARTIFACT.read_text())


def test_artifacts_exist():
    assert MAIN_ARTIFACT.exists()
    assert MATRIX_ARTIFACT.exists()
    assert FUNNEL_ARTIFACT.exists()


def test_real_main_population_freeze_matches_live_query(main_doc):
    """The Part 1 population count must match a live, read-only recount --
    catches drift between the frozen artifact and the actual DB state."""
    conn = sqlite3.connect(f"file:{ROOT}/database/flex_complete_database.db?mode=ro", uri=True)
    count = conn.execute(
        "SELECT COUNT(*) FROM creator_funders WHERE funder_address=?", (DV34,)
    ).fetchone()[0]
    conn.close()
    assert count == main_doc["part1_freeze_the_authoritative_population"]["closest_real_populations_found"][
        "creator_funders_full_relationship_population"
    ]
    assert count == 123


def test_claimed_70_79_population_not_locally_reproducible(main_doc):
    """The human's claimed 70/79 figures must be honestly reported as not
    found locally, not silently forced to match."""
    finding = main_doc["part1_freeze_the_authoritative_population"]["finding"]
    assert finding.startswith("NO_LOCAL_SOURCE_PRODUCES_70_OR_79_FOR_DV34")


def test_new_discovery_23_reproducible_via_real_pipeline_function():
    """Re-executes the ACTUAL production function (not a hand replay) and
    confirms it yields exactly 23 for Dv34 -- this is the test that would
    fail if the earlier manual-SQL-replay bug (29 vs 23) were mistaken for
    a real pipeline bug."""
    import sys

    sys.path.insert(0, str(ROOT))
    from src.discovery.local_operation_discovery_projection import (
        build_high_confidence_direct_funding_edges,
    )

    source = sqlite3.connect(f"file:{ROOT}/database/flex_complete_database.db?mode=ro", uri=True)
    out = sqlite3.connect(":memory:")
    out.execute(
        """CREATE TABLE direct_funding_edges (
        run_id TEXT, mint TEXT, create_creator TEXT, direct_funder TEXT, funding_signature TEXT,
        amount_lamports INTEGER, funding_block_time INTEGER, migrated_at INTEGER, gap_seconds INTEGER,
        confidence TEXT, has_extraction_failure INTEGER)"""
    )
    build_high_confidence_direct_funding_edges(source, out, "test_run")
    count = out.execute(
        "SELECT COUNT(*) FROM direct_funding_edges WHERE direct_funder=?", (DV34,)
    ).fetchone()[0]
    source.close()
    out.close()
    assert count == 23


def test_intersection_is_exact_clean_subset(main_doc):
    part4 = main_doc["part4_exact_set_comparison"]
    assert part4["intersection"] == 23
    assert part4["new_discovery_only"] == 0
    assert part4["main_db_only"] == 100
    assert part4["intersection"] + part4["main_db_only"] == 123


def test_no_unexplained_members_in_matrix(matrix_doc):
    """Every single one of the 123 members must have a reason drawn from the
    fixed category list -- no 'UNCLASSIFIED' or free-text reason allowed."""
    matrix = matrix_doc["matrix"]
    assert len(matrix) == 123
    for row in matrix:
        assert row["classification_reason"] in VALID_REASONS, row


def test_classification_distribution_sums_to_difference_set(main_doc):
    dist = main_doc["part6_reconcile_the_difference_100_members"]["classification_distribution"]
    assert sum(dist.values()) == 100


def test_79_transition_not_fabricated(main_doc):
    part2 = main_doc["part2_explain_79_to_70"]
    assert part2["finding"] == "NO_EVIDENCE_OF_79_TRANSITION_FOUND"


def test_low_confidence_not_conflated_with_false(main_doc):
    """Part 7 must preserve VALID_HISTORICAL_ASSOCIATION_BUT_NOT_HIGH_QUALIFIED
    as a real category, not silently drop these members as 'wrong'."""
    part7 = main_doc["part7_low_is_not_wrong"]
    assert part7["principle_applied"] == "VALID_HISTORICAL_ASSOCIATION_BUT_NOT_HIGH_QUALIFIED"
    assert part7["count_with_valid_historical_association_not_high_qualified"] == 82


def test_direct_vs_recency_tiebreak_distinction_exists(main_doc):
    """The 6-member tie-break group must be classified distinctly from the
    76-member gap-failure group -- collapsing them would hide a real
    semantic difference (multi-funder co-funding vs simple timing failure)."""
    dist = main_doc["part6_reconcile_the_difference_100_members"]["classification_distribution"]
    assert dist["DIRECT_EDGE_NOT_PROJECTED_LOST_RECENCY_TIEBREAK"] == 6
    assert dist["FUNDING_GAP_GT_3600S"] == 76
    assert dist["DIRECT_EDGE_NOT_PROJECTED_LOST_RECENCY_TIEBREAK"] != dist["FUNDING_GAP_GT_3600S"]


def test_recency_tiebreak_members_verified_against_live_data():
    """Structural verification (not just trusting the artifact) that all 6
    tie-break members really do lose to a more-recent competing funder."""
    conn = sqlite3.connect(f"file:{ROOT}/database/flex_complete_database.db?mode=ro", uri=True)
    matrix = json.loads(MATRIX_ARTIFACT.read_text())["matrix"]
    tiebreak_creators = [
        r["creator_address"]
        for r in matrix
        if r["classification_reason"] == "DIRECT_EDGE_NOT_PROJECTED_LOST_RECENCY_TIEBREAK"
    ]
    assert len(tiebreak_creators) == 6
    for creator in tiebreak_creators:
        row = conn.execute(
            """
            SELECT ti.source FROM token_analysis ta
            JOIN pumpfun_migration_verification pmv ON ta.mint = pmv.mint
            JOIN transfer_index ti ON ti.destination = ta.pf_ws_creator
            WHERE ta.pf_ws_creator=? AND ti.source != ta.pf_ws_creator
              AND ti.block_time < pmv.migrated_at AND ti.amount_lamports >= 10000000
              AND (pmv.migrated_at - ti.block_time) <= 3600
            ORDER BY ti.block_time DESC LIMIT 1
            """,
            (creator,),
        ).fetchone()
        assert row is not None
        assert row[0] != DV34, f"{creator} should lose the tie-break to a non-Dv34 funder"
    conn.close()


def test_funnel_stages_monotonically_non_increasing(funnel_doc):
    stages = list(funnel_doc["funnel"].values())
    for a, b in zip(stages, stages[1:]):
        assert b <= a


def test_funnel_final_stage_matches_23(funnel_doc):
    assert funnel_doc["funnel"]["final_high_confidence_dv34_family"] == 23


def test_dual_source_representation_sums_to_123(main_doc):
    part13 = main_doc["part13_dual_source_representation"]
    assert part13["check_sums_to_123"] is True
    model = part13["proposed_model"]
    total = (
        model["HIGH_EVIDENCE_QUALIFIED"]
        + model["VALID_HISTORICAL_ASSOCIATION_NOT_HIGH_QUALIFIED"]
        + model["HISTORICAL_ONLY_NOT_REPRODUCIBLE"]
    )
    assert total == 123


def test_no_local_upgrade_recommended_without_weakening_standards(main_doc):
    """Part 11 must not recommend promoting any member by loosening the
    calibrated thresholds -- locally_upgradable must be 0."""
    part11 = main_doc["part11_local_upgradability"]
    assert part11["locally_upgradable_without_weakening_standards"] == 0


def test_no_selective_rpc_recommended_this_milestone(main_doc):
    """Part 19-equivalent guard: this milestone must not recommend new RPC,
    since it is an accounting/coverage reconciliation, not a funding-edge
    re-verification."""
    part11 = main_doc["part11_local_upgradability"]
    assert part11["missing_requires_selective_rpc"] == 0


def test_main_population_write_attestation_present(main_doc):
    part12 = main_doc["part12_main_population_unchanged"]
    assert "No write" in part12["attestation"]
    assert "mode=ro" in part12["attestation"]


def test_systemic_pattern_check_present_and_bounded(main_doc):
    """Part 14 diagnostic must be present, bounded (a handful of other
    funders, not a full re-run), and support the same ratio order of
    magnitude as Dv34's own 123:23."""
    part14 = main_doc["part14_systemic_check"]
    assert 2 <= len(part14["findings"]) <= 5
    for f in part14["findings"]:
        assert f["ratio"] >= 1.0
    assert part14["dv34_ratio_for_comparison"] == round(123 / 23, 1)


def test_terminal_verdict_is_one_of_defined_set(main_doc):
    assert main_doc["terminal_verdict"] in {
        "DV34_70_VS_23_MEMBERSHIP_RECONCILED",
        "DV34_NEW_DISCOVERY_COVERAGE_GAP_CONFIRMED",
        "HOLD_DV34_NEW_DISCOVERY_PROJECTION_BUG",
        "HOLD_DV34_MEMBERSHIP_RECONCILIATION_INCOMPLETE",
    }


def test_deterministic_digest_present_and_stable(main_doc):
    assert "deterministic_digest" in main_doc
    assert len(main_doc["deterministic_digest"]) == 64


def test_no_write_statements_in_this_milestones_python_snippets():
    """Structural guard: no .execute(...) call in this test module may pass
    a DB-mutating statement against the two real production tables this
    milestone reads from."""
    src = Path(__file__).read_text()
    lines = src.splitlines()
    execute_lines = [ln for ln in lines if ".execute(" in ln and "test_no_write_statements" not in ln]
    combined = "\n".join(execute_lines).upper()
    for table in ("CREATOR_FUNDERS", "TRANSFER_INDEX"):
        for verb in ("INSERT INTO " + table, "UPDATE " + table, "DELETE FROM " + table):
            assert verb not in combined, f"forbidden write found: {verb}"
