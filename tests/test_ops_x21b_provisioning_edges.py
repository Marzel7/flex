"""X21B — Provisioning Relationship Capture.

Facts only: append-only edges (Treasury->SubProv, SubProv->Creator), immutable
per-mint provisioning sessions, and observed latency between funding stages.
Never operator identity, never confidence, never attribution, never RPC of its
own. Explicitly does NOT compute true wallet age (would require new RPC) —
only "first/last observed by this platform" and inter-stage latency.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.ops.provisioning_edges import (
    EDGE_SUBPROV_TO_CREATOR,
    EDGE_TREASURY_TO_SUBPROV,
    capture_provisioning_relationship,
    edges_for_wallet,
    ensure_schema,
    sessions_for_wallet,
    timing_summary_for_wallet,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_schema(c)
    yield c
    c.close()


def test_schema_creates_no_columns_named_confidence_or_operator():
    """Constraint check: this module must never persist operator identity,
    confidence, affinity, or promotion state — only structural facts."""
    from src.ops.provisioning_edges import DDL
    forbidden = ("operator_id", "confidence", "affinity", "promotion", "watchtower")
    lowered = DDL.lower()
    for term in forbidden:
        assert term not in lowered, f"schema must not reference {term!r}"


def test_capture_records_both_edges_and_a_session(conn):
    result = capture_provisioning_relationship(
        conn, source_mint="mintA",
        treasury="TREASURY1", subprov="SUBPROV1", creator="CREATOR1",
        treasury_to_subprov_sig="sig1", treasury_to_subprov_block_time=1000,
        treasury_to_subprov_amount_sol=0.203, treasury_to_subprov_mechanism="WSOL_WRAP_CLOSE",
        subprov_to_creator_sig="sig2", subprov_to_creator_block_time=1018,
        subprov_to_creator_amount_sol=0.05, subprov_to_creator_mechanism="PLAIN_XFER",
        creator_launch_time=1026,
    )
    conn.commit()
    assert set(result["edges_captured"]) == {EDGE_TREASURY_TO_SUBPROV, EDGE_SUBPROV_TO_CREATOR}
    assert result["session_captured"] is True

    edges = edges_for_wallet(conn, "TREASURY1")
    assert len(edges["outgoing"]) == 1
    edge = edges["outgoing"][0]
    assert edge["edge_type"] == EDGE_TREASURY_TO_SUBPROV
    assert edge["to_wallet"] == "SUBPROV1"
    assert edge["observation_count"] == 1
    assert edge["funding_amount_sol"] == 0.203

    sessions = sessions_for_wallet(conn, "TREASURY1")
    assert len(sessions) == 1
    session = sessions[0]
    assert session["treasury_to_subprov_latency_seconds"] == 18
    assert session["subprov_to_creator_latency_seconds"] == 8
    assert session["creator_to_launch_latency_seconds"] == 26


def test_repeat_observation_across_different_mints_accumulates_not_overwrites(conn):
    capture_provisioning_relationship(
        conn, source_mint="mintA", treasury="TREASURY1", subprov="SUBPROV1", creator="CREATOR1",
        treasury_to_subprov_block_time=1000, subprov_to_creator_block_time=1018,
    )
    conn.commit()
    capture_provisioning_relationship(
        conn, source_mint="mintB", treasury="TREASURY1", subprov="SUBPROV1", creator="CREATOR2",
        treasury_to_subprov_block_time=5000, subprov_to_creator_block_time=5015,
    )
    conn.commit()

    edges = edges_for_wallet(conn, "TREASURY1")
    assert len(edges["outgoing"]) == 1  # same edge identity, not two rows
    edge = edges["outgoing"][0]
    assert edge["observation_count"] == 2
    assert edge["first_observed_by_flex"] <= edge["last_observed_by_flex"]

    # Two DISTINCT sessions must still exist — one per mint — since a session
    # records a specific walk's characteristics, not the edge's aggregate state.
    sessions = sessions_for_wallet(conn, "TREASURY1")
    assert len(sessions) == 2
    assert {s["source_mint"] for s in sessions} == {"mintA", "mintB"}


def test_same_mint_processed_twice_does_not_duplicate_the_session(conn):
    """wt_walkback_queue's PRIMARY KEY(mint) already prevents a mint from being
    re-enqueued after completion, but this is a defense-in-depth check that the
    session table's own UNIQUE(source_mint) holds even if called twice."""
    capture_provisioning_relationship(
        conn, source_mint="mintA", treasury="T1", subprov="S1", creator="C1",
        treasury_to_subprov_block_time=1000, subprov_to_creator_block_time=1010,
    )
    conn.commit()
    capture_provisioning_relationship(
        conn, source_mint="mintA", treasury="T1", subprov="S1", creator="C1",
        treasury_to_subprov_block_time=1000, subprov_to_creator_block_time=1010,
    )
    conn.commit()
    sessions = sessions_for_wallet(conn, "T1")
    assert len(sessions) == 1


