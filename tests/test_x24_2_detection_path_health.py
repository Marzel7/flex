"""X24.2 Phase 5 — detection-path health reporting tests.

Verifies detection_path_health() buckets every real detection_source value
correctly, reports an honest baseline (no invented target percentages), and
never silently drops an unrecognised source.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from src.ops.detection_path_health import detection_path_health, _bucket


@pytest.fixture
def ops_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE wt_watchtower_launches (
            mint TEXT PRIMARY KEY, create_time INTEGER, detection_source TEXT
        );
        CREATE TABLE wt_provisioning_sessions (
            session_id TEXT, source_mint TEXT, treasury TEXT, subprov TEXT, creator TEXT,
            treasury_to_subprov_mechanism TEXT, subprov_to_creator_mechanism TEXT,
            treasury_to_subprov_block_time INTEGER, subprov_to_creator_block_time INTEGER,
            creator_launch_time INTEGER, recorded_at INTEGER
        );
        CREATE TABLE wt_provisioning_edges (
            edge_id TEXT PRIMARY KEY, edge_type TEXT, from_wallet TEXT, to_wallet TEXT,
            funding_mechanism TEXT, funding_block_time INTEGER, source_mint TEXT
        );
        CREATE TABLE wt_active_subprov_sessions (
            id INTEGER PRIMARY KEY, subprov_wallet TEXT, treasury_wallet TEXT,
            funding_time INTEGER, expires_at INTEGER, monitoring_state TEXT,
            funding_mechanism TEXT, detected_at INTEGER
        );
    """)
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


def _insert_launch(path, mint, create_time, detection_source):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO wt_watchtower_launches (mint, create_time, detection_source) VALUES (?,?,?)",
        (mint, create_time, detection_source))
    conn.commit()
    conn.close()


def test_bucket_classification_matches_sprint_taxonomy():
    assert _bucket("LIVE_SUBPROV_WS") == "primary_live_path"
    assert _bucket("ACTIVE_CATCHUP") == "catch_up_path"
    assert _bucket("OPENING_CATCHUP") == "catch_up_path"
    assert _bucket("PROGRAM_LOGS") == "catch_up_path"
    assert _bucket("PENDING_CREATE_RETRY") == "retry_recovery_path"
    assert _bucket("MANUAL_USER_ATTESTATION") == "manual"


def test_unrecognised_source_reported_not_dropped():
    assert _bucket("SOME_FUTURE_SOURCE") == "other_unclassified"


def test_measured_baseline_matches_real_inserted_data(ops_db):
    import time
    now = int(time.time())
    _insert_launch(ops_db, "MINT1", now - 100, "ACTIVE_CATCHUP")
    _insert_launch(ops_db, "MINT2", now - 200, "PROGRAM_LOGS")
    _insert_launch(ops_db, "MINT3", now - 300, "PENDING_CREATE_RETRY")
    result = detection_path_health(ops_db, window_days=30)
    assert result["total_live_detected_launches"] == 3
    assert result["bucket_summary"]["catch_up_path"] == 2
    assert result["bucket_summary"]["retry_recovery_path"] == 1
    assert result["bucket_summary"]["primary_live_path"] == 0


def test_no_target_percentage_asserted_in_output(ops_db):
    result = detection_path_health(ops_db)
    # the report must describe reality, not prescribe a goal
    assert "target" not in result["note"].lower() or "no target" in result["note"].lower()
    assert not any(k for k in result if "target" in k.lower())


def test_window_excludes_launches_outside_the_window(ops_db):
    import time
    now = int(time.time())
    _insert_launch(ops_db, "OLD_MINT", now - (60 * 86400), "ACTIVE_CATCHUP")  # 60 days ago
    result = detection_path_health(ops_db, window_days=30)
    assert result["total_live_detected_launches"] == 0
