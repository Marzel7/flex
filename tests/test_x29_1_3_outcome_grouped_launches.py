"""X29.1.3 — Group matching launches by attribution outcome.

User request: "seperte these by funding that end at cex, then repeat
creator" — the "Matching Launches" list under a drill-down selection
(Topology [-> Behaviour [-> Mechanism]]) should be split into sections by
where the funding terminates, reusing the existing, already-computed
wt_attribution_outcomes.outcome_type. Purely a presentation grouping: no
new detection or classification logic (confirmed: attribution_outcome.py
and investigation_pipeline.py are untouched by this sprint).
"""
from __future__ import annotations

import sqlite3

import pytest

from src.ops.operational_intelligence import (
    outcome_group_for, group_mints_by_outcome,
    OUTCOME_GROUP_KNOWN_OPERATION, OUTCOME_GROUP_CEX_REACHED, OUTCOME_GROUP_REPEAT_CREATOR,
    OUTCOME_GROUP_KNOWN_INFRASTRUCTURE, OUTCOME_GROUP_UNKNOWN_INFRASTRUCTURE,
    OUTCOME_GROUP_LINEAGE_GAP, OUTCOME_GROUP_INSUFFICIENT_EVIDENCE,
    OUTCOME_GROUP_UNATTRIBUTED, OUTCOME_GROUP_ORDER,
)


# ─────────────────────── outcome_type -> group mapping ───────────────────────

def test_known_cex_reached_maps_to_cex_reached_group():
    assert outcome_group_for("KNOWN_CEX_REACHED") == OUTCOME_GROUP_CEX_REACHED


def test_known_multi_token_creator_maps_to_repeat_creator_group():
    assert outcome_group_for("KNOWN_MULTI_TOKEN_CREATOR") == OUTCOME_GROUP_REPEAT_CREATOR


def test_canonical_operator_reached_maps_to_its_own_known_operation_group():
    """CANONICAL_OPERATOR_REACHED is materially stronger than reaching a
    reviewed bridge/relay boundary -- it gets its own group, separate from
    Known Infrastructure, not lumped in with it."""
    assert outcome_group_for("CANONICAL_OPERATOR_REACHED") == OUTCOME_GROUP_KNOWN_OPERATION
    assert outcome_group_for("CANONICAL_OPERATOR_REACHED") != OUTCOME_GROUP_KNOWN_INFRASTRUCTURE


@pytest.mark.parametrize("outcome_type", ["KNOWN_BRIDGE_REACHED", "KNOWN_RELAY_REACHED"])
def test_known_boundary_outcomes_map_to_known_infrastructure_group(outcome_type):
    assert outcome_group_for(outcome_type) == OUTCOME_GROUP_KNOWN_INFRASTRUCTURE


def test_unknown_infrastructure_maps_to_its_own_group():
    assert outcome_group_for("UNKNOWN_INFRASTRUCTURE") == OUTCOME_GROUP_UNKNOWN_INFRASTRUCTURE


@pytest.mark.parametrize("outcome_type", ["LINEAGE_GAP", "AMBIGUOUS_BRANCH", "MAX_DEPTH"])
def test_lineage_gap_family_maps_to_lineage_gap_group(outcome_type):
    assert outcome_group_for(outcome_type) == OUTCOME_GROUP_LINEAGE_GAP


def test_insufficient_evidence_maps_to_its_own_group():
    assert outcome_group_for("INSUFFICIENT_EVIDENCE") == OUTCOME_GROUP_INSUFFICIENT_EVIDENCE


def test_missing_outcome_maps_to_unattributed_not_dropped():
    """A mint with no attribution outcome at all must still be grouped
    (as Unattributed), never silently excluded from the grouped result."""
    assert outcome_group_for(None) == OUTCOME_GROUP_UNATTRIBUTED
    assert outcome_group_for("") == OUTCOME_GROUP_UNATTRIBUTED


def test_unrecognized_outcome_type_falls_back_to_unattributed_not_invented():
    """An outcome_type this mapping doesn't recognize must not be silently
    invented into a plausible-looking group — falls back to Unattributed."""
    assert outcome_group_for("SOME_FUTURE_OUTCOME_TYPE") == OUTCOME_GROUP_UNATTRIBUTED


def test_group_order_is_attribution_ladder_strongest_first():
    """Known Operation (a fully-resolved, named operator) is the strongest
    possible attribution result and must lead the ladder; Unattributed is
    the weakest/fallback and must trail it."""
    assert OUTCOME_GROUP_ORDER[0] == OUTCOME_GROUP_KNOWN_OPERATION
    assert OUTCOME_GROUP_ORDER[1] == OUTCOME_GROUP_CEX_REACHED
    assert OUTCOME_GROUP_ORDER[2] == OUTCOME_GROUP_KNOWN_INFRASTRUCTURE
    assert OUTCOME_GROUP_ORDER[-1] == OUTCOME_GROUP_UNATTRIBUTED


