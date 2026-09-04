"""LIQUIDITY_REAL_POSITION_BOUNDARY: bounded liquidity state for real,
currently-held positions only -- trade_simulations (paper/automated
simulation) must NEVER be treated as production ownership authority.

Proves, against a real temp SQLite file (LiquidityWorker opens its own
connections so :memory: cannot be shared across them):
  - a paper-simulation OPEN trade_simulations row does NOT qualify as a real
    liquidity position: zero price_service calls, zero
    token_owned_liquidity_state rows, zero token_liquidity_snapshots rows
  - with no real-position authority implemented (the current, correct
    production state), _get_live_position_mints() returns [] and the worker
    does zero work
  - a synthetic future real-position source (patched onto
    _get_live_position_mints, standing in for a not-yet-built real trading
    system), first observation: exactly one row in token_owned_liquidity_state
    with entry == latest
  - subsequent observations for that same synthetic real position: latest
    updates in place, row count stays at 1 (bounded, not append-only)
  - a mint dropping out of the eligible set (position closed): no further
    updates
  - the legacy health/risk pipeline is never invoked
  - a liquidity persistence failure never raises
"""
import sqlite3
import time
from unittest.mock import patch

import pytest

from src.core.liquidity_worker import LiquidityWorker
from src.core.price_service import TokenPrice


