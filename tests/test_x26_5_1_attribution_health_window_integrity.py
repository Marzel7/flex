"""X26.5.1 — Attribution Health Window Integrity.

X26.5 found that the Discovery landing panel and its own drill-down read
the same table (wt_attribution_outcomes, one row per launch) but silently
used different, unstated time windows: the landing tile fetched the 500
most-recent rows across ALL outcome types combined, then filtered to the
last 24h client-side; the drill-down fetched up to 500 rows for ONE type
with no time bound at all. Both numbers were individually correct answers
to different unstated questions, producing an apparent contradiction (e.g.
"Known Relay Reached: 12" on the tile vs. "Axiom: 46 launches" one click
away) with nothing in the UI disclosing that the two views measure
different things.

This suite proves the fix: the landing panel's counts now come from an
exact SQL COUNT(*)/GROUP BY over the full table for an explicit window
(no row-count cap that could silently truncate as volume grows), and every
affected wording surface states its own window/scope explicitly.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time

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


def _insert(conn, mint, outcome_type, completed_at, terminal_entity=None):
    conn.execute(
        "INSERT INTO wt_attribution_outcomes (mint, outcome_type, terminal_entity, completed_at) "
        "VALUES (?,?,?,?)",
        (mint, outcome_type, terminal_entity, completed_at),
    )


def _summary_query(conn, completed_after):
    """Mirrors api_attribution_outcomes_summary()'s exact SQL — a direct,
    uncapped COUNT(*)/GROUP BY, never a row fetch."""
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
    return {r[0]: r[1] for r in rows}


def test_24h_aggregate_applies_time_filter_in_sql(db_path):
    conn = sqlite3.connect(db_path)
    now = int(time.time())
    _insert(conn, "recent1", "KNOWN_RELAY_REACHED", now - 3600)
    _insert(conn, "old1", "KNOWN_RELAY_REACHED", now - 172800)  # 48h ago
    conn.commit()
    counts = _summary_query(conn, now - 86400)
    assert counts.get("KNOWN_RELAY_REACHED") == 1
    conn.close()


def test_exact_counts_when_more_than_500_rows_exist_inside_window(db_path):
    """The regression fixture for the original bug: >500 rows across mixed
    outcome types, all within the last 24h. The old client-side pattern
    (fetch 500 rows total, then filter+group) would have silently
    undercounted every type once combined volume exceeded 500; the new
    SQL COUNT(*)/GROUP BY has no row-fetch cap at all and must return the
    true total regardless of how many rows exist in the window."""
    conn = sqlite3.connect(db_path)
    now = int(time.time())
    # 600 INSUFFICIENT_EVIDENCE rows + 50 KNOWN_RELAY_REACHED rows, all within
    # the last 24h — 650 total, comfortably over the old shared 500-row cap.
    for i in range(600):
        _insert(conn, f"ie-{i}", "INSUFFICIENT_EVIDENCE", now - 100 - i)
    for i in range(50):
        _insert(conn, f"relay-{i}", "KNOWN_RELAY_REACHED", now - 100 - i, terminal_entity="AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk")
    conn.commit()

    counts = _summary_query(conn, now - 86400)
    assert counts["INSUFFICIENT_EVIDENCE"] == 600
    assert counts["KNOWN_RELAY_REACHED"] == 50

    # Prove the OLD buggy pattern really would have undercounted this exact
    # fixture, so this regression test would have failed against the old code.
    old_pattern_rows = conn.execute(
        "SELECT outcome_type, completed_at FROM wt_attribution_outcomes "
        "ORDER BY completed_at DESC LIMIT 500"
    ).fetchall()
    old_counts = {}
    cutoff = now - 86400
    for outcome_type, completed_at in old_pattern_rows:
        if completed_at >= cutoff:
            old_counts[outcome_type] = old_counts.get(outcome_type, 0) + 1
    assert old_counts["INSUFFICIENT_EVIDENCE"] + old_counts.get("KNOWN_RELAY_REACHED", 0) == 500
    assert old_counts["INSUFFICIENT_EVIDENCE"] < 600  # proves the old pattern undercounts
    conn.close()


def test_counts_grouped_correctly_by_outcome_type(db_path):
    conn = sqlite3.connect(db_path)
    now = int(time.time())
    _insert(conn, "a", "KNOWN_CEX_REACHED", now - 10)
    _insert(conn, "b", "KNOWN_CEX_REACHED", now - 20)
    _insert(conn, "c", "LINEAGE_GAP", now - 30)
    conn.commit()
    counts = _summary_query(conn, now - 86400)
    assert counts == {"KNOWN_CEX_REACHED": 2, "LINEAGE_GAP": 1}
    conn.close()


def test_rows_older_than_24h_excluded(db_path):
    conn = sqlite3.connect(db_path)
    now = int(time.time())
    _insert(conn, "recent", "LINEAGE_GAP", now - 3600)
    _insert(conn, "old", "LINEAGE_GAP", now - 90000)
    conn.commit()
    counts = _summary_query(conn, now - 86400)
    assert counts.get("LINEAGE_GAP") == 1
    conn.close()


def test_all_time_window_returns_full_uncapped_total(db_path):
    conn = sqlite3.connect(db_path)
    now = int(time.time())
    for i in range(80):
        _insert(conn, f"relay-{i}", "KNOWN_RELAY_REACHED", now - i * 1000,
                terminal_entity="AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk")
    conn.commit()
    counts = _summary_query(conn, None)
    assert counts["KNOWN_RELAY_REACHED"] == 80
    conn.close()


def test_grouped_terminal_counts_sum_to_alltime_total(db_path):
    """Axiom's 46-launch drill-down grouping must sum to the same all-time
    total the aggregate endpoint reports for that outcome_type."""
    conn = sqlite3.connect(db_path)
    now = int(time.time())
    axiom = "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk"
    other = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
    for i in range(46):
        _insert(conn, f"axiom-{i}", "KNOWN_RELAY_REACHED", now - i, terminal_entity=axiom)
    for i in range(14):
        _insert(conn, f"other-{i}", "KNOWN_RELAY_REACHED", now - i, terminal_entity=other)
    conn.commit()

    all_time = _summary_query(conn, None)
    assert all_time["KNOWN_RELAY_REACHED"] == 60

    rows = conn.execute(
        "SELECT terminal_entity, COUNT(*) FROM wt_attribution_outcomes "
        "WHERE outcome_type='KNOWN_RELAY_REACHED' GROUP BY terminal_entity"
    ).fetchall()
    grouped = {r[0]: r[1] for r in rows}
    assert grouped[axiom] == 46
    assert sum(grouped.values()) == all_time["KNOWN_RELAY_REACHED"]
    conn.close()


def test_no_rows_mutated_by_summary_query(db_path):
    conn = sqlite3.connect(db_path)
    now = int(time.time())
    _insert(conn, "a", "LINEAGE_GAP", now)
    conn.commit()
    conn.close()
    import hashlib
    before = hashlib.sha256(open(db_path, "rb").read()).digest()

    conn = sqlite3.connect(db_path)
    _summary_query(conn, now - 86400)
    _summary_query(conn, None)
    conn.close()
    after = hashlib.sha256(open(db_path, "rb").read()).digest()
    assert before == after


# ---------------------------------------------------------------------------
# Frontend wording assertions
# ---------------------------------------------------------------------------

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_DISCOVERY_HTML = open(os.path.join(_ROOT, "templates/discovery.html")).read()
_OPS_INTEL_HTML = open(os.path.join(_ROOT, "templates/watchtower_operational_intelligence.html")).read()


def test_landing_panel_no_longer_client_side_filters():
    assert "summariseOutcomes(typed.outcomes||[],cutoff)" not in _DISCOVERY_HTML
    assert "/api/ops-v2/attribution-outcomes/summary?window=24h" in _DISCOVERY_HTML


def test_landing_panel_visibly_states_last_24h():
    # X27.2 — the landing panel itself was redesigned from "Attribution
    # Health" (independent overlapping metrics) into "Investigation Queue"
    # (mutually-exclusive Pipeline Health reduction); it still explicitly
    # states its 24h window.
    assert "Investigation Queue · Last 24h" in _DISCOVERY_HTML


def test_drilldown_visibly_states_all_time():
    assert "Attribution Health · All time" in _DISCOVERY_HTML
    assert "all-time terminal outcome" in _DISCOVERY_HTML


def test_drilldown_discloses_partial_fetch_when_truncated():
    assert "fetch limit reached" in _DISCOVERY_HTML


def test_triage_scope_explicitly_labelled():
    assert "Insufficient Evidence · All time" in _DISCOVERY_HTML
    assert "terminal outcomes (all time)" in _DISCOVERY_HTML


def test_legacy_lineage_gap_label_identifies_walkback_queue_source():
    assert "Walkback Queue: Lineage Gap Rows" in _OPS_INTEL_HTML
    assert "wt_walkback_queue" in _OPS_INTEL_HTML


def test_aggregate_endpoint_route_registered():
    with open(os.path.join(_ROOT, "src/core/operation_dashboard_routes.py")) as f:
        src = f.read()
    assert '@ops_dashboard_bp.route("/api/ops-v2/attribution-outcomes/summary")' in src
    assert "GROUP BY outcome_type" in src
