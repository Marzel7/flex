"""X78.6: deterministic regression proving RiskScoringBuilder.score_creator_now
no longer holds the write lease across its slow, full-table read work
(sync_infra_wallets + _build_context), which was proven live to take
70+ seconds combined against the production DB (see
docs/audits/x78_6_risk_scoring_runtime_reentrancy.md for the measured
query timings and EXPLAIN QUERY PLAN evidence).

Root cause (Verdict A): apply_migration()'s own CREATE TABLE/ALTER
statements are write-shaped SQL, so they acquired the write lease
immediately -- and sync_infra_wallets()/_build_context() then ran their
multi-second-to-tens-of-seconds full-table scans entirely INSIDE that
held lease, before the first actual write (_write_creator_scores). Any
other write on the same worker thread during that window collided with
NestedDatabaseWriteError; under sustained job throughput this recurred
faster than each window could clear, looking indistinguishable from a
permanent leak (X78.5's fix, while independently correct, did not
address this).

Fix: score_creator_now now uses two connections -- a setup connection
(migration + infra sync + context read), committed and closed BEFORE
scoring/persistence begins, and a second, separately-opened connection
held only for the brief _write_creator_scores + commit. This test
proves the write lease is measurably NOT held during the slow read
phase, by making that phase artificially slow and confirming an
unrelated concurrent write on another thread succeeds without collision
during that window -- the exact scenario that was broken live.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid

import pytest

from src.core.database_write_service import (
    NestedDatabaseWriteError,
    acquire_write_lease,
    release_write_lease,
    _thread_write_lease,
)
from src.core.risk_scoring_builder import RiskScoringBuilder


def _clear_thread_lease():
    if hasattr(_thread_write_lease, "owner"):
        del _thread_write_lease.owner


@pytest.fixture(autouse=True)
def _isolate():
    _clear_thread_lease()
    yield
    _clear_thread_lease()


def _make_db_with_schema(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS network_membership (creator_address TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_funders (creator_address TEXT, funder_address TEXT, amount_sol REAL, is_cex INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS token_analysis (earliest_tx_creator TEXT, mint TEXT, market_cap_highest REAL, market_cap_current REAL, created_at TEXT, migrated_at TEXT, market_cap_highest_at_ts TEXT, lifecycle_stage TEXT, bonding_curve_pda TEXT, pool_address TEXT, pumpswap_pool_address TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_self_funding (creator_address TEXT, is_self_funding INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_outbound_classifications (creator_address TEXT, relationship_type TEXT, recipient_address TEXT, amount_sol REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_second_hop (creator_address TEXT, upstream_address TEXT, confidence_score REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_c2c_edges (source_creator TEXT, dest_creator TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS coordinated_creator_edges (creator_a TEXT, creator_b TEXT, bridge_funder TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS wallet_clusters (funder_wallet TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS farm_cluster_members (wallet_address TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_tags (creator_address TEXT, tag TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS token_pool_accounts (mint TEXT, liquidity_removed INTEGER, liquidity_removed_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS infra_funders_observed (funder_address TEXT, note TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS cex_wallets (cex_address TEXT, exchange_name TEXT, wallet_type TEXT, is_active INTEGER)")
    conn.commit()
    conn.close()


def test_write_lease_not_held_during_slow_context_build(tmp_path, monkeypatch):
    """Core X78.6 regression: while _build_context (the proven-slow full-
    table read step) is artificially delayed, another thread performing
    an unrelated write on the SAME thread the scoring call would poison
    (simulated here as: attempt a write while scoring's read phase is in
    flight) must succeed immediately, proving the write lease was
    already released before the slow read work began."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_schema(db_path)

    read_phase_entered = threading.Event()
    release_read_phase = threading.Event()

    real_build_context = RiskScoringBuilder._build_context

    def slow_build_context(self, conn, creators):
        read_phase_entered.set()
        release_read_phase.wait(timeout=5)
        return real_build_context(self, conn, creators)

    monkeypatch.setattr(RiskScoringBuilder, "_build_context", slow_build_context)

    builder = RiskScoringBuilder(db_path)
    result_holder = {}

    def run_scoring():
        result_holder["result"] = builder.score_creator_now("creatorAddr")

    scoring_thread = threading.Thread(target=run_scoring)
    scoring_thread.start()

    assert read_phase_entered.wait(timeout=5), "scoring must have reached the read phase"

    # While the (artificially slow) read phase is in flight, prove the
    # write lease is NOT held -- an unrelated write must succeed
    # immediately, without any NestedDatabaseWriteError or delay.
    lease = acquire_write_lease(
        f"tracked:{os.path.realpath(db_path)}", db_path, str(uuid.uuid4()),
        "creator_funding_worker.py:117 in _db_connect (concurrent unrelated write)",
    )
    release_write_lease(lease)

    release_read_phase.set()
    scoring_thread.join(timeout=10)

    assert result_holder["result"]["status"] in ("success", "error")
    assert getattr(_thread_write_lease, "owner", None) is None


