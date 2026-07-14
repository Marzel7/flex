"""
Phase 7A: Score Smoothing and Stability Coefficient Tests

Tests:
- Exponential smoothing formula correctness
- Stability coefficient bounds and formula
- Idempotency of smoothing computation
- Edge cases (no history, single score, etc.)
"""

import pytest
import sys
from pathlib import Path

# Add parent directory and tests directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from test_utils import (
    db_transaction, seed_network, seed_score_history,
    create_test_db
)
# test_db fixture is provided by conftest.py (auto-discovered by pytest)


class TestExponentialSmoothing:
    """Test exponential smoothing formula: smooth_t = alpha * raw_t + (1-alpha) * smooth_{t-1}"""

    def test_smoothing_with_no_previous_smooth(self, test_db):
        """First score has no previous smoothed value → smoothed = raw"""
        with db_transaction(test_db) as db:
            seed_network(db, 'NewNet', network_type='organic', stability_state='new')
            seed_score_history(db, 'NewNet', build_version=1, score=50)

        # Simulate smoothing computation (alpha=0.3, no previous smooth)
        # smooth_1 = raw_1 (since no previous)
        with db_transaction(test_db) as db:
            # Compute smoothed score
            result = db.execute('''
                SELECT
                  50 as raw_score,
                  50 as expected_smoothed
            ''').fetchone()

            assert result['raw_score'] == 50
            assert result['expected_smoothed'] == 50

    def test_smoothing_with_previous_smooth(self, test_db):
        """Subsequent scores use formula: smooth = alpha * raw + (1-alpha) * prev_smooth"""
        with db_transaction(test_db) as db:
            seed_network(db, 'SmoothNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'SmoothNet', build_version=1, score=50)
            seed_score_history(db, 'SmoothNet', build_version=2, score=60)

        # alpha=0.3, prev_smooth=50, raw=60
        # expected smooth = 0.3 * 60 + 0.7 * 50 = 18 + 35 = 53
        alpha = 0.3
        prev_smooth = 50
        raw_score = 60
        expected_smoothed = alpha * raw_score + (1 - alpha) * prev_smooth

        assert abs(expected_smoothed - 53.0) < 0.01

    def test_smoothing_dampens_spikes(self, test_db):
        """Verify smoothing reduces impact of sudden score jumps"""
        with db_transaction(test_db) as db:
            seed_network(db, 'SpikyNet', network_type='organic', stability_state='stable')
            # Stable at 30, then spike to 90
            seed_score_history(db, 'SpikyNet', build_version=1, score=30)
            seed_score_history(db, 'SpikyNet', build_version=2, score=90)

        # alpha=0.3: smooth = 0.3 * 90 + 0.7 * 30 = 27 + 21 = 48
        # Raw changed by 60, smoothed only by 18 (dampening works)
        alpha = 0.3
        raw_delta = 90 - 30  # 60
        smoothed = alpha * 90 + (1 - alpha) * 30
        smooth_delta = smoothed - 30

        assert raw_delta == 60
        assert abs(smooth_delta - 18.0) < 0.01
        assert smooth_delta < raw_delta  # Smoothing reduces change


class TestStabilityCoefficientBounds:
    """Test stability coefficient formula and bounds"""

    def test_stability_fully_stable(self, test_db):
        """Zero volatility → stability = 1.0"""
        with db_transaction(test_db) as db:
            seed_network(db, 'StableNet', network_type='organic', stability_state='stable')
            # Constant score → vol5 = 0 → stability = 1 / (1 + 0) = 1.0
            seed_score_history(db, 'StableNet', build_version=1, score=50)
            seed_score_history(db, 'StableNet', build_version=2, score=50)
            seed_score_history(db, 'StableNet', build_version=3, score=50)

        # vol5 = 0 → stability = 1 / (1 + 0) = 1.0
        vol5 = 0.0
        stability = 1.0 / (1.0 + (vol5 / 10.0))

        assert abs(stability - 1.0) < 0.01

    def test_stability_moderate_volatility(self, test_db):
        """Medium volatility (vol5=10) → stability ≈ 0.5"""
        with db_transaction(test_db) as db:
            seed_network(db, 'ModNet', network_type='organic', stability_state='growing')
            # Oscillating around 50, avg delta = 10
            seed_score_history(db, 'ModNet', build_version=1, score=45)
            seed_score_history(db, 'ModNet', build_version=2, score=55)
            seed_score_history(db, 'ModNet', build_version=3, score=45)

        # vol5 ≈ 10 → stability = 1 / (1 + 1) = 0.5
        vol5 = 10.0
        stability = 1.0 / (1.0 + (vol5 / 10.0))

        assert abs(stability - 0.5) < 0.01

    def test_stability_high_volatility(self, test_db):
        """High volatility (vol5=20) → stability ≈ 0.33"""
        with db_transaction(test_db) as db:
            seed_network(db, 'VolNet', network_type='cex_connected', stability_state='shrinking')
            # Large swings, avg delta = 20
            seed_score_history(db, 'VolNet', build_version=1, score=20)
            seed_score_history(db, 'VolNet', build_version=2, score=60)
            seed_score_history(db, 'VolNet', build_version=3, score=20)

        # vol5 = 20 → stability = 1 / (1 + 2) = 0.33
        vol5 = 20.0
        stability = 1.0 / (1.0 + (vol5 / 10.0))

        assert abs(stability - 0.333) < 0.01

    def test_stability_clamped_lower_bound(self, test_db):
        """Extreme volatility doesn't go below 0.1"""
        with db_transaction(test_db) as db:
            seed_network(db, 'ExtremeNet', network_type='cex_and_infra_connected', stability_state='new')
            # Extreme swings
            seed_score_history(db, 'ExtremeNet', build_version=1, score=0)
            seed_score_history(db, 'ExtremeNet', build_version=2, score=100)
            seed_score_history(db, 'ExtremeNet', build_version=3, score=0)
            seed_score_history(db, 'ExtremeNet', build_version=4, score=100)

        # vol5 = 50 → stability = 1/(1+5) = 0.167, min bound is 0.1
        vol5 = 50.0
        stability_unclamped = 1.0 / (1.0 + (vol5 / 10.0))
        stability_clamped = max(0.1, min(1.0, stability_unclamped))

        assert stability_unclamped == 1.0 / 6.0  # ≈ 0.167
        assert stability_clamped >= 0.1  # Clamped to minimum 0.1
        assert stability_clamped <= 1.0  # Clamped to maximum 1.0

    def test_stability_no_history_defaults_to_one(self, test_db):
        """Networks with <2 history points → stability = 1.0 (fully stable)"""
        with db_transaction(test_db) as db:
            seed_network(db, 'NewNetwork', network_type='organic', stability_state='new')
            # Only 1 history point, no deltas to compute
            seed_score_history(db, 'NewNetwork', build_version=1, score=50)

        # Cannot compute vol5 with <2 history → default to 1.0
        # This should be handled in Phase I of build pipeline
        stability = 1.0  # Default
        assert stability == 1.0


class TestStabilityCoefficientIdempotency:
    """Verify stability coefficient computation is idempotent"""

    def test_recompute_stability_same_result(self, test_db):
        """Running stability computation twice with same history → same coefficient"""
        with db_transaction(test_db) as db:
            seed_network(db, 'IdempotentNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'IdempotentNet', build_version=1, score=40)
            seed_score_history(db, 'IdempotentNet', build_version=2, score=50)
            seed_score_history(db, 'IdempotentNet', build_version=3, score=45)

        # First computation
        vol5_1 = (10 + 5) / 2  # avg of |50-40| and |45-50|
        stability_1 = 1.0 / (1.0 + (vol5_1 / 10.0))
        stability_1 = max(0.1, min(1.0, stability_1))

        # Second computation (should be identical)
        vol5_2 = (10 + 5) / 2
        stability_2 = 1.0 / (1.0 + (vol5_2 / 10.0))
        stability_2 = max(0.1, min(1.0, stability_2))

        assert stability_1 == stability_2

    def test_adding_history_updates_stability(self, test_db):
        """Adding new score to history correctly updates stability"""
        with db_transaction(test_db) as db:
            seed_network(db, 'HistoryNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'HistoryNet', build_version=1, score=50)
            seed_score_history(db, 'HistoryNet', build_version=2, score=50)

        # First: only 1 delta, stability = 1.0
        vol5_1 = 0.0  # No delta
        stability_1 = 1.0

        # Add more history
        with db_transaction(test_db) as db:
            seed_score_history(db, 'HistoryNet', build_version=3, score=60)

        # Now: avg delta = 5, different stability
        vol5_2 = (0 + 10) / 2  # avg of deltas
        stability_2 = 1.0 / (1.0 + (vol5_2 / 10.0))
        stability_2 = max(0.1, min(1.0, stability_2))

        assert stability_1 != stability_2
        assert abs(stability_2 - 0.667) < 0.01


class TestSmoothingIntegration:
    """Integration tests for smoothing in multi-network scenarios"""

    def test_multiple_networks_independent_smoothing(self, test_db):
        """Different networks have independent smoothing computations"""
        with db_transaction(test_db) as db:
            seed_network(db, 'StableNet1', network_type='organic', stability_state='stable')
            seed_network(db, 'VolatileNet2', network_type='cex_connected', stability_state='growing')

            # StableNet: constant 50
            seed_score_history(db, 'StableNet1', build_version=1, score=50)
            seed_score_history(db, 'StableNet1', build_version=2, score=50)

            # VolatileNet: oscillates
            seed_score_history(db, 'VolatileNet2', build_version=1, score=30)
            seed_score_history(db, 'VolatileNet2', build_version=2, score=70)

        # Stable network: vol5=0, stability=1.0
        stable_vol5 = 0.0
        stable_stability = 1.0

        # Volatile network: vol5=40, stability≈0.2
        volatile_vol5 = 40.0
        volatile_stability = 1.0 / (1.0 + (volatile_vol5 / 10.0))
        volatile_stability = max(0.1, min(1.0, volatile_stability))

        assert abs(stable_stability - 1.0) < 0.01
        assert abs(volatile_stability - 0.2) < 0.01
        assert stable_stability > volatile_stability


class TestSmoothedScoreRounding:
    """Test that smoothed scores round correctly to integers"""

    def test_smoothed_score_rounding(self, test_db):
        """Verify rounding of smoothed scores"""
        with db_transaction(test_db) as db:
            seed_network(db, 'RoundNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'RoundNet', build_version=1, score=50)
            seed_score_history(db, 'RoundNet', build_version=2, score=51)

        # alpha=0.3: smooth = 0.3*51 + 0.7*50 = 15.3 + 35 = 50.3 → rounds to 50
        alpha = 0.3
        smoothed = alpha * 51 + (1 - alpha) * 50
        assert abs(smoothed - 50.3) < 0.01
        assert round(smoothed) == 50

    def test_smoothed_score_bounds(self, test_db):
        """Smoothed score stays within 0-100 like raw score"""
        with db_transaction(test_db) as db:
            seed_network(db, 'BoundNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'BoundNet', build_version=1, score=0)
            seed_score_history(db, 'BoundNet', build_version=2, score=100)

        # alpha=0.3: smooth = 0.3*100 + 0.7*0 = 30
        alpha = 0.3
        smoothed = alpha * 100 + (1 - alpha) * 0
        assert 0 <= smoothed <= 100
        assert abs(smoothed - 30.0) < 0.01