def _make_db(tmp_path):
    db_path = str(tmp_path / "test_liquidity_gate.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE trade_simulations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mint TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            opened_at INTEGER,
            closed_at INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE token_liquidity_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            mint TEXT NOT NULL,
            pair_address TEXT,
            liquidity_usd REAL NOT NULL,
            liquidity_sol REAL NOT NULL,
            captured_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE token_liquidity_health (
            mint TEXT PRIMARY KEY,
            health_score REAL,
            assessed_at INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE token_liquidity_risks (
            mint TEXT PRIMARY KEY,
            risk_score REAL,
            last_assessed INTEGER
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _open_paper_simulation(db_path, mint, opened_at=None):
    """A paper/automated simulation position -- must NEVER be read by the
    production liquidity worker as an eligibility source."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO trade_simulations (mint, status, opened_at) VALUES (?, 'OPEN', ?)",
        (mint, opened_at or int(time.time())),
    )
    conn.commit()
    conn.close()


def _owned_state_rows(db_path, mint=None):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if mint:
            rows = conn.execute(
                "SELECT * FROM token_owned_liquidity_state WHERE mint=?", (mint,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM token_owned_liquidity_state").fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _snapshot_rows(db_path, mint=None):
    conn = sqlite3.connect(db_path)
    if mint:
        rows = conn.execute(
            "SELECT * FROM token_liquidity_snapshots WHERE mint=?", (mint,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM token_liquidity_snapshots").fetchall()
    conn.close()
    return rows


def _fake_price(mint, liquidity_usd, source="dexscreener"):
    return TokenPrice(
        mint=mint, price_usd=0.001, price_sol=0.00001, liquidity_usd=liquidity_usd,
        volume_24h=500.0, market_cap=50_000.0, source=source, pair_address=None,
    )


def test_default_no_real_position_authority_zero_work(tmp_path):
    """Current, correct production state: no real-position authority exists,
    so _get_live_position_mints() returns [] and the worker does nothing."""
    db_path = _make_db(tmp_path)
    worker = LiquidityWorker(db_path=db_path)
    assert worker._get_live_position_mints() == []
    with patch.object(worker.price_service, "get_token_prices_sync") as mock_fetch:
        worker._refresh_cycle()
        mock_fetch.assert_not_called()
    assert _snapshot_rows(db_path) == []
    assert _owned_state_rows(db_path) == []


def test_paper_simulation_open_does_not_qualify(tmp_path):
    """An OPEN trade_simulations row (paper/automated simulation) must NOT be
    treated as a real position -- this is the exact bug this milestone fixes."""
    db_path = _make_db(tmp_path)
    _open_paper_simulation(db_path, "PAPER_MINT")
    worker = LiquidityWorker(db_path=db_path)
    # The default eligibility interface must not read trade_simulations at all.
    assert worker._get_live_position_mints() == []
    with patch.object(worker.price_service, "get_token_prices_sync") as mock_fetch:
        worker._refresh_cycle()
        mock_fetch.assert_not_called()
    assert _snapshot_rows(db_path, "PAPER_MINT") == []
    assert _owned_state_rows(db_path, "PAPER_MINT") == []


def test_synthetic_real_position_first_observation_freezes_entry(tmp_path):
    """Stands in for a future real trading system implementing
    _get_live_position_mints() with an actual authoritative source."""
    db_path = _make_db(tmp_path)
    worker = LiquidityWorker(db_path=db_path)
    with patch.object(worker, "_get_live_position_mints", return_value=["MINT_A"]), \
         patch.object(worker.price_service, "get_token_prices_sync",
                       return_value={"MINT_A": _fake_price("MINT_A", 10_000.0)}):
        worker._refresh_cycle()

    rows = _owned_state_rows(db_path, "MINT_A")
    assert len(rows) == 1
    assert rows[0]["entry_liquidity_usd"] == 10_000.0
    assert rows[0]["latest_liquidity_usd"] == 10_000.0
    assert rows[0]["entry_liquidity_at"] == rows[0]["latest_liquidity_at"]
    # never a dense history row
    assert _snapshot_rows(db_path, "MINT_A") == []


def test_synthetic_real_position_subsequent_observations_update_in_place(tmp_path):
    db_path = _make_db(tmp_path)
    worker = LiquidityWorker(db_path=db_path)

    with patch.object(worker, "_get_live_position_mints", return_value=["MINT_B"]), \
         patch.object(worker.price_service, "get_token_prices_sync",
                       return_value={"MINT_B": _fake_price("MINT_B", 5_000.0)}):
        worker._refresh_cycle()
    time.sleep(1.1)  # ensure a distinct unix-second timestamp
    with patch.object(worker, "_get_live_position_mints", return_value=["MINT_B"]), \
         patch.object(worker.price_service, "get_token_prices_sync",
                       return_value={"MINT_B": _fake_price("MINT_B", 7_500.0)}):
        worker._refresh_cycle()

    rows = _owned_state_rows(db_path, "MINT_B")
    assert len(rows) == 1  # bounded: still exactly one row, not append-only
    assert rows[0]["entry_liquidity_usd"] == 5_000.0  # entry frozen
    assert rows[0]["latest_liquidity_usd"] == 7_500.0  # latest updated
    assert rows[0]["latest_liquidity_at"] > rows[0]["entry_liquidity_at"]
    assert _snapshot_rows(db_path, "MINT_B") == []


def test_position_dropping_out_of_eligibility_stops_updates(tmp_path):
    """Once a mint is no longer returned by _get_live_position_mints()
    (position closed in a future real trading system), it must stop
    receiving updates."""
    db_path = _make_db(tmp_path)
    worker = LiquidityWorker(db_path=db_path)
    with patch.object(worker, "_get_live_position_mints", return_value=["MINT_C"]), \
         patch.object(worker.price_service, "get_token_prices_sync",
                       return_value={"MINT_C": _fake_price("MINT_C", 3_000.0)}):
        worker._refresh_cycle()

    # Position "closes": no longer eligible.
    with patch.object(worker, "_get_live_position_mints", return_value=[]), \
         patch.object(worker.price_service, "get_token_prices_sync") as mock_fetch:
        worker._refresh_cycle()
        mock_fetch.assert_not_called()

    rows = _owned_state_rows(db_path, "MINT_C")
    assert len(rows) == 1
    assert rows[0]["latest_liquidity_usd"] == 3_000.0  # unchanged after drop-out


def test_legacy_health_risk_never_invoked(tmp_path):
    """LiquidityWorker must not call the legacy stale health/risk pipeline at all."""
    db_path = _make_db(tmp_path)
    worker = LiquidityWorker(db_path=db_path)
    assert not hasattr(worker, "intelligence")  # legacy LiquidityIntelligence dependency removed
    with patch.object(worker, "_get_live_position_mints", return_value=["MINT_D"]), \
         patch.object(worker.price_service, "get_token_prices_sync",
                       return_value={"MINT_D": _fake_price("MINT_D", 1_000.0)}):
        worker._refresh_cycle()
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM token_liquidity_health").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM token_liquidity_risks").fetchone()[0] == 0
    conn.close()


def test_liquidity_persistence_failure_is_non_fatal(tmp_path):
    """A broken/missing owned-state table (or any write failure) must never raise."""
    db_path = _make_db(tmp_path)
    worker = LiquidityWorker(db_path=db_path)
    with patch.object(worker, "_get_live_position_mints", return_value=["MINT_E"]), \
         patch.object(worker.price_service, "get_token_prices_sync",
                       return_value={"MINT_E": _fake_price("MINT_E", 2_000.0)}), \
         patch.object(worker, "_upsert_owned_liquidity_state",
                      side_effect=sqlite3.OperationalError("simulated failure")):
        # must not raise
        worker._refresh_cycle()


def test_multiple_synthetic_real_positions_each_bounded(tmp_path):
    db_path = _make_db(tmp_path)
    worker = LiquidityWorker(db_path=db_path)
    with patch.object(worker, "_get_live_position_mints", return_value=["MINT_F", "MINT_G"]), \
         patch.object(worker.price_service, "get_token_prices_sync",
                       return_value={
                           "MINT_F": _fake_price("MINT_F", 100.0),
                           "MINT_G": _fake_price("MINT_G", 200.0),
                       }):
        worker._refresh_cycle()
        worker._refresh_cycle()

    assert len(_owned_state_rows(db_path, "MINT_F")) == 1
    assert len(_owned_state_rows(db_path, "MINT_G")) == 1
    assert len(_owned_state_rows(db_path)) == 2
