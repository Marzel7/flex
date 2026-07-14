"""
Test suite for token behaviour classification module.

Tests all components:
- Schema creation and idempotency
- Feature computation from snapshots
- Classification rules and priority ordering
- Confidence scoring bounds
- Database operations (upsert, history tracking)
- End-to-end integration (mint → category)

Uses tempfile for isolated SQLite DBs with synthetic price sequences.
"""

import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from dataclasses import asdict

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.token_behavior import (
    TokenBehaviorFeatures,
    create_schema,
    load_snapshots,
    compute_features,
    classify_token,
    upsert_behavior,
    classify_mint,
)


def _seed_snapshots(db_path: str, mint: str, prices: list, interval_secs: int = 60):
    """
    Insert synthetic price snapshots for testing.

    Args:
        db_path: Path to database
        mint: Mint address
        prices: List of prices (in USD)
        interval_secs: Time between snapshots in seconds (default 60)

    Each snapshot is inserted with captured_at incrementing by interval_secs.
    """
    conn = sqlite3.connect(db_path)
    try:
        # Create table if needed
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_price_snapshots (
                snapshot_id INTEGER PRIMARY KEY,
                mint TEXT NOT NULL,
                price_usd REAL NOT NULL,
                price_sol REAL,
                liquidity_usd REAL,
                volume_24h REAL,
                market_cap REAL,
                source TEXT NOT NULL,
                pair_address TEXT,
                captured_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)

        # Insert snapshots
        base_ts = 1711270000
        for idx, price in enumerate(prices):
            captured_at = base_ts + (idx * interval_secs)
            conn.execute("""
                INSERT INTO token_price_snapshots
                (mint, price_usd, price_sol, liquidity_usd, volume_24h, market_cap,
                 source, pair_address, captured_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (mint, price, 0.0, 0.0, 0.0, 0.0, 'test', 'test', captured_at, captured_at))

        conn.commit()
    finally:
        conn.close()


class TestSchema(unittest.TestCase):
    """Test schema creation and idempotency."""

    def test_schema_creation(self):
        """Schema can be created."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            create_schema(db_path)

            # Verify tables exist
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            tables = cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [t[0] for t in tables]

            self.assertIn('token_behavior', table_names)
            self.assertIn('token_behavior_history', table_names)

            # Verify columns in token_behavior
            columns = cursor.execute(
                "PRAGMA table_info(token_behavior)"
            ).fetchall()
            col_names = [c[1] for c in columns]

            expected_cols = [
                'mint', 'category', 'confidence', 'initial_price_usd',
                'peak_price_usd', 'latest_price_usd', 'max_return_multiple',
                'drawdown_from_peak', 'recovery_ratio', 'time_to_peak_secs',
                'lifetime_secs', 'snapshot_count', 'volatility',
                'slope_early', 'slope_total', 'classified_at', 'created_at'
            ]

            for col in expected_cols:
                self.assertIn(col, col_names)

            conn.close()

        finally:
            import os
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_schema_idempotent(self):
        """Schema creation is idempotent (can be called multiple times)."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            create_schema(db_path)
            create_schema(db_path)  # Should not error

            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            tables = cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            conn.close()

            # Should have token_behavior and token_behavior_history
            table_names = [t[0] for t in tables]
            self.assertIn('token_behavior', table_names)
            self.assertIn('token_behavior_history', table_names)

        finally:
            import os
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestComputeFeatures(unittest.TestCase):
    """Test feature computation from snapshots."""

    def setUp(self):
        """Create temp database for each test."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            self.db_path = f.name
        create_schema(self.db_path)

    def tearDown(self):
        """Clean up temp database."""
        import os
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_immediate_rug_features(self):
        """Compute features for immediate rug (spike then crash)."""
        mint = 'test_immediate_rug'
        prices = [0.001, 0.010, 0.008, 0.002, 0.001]  # Peak at index 1
        _seed_snapshots(self.db_path, mint, prices, interval_secs=60)

        snapshots = load_snapshots(mint, self.db_path)
        features = compute_features(mint, snapshots)

        self.assertEqual(features.snapshot_count, 5)
        self.assertGreater(features.max_return_multiple, 1.0)  # 0.010 / 0.001 = 10
        self.assertGreater(features.drawdown_from_peak, 0.8)  # Huge drop from peak
        self.assertEqual(features.time_to_peak_secs, 60)  # Peak at index 1 (60 secs in)
        self.assertGreater(features.lifetime_secs, 0)

    def test_runner_features(self):
        """Compute features for runner (sustained growth)."""
        mint = 'test_runner'
        prices = [0.001, 0.002, 0.005, 0.010, 0.020, 0.030]  # Consistent growth
        _seed_snapshots(self.db_path, mint, prices, interval_secs=60)

        snapshots = load_snapshots(mint, self.db_path)
        features = compute_features(mint, snapshots)

        self.assertEqual(features.snapshot_count, 6)
        self.assertGreater(features.max_return_multiple, 5.0)  # 0.030 / 0.001 = 30
        self.assertLess(features.drawdown_from_peak, 0.1)  # Minimal drawdown

    def test_zero_price_filtering(self):
        """Snapshots with price_usd <= 0 are filtered out."""
        mint = 'test_zero_filter'
        prices = [0.0, -0.001, 0.001, 0.002]  # First two should be filtered
        _seed_snapshots(self.db_path, mint, prices, interval_secs=60)

        snapshots = load_snapshots(mint, self.db_path)
        self.assertEqual(len(snapshots), 2)  # Only positive prices

    def test_single_snapshot(self):
        """Features computed from single snapshot are handled."""
        mint = 'test_single'
        prices = [0.001]
        _seed_snapshots(self.db_path, mint, prices, interval_secs=60)

        snapshots = load_snapshots(mint, self.db_path)
        features = compute_features(mint, snapshots)

        self.assertEqual(features.snapshot_count, 1)
        self.assertEqual(features.max_return_multiple, 1.0)
        self.assertEqual(features.lifetime_secs, 0)

    def test_volatility_computation(self):
        """Volatility is computed correctly."""
        mint = 'test_volatility'
        prices = [0.001, 0.002, 0.001, 0.002, 0.001]  # Oscillating
        _seed_snapshots(self.db_path, mint, prices, interval_secs=60)

        snapshots = load_snapshots(mint, self.db_path)
        features = compute_features(mint, snapshots)

        self.assertGreater(features.volatility, 0.3)  # Should be substantial

    def test_slope_computation(self):
        """Linear slope is computed."""
        mint = 'test_slope'
        prices = [0.001, 0.002, 0.003, 0.004, 0.005]  # Linear increase
        _seed_snapshots(self.db_path, mint, prices, interval_secs=60)

        snapshots = load_snapshots(mint, self.db_path)
        features = compute_features(mint, snapshots)

        self.assertGreater(features.slope_total, 0)  # Positive slope
        self.assertGreater(features.slope_early, 0)  # Early slope also positive


class TestClassifyToken(unittest.TestCase):
    """Test classification rules and priority ordering."""

    def test_immediate_rug_classification(self):
        """Token meeting immediate_rug criteria is classified correctly."""
        features = TokenBehaviorFeatures(
            mint='test',
            snapshot_count=10,
            initial_price_usd=0.001,
            peak_price_usd=0.010,
            latest_price_usd=0.001,
            max_return_multiple=10.0,
            drawdown_from_peak=0.90,  # >= 0.85
            recovery_ratio=0.10,  # <= 0.25
            time_to_peak_secs=200,  # <= 300
            lifetime_secs=600,
            volatility=0.5,
            slope_early=0.001,
            slope_total=-0.001,
        )

        category, confidence = classify_token(features)
        self.assertEqual(category, 'immediate_rug')
        self.assertGreater(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)

    def test_runner_classification(self):
        """Token meeting runner criteria is classified correctly."""
        features = TokenBehaviorFeatures(
            mint='test',
            snapshot_count=10,
            initial_price_usd=0.001,
            peak_price_usd=0.030,
            latest_price_usd=0.025,
            max_return_multiple=30.0,  # >= 5.0
            drawdown_from_peak=0.17,  # <= 0.50
            recovery_ratio=0.83,  # >= 0.50
            time_to_peak_secs=300,
            lifetime_secs=600,
            volatility=0.2,
            slope_early=0.010,
            slope_total=0.005,
        )

        category, confidence = classify_token(features)
        self.assertEqual(category, 'runner')
        self.assertGreater(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)

    def test_choppy_runner_classification(self):
        """Token meeting choppy_runner criteria is classified correctly."""
        features = TokenBehaviorFeatures(
            mint='test',
            snapshot_count=10,
            initial_price_usd=0.001,
            peak_price_usd=0.012,
            latest_price_usd=0.008,
            max_return_multiple=4.0,  # >= 3.0 (but < 5.0, so not runner)
            drawdown_from_peak=0.33,  # <= 0.50 (meets runner drawdown), but low max_return so not runner
            recovery_ratio=0.40,  # >= 0.35, but < 0.50 so not runner
            time_to_peak_secs=400,  # > 300, so not immediate_rug
            lifetime_secs=600,
            volatility=0.4,
            slope_early=0.005,
            slope_total=0.002,
        )

        category, confidence = classify_token(features)
        self.assertEqual(category, 'choppy_runner')
        self.assertGreater(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)

    def test_rug_classification(self):
        """Token meeting rug criteria is classified correctly."""
        features = TokenBehaviorFeatures(
            mint='test',
            snapshot_count=10,
            initial_price_usd=0.001,
            peak_price_usd=0.005,
            latest_price_usd=0.0001,
            max_return_multiple=5.0,  # >= 2.0
            drawdown_from_peak=0.98,  # >= 0.90
            recovery_ratio=0.02,  # <= 0.25, so would be immediate_rug if time_to_peak <= 300
            time_to_peak_secs=400,  # > 300, so NOT immediate_rug despite low recovery
            lifetime_secs=600,
            volatility=0.3,
            slope_early=0.002,
            slope_total=-0.001,
        )

        category, confidence = classify_token(features)
        self.assertEqual(category, 'rug')
        self.assertGreater(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)

    def test_slow_rug_classification(self):
        """Token meeting slow_rug criteria is classified correctly."""
        features = TokenBehaviorFeatures(
            mint='test',
            snapshot_count=10,
            initial_price_usd=0.001,
            peak_price_usd=0.0015,
            latest_price_usd=0.0001,
            max_return_multiple=1.5,  # < 2.0
            drawdown_from_peak=0.93,  # >= 0.70
            recovery_ratio=0.07,  # <= 0.25, so would be immediate_rug if time_to_peak <= 300
            time_to_peak_secs=400,  # > 300, so NOT immediate_rug
            lifetime_secs=600,
            volatility=0.2,
            slope_early=-0.0001,
            slope_total=-0.0001,  # < 0
        )

        category, confidence = classify_token(features)
        self.assertEqual(category, 'slow_rug')
        self.assertGreater(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)

    def test_unknown_too_few_snapshots(self):
        """Token with too few snapshots is classified as unknown."""
        features = TokenBehaviorFeatures(
            mint='test',
            snapshot_count=3,  # < MIN_SNAPSHOTS (8)
            initial_price_usd=0.001,
            peak_price_usd=0.010,
            latest_price_usd=0.005,
            max_return_multiple=10.0,
            drawdown_from_peak=0.50,
            recovery_ratio=0.50,
            time_to_peak_secs=100,
            lifetime_secs=200,
            volatility=0.3,
            slope_early=0.001,
            slope_total=0.001,
        )

        category, confidence = classify_token(features)
        self.assertEqual(category, 'unknown')
        self.assertEqual(confidence, 0.0)

    def test_unknown_too_short_lifetime(self):
        """Token with lifetime < MIN_LIFETIME_SECS is classified as unknown."""
        features = TokenBehaviorFeatures(
            mint='test',
            snapshot_count=10,
            initial_price_usd=0.001,
            peak_price_usd=0.010,
            latest_price_usd=0.005,
            max_return_multiple=10.0,
            drawdown_from_peak=0.50,
            recovery_ratio=0.50,
            time_to_peak_secs=100,
            lifetime_secs=60,  # < MIN_LIFETIME_SECS (180)
            volatility=0.3,
            slope_early=0.001,
            slope_total=0.001,
        )

        category, confidence = classify_token(features)
        self.assertEqual(category, 'unknown')
        self.assertEqual(confidence, 0.0)

    def test_priority_immediate_rug_over_runner(self):
        """immediate_rug has priority over runner."""
        features = TokenBehaviorFeatures(
            mint='test',
            snapshot_count=10,
            initial_price_usd=0.001,
            peak_price_usd=0.030,
            latest_price_usd=0.002,
            max_return_multiple=30.0,  # Meets runner (>= 5.0)
            drawdown_from_peak=0.93,  # Meets immediate_rug (>= 0.85)
            recovery_ratio=0.07,  # Meets immediate_rug (<= 0.25)
            time_to_peak_secs=200,  # Meets immediate_rug (<= 300)
            lifetime_secs=600,
            volatility=0.3,
            slope_early=0.010,
            slope_total=0.005,
        )

        category, confidence = classify_token(features)
        self.assertEqual(category, 'immediate_rug')  # immediate_rug takes priority

    def test_confidence_bounds(self):
        """Confidence is always in [0, 1]."""
        test_cases = [
            ('immediate_rug', TokenBehaviorFeatures(
                mint='test', snapshot_count=10, initial_price_usd=0.001,
                peak_price_usd=0.010, latest_price_usd=0.001,
                max_return_multiple=10.0, drawdown_from_peak=0.90,
                recovery_ratio=0.10, time_to_peak_secs=200, lifetime_secs=600,
                volatility=0.5, slope_early=0.001, slope_total=-0.001,
            )),
            ('runner', TokenBehaviorFeatures(
                mint='test', snapshot_count=10, initial_price_usd=0.001,
                peak_price_usd=0.030, latest_price_usd=0.025,
                max_return_multiple=30.0, drawdown_from_peak=0.17,
                recovery_ratio=0.83, time_to_peak_secs=300, lifetime_secs=600,
                volatility=0.2, slope_early=0.010, slope_total=0.005,
            )),
            ('unknown', TokenBehaviorFeatures(
                mint='test', snapshot_count=3, initial_price_usd=0.001,
                peak_price_usd=0.010, latest_price_usd=0.005,
                max_return_multiple=10.0, drawdown_from_peak=0.50,
                recovery_ratio=0.50, time_to_peak_secs=100, lifetime_secs=200,
                volatility=0.3, slope_early=0.001, slope_total=0.001,
            )),
        ]

        for expected_cat, features in test_cases:
            category, confidence = classify_token(features)
            self.assertGreaterEqual(confidence, 0.0, f"{category} confidence too low")
            self.assertLessEqual(confidence, 1.0, f"{category} confidence too high")


class TestUpsertBehavior(unittest.TestCase):
    """Test database operations (upsert, history tracking)."""

    def setUp(self):
        """Create temp database for each test."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            self.db_path = f.name
        create_schema(self.db_path)

    def tearDown(self):
        """Clean up temp database."""
        import os
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_upsert_insert(self):
        """Upsert inserts new record."""
        features = TokenBehaviorFeatures(
            mint='test_insert',
            snapshot_count=10,
            initial_price_usd=0.001,
            peak_price_usd=0.010,
            latest_price_usd=0.005,
            max_return_multiple=10.0,
            drawdown_from_peak=0.50,
            recovery_ratio=0.50,
            time_to_peak_secs=100,
            lifetime_secs=600,
            volatility=0.3,
            slope_early=0.001,
            slope_total=0.001,
        )

        result = upsert_behavior(features, 'runner', 0.85, self.db_path)
        self.assertTrue(result)

        # Verify record exists
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM token_behavior WHERE mint = ?",
            ('test_insert',)
        ).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row['category'], 'runner')
        self.assertEqual(row['confidence'], 0.85)

    def test_upsert_update(self):
        """Upsert updates existing record."""
        mint = 'test_update'
        features = TokenBehaviorFeatures(
            mint=mint,
            snapshot_count=10,
            initial_price_usd=0.001,
            peak_price_usd=0.010,
            latest_price_usd=0.005,
            max_return_multiple=10.0,
            drawdown_from_peak=0.50,
            recovery_ratio=0.50,
            time_to_peak_secs=100,
            lifetime_secs=600,
            volatility=0.3,
            slope_early=0.001,
            slope_total=0.001,
        )

        # First upsert
        upsert_behavior(features, 'runner', 0.85, self.db_path)

        # Second upsert with different category/confidence
        upsert_behavior(features, 'rug', 0.90, self.db_path)

        # Verify only one record exists
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM token_behavior WHERE mint = ?",
            (mint,)
        ).fetchall()
        conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['category'], 'rug')
        self.assertEqual(rows[0]['confidence'], 0.90)

    def test_created_at_preserved(self):
        """created_at timestamp is preserved on upsert."""
        mint = 'test_created_at'
        features = TokenBehaviorFeatures(
            mint=mint,
            snapshot_count=10,
            initial_price_usd=0.001,
            peak_price_usd=0.010,
            latest_price_usd=0.005,
            max_return_multiple=10.0,
            drawdown_from_peak=0.50,
            recovery_ratio=0.50,
            time_to_peak_secs=100,
            lifetime_secs=600,
            volatility=0.3,
            slope_early=0.001,
            slope_total=0.001,
        )

        # First insert
        upsert_behavior(features, 'runner', 0.85, self.db_path)

        # Get created_at from first insert
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row1 = conn.execute(
            "SELECT created_at FROM token_behavior WHERE mint = ?",
            (mint,)
        ).fetchone()
        created_at_1 = row1['created_at']
        conn.close()

        # Wait a moment
        import time
        time.sleep(0.1)

        # Second upsert
        upsert_behavior(features, 'rug', 0.90, self.db_path)

        # Get created_at from second insert
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row2 = conn.execute(
            "SELECT created_at FROM token_behavior WHERE mint = ?",
            (mint,)
        ).fetchone()
        created_at_2 = row2['created_at']
        conn.close()

        # created_at should be the same
        self.assertEqual(created_at_1, created_at_2)

    def test_history_appended(self):
        """History records are appended on upsert."""
        mint = 'test_history'
        features = TokenBehaviorFeatures(
            mint=mint,
            snapshot_count=10,
            initial_price_usd=0.001,
            peak_price_usd=0.010,
            latest_price_usd=0.005,
            max_return_multiple=10.0,
            drawdown_from_peak=0.50,
            recovery_ratio=0.50,
            time_to_peak_secs=100,
            lifetime_secs=600,
            volatility=0.3,
            slope_early=0.001,
            slope_total=0.001,
        )

        # First upsert
        upsert_behavior(features, 'runner', 0.85, self.db_path)

        # Second upsert
        upsert_behavior(features, 'rug', 0.90, self.db_path)

        # Verify two history records exist
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT * FROM token_behavior_history WHERE mint = ? ORDER BY history_id",
            (mint,)
        ).fetchall()
        conn.close()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][2], 'runner')  # First category
        self.assertEqual(rows[1][2], 'rug')     # Second category


class TestClassifyMint(unittest.TestCase):
    """Test end-to-end integration: mint → snapshots → features → category."""

    def setUp(self):
        """Create temp database for each test."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            self.db_path = f.name
        create_schema(self.db_path)

    def tearDown(self):
        """Clean up temp database."""
        import os
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_classify_mint_immediate_rug(self):
        """classify_mint detects immediate rug."""
        mint = 'test_rug'
        prices = [0.001, 0.010, 0.008, 0.002, 0.001, 0.001, 0.001, 0.001]
        _seed_snapshots(self.db_path, mint, prices, interval_secs=60)

        category, confidence = classify_mint(mint, self.db_path)

        self.assertEqual(category, 'immediate_rug')
        self.assertGreater(confidence, 0.0)

    def test_classify_mint_runner(self):
        """classify_mint detects runner."""
        mint = 'test_runner'
        prices = [0.001, 0.002, 0.005, 0.010, 0.020, 0.030, 0.025, 0.020]
        _seed_snapshots(self.db_path, mint, prices, interval_secs=60)

        category, confidence = classify_mint(mint, self.db_path)

        self.assertEqual(category, 'runner')
        self.assertGreater(confidence, 0.0)

    def test_classify_mint_too_few_snapshots(self):
        """classify_mint returns unknown for too few snapshots."""
        mint = 'test_few'
        prices = [0.001, 0.002, 0.003]  # Only 3 snapshots
        _seed_snapshots(self.db_path, mint, prices, interval_secs=60)

        category, confidence = classify_mint(mint, self.db_path)

        self.assertEqual(category, 'unknown')
        self.assertEqual(confidence, 0.0)

    def test_classify_mint_no_snapshots(self):
        """classify_mint handles mint with no snapshots."""
        mint = 'nonexistent'

        # Ensure price snapshots table exists (empty)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_price_snapshots (
                snapshot_id INTEGER PRIMARY KEY,
                mint TEXT NOT NULL,
                price_usd REAL NOT NULL,
                price_sol REAL,
                liquidity_usd REAL,
                volume_24h REAL,
                market_cap REAL,
                source TEXT NOT NULL,
                pair_address TEXT,
                captured_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        category, confidence = classify_mint(mint, self.db_path)

        self.assertEqual(category, 'unknown')
        self.assertEqual(confidence, 0.0)

    def test_classify_mint_stores_result(self):
        """classify_mint stores result to database."""
        mint = 'test_store'
        prices = [0.001, 0.010, 0.008, 0.002, 0.001, 0.001, 0.001, 0.001]
        _seed_snapshots(self.db_path, mint, prices, interval_secs=60)

        classify_mint(mint, self.db_path)

        # Verify stored in database
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM token_behavior WHERE mint = ?",
            (mint,)
        ).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row['category'], 'immediate_rug')


if __name__ == '__main__':
    unittest.main()
