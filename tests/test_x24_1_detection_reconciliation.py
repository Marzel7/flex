"""X24.1 Phase 4 — detection reconciliation classification tests.

Verifies classify_walkback_confirmed_launches() against a synthetic ops DB
covering all four taxonomy buckets, and confirms it never writes to
wt_watchtower_launches (read-only; a walkback-only launch must never be
silently presented as a live detection).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from src.ops.detection_reconciliation import classify_walkback_confirmed_launches


@pytest.fixture
def ops_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE wt_provisioning_sessions (
            session_id TEXT, source_mint TEXT, treasury TEXT, subprov TEXT, creator TEXT,
            treasury_to_subprov_block_time INTEGER, subprov_to_creator_block_time INTEGER,
            creator_launch_time INTEGER,
            treasury_to_subprov_latency_seconds INTEGER, subprov_to_creator_latency_seconds INTEGER,
            creator_to_launch_latency_seconds INTEGER,
            treasury_to_subprov_mechanism TEXT, subprov_to_creator_mechanism TEXT,
            treasury_to_subprov_amount_sol REAL, subprov_to_creator_amount_sol REAL,
            recorded_at INTEGER
        );
        CREATE TABLE wt_provisioning_edges (
            edge_id TEXT PRIMARY KEY, edge_type TEXT, from_wallet TEXT, to_wallet TEXT,
            funding_mechanism TEXT, funding_block_time INTEGER, source_mint TEXT
        );
        CREATE TABLE wt_watchtower_launches (
            mint TEXT PRIMARY KEY, creator_wallet TEXT, treasury_wallet TEXT, subprov_wallet TEXT,
            create_time INTEGER, detection_source TEXT
        );
        CREATE TABLE wt_active_subprov_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, subprov_wallet TEXT, treasury_wallet TEXT,
            funding_time INTEGER, expires_at INTEGER, monitoring_state TEXT,
            funding_mechanism TEXT, detected_at INTEGER
        );
        CREATE TABLE wt_walkback_queue (
            mint TEXT PRIMARY KEY, intelligence_outcome TEXT
        );
    """)
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


def _insert(path, table, **cols):
    conn = sqlite3.connect(path)
    keys = ",".join(cols.keys())
    placeholders = ",".join("?" for _ in cols)
    conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", tuple(cols.values()))
    conn.commit()
    conn.close()


def test_live_detected_when_launch_row_has_live_source(ops_db):
    _insert(ops_db, "wt_provisioning_sessions", source_mint="MINT_LIVE", treasury="T1",
            subprov="S1", creator="C1", subprov_to_creator_mechanism="WSOL_WRAP_CLOSE",
            recorded_at=100)
    _insert(ops_db, "wt_watchtower_launches", mint="MINT_LIVE", creator_wallet="C1",
            treasury_wallet="T1", subprov_wallet="S1", create_time=100,
            detection_source="LIVE_STREAM")
    result = classify_walkback_confirmed_launches(ops_db)
    row = next(r for r in result["rows"] if r["mint"] == "MINT_LIVE")
    assert row["classification"] == "LIVE_DETECTED"


def test_reconciled_when_launch_row_has_reconciler_source(ops_db):
    _insert(ops_db, "wt_provisioning_sessions", source_mint="MINT_RECON", treasury="T2",
            subprov="S2", creator="C2", subprov_to_creator_mechanism="WSOL_WRAP_CLOSE",
            recorded_at=200)
    _insert(ops_db, "wt_watchtower_launches", mint="MINT_RECON", creator_wallet="C2",
            treasury_wallet="T2", subprov_wallet="S2", create_time=200,
            detection_source="RECONCILE")
    result = classify_walkback_confirmed_launches(ops_db)
    row = next(r for r in result["rows"] if r["mint"] == "MINT_RECON")
    assert row["classification"] == "RECONCILED"


def test_walkback_recovered_when_no_live_armed_session_ever_covered_it(ops_db):
    """No wt_watchtower_launches row, and no LIVE_ARMED session covering CREATE
    time — the system never believed it was watching. Not a pipeline defect.
    X25.5.1: requires the mint's wt_walkback_queue outcome to be
    WATCHTOWER_CONFIRMED — a bare provisioning-session row is not enough."""
    _insert(ops_db, "wt_provisioning_sessions", source_mint="MINT_WB", treasury="T3",
            subprov="S3", creator="C3", subprov_to_creator_mechanism="WSOL_WRAP_CLOSE",
            creator_launch_time=300, recorded_at=310)
    _insert(ops_db, "wt_walkback_queue", mint="MINT_WB", intelligence_outcome="WATCHTOWER_CONFIRMED")
    # session exists but is INTEL_ONLY (never armed for live watching)
    _insert(ops_db, "wt_active_subprov_sessions", subprov_wallet="S3", treasury_wallet="T3",
            funding_time=250, expires_at=2000, monitoring_state="INTEL_ONLY",
            funding_mechanism="PLAIN_TRANSFER", detected_at=250)
    result = classify_walkback_confirmed_launches(ops_db)
    row = next(r for r in result["rows"] if r["mint"] == "MINT_WB")
    assert row["classification"] == "WALKBACK_RECOVERED"


