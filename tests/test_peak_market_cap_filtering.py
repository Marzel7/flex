import sqlite3

import src.core.price_service as price_service_module
from src.core.price_service import TokenPrice, TokenPriceService
from src.core.price_worker import BackgroundPriceWorker


def test_store_snapshot_advances_peak_monotonically_and_sets_timestamp(tmp_path):
    db_path = tmp_path / "peaks.db"
    service = TokenPriceService(str(db_path))
    mint = "mint-service"

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE token_analysis (
            mint TEXT PRIMARY KEY,
            market_cap_highest REAL,
            market_cap_highest_at_ts INTEGER,
            market_cap_highest_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE token_market_cap_peaks (
            mint TEXT PRIMARY KEY,
            peak_market_cap REAL DEFAULT 0,
            peak_market_cap_at INTEGER,
            raw_peak_mc REAL DEFAULT 0,
            effective_peak_mc REAL DEFAULT 0,
            candidate_peak_mc REAL,
            candidate_peak_count INTEGER DEFAULT 0,
            raw_peak_mc_at INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_snapshot_counts (
            mint TEXT PRIMARY KEY,
            snap_count INTEGER DEFAULT 0,
            last_updated INTEGER DEFAULT 0
        )
        """
    )
    conn.execute("INSERT INTO token_analysis (mint) VALUES (?)", (mint,))
    conn.commit()
    conn.close()

    service._store_snapshot(
        TokenPrice(
            mint=mint,
            price_usd=0.5,
            price_sol=0.0,
            liquidity_usd=25_000.0,
            volume_24h=0.0,
            market_cap=123_000.0,
            source="pool",
            timestamp=1710000000,
        )
    )
    service._store_snapshot(
        TokenPrice(
            mint=mint,
            price_usd=0.4,
            price_sol=0.0,
            liquidity_usd=25_000.0,
            volume_24h=0.0,
            market_cap=100_000.0,
            source="pool",
            timestamp=1710000300,
        )
    )
    service._store_snapshot(
        TokenPrice(
            mint=mint,
            price_usd=0.8,
            price_sol=0.0,
            liquidity_usd=25_000.0,
            volume_24h=0.0,
            market_cap=150_000.0,
            source="pool",
            timestamp=1710000600,
        )
    )

    conn = sqlite3.connect(db_path)
    peak_row = conn.execute(
        """
        SELECT peak_market_cap, peak_market_cap_at, raw_peak_mc, raw_peak_mc_at
        FROM token_market_cap_peaks
        WHERE mint = ?
        """,
        (mint,),
    ).fetchone()
    analysis_row = conn.execute(
        """
        SELECT market_cap_highest, market_cap_highest_at_ts, market_cap_highest_at
        FROM token_analysis
        WHERE mint = ?
        """,
        (mint,),
    ).fetchone()
    conn.close()

    assert peak_row == (150_000.0, 1710000600, 150_000.0, 1710000600)
    assert analysis_row == (150_000.0, 1710000600, "2024-03-09T16:10:00Z")


def test_store_snapshot_backfills_missing_peak_timestamp_without_regressing_peak(tmp_path):
    db_path = tmp_path / "peaks-backfill.db"
    service = TokenPriceService(str(db_path))
    mint = "mint-backfill"

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE token_analysis (
            mint TEXT PRIMARY KEY,
            market_cap_highest REAL,
            market_cap_highest_at_ts INTEGER,
            market_cap_highest_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE token_market_cap_peaks (
            mint TEXT PRIMARY KEY,
            peak_market_cap REAL DEFAULT 0,
            peak_market_cap_at INTEGER,
            raw_peak_mc REAL DEFAULT 0,
            effective_peak_mc REAL DEFAULT 0,
            candidate_peak_mc REAL,
            candidate_peak_count INTEGER DEFAULT 0,
            raw_peak_mc_at INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_snapshot_counts (
            mint TEXT PRIMARY KEY,
            snap_count INTEGER DEFAULT 0,
            last_updated INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        INSERT INTO token_analysis (mint, market_cap_highest)
        VALUES (?, ?)
        """,
        (mint, 250_000.0),
    )
    conn.execute(
        """
        INSERT INTO token_market_cap_peaks (
            mint, peak_market_cap, peak_market_cap_at, raw_peak_mc, effective_peak_mc, candidate_peak_mc, candidate_peak_count, raw_peak_mc_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (mint, 250_000.0, None, 250_000.0, 250_000.0, 250_000.0, 1, None),
    )
    conn.commit()
    conn.close()

    service._store_snapshot(
        TokenPrice(
            mint=mint,
            price_usd=0.2,
            price_sol=0.0,
            liquidity_usd=12_000.0,
            volume_24h=0.0,
            market_cap=200_000.0,
            source="pool",
            timestamp=1710000900,
        )
    )

    conn = sqlite3.connect(db_path)
    peak_row = conn.execute(
        """
        SELECT peak_market_cap, peak_market_cap_at, raw_peak_mc, raw_peak_mc_at
        FROM token_market_cap_peaks
        WHERE mint = ?
        """,
        (mint,),
    ).fetchone()
    analysis_row = conn.execute(
        """
        SELECT market_cap_highest, market_cap_highest_at_ts, market_cap_highest_at
        FROM token_analysis
        WHERE mint = ?
        """,
        (mint,),
    ).fetchone()
    conn.close()

    assert peak_row == (250_000.0, 1710000900, 250_000.0, 1710000900)
    assert analysis_row == (250_000.0, 1710000900, "2024-03-09T16:15:00Z")


def test_worker_peak_update_is_monotonic_and_timestamp_only_changes_on_new_high(tmp_path):
    db_path = tmp_path / "worker.db"
    mint = "mint-worker"

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE token_market_cap_peaks (
            mint TEXT PRIMARY KEY,
            peak_market_cap REAL DEFAULT 0,
            peak_market_cap_at INTEGER,
            raw_peak_mc REAL DEFAULT 0,
            effective_peak_mc REAL DEFAULT 0,
            candidate_peak_mc REAL,
            candidate_peak_count INTEGER DEFAULT 0,
            raw_peak_mc_at INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE token_analysis (
            mint TEXT PRIMARY KEY,
            market_cap_highest REAL,
            market_cap_highest_at_ts INTEGER,
            market_cap_highest_at TEXT
        )
        """
    )
    conn.execute("INSERT INTO token_analysis (mint) VALUES (?)", (mint,))
    conn.commit()
    conn.close()

    worker = BackgroundPriceWorker.__new__(BackgroundPriceWorker)
    worker.db_path = str(db_path)
    worker._peak_state = {}

    BackgroundPriceWorker._update_peak_market_cap(worker, mint, 100_000.0, 1710000000, liquidity_usd=6_000.0)
    BackgroundPriceWorker._update_peak_market_cap(worker, mint, 90_000.0, 1710000300, liquidity_usd=6_000.0)
    BackgroundPriceWorker._update_peak_market_cap(worker, mint, 140_000.0, 1710000600, liquidity_usd=6_000.0)

    conn = sqlite3.connect(db_path)
    peak_row = conn.execute(
        """
        SELECT peak_market_cap, peak_market_cap_at, raw_peak_mc, raw_peak_mc_at, effective_peak_mc, candidate_peak_mc, candidate_peak_count
        FROM token_market_cap_peaks
        WHERE mint = ?
        """,
        (mint,),
    ).fetchone()
    analysis_row = conn.execute(
        """
        SELECT market_cap_highest, market_cap_highest_at_ts, market_cap_highest_at
        FROM token_analysis
        WHERE mint = ?
        """,
        (mint,),
    ).fetchone()
    conn.close()

    assert peak_row == (140_000.0, 1710000600, 140_000.0, 1710000600, 140_000.0, 140_000.0, 1)
    assert analysis_row == (140_000.0, 1710000600, "2024-03-09T16:10:00Z")


def test_cached_snapshot_uses_observation_time_for_freshness(tmp_path, monkeypatch):
    db_path = tmp_path / "cached-observed.db"
    service = TokenPriceService(str(db_path))
    mint = "mint-cached"

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE token_analysis (
            mint TEXT PRIMARY KEY,
            market_cap_highest REAL,
            market_cap_highest_at_ts INTEGER,
            market_cap_highest_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE token_market_cap_peaks (
            mint TEXT PRIMARY KEY,
            peak_market_cap REAL DEFAULT 0,
            peak_market_cap_at INTEGER,
            raw_peak_mc REAL DEFAULT 0,
            effective_peak_mc REAL DEFAULT 0,
            candidate_peak_mc REAL,
            candidate_peak_count INTEGER DEFAULT 0,
            raw_peak_mc_at INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_snapshot_counts (
            mint TEXT PRIMARY KEY,
            snap_count INTEGER DEFAULT 0,
            last_updated INTEGER DEFAULT 0
        )
        """
    )
    conn.execute("INSERT INTO token_analysis (mint) VALUES (?)", (mint,))
    conn.commit()
    conn.close()

    old_source_ts = 1710000000
    observed_ts = 1710000900
    monkeypatch.setattr(price_service_module.time, "time", lambda: observed_ts)

    service._store_snapshot(
        TokenPrice(
            mint=mint,
            price_usd=0.25,
            price_sol=0.0,
            liquidity_usd=8_000.0,
            volume_24h=0.0,
            market_cap=25_000.0,
            source="cached",
            timestamp=old_source_ts,
            is_stale=True,
        )
    )

    conn = sqlite3.connect(db_path)
    snapshot_row = conn.execute(
        """
        SELECT captured_at, created_at
        FROM token_price_snapshots
        WHERE mint = ?
        ORDER BY snapshot_id DESC
        LIMIT 1
        """,
        (mint,),
    ).fetchone()
    peak_row = conn.execute(
        """
        SELECT peak_market_cap_at, raw_peak_mc_at
        FROM token_market_cap_peaks
        WHERE mint = ?
        """,
        (mint,),
    ).fetchone()
    conn.close()

    assert snapshot_row == (observed_ts, observed_ts)
    assert peak_row == (observed_ts, observed_ts)