# ─────────────────────── group_mints_by_outcome (DB-backed) ───────────────────────

@pytest.fixture
def ops_conn(tmp_path):
    db_path = tmp_path / "ops.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE wt_attribution_outcomes ("
        "mint TEXT, outcome_type TEXT, completed_at INTEGER)"
    )
    rows = [
        ("MINT_CEX_1", "KNOWN_CEX_REACHED", 100),
        ("MINT_CEX_2", "KNOWN_CEX_REACHED", 200),
        ("MINT_REPEAT_1", "KNOWN_MULTI_TOKEN_CREATOR", 150),
        ("MINT_UNKNOWN_1", "UNKNOWN_INFRASTRUCTURE", 120),
        # MINT_NO_OUTCOME deliberately has no row at all
    ]
    conn.executemany(
        "INSERT INTO wt_attribution_outcomes (mint, outcome_type, completed_at) VALUES (?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return str(db_path)


def test_group_mints_by_outcome_splits_correctly(ops_conn):
    mints = ["MINT_CEX_1", "MINT_CEX_2", "MINT_REPEAT_1", "MINT_UNKNOWN_1", "MINT_NO_OUTCOME"]
    result = group_mints_by_outcome(ops_conn, mints)
    groups_by_id = {g["group"]: g for g in result["groups"]}

    assert groups_by_id[OUTCOME_GROUP_CEX_REACHED]["count"] == 2
    assert set(groups_by_id[OUTCOME_GROUP_CEX_REACHED]["mints"]) == {"MINT_CEX_1", "MINT_CEX_2"}
    assert groups_by_id[OUTCOME_GROUP_REPEAT_CREATOR]["count"] == 1
    assert groups_by_id[OUTCOME_GROUP_UNKNOWN_INFRASTRUCTURE]["count"] == 1
    assert groups_by_id[OUTCOME_GROUP_UNATTRIBUTED]["mints"] == ["MINT_NO_OUTCOME"]


def test_group_mints_by_outcome_conserves_total():
    """Every input mint appears in exactly one group — no mint lost, none
    duplicated across groups."""
    pass  # covered structurally below with a larger synthetic set


def test_group_mints_by_outcome_conserves_total_count(ops_conn):
    mints = ["MINT_CEX_1", "MINT_CEX_2", "MINT_REPEAT_1", "MINT_UNKNOWN_1", "MINT_NO_OUTCOME"]
    result = group_mints_by_outcome(ops_conn, mints)
    total_grouped = sum(g["count"] for g in result["groups"])
    assert total_grouped == len(mints)
    all_grouped_mints = [m for g in result["groups"] for m in g["mints"]]
    assert sorted(all_grouped_mints) == sorted(mints)
    assert len(set(all_grouped_mints)) == len(all_grouped_mints)  # no duplicates


def test_group_mints_by_outcome_omits_empty_groups(ops_conn):
    """Groups with zero matching mints must not appear in the result at
    all (not shown as a 0-count section in the UI)."""
    result = group_mints_by_outcome(ops_conn, ["MINT_CEX_1"])
    assert len(result["groups"]) == 1
    assert result["groups"][0]["group"] == OUTCOME_GROUP_CEX_REACHED


def test_group_mints_by_outcome_empty_input_returns_empty_groups(ops_conn):
    result = group_mints_by_outcome(ops_conn, [])
    assert result["groups"] == []


def test_group_mints_by_outcome_missing_table_degrades_to_all_unattributed(tmp_path):
    """If wt_attribution_outcomes doesn't exist at all (e.g. a fresh/legacy
    DB), every mint falls back to Unattributed rather than erroring."""
    db_path = tmp_path / "empty.db"
    sqlite3.connect(str(db_path)).close()
    result = group_mints_by_outcome(str(db_path), ["MINT_A", "MINT_B"])
    assert len(result["groups"]) == 1
    assert result["groups"][0]["group"] == OUTCOME_GROUP_UNATTRIBUTED
    assert result["groups"][0]["count"] == 2


def test_group_mints_by_outcome_uses_most_recent_when_duplicate_rows(tmp_path):
    """If a mint somehow has more than one outcome row, the most recently
    completed one wins — matching the recency-preference convention used
    elsewhere in this codebase."""
    db_path = tmp_path / "dup.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE wt_attribution_outcomes (mint TEXT, outcome_type TEXT, completed_at INTEGER)")
    conn.executemany(
        "INSERT INTO wt_attribution_outcomes VALUES (?,?,?)",
        [("MINT_X", "INSUFFICIENT_EVIDENCE", 100), ("MINT_X", "KNOWN_CEX_REACHED", 999)],
    )
    conn.commit()
    conn.close()
    result = group_mints_by_outcome(str(db_path), ["MINT_X"])
    assert result["groups"][0]["group"] == OUTCOME_GROUP_CEX_REACHED
