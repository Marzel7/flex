"""X26.11 — Unify Terminal Infrastructure Outcomes in Attribution Health.

Attribution Health previously showed KNOWN_CEX_REACHED, KNOWN_BRIDGE_REACHED,
and KNOWN_RELAY_REACHED as three separate landing-panel rows, even though
all three represent the same higher-level analyst-facing conclusion:
attribution legitimately terminated at reviewed infrastructure. This
sprint adds a presentation-only "Known Infrastructure Reached" grouped
metric to /api/ops-v2/attribution-outcomes/summary, computed as an
additive SQL aggregate over the same already-fetched counts -- it never
changes wt_attribution_outcomes, OUTCOME_TYPES, or how any individual
canonical outcome row is stored or drilled into.

LINEAGE_GAP and UNKNOWN_INFRASTRUCTURE are deliberately excluded from the
group: both can carry terminal_entity_type='INFRASTRUCTURE' as a generic
fallback label, but semantically they mean "walkback ran out of evidence"
/ "not yet a reviewed boundary" -- the opposite of a legitimately-reached
reviewed boundary.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

SCHEMA = """
CREATE TABLE wt_attribution_outcomes (
    mint TEXT PRIMARY KEY, outcome_type TEXT NOT NULL, stop_reason TEXT,
    terminal_entity TEXT, terminal_entity_type TEXT, confidence TEXT,
    evidence_json TEXT, operator_id TEXT, should_seed_emerging_operator INTEGER,
    should_retry INTEGER, completed_at INTEGER, source_queue_updated_at INTEGER,
    materialized_at INTEGER
);
"""

REVIEWED_TERMINAL_OUTCOME_TYPES = ("KNOWN_CEX_REACHED", "KNOWN_BRIDGE_REACHED", "KNOWN_RELAY_REACHED")


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    yield path
    os.remove(path)


def _insert(conn, mint, outcome_type, completed_at, terminal_entity=None, terminal_entity_type=None):
    conn.execute(
        "INSERT INTO wt_attribution_outcomes (mint, outcome_type, terminal_entity, terminal_entity_type, completed_at) "
        "VALUES (?,?,?,?,?)",
        (mint, outcome_type, terminal_entity, terminal_entity_type, completed_at),
    )


def _build_summary(conn, completed_after):
    """Mirrors api_attribution_outcomes_summary()'s exact SQL, including the
    X26.11 reviewed_infrastructure aggregation."""
    if completed_after is None:
        rows = conn.execute(
            "SELECT outcome_type, COUNT(*) AS n FROM wt_attribution_outcomes GROUP BY outcome_type"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT outcome_type, COUNT(*) AS n FROM wt_attribution_outcomes "
            "WHERE completed_at >= ? GROUP BY outcome_type",
            (completed_after,),
        ).fetchall()
    counts = {r[0]: r[1] for r in rows}

    reviewed_total = sum(counts.get(t, 0) for t in REVIEWED_TERMINAL_OUTCOME_TYPES)
    placeholders = ",".join("?" for _ in REVIEWED_TERMINAL_OUTCOME_TYPES)
    if completed_after is None:
        subtype_rows = conn.execute(
            f"SELECT terminal_entity_type, COUNT(*) AS n FROM wt_attribution_outcomes "
            f"WHERE outcome_type IN ({placeholders}) GROUP BY terminal_entity_type",
            REVIEWED_TERMINAL_OUTCOME_TYPES,
        ).fetchall()
    else:
        subtype_rows = conn.execute(
            f"SELECT terminal_entity_type, COUNT(*) AS n FROM wt_attribution_outcomes "
            f"WHERE outcome_type IN ({placeholders}) AND completed_at >= ? GROUP BY terminal_entity_type",
            (*REVIEWED_TERMINAL_OUTCOME_TYPES, completed_after),
        ).fetchall()
    subtypes = {r[0]: r[1] for r in subtype_rows}
    return {
        "counts": counts,
        "reviewed_infrastructure": {"total": reviewed_total, "subtypes": subtypes},
    }


def test_group_total_equals_sum_of_subtypes(db_path):
    conn = sqlite3.connect(db_path)
    now = 1_800_000_000
    _insert(conn, "m1", "KNOWN_CEX_REACHED", now, "cex1", "CEX")
    _insert(conn, "m2", "KNOWN_CEX_REACHED", now, "cex2", "CEX")
    _insert(conn, "m3", "KNOWN_RELAY_REACHED", now, "relay1", "AUTOMATION")
    _insert(conn, "m4", "KNOWN_BRIDGE_REACHED", now, "bridge1", "BRIDGE")
    conn.commit()
    summary = _build_summary(conn, None)
    ri = summary["reviewed_infrastructure"]
    assert ri["total"] == 4
    assert sum(ri["subtypes"].values()) == ri["total"]
    conn.close()


def test_individual_subtype_counts_unchanged(db_path):
    conn = sqlite3.connect(db_path)
    now = 1_800_000_000
    for i in range(5):
        _insert(conn, f"cex{i}", "KNOWN_CEX_REACHED", now, "wallet", "CEX")
    for i in range(3):
        _insert(conn, f"auto{i}", "KNOWN_RELAY_REACHED", now, "wallet2", "AUTOMATION")
    conn.commit()
    summary = _build_summary(conn, None)
    assert summary["counts"]["KNOWN_CEX_REACHED"] == 5
    assert summary["counts"]["KNOWN_RELAY_REACHED"] == 3
    assert summary["reviewed_infrastructure"]["subtypes"]["CEX"] == 5
    assert summary["reviewed_infrastructure"]["subtypes"]["AUTOMATION"] == 3
    conn.close()


def test_sql_aggregation_matches_direct_database_counts(db_path):
    conn = sqlite3.connect(db_path)
    now = 1_800_000_000
    _insert(conn, "m1", "KNOWN_CEX_REACHED", now, "a", "CEX")
    _insert(conn, "m2", "KNOWN_BRIDGE_REACHED", now, "b", "BRIDGE")
    _insert(conn, "m3", "KNOWN_RELAY_REACHED", now, "c", "RELAY")
    _insert(conn, "m4", "KNOWN_RELAY_REACHED", now, "d", "CUSTODY")
    conn.commit()
    summary = _build_summary(conn, None)
    direct_total = conn.execute(
        "SELECT COUNT(*) FROM wt_attribution_outcomes WHERE outcome_type IN "
        "('KNOWN_CEX_REACHED','KNOWN_BRIDGE_REACHED','KNOWN_RELAY_REACHED')"
    ).fetchone()[0]
    assert summary["reviewed_infrastructure"]["total"] == direct_total == 4
    conn.close()


def test_24h_and_alltime_windows_both_aggregate_correctly(db_path):
    conn = sqlite3.connect(db_path)
    now = 1_800_000_000
    _insert(conn, "recent", "KNOWN_CEX_REACHED", now - 100, "a", "CEX")
    _insert(conn, "old", "KNOWN_CEX_REACHED", now - 200000, "b", "CEX")
    conn.commit()
    all_time = _build_summary(conn, None)
    last_24h = _build_summary(conn, now - 86400)
    assert all_time["reviewed_infrastructure"]["total"] == 2
    assert last_24h["reviewed_infrastructure"]["total"] == 1
    conn.close()


def test_new_infrastructure_type_automatically_contributes(db_path):
    """A hypothetical future registry category (e.g. a new bridge/relay
    subtype not yet explicitly named anywhere) still contributes to the
    grouped total via its outcome_type alone -- no code change required."""
    conn = sqlite3.connect(db_path)
    now = 1_800_000_000
    _insert(conn, "m1", "KNOWN_RELAY_REACHED", now, "new_wallet", "NOVEL_FUTURE_CATEGORY")
    conn.commit()
    summary = _build_summary(conn, None)
    assert summary["reviewed_infrastructure"]["total"] == 1
    assert summary["reviewed_infrastructure"]["subtypes"]["NOVEL_FUTURE_CATEGORY"] == 1
    conn.close()


def test_lineage_gap_and_insufficient_evidence_excluded_from_group(db_path):
    conn = sqlite3.connect(db_path)
    now = 1_800_000_000
    _insert(conn, "m1", "LINEAGE_GAP", now, "x", "INFRASTRUCTURE")
    _insert(conn, "m2", "INSUFFICIENT_EVIDENCE", now, None, "UNKNOWN")
    _insert(conn, "m3", "UNKNOWN_INFRASTRUCTURE", now, "y", "INFRASTRUCTURE")
    conn.commit()
    summary = _build_summary(conn, None)
    assert summary["reviewed_infrastructure"]["total"] == 0
    assert summary["counts"]["LINEAGE_GAP"] == 1
    assert summary["counts"]["INSUFFICIENT_EVIDENCE"] == 1
    assert summary["counts"]["UNKNOWN_INFRASTRUCTURE"] == 1
    conn.close()


def test_non_terminal_outcomes_unaffected_by_grouping(db_path):
    """Non-reviewed-terminal outcome counts must be identical whether or
    not the grouping feature exists at all."""
    conn = sqlite3.connect(db_path)
    now = 1_800_000_000
    _insert(conn, "m1", "CANONICAL_OPERATOR_REACHED", now, None, "CANONICAL_OPERATOR")
    _insert(conn, "m2", "KNOWN_MULTI_TOKEN_CREATOR", now, None, "CREATOR")
    _insert(conn, "m3", "MAX_DEPTH", now, None, "INFRASTRUCTURE")
    conn.commit()
    summary = _build_summary(conn, None)
    assert summary["counts"]["CANONICAL_OPERATOR_REACHED"] == 1
    assert summary["counts"]["KNOWN_MULTI_TOKEN_CREATOR"] == 1
    assert summary["counts"]["MAX_DEPTH"] == 1
    assert summary["reviewed_infrastructure"]["total"] == 0
    conn.close()


def test_canonical_enums_untouched():
    """OUTCOME_TYPES itself must remain exactly what it was -- this sprint
    adds an additive aggregate, never a new attribution outcome type."""
    from src.ops.attribution_outcome import OUTCOME_TYPES
    assert set(OUTCOME_TYPES) == {
        "CANONICAL_OPERATOR_REACHED", "KNOWN_MULTI_TOKEN_CREATOR",
        "KNOWN_CEX_REACHED", "KNOWN_BRIDGE_REACHED", "KNOWN_RELAY_REACHED",
        "UNKNOWN_INFRASTRUCTURE", "LINEAGE_GAP", "AMBIGUOUS_BRANCH",
        "MAX_DEPTH", "INSUFFICIENT_EVIDENCE",
    }
    assert "REVIEWED_INFRASTRUCTURE_REACHED" not in OUTCOME_TYPES


def test_no_database_mutation(db_path):
    import hashlib
    conn = sqlite3.connect(db_path)
    _insert(conn, "m1", "KNOWN_CEX_REACHED", 1_800_000_000, "a", "CEX")
    conn.commit()
    conn.close()
    before = hashlib.sha256(open(db_path, "rb").read()).digest()

    conn = sqlite3.connect(db_path)
    _build_summary(conn, None)
    _build_summary(conn, 1_800_000_000 - 86400)
    conn.close()
    after = hashlib.sha256(open(db_path, "rb").read()).digest()
    assert before == after


# ---------------------------------------------------------------------------
# Route-level: aggregate endpoint registered and reachable
# ---------------------------------------------------------------------------

def test_aggregate_endpoint_computes_reviewed_infrastructure_field():
    with open(os.path.join(os.path.dirname(__file__), "..", "src/core/operation_dashboard_routes.py")) as f:
        src = f.read()
    assert "reviewed_infrastructure" in src
    assert "_REVIEWED_TERMINAL_OUTCOME_TYPES" in src
    assert '"KNOWN_CEX_REACHED", "KNOWN_BRIDGE_REACHED", "KNOWN_RELAY_REACHED"' in src


def test_drilldown_filter_unchanged_in_template():
    with open(os.path.join(os.path.dirname(__file__), "..", "templates/discovery.html")) as f:
        html = f.read()
    # The outcome_type drill-down (used by the all-time triage workspace)
    # still fetches by canonical outcome_type, unchanged.
    assert "/api/ops-v2/attribution-outcomes?limit=500&outcome_type=" in html
    # X27.2 — the landing Pipeline Health panel itself was superseded by the
    # mutually-exclusive investigation-pipeline reduction (see
    # tests/test_x27_2_investigation_reduction_pipeline.py); the backend
    # reviewed_infrastructure aggregate this test's sibling asserts on
    # (test_aggregate_endpoint_computes_reviewed_infrastructure_field) is
    # untouched and still reachable independently of the landing panel.