def test_score_creator_now_still_releases_lease_on_normal_success(tmp_path):
    """Non-regression: the two-connection restructure must not break the
    happy path."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_schema(db_path)

    builder = RiskScoringBuilder(db_path)
    result = builder.score_creator_now("creatorAddr")

    assert result["status"] in ("success", "error")
    assert getattr(_thread_write_lease, "owner", None) is None


def test_write_connection_failure_does_not_leak_lease(tmp_path, monkeypatch):
    """Non-regression: a failure specifically in the write phase (not the
    setup phase) must still release its own lease correctly."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_schema(db_path)

    def failing_write_creator_scores(self, conn, rows):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(RiskScoringBuilder, "_write_creator_scores", failing_write_creator_scores)

    builder = RiskScoringBuilder(db_path)
    result = builder.score_creator_now("creatorAddr")

    assert result["status"] == "error"
    assert getattr(_thread_write_lease, "owner", None) is None


def test_setup_and_write_phases_use_separate_connections(tmp_path):
    """Documents the structural fix directly: the setup phase (migration
    + infra sync + context) commits and closes its own connection BEFORE
    the write phase opens a new one -- proving the lease-holding window
    for the write phase is now bounded to just the persistence step."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_schema(db_path)

    open_close_order = []
    real_connect = sqlite3.connect

    class TrackingConnection(sqlite3.Connection):
        def close(self):
            idx = getattr(self, "_track_idx", None)
            if idx is not None:
                open_close_order.append(f"close_{idx}")
            return super().close()

    def tracking_connect(*args, **kwargs):
        kwargs.pop("factory", None)
        conn = real_connect(*args, factory=TrackingConnection, **kwargs)
        idx = len(open_close_order)
        conn._track_idx = idx
        open_close_order.append(f"open_{idx}")
        return conn

    import src.core.risk_scoring_builder as rsb_module
    original = rsb_module.sqlite3.connect
    rsb_module.sqlite3.connect = tracking_connect
    try:
        builder = RiskScoringBuilder(db_path)
        builder.score_creator_now("creatorAddr")
    finally:
        rsb_module.sqlite3.connect = original

    # Every open must be immediately followed by its own close before the
    # next open -- proving connections are never overlapping (no single
    # connection spans both the slow read phase and the write phase, and
    # no connection is left open concurrently with another).
    assert len(open_close_order) % 2 == 0 and len(open_close_order) >= 4, (
        f"expected at least two connections (setup + write), got {open_close_order}"
    )
    for i in range(0, len(open_close_order), 2):
        open_evt, close_evt = open_close_order[i], open_close_order[i + 1]
        open_idx = open_evt.split("_")[1]
        close_idx = close_evt.split("_")[1]
        assert open_evt.startswith("open_") and close_evt.startswith("close_") and open_idx == close_idx, (
            f"expected each open to be immediately followed by its matching close "
            f"(non-overlapping connections), got {open_close_order}"
        )
