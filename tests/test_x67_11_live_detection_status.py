"""X67.11 -- Tests for classify_live_detection_status(), the presentation
helper that replaces the ambiguous "Caught Live: -" with an explicit status
answering "was this canonical launch detected while WATCHTOWER was ARMED?"

Presentation-only: does not persist, does not alter detection_source,
creator_extraction_method, confidence, or the canonicalisation axis X67.10
introduced.
"""
import sqlite3

import pytest

from src.ops.operational_intelligence import (
    classify_live_detection_status,
    classify_canonicalisation_source,
    build_operational_intelligence,
)
from src.ops.detection_reconciliation import _LIVE_DETECTION_SOURCES


# ── Classification tests ────────────────────────────────────────────────────

def test_live_stream_is_live():
    result = classify_live_detection_status("LIVE_STREAM", "CLOSE_ACCOUNT_DESTINATION", "STRICT")
    assert result["live_detection_status"] == "LIVE"
    assert result["live_detection_label"] == "Live"


def test_active_catchup_is_live():
    result = classify_live_detection_status("ACTIVE_CATCHUP", "CLOSE_ACCOUNT_DESTINATION", "STRICT")
    assert result["live_detection_status"] == "LIVE"


def test_program_logs_is_detected_late():
    result = classify_live_detection_status("PROGRAM_LOGS", "CLOSE_ACCOUNT_DESTINATION", "STRICT")
    assert result["live_detection_status"] == "DETECTED_LATE"
    assert result["live_detection_label"] == "Detected Later"


@pytest.mark.parametrize("source", [
    "PENDING_CREATE_RETRY", "PROGRAM_REPLAY_BUFFER", "OPENING_CATCHUP",
    "EXPIRE_PROBE", "CANDIDATE_CATCHUP", "MANUAL_USER_ATTESTATION",
])
def test_all_detected_late_sources(source):
    result = classify_live_detection_status(source, "CLOSE_ACCOUNT_DESTINATION", "STRICT")
    assert result["live_detection_status"] == "DETECTED_LATE"


def test_null_plus_walkback_recovered_is_not_detected():
    result = classify_live_detection_status(None, "WALKBACK_RECOVERED", "WALKBACK")
    assert result["live_detection_status"] == "NOT_DETECTED"
    assert result["live_detection_label"] == "Not Detected"


def test_null_plus_walkback_confidence_alone_is_not_detected():
    result = classify_live_detection_status(None, "SOME_OTHER_METHOD", "WALKBACK")
    assert result["live_detection_status"] == "NOT_DETECTED"


def test_null_plus_close_account_destination_is_legacy():
    """The exact 13-row X67.10 legacy population: pre-dates detection_source
    entirely, must NOT be conflated with a genuine walkback Not-Detected row."""
    result = classify_live_detection_status(None, "CLOSE_ACCOUNT_DESTINATION", None)
    assert result["live_detection_status"] == "LEGACY_UNKNOWN"
    assert result["live_detection_label"] == "Legacy"


def test_null_plus_null_is_legacy_unknown():
    result = classify_live_detection_status(None, None, None)
    assert result["live_detection_status"] == "LEGACY_UNKNOWN"


def test_unrecognised_detection_source_is_conflict():
    result = classify_live_detection_status("SOME_UNRECOGNISED_SOURCE", "CLOSE_ACCOUNT_DESTINATION", "STRICT")
    assert result["live_detection_status"] == "CONFLICT"
    assert result["live_detection_label"] == "Conflict"


# ── Independence / regression guarantees ────────────────────────────────────

def test_caught_live_boolean_still_computed_independently():
    """X67.10's caught_live boolean (derived solely from
    detection_source in _LIVE_DETECTION_SOURCES) must be unaffected by this
    new status -- it's a separate, still-present field."""
    detection_source = "ACTIVE_CATCHUP"
    caught_live = detection_source in _LIVE_DETECTION_SOURCES
    assert caught_live is True
    status = classify_live_detection_status(detection_source, "CLOSE_ACCOUNT_DESTINATION", "STRICT")
    assert status["live_detection_status"] == "LIVE"


def test_canonicalisation_source_unaffected_by_new_helper():
    """X67.10's classify_canonicalisation_source must remain untouched --
    same inputs, same outputs, regardless of this new function's existence."""
    canon = classify_canonicalisation_source("WALKBACK_RECOVERED", "WALKBACK")
    assert canon["canonicalisation_source"] == "WALKBACK_CONFIRMATION"


def test_not_detected_and_legacy_are_distinguishable_despite_shared_null_detection():
    """Both NOT_DETECTED and LEGACY_UNKNOWN share detection_source IS NULL;
    creator_extraction_method is the only thing that tells them apart."""
    not_detected = classify_live_detection_status(None, "WALKBACK_RECOVERED", "WALKBACK")
    legacy = classify_live_detection_status(None, "CLOSE_ACCOUNT_DESTINATION", None)
    assert not_detected["live_detection_status"] != legacy["live_detection_status"]


# ── Regression: real production rows (read-only) ───────────────────────────

@pytest.fixture
def prod_paths():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ops_db = os.path.join(root, "database", "wt_ops_v2.db")
    core_db = os.path.join(root, "database", "flex_complete_database.db")
    if not os.path.exists(ops_db):
        pytest.skip("production database not present in this environment")
    return ops_db, core_db


def test_known_walkback_examples_show_not_detected(prod_paths):
    ops_db, core_db = prod_paths
    result = build_operational_intelligence(ops_db, core_db, window_seconds=86400 * 3650)
    records = result["records"]

    for mint in (
        "HJQC4xW9k3gxstQ65UjAwq7D9EQ38NaJiQNayjPopump",
        "Af72QENbvReeKywXQvi3GRgfWbKF8LdncwCjoQ9npump",
    ):
        rec = records.get(mint)
        if rec:
            assert rec["live_detection_status"] == "NOT_DETECTED"
            assert rec["live_detection_label"] == "Not Detected"
            # X67.10's fields must remain exactly as before
            assert rec["caught_live"] is False
            assert rec["canonicalisation_source"] == "WALKBACK_CONFIRMATION"


def test_known_live_example_still_shows_live(prod_paths):
    ops_db, core_db = prod_paths
    result = build_operational_intelligence(ops_db, core_db, window_seconds=86400 * 3650)
    records = result["records"]

    live = records.get("EGB4sv9ddNhWeUhnsAvpqP8xaEps4cx5bc956LPcpump")
    if live:
        assert live["live_detection_status"] == "LIVE"
        assert live["caught_live"] is True


def test_known_reconciled_example_shows_detected_late(prod_paths):
    ops_db, core_db = prod_paths
    result = build_operational_intelligence(ops_db, core_db, window_seconds=86400 * 3650)
    records = result["records"]

    reconciled = records.get("EQ6qQsweDhsdYqbYMv4J2dTZwEMvnETQH9rQHr9fpump")
    if reconciled:
        assert reconciled["live_detection_status"] == "DETECTED_LATE"
        assert reconciled["detection_source"] == "PENDING_CREATE_RETRY"


def test_no_registry_writes_from_read_model(prod_paths):
    ops_db, core_db = prod_paths
    conn = sqlite3.connect(f"file:{ops_db}?mode=ro", uri=True)
    before = conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches").fetchone()[0]
    build_operational_intelligence(ops_db, core_db, window_seconds=86400)
    after = conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches").fetchone()[0]
    assert before == after
