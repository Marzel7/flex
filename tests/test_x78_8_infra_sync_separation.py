"""X78.8: regression tests for infrastructure-sync hot-path separation.

Root cause: RiskScoringBuilder.score_creator_now() called
sync_infra_wallets() (three full SELECT DISTINCT scans of token_analysis,
~48s+ measured in isolation, worse under concurrent DB load) either
directly (pre-X78.7) or via a 300s debounce (X78.7) -- but the debounce
only reduced FREQUENCY, not per-call COST, and the worker's real job
cadence (~15-20 min/job under RPC-bound load) meant the debounce window
routinely went cold between calls, so risk_scoring_builder.py remained a
frequent NestedDatabaseWriteError source even after X78.7 (43+
occurrences / 24 minutes measured live).

X78.8 removes sync_infra_wallets() from score_creator_now entirely.
Refresh ownership moves to src.core.infra_sync_scheduler, a standalone
supervised process (same pattern as intelligence_snapshot_scheduler/
operation_scheduler) that runs on its own fixed cadence, independent of
any scoring call. All tests here use isolated tmp_path databases -- NEVER
the live production DB (a prior investigation step accidentally
contended with the live creator_funding_worker process via a blocking
benchmark script and caused two real crash-loop restarts; this file
exists specifically to validate correctness without repeating that).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

import pytest

from src.core.risk_scoring_builder import RiskScoringBuilder
import src.core.infra_sync_scheduler as scheduler


def _make_db_with_data(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS network_membership (creator_address TEXT)")
    conn.execute("""CREATE TABLE IF NOT EXISTS creator_funders (
        creator_address TEXT, funder_address TEXT, amount_sol REAL, is_cex INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS token_analysis (
        earliest_tx_creator TEXT, mint TEXT, market_cap_highest REAL, market_cap_current REAL,
        created_at TEXT, migrated_at TEXT, market_cap_highest_at_ts TEXT, lifecycle_stage TEXT,
        bonding_curve_pda TEXT, pool_address TEXT, pumpswap_pool_address TEXT)""")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_self_funding (creator_address TEXT, is_self_funding INTEGER)")
    conn.execute("""CREATE TABLE IF NOT EXISTS creator_outbound_classifications (
        creator_address TEXT, relationship_type TEXT, recipient_address TEXT, amount_sol REAL)""")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_second_hop (creator_address TEXT, upstream_address TEXT, confidence_score REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_c2c_edges (source_creator TEXT, dest_creator TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS coordinated_creator_edges (creator_a TEXT, creator_b TEXT, bridge_funder TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS wallet_clusters (funder_wallet TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS farm_cluster_members (wallet_address TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_tags (creator_address TEXT, tag TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS token_pool_accounts (mint TEXT, liquidity_removed INTEGER, liquidity_removed_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS infra_funders_observed (funder_address TEXT, note TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS networks_release (network_name TEXT, network_size INTEGER, network_type TEXT)")
    conn.execute("INSERT INTO creator_funders (creator_address, funder_address, amount_sol, is_cex) VALUES ('creatorA', 'funder1', 1.5, 0)")
    conn.execute("INSERT INTO token_analysis (earliest_tx_creator, mint, bonding_curve_pda) VALUES ('creatorA', 'mintA1', 'curveX')")
    conn.commit()
    conn.close()


def test_score_creator_now_does_not_call_sync_infra_wallets(tmp_path, monkeypatch):
    """Core X78.8 regression: score_creator_now must not invoke
    sync_infra_wallets at all -- refresh ownership moved to the
    standalone scheduler."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_data(db_path)

    import src.core.risk_scoring_builder as rsb_module
    call_count = {"n": 0}
    real_sync = rsb_module.sync_infra_wallets

    def counting_sync(conn, *args, **kwargs):
        call_count["n"] += 1
        return real_sync(conn, *args, **kwargs)

    monkeypatch.setattr(rsb_module, "sync_infra_wallets", counting_sync)

    builder = RiskScoringBuilder(db_path)
    builder.score_creator_now("creatorA")
    builder.score_creator_now("creatorA")
    builder.score_creator_now("creatorA")

    assert call_count["n"] == 0, (
        f"score_creator_now must never call sync_infra_wallets -- called {call_count['n']} times"
    )


def test_score_creator_now_still_works_with_no_infra_wallets_table(tmp_path):
    """Non-regression: on a freshly-created database (no scheduler has
    ever run yet), score_creator_now must still succeed -- it ensures
    the table exists (empty) rather than requiring pre-seeded data."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_data(db_path)

    builder = RiskScoringBuilder(db_path)
    result = builder.score_creator_now("creatorA")
    assert result["status"] == "success"


def test_score_creator_now_uses_persisted_infra_state(tmp_path):
    """score_creator_now must correctly READ whatever infra_wallets state
    already exists (as the scheduler would have persisted it) -- proving
    the read-only consumption path still works end to end."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_data(db_path)

    # Simulate the scheduler having already run once.
    scheduler_result = scheduler.run_once.__wrapped__ if hasattr(scheduler.run_once, "__wrapped__") else None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from src.utils.infra_mapping import sync_infra_wallets
    sync_infra_wallets(conn)
    conn.commit()
    # funder1 is not an infra wallet in this fixture, so it should remain visible.
    row = conn.execute("SELECT COUNT(*) FROM infra_wallets WHERE address = 'funder1'").fetchone()
    conn.close()
    assert row[0] == 0  # funder1 correctly excluded from infra_wallets

    builder = RiskScoringBuilder(db_path)
    result = builder.score_creator_now("creatorA")
    assert result["status"] == "success"


def test_infra_sync_scheduler_run_once_persists_status(tmp_path, monkeypatch):
    """Core scheduler regression: run_once() must persist success status
    with a timestamp, duration, and rows_processed."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_data(db_path)
    monkeypatch.setattr(scheduler, "DB_PATH", db_path)

    result = scheduler.run_once()
    assert result["status"] == "success"
    assert result["duration_ms"] >= 0

    status = scheduler.get_status()
    assert status["health"] == "healthy"
    assert status["last_status"] == "success"
    assert status["last_success_at"] is not None
    assert status["age_seconds"] is not None and status["age_seconds"] < 5


def test_infra_sync_scheduler_failure_does_not_raise(tmp_path, monkeypatch):
    """Phase 17: a refresh failure must never propagate/crash the caller
    -- it must be recorded and swallowed."""
    db_path = str(tmp_path / "nonexistent_dir" / "x.db")  # will fail to open
    monkeypatch.setattr(scheduler, "DB_PATH", db_path)

    result = scheduler.run_once()  # must not raise
    assert result["status"] == "error"


def test_infra_sync_scheduler_status_reflects_failure(tmp_path, monkeypatch):
    """After a genuine failure (on a valid DB, forced via a broken
    sync_infra_wallets), status must reflect 'failed' without losing the
    last successful state's rows_processed."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_data(db_path)
    monkeypatch.setattr(scheduler, "DB_PATH", db_path)

    # First: a real success.
    result1 = scheduler.run_once()
    assert result1["status"] == "success"
    status1 = scheduler.get_status()
    first_success_rows = status1["rows_processed"]

    # Second: force a failure.
    def failing_collect(conn):
        raise RuntimeError("simulated sync failure")

    monkeypatch.setattr(scheduler, "collect_infra_wallet_rows", failing_collect)
    result2 = scheduler.run_once()
    assert result2["status"] == "error"

    status2 = scheduler.get_status()
    assert status2["health"] == "failed"
    assert status2["last_status"] == "failed"
    # last_success_at / rows_processed from the PRIOR success must be
    # preserved, not wiped by the failed attempt.
    assert status2["last_success_at"] == status1["last_success_at"]
    assert status2["rows_processed"] == first_success_rows


def test_infra_sync_scheduler_single_flight_lock(tmp_path):
    """Phase 15: only one instance may hold the lock at a time; a dead
    owner's lock must be reclaimed automatically."""
    lock_path = str(tmp_path / "test.lock")

    assert scheduler.acquire_lock(lock_path) is True
    # Simulate a second instance (same process, different logical
    # "owner" check) -- since it's the same PID, acquire_lock succeeds
    # again (reentrant for the same process); the real test is a DIFFERENT
    # pid being blocked, modeled by writing a fake live PID.
    with open(lock_path, "w") as f:
        f.write("999999999")  # a PID that (almost certainly) doesn't exist -- but test the ALIVE case separately
    assert scheduler.acquire_lock(lock_path) is True  # stale/dead pid reclaimed

    scheduler.release_lock(lock_path)
    assert not os.path.exists(lock_path)


def test_infra_sync_scheduler_lock_blocks_live_owner(tmp_path, monkeypatch):
    """A genuinely live owner (this test process itself) must block a
    second acquisition attempt with a different simulated PID."""
    lock_path = str(tmp_path / "test2.lock")
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))  # this test process IS alive

    real_pid = os.getpid()
    monkeypatch.setattr(os, "getpid", lambda: real_pid + 1)  # pretend to be a different process
    try:
        assert scheduler.acquire_lock(lock_path) is False
    finally:
        monkeypatch.undo()
        os.remove(lock_path)
