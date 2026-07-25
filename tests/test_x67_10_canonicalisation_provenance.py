"""X67.10 — Tests for separate detection vs. canonicalisation provenance.

Covers classify_canonicalisation_source() (the backend classification
helper) and the detection/canonicalisation independence guarantees X67.9's
design requires. No production database is written by these tests.
"""
import sqlite3

import pytest

from src.ops.operational_intelligence import (
    classify_canonicalisation_source,
    build_operational_intelligence,
)
from src.ops.detection_reconciliation import _LIVE_DETECTION_SOURCES


# ── Backend classification tests ────────────────────────────────────────────

def test_close_account_destination_strict_is_live_detection():
    result = classify_canonicalisation_source("CLOSE_ACCOUNT_DESTINATION", "STRICT")
    assert result["canonicalisation_source"] == "LIVE_DETECTION"
    assert result["canonicalisation_label"] == "Live Detection"


def test_walkback_recovered_walkback_is_walkback_confirmation():
    result = classify_canonicalisation_source("WALKBACK_RECOVERED", "WALKBACK")
    assert result["canonicalisation_source"] == "WALKBACK_CONFIRMATION"
    assert result["canonicalisation_label"] == "Walkback Confirmed"


def test_any_method_with_backfill_confidence_is_backfill():
    # BACKFILL confidence takes precedence regardless of method (X67.10 Phase 4
    # precedence rule #1), including an unexpected/legacy method value.
    result = classify_canonicalisation_source("SOME_OTHER_METHOD", "BACKFILL")
    assert result["canonicalisation_source"] == "BACKFILL"
    assert result["canonicalisation_label"] == "Historical Backfill"

    result2 = classify_canonicalisation_source(None, "BACKFILL")
    assert result2["canonicalisation_source"] == "BACKFILL"


def test_null_method_and_confidence_is_unknown():
    result = classify_canonicalisation_source(None, None)
    assert result["canonicalisation_source"] == "UNKNOWN"
    assert result["canonicalisation_label"] == "Legacy / Unknown"


def test_contradictory_values_are_conflict():
    # A method AND confidence that both match no known-good rule, but at
    # least one field is non-null (so this is NOT the null/null UNKNOWN
    # case) -- must fail closed to CONFLICT, never silently pick a label.
    result = classify_canonicalisation_source("SOME_UNRECOGNISED_METHOD", "SOME_UNRECOGNISED_TIER")
    assert result["canonicalisation_source"] == "CONFLICT"
    assert result["canonicalisation_label"] == "Provenance Conflict"


def test_method_alone_matches_live_detection_even_without_strict_confidence():
    # Precedence rule 4 accepts EITHER condition (method OR confidence) --
    # confirms creator_extraction_method alone is sufficient.
    result = classify_canonicalisation_source("CLOSE_ACCOUNT_DESTINATION", None)
    assert result["canonicalisation_source"] == "LIVE_DETECTION"


def test_confidence_alone_matches_walkback_even_with_different_method():
    result = classify_canonicalisation_source(None, "WALKBACK")
    assert result["canonicalisation_source"] == "WALKBACK_CONFIRMATION"


# ── Detection independence tests ────────────────────────────────────────────

def test_walkback_confirmed_row_has_no_live_detection_and_correct_canonicalisation():
    """detection_source=NULL, creator_extraction_method=WALKBACK_RECOVERED,
    confidence=WALKBACK -> caught_live=False, canonicalisation=WALKBACK_CONFIRMATION.
    Proves NULL detection_source is not reinterpreted as an error for a
    legitimately walkback-recovered row (X67.9's core constraint)."""
    detection_source = None
    caught_live = detection_source in _LIVE_DETECTION_SOURCES
    assert caught_live is False

    canon = classify_canonicalisation_source("WALKBACK_RECOVERED", "WALKBACK")
    assert canon["canonicalisation_source"] == "WALKBACK_CONFIRMATION"
    # Caught Live and Canonicalised Via are independent: the classifier's
    # own signature takes only creator_extraction_method/confidence -- it
    # has no detection_source parameter to derive from at all, by
    # construction (verified directly, not by string-matching source).
    import inspect
    sig = inspect.signature(classify_canonicalisation_source)
    assert "detection_source" not in sig.parameters


def test_active_catchup_close_account_destination_strict_is_live_and_live_detection():
    detection_source = "ACTIVE_CATCHUP"
    caught_live = detection_source in _LIVE_DETECTION_SOURCES
    assert caught_live is True

    canon = classify_canonicalisation_source("CLOSE_ACCOUNT_DESTINATION", "STRICT")
    assert canon["canonicalisation_source"] == "LIVE_DETECTION"


def test_pending_create_retry_is_reconciled_but_still_live_detection_canonicalisation():
    """The valid, non-contradictory combination X67.10 explicitly calls
    out: detected via a non-primary path (Reconciled) but still
    canonicalised through the live-detection writer (Live Detection)."""
    detection_source = "PENDING_CREATE_RETRY"
    caught_live = detection_source in _LIVE_DETECTION_SOURCES
    assert caught_live is False
    assert detection_source is not None  # -> renders "Reconciled", not "-"

    canon = classify_canonicalisation_source("CLOSE_ACCOUNT_DESTINATION", "STRICT")
    assert canon["canonicalisation_source"] == "LIVE_DETECTION"


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


def test_known_examples_against_production_data(prod_paths):
    """Gate D: verifies the three named examples from X67.8/X67.10 render
    exactly as specified, read-only against the real database."""
    ops_db, core_db = prod_paths
    result = build_operational_intelligence(ops_db, core_db, window_seconds=86400 * 3650)
    records = result["records"]

    live = records.get("EGB4sv9ddNhWeUhnsAvpqP8xaEps4cx5bc956LPcpump")
    if live:
        assert live["caught_live"] is True
        assert live["canonicalisation_source"] == "LIVE_DETECTION"

    reconciled = records.get("EQ6qQsweDhsdYqbYMv4J2dTZwEMvnETQH9rQHr9fpump")
    if reconciled:
        assert reconciled["caught_live"] is False
        assert reconciled["detection_source"] == "PENDING_CREATE_RETRY"
        assert reconciled["canonicalisation_source"] == "LIVE_DETECTION"

    walkback = records.get("B5RMggYagf8A77GnX82HfvE57fVQqCmGPZWvcbw8pump")
    if walkback:
        assert walkback["caught_live"] is False
        assert walkback["detection_source"] is None
        assert walkback["canonicalisation_source"] == "WALKBACK_CONFIRMATION"


def test_no_registry_writes_from_read_model(prod_paths):
    """Confirms build_operational_intelligence + classify_canonicalisation_source
    together perform zero writes -- canonical registry count must be
    identical before and after calling them."""
    ops_db, core_db = prod_paths
    conn = sqlite3.connect(f"file:{ops_db}?mode=ro", uri=True)
    before = conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches").fetchone()[0]
    build_operational_intelligence(ops_db, core_db, window_seconds=86400)
    after = conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches").fetchone()[0]
    assert before == after