def test_partial_evidence_only_captures_the_edge_it_has(conn):
    """No treasury known yet — only the subprov->creator edge should be captured,
    never a fabricated treasury->subprov edge."""
    result = capture_provisioning_relationship(
        conn, source_mint="mintC", treasury=None, subprov="SUBPROV2", creator="CREATOR3",
        subprov_to_creator_block_time=2000, subprov_to_creator_amount_sol=0.1,
    )
    conn.commit()
    assert result["edges_captured"] == [EDGE_SUBPROV_TO_CREATOR]
    edges = edges_for_wallet(conn, "SUBPROV2")
    assert len(edges["incoming"]) == 0
    assert len(edges["outgoing"]) == 1


def test_negative_or_out_of_order_block_times_never_produce_a_negative_latency(conn):
    """If block times arrive out of the expected order (clock skew, RPC oddity), the
    module must not report a negative latency as if it were a real observation."""
    capture_provisioning_relationship(
        conn, source_mint="mintD", treasury="T2", subprov="S2", creator="C2",
        treasury_to_subprov_block_time=5000,
        subprov_to_creator_block_time=100,  # earlier than treasury funding — inconsistent
        creator_launch_time=50,
    )
    conn.commit()
    sessions = sessions_for_wallet(conn, "T2")
    session = sessions[0]
    assert session["treasury_to_subprov_latency_seconds"] is None
    assert session["subprov_to_creator_latency_seconds"] is None
    assert session["creator_to_launch_latency_seconds"] is None


def test_timing_summary_is_a_plain_mean_of_persisted_sessions(conn):
    capture_provisioning_relationship(
        conn, source_mint="mintE", treasury="T3", subprov="S3", creator="C3",
        treasury_to_subprov_block_time=0, subprov_to_creator_block_time=10,
    )
    capture_provisioning_relationship(
        conn, source_mint="mintF", treasury="T3", subprov="S3", creator="C4",
        treasury_to_subprov_block_time=0, subprov_to_creator_block_time=20,
    )
    conn.commit()
    summary = timing_summary_for_wallet(conn, "T3")
    assert summary["session_count"] == 2
    assert summary["mean_treasury_to_subprov_latency_seconds"] == 15.0


def test_no_write_when_no_wallets_are_known_at_all(conn):
    result = capture_provisioning_relationship(conn, source_mint="mintG")
    conn.commit()
    assert result["edges_captured"] == []
    assert result["session_captured"] is False
    assert sessions_for_wallet(conn, "anything") == []


def test_read_functions_work_without_row_factory_set():
    """The module must not assume the caller set conn.row_factory = sqlite3.Row —
    walkback_worker's shared ops connection always sets it, but this module should
    not silently break if reused from a plain-tuple connection."""
    plain_conn = sqlite3.connect(":memory:")
    ensure_schema(plain_conn)
    capture_provisioning_relationship(
        plain_conn, source_mint="mintH", treasury="T4", subprov="S4", creator="C5",
        treasury_to_subprov_block_time=100, subprov_to_creator_block_time=110,
    )
    plain_conn.commit()
    edges = edges_for_wallet(plain_conn, "T4")
    assert edges["outgoing"][0]["to_wallet"] == "S4"
    plain_conn.close()