def test_pipeline_inconsistency_when_live_armed_session_covered_create_but_no_launch(ops_db):
    """The AWiaGsus-class defect: a LIVE_ARMED session's window covers the
    CREATE time, yet no wt_watchtower_launches row exists. This is the bucket
    this fix targets — must be classified distinctly from WALKBACK_RECOVERED.
    X25.5.1: requires a confirmed wt_walkback_queue outcome, same as above —
    a confirmed live-armed miss still renders PIPELINE_INCONSISTENCY when
    membership is genuinely established."""
    _insert(ops_db, "wt_provisioning_sessions", source_mint="MINT_BUG", treasury="T4",
            subprov="S4", creator="C4", subprov_to_creator_mechanism="WSOL_WRAP_CLOSE",
            creator_launch_time=1784052892, recorded_at=1784052909)
    _insert(ops_db, "wt_walkback_queue", mint="MINT_BUG", intelligence_outcome="WATCHTOWER_CONFIRMED")
    _insert(ops_db, "wt_active_subprov_sessions", subprov_wallet="S4", treasury_wallet="T4",
            funding_time=1784051480, expires_at=1784053553, monitoring_state="LIVE_ARMED",
            funding_mechanism="PLAIN_TRANSFER", detected_at=1784051480)
    result = classify_walkback_confirmed_launches(ops_db)
    row = next(r for r in result["rows"] if r["mint"] == "MINT_BUG")
    assert row["classification"] == "PIPELINE_INCONSISTENCY"
    assert row["plain_transfer_associated"] is True


def test_plain_xfer_and_plain_transfer_labels_both_recognised(ops_db):
    """wt_provisioning_edges uses 'PLAIN_XFER' while wt_active_subprov_sessions
    uses 'PLAIN_TRANSFER' for the same real mechanism — both must be treated as
    plain-transfer-associated."""
    _insert(ops_db, "wt_provisioning_sessions", source_mint="MINT_XFER", treasury="T5",
            subprov="S5", creator="C5", subprov_to_creator_mechanism="WSOL_WRAP_CLOSE",
            recorded_at=400)
    _insert(ops_db, "wt_provisioning_edges", edge_id="e1", edge_type="TREASURY_TO_SUBPROV",
            from_wallet="T5", to_wallet="S5", funding_mechanism="PLAIN_XFER",
            funding_block_time=390, source_mint="MINT_XFER")
    result = classify_walkback_confirmed_launches(ops_db)
    row = next(r for r in result["rows"] if r["mint"] == "MINT_XFER")
    assert row["plain_transfer_associated"] is True
    assert row["treasury_to_subprov_mechanism"] == "PLAIN_XFER"


def test_never_writes_to_wt_watchtower_launches(ops_db):
    """A walkback-only launch must remain walkback-only — this module is
    read-only and must never insert a row to make it look like a live catch."""
    _insert(ops_db, "wt_provisioning_sessions", source_mint="MINT_NOWRITE", treasury="T6",
            subprov="S6", creator="C6", subprov_to_creator_mechanism="WSOL_WRAP_CLOSE",
            recorded_at=500)
    before = sqlite3.connect(ops_db).execute("SELECT COUNT(*) FROM wt_watchtower_launches").fetchone()[0]
    classify_walkback_confirmed_launches(ops_db)
    after = sqlite3.connect(ops_db).execute("SELECT COUNT(*) FROM wt_watchtower_launches").fetchone()[0]
    assert before == after == 0


def test_summary_counts_match_row_classifications(ops_db):
    _insert(ops_db, "wt_provisioning_sessions", source_mint="M1", treasury="T", subprov="S",
            creator="C1", subprov_to_creator_mechanism="WSOL_WRAP_CLOSE", recorded_at=1)
    _insert(ops_db, "wt_provisioning_sessions", source_mint="M2", treasury="T", subprov="S2",
            creator="C2", subprov_to_creator_mechanism="WSOL_WRAP_CLOSE", recorded_at=2)
    _insert(ops_db, "wt_watchtower_launches", mint="M1", creator_wallet="C1", treasury_wallet="T",
            subprov_wallet="S", create_time=1, detection_source="LIVE_STREAM")
    _insert(ops_db, "wt_walkback_queue", mint="M2", intelligence_outcome="WATCHTOWER_CONFIRMED")
    result = classify_walkback_confirmed_launches(ops_db)
    assert result["summary"]["LIVE_DETECTED"] == 1
    assert result["summary"]["WALKBACK_RECOVERED"] == 1
    assert result["total"] == 2
