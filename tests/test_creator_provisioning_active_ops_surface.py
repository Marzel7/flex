"""Classification UI contract for the historically established direct-10K operation."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from src.ops import potential_operations
from src.ops import operator_reader
from src.ops.operator_reader import OperatorReader, activity_read_model
from src.ops.operator_reader import _byzantine_infrastructure_activity
from src.ops.operator_reader import _nexus_detector_projection


OID = "bd7d7479-1454-5d41-9f68-115550348f3e"
CID = "p3r-v2-6437acd385e566e301a7"


def test_direct_10k_is_active_with_live_windows_and_prospective_hold(tmp_path, monkeypatch):
    path = tmp_path / "ops.db"
    now = 2_000_000_000
    monkeypatch.setattr(operator_reader.time, "time", lambda: now)
    conn = sqlite3.connect(path)
    conn.executescript("""
      CREATE TABLE operators (operator_id TEXT PRIMARY KEY, display_name TEXT, status TEXT, updated_at INTEGER);
      CREATE TABLE operation_registry_dispositions (operator_id TEXT, disposition TEXT, source_candidate_id TEXT, updated_at INTEGER);
      CREATE TABLE operation_qualification_contracts (operator_id TEXT, qualification_category TEXT, automation_eligibility TEXT, detector_version TEXT, parent_mechanism TEXT, benchmark_json TEXT);
      CREATE TABLE operation_activity_snapshots (snapshot_id TEXT PRIMARY KEY, operator_id TEXT, observed_at INTEGER, timestamp_semantics TEXT, metrics_json TEXT, activity_state TEXT);
      CREATE TABLE operation_behavioural_profiles (operator_id TEXT, profile_version INTEGER, member_mints_json TEXT, provenance_json TEXT);
      CREATE TABLE operator_launch_membership (operator_id TEXT, mint TEXT);
      CREATE TABLE wt_walkback_queue (mint TEXT, create_anchor_block_time INTEGER, funder_block_time INTEGER, completed_at INTEGER);
    """)
    conn.execute("INSERT INTO operators VALUES (?,?,?,?)", (OID, "Creator Launch Provisioning", "ACTIVE", 1))
    conn.execute("INSERT INTO operation_registry_dispositions VALUES (?,?,?,?)", (OID, "ACTIVE_MANUAL", CID, 1))
    conn.execute("INSERT INTO operation_behavioural_profiles VALUES (?,?,?,?)", (OID, 1, json.dumps(["a", "b", "c"]), json.dumps({"detector": "DIRECT_10K_CREATOR_PROVISIONING"})))
    for mint, stamp in (("a", now - 3600), ("b", now - 2 * 86400), ("c", now - 10 * 86400)):
        conn.execute("INSERT INTO operator_launch_membership VALUES (?,?)", (OID, mint))
        conn.execute("INSERT INTO wt_walkback_queue VALUES (?,?,?,?)", (mint, stamp, None, None))
    conn.commit(); conn.close()

    row = next(item for item in OperatorReader(str(path)).fetch_active_manual_operators() if item["operator_id"] == OID)
    assert (row["live_launches_24h"], row["live_launches_7d"], row["live_launches_30d"]) == (1, 2, 3)
    assert row["prospective_detector_status"] == "HOLD"
    assert "retained transaction-role evidence" in row["prospective_detector_detail"]
    assert row["total_launches"] == 3  # Established membership backs the count without a snapshot.


def test_direct_10k_candidate_is_excluded_from_potential_rows(monkeypatch, tmp_path):
    direct = {"candidate_id": CID, "canonical_tier": "TIER_0001", "new_rank": 1,
              "priority_rank": 1, "operational_likeness": 1.0, "activity_score": 1.0, "priority_score": 1.0, "operation_priority_score": 1.0}
    other = {"candidate_id": "other", "canonical_tier": "TIER_0001", "new_rank": 2,
             "priority_rank": 2, "operational_likeness": 0.5, "activity_score": 0.5, "priority_score": 0.5, "operation_priority_score": 0.5}
    monkeypatch.setattr(potential_operations, "_frozen_workflow_rows", lambda: [direct, other])
    monkeypatch.setattr(potential_operations, "_current_census_evidence", lambda: {})
    monkeypatch.setattr("src.ops.live_potential_activity.aggregate", lambda _path: ({}, {}))
    monkeypatch.setattr(potential_operations, "_creator_quality", lambda _row: {"creator_risk_class": "INSUFFICIENT_DATA", "creator_quality_label": "CREATOR DATA LIMITED"})
    visible = potential_operations.rows(str(tmp_path / "potential.db"))
    assert [row["candidate_id"] for row in visible] == ["other"]
    assert potential_operations._overrides(CID)["related_operator_id"] == OID


def test_active_window_boundaries_match_registry_contract():
    now = 2_000_000_000
    model = activity_read_model([now - 86400, now - 86400 + 1, now - 7 * 86400 + 1, now - 30 * 86400 + 1], {}, None, now=now)
    assert (model["live_launches_24h"], model["live_launches_7d"], model["live_launches_30d"]) == (1, 3, 4)


def test_byzantine_infrastructure_telemetry_is_separate_from_strict_membership():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE wt_walkback_queue (subprov TEXT, status TEXT, funder_block_time INTEGER)")
    address = "ByZc7RNeYowEg2jKo2giytWb9WmNyZPrQ1hXhnGSzHTY"
    conn.executemany("INSERT INTO wt_walkback_queue VALUES (?,?,?)", [
        (address, "complete", 2_000_000_000 - 60),
        (address, "complete", 2_000_000_000 - 120),
        (address, "complete", 2_000_000_000 - 180),
        (address, "complete", 2_000_000_000 - 8 * 86400),
    ])
    telemetry = _byzantine_infrastructure_activity(conn, now=2_000_000_000)
    assert telemetry["activity_source"] == "LIVE_BYZANTINE_INFRASTRUCTURE"
    assert (telemetry["live_launches_24h"], telemetry["live_launches_7d"], telemetry["live_launches_30d"]) == (3, 3, 4)
    assert telemetry["total_observed_launches"] == 4


def test_nexus_detector_projection_uses_complete_retained_v2_replay():
    projection = _nexus_detector_projection()
    assert projection["reviewed"] == 93
    assert projection["counts"] == {
        "UNIQUE_MATCH": 84,
        "NO_MATCH": 9,
        "INSUFFICIENT_INPUT": 0,
        "AMBIGUOUS": 0,
    }
    qvtw = [row for row in projection["rows"] if row["cohort"] == "QVtW"]
    assert len(qvtw) == 6
    assert {row["reason"] for row in qvtw} == {"NO_MATCH_INTERMEDIARY_ROUTE"}


def test_nexus_summary_has_a_visible_detector_replay_card():
    template = Path("templates/operator_intelligence.html").read_text()
    assert "Retained Detector Replay" in template
    assert "nexusDetector.counts.UNIQUE_MATCH" in template
    assert "six QVtW rows are No match: intermediary route" in template


def test_nexus_launch_rows_have_subtle_detector_marks():
    template = Path("templates/operator_intelligence.html").read_text()
    assert "oi-detector-mark" in template
    assert "UNIQUE_MATCH:['exact','●','Exact']" in template
    assert "NO_MATCH:['no-match','○','No match']" in template
    assert "Detector: '+esc(marks[detector.raw_result][2])" in template
    assert "not replayed — current observation is outside the retained v2 historical input" in template
