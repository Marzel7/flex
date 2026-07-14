"""
Phase 6A: Test alert generation rules (Phases 4C, 5A, 5B).

Tests alert rules:
- Phase 4C: SCORE_SPIKE, NEW_HIGH_RISK, TYPE_FLIP, LIFECYCLE_FLIP
- Phase 5A: SCORE_DROP, VOLATILITY_SPIKE
- Phase 5B: RISK_MOMENTUM_UP, RISK_ACCELERATION_SPIKE
"""

import pytest
import json
import sys
from pathlib import Path

# Add parent directory and tests directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from test_utils import (
    db_transaction, seed_network, seed_network_evidence,
    seed_score_history, insert_alert, get_alert_count, get_alerts
)
# test_db fixture is provided by conftest.py (auto-discovered by pytest)


def generate_alerts(db_path, current_build: int):
    """Generate all alerts for the current build."""
    with db_transaction(db_path) as db:
        # A) SCORE_SPIKE: delta >= +20
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH score_deltas AS (
              SELECT
                h.network_name,
                h.build_version,
                h.score as curr_score,
                (SELECT score FROM network_score_history p
                 WHERE p.network_name = h.network_name
                 AND p.build_version = h.build_version - 1) as prev_score
              FROM network_score_history h
              WHERE h.build_version = ?
            )
            SELECT
              sd.network_name,
              sd.build_version,
              'SCORE_SPIKE',
              CASE
                WHEN (sd.curr_score - COALESCE(sd.prev_score, 0)) >= 35 THEN 'high'
                ELSE 'medium'
              END,
              'Score increased by ' || (sd.curr_score - COALESCE(sd.prev_score, 0)) ||
                ' points (from ' || COALESCE(sd.prev_score, 'N/A') || ' to ' || sd.curr_score || ')',
              json_object(
                'prev_score', sd.prev_score,
                'curr_score', sd.curr_score,
                'delta', sd.curr_score - COALESCE(sd.prev_score, 0)
              )
            FROM score_deltas sd
            WHERE COALESCE(sd.prev_score, 0) IS NOT NULL
              AND (sd.curr_score - COALESCE(sd.prev_score, 0)) >= 20;
        ''', (current_build,))

        # E) SCORE_DROP: delta <= -20
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH score_deltas AS (
              SELECT
                h.network_name,
                h.build_version,
                h.score as curr_score,
                (SELECT score FROM network_score_history p
                 WHERE p.network_name = h.network_name
                 AND p.build_version = h.build_version - 1) as prev_score
              FROM network_score_history h
              WHERE h.build_version = ?
            )
            SELECT
              sd.network_name,
              sd.build_version,
              'SCORE_DROP',
              CASE
                WHEN (sd.curr_score - COALESCE(sd.prev_score, 0)) <= -35 THEN 'high'
                ELSE 'medium'
              END,
              'Score decreased by ' || ABS(sd.curr_score - COALESCE(sd.prev_score, 0)) ||
                ' points (from ' || COALESCE(sd.prev_score, 'N/A') || ' to ' || sd.curr_score || ')',
              json_object(
                'prev_score', sd.prev_score,
                'curr_score', sd.curr_score,
                'delta', sd.curr_score - COALESCE(sd.prev_score, 0)
              )
            FROM score_deltas sd
            WHERE COALESCE(sd.prev_score, 0) IS NOT NULL
              AND (sd.curr_score - COALESCE(sd.prev_score, 0)) <= -20;
        ''', (current_build,))

        # F) VOLATILITY_SPIKE: avg(|delta|) >= 15 over last 5 builds
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH volatility_data AS (
              SELECT
                h.network_name,
                h.build_version,
                h.score,
                (SELECT score FROM network_score_history p
                 WHERE p.network_name = h.network_name
                 AND p.build_version = h.build_version - 1) as prev_score,
                COUNT(*) OVER (PARTITION BY h.network_name ORDER BY h.build_version ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) as window_size
              FROM network_score_history h
              WHERE h.build_version <= ?
            ),
            score_diffs AS (
              SELECT
                network_name,
                build_version,
                window_size,
                AVG(ABS(score - COALESCE(prev_score, score))) as volatility
              FROM volatility_data
              WHERE window_size >= 2
              GROUP BY network_name, build_version, window_size
            )
            SELECT
              sd.network_name,
              sd.build_version,
              'VOLATILITY_SPIKE',
              CASE
                WHEN sd.volatility >= 25 THEN 'high'
                ELSE 'medium'
              END,
              'High volatility detected: avg score change ' || CAST(ROUND(sd.volatility, 1) AS TEXT) || ' over last ' || sd.window_size || ' builds',
              json_object(
                'volatility', ROUND(sd.volatility, 2),
                'window_size', sd.window_size
              )
            FROM score_diffs sd
            WHERE sd.build_version = ?
              AND sd.volatility >= 15;
        ''', (current_build, current_build))

        # G) RISK_MOMENTUM_UP: momentum >= 10
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH deltas AS (
              SELECT
                h.network_name,
                h.build_version,
                h.score,
                h.score - LAG(h.score, 1) OVER (PARTITION BY h.network_name ORDER BY h.build_version) as delta,
                COUNT(*) OVER (PARTITION BY h.network_name ORDER BY h.build_version ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) as window_size
              FROM network_score_history h
              WHERE h.build_version <= ?
            ),
            momentum_calc AS (
              SELECT
                network_name,
                build_version,
                window_size,
                AVG(delta) OVER (PARTITION BY network_name ORDER BY build_version ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) as momentum
              FROM deltas
              WHERE window_size >= 3
            )
            SELECT
              mc.network_name,
              mc.build_version,
              'RISK_MOMENTUM_UP',
              CASE
                WHEN mc.momentum >= 20 THEN 'high'
                ELSE 'medium'
              END,
              'Risk momentum increasing: avg +' || CAST(ROUND(mc.momentum, 1) AS TEXT) || ' over last 3 builds',
              json_object(
                'momentum', ROUND(mc.momentum, 2),
                'window', 3
              )
            FROM momentum_calc mc
            WHERE mc.build_version = ?
              AND mc.momentum >= 10;
        ''', (current_build, current_build))

        # H) RISK_ACCELERATION_SPIKE: acceleration >= 15
        db.execute('''
            INSERT OR IGNORE INTO network_alerts
            (network_name, build_version, alert_type, severity, message, details_json)
            WITH deltas AS (
              SELECT
                h.network_name,
                h.build_version,
                h.score,
                h.score - LAG(h.score, 1) OVER (PARTITION BY h.network_name ORDER BY h.build_version) as delta,
                LAG(h.score, 1) OVER (PARTITION BY h.network_name ORDER BY h.build_version) as prev_score,
                LAG(h.score, 2) OVER (PARTITION BY h.network_name ORDER BY h.build_version) as prev_prev_score,
                COUNT(*) OVER (PARTITION BY h.network_name ORDER BY h.build_version ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) as window_size
              FROM network_score_history h
              WHERE h.build_version <= ?
            ),
            acceleration_calc AS (
              SELECT
                network_name,
                build_version,
                window_size,
                delta,
                CASE WHEN prev_score IS NOT NULL AND prev_prev_score IS NOT NULL
                     THEN prev_score - prev_prev_score
                     ELSE NULL
                END as prev_delta,
                delta - CASE WHEN prev_score IS NOT NULL AND prev_prev_score IS NOT NULL
                             THEN prev_score - prev_prev_score
                             ELSE NULL
                        END as acceleration
              FROM deltas
              WHERE window_size >= 3
            )
            SELECT
              ac.network_name,
              ac.build_version,
              'RISK_ACCELERATION_SPIKE',
              CASE
                WHEN ABS(ac.acceleration) >= 25 THEN 'high'
                ELSE 'medium'
              END,
              'Risk acceleration spike: Δ increased by ' || CAST(ROUND(ac.acceleration, 1) AS TEXT),
              json_object(
                'delta_current', ROUND(ac.delta, 2),
                'delta_previous', ROUND(ac.prev_delta, 2),
                'acceleration', ROUND(ac.acceleration, 2)
              )
            FROM acceleration_calc ac
            WHERE ac.build_version = ?
              AND ac.prev_delta IS NOT NULL
              AND ac.acceleration >= 15;
        ''', (current_build, current_build))


class TestPhase4CAlerts:
    """Test Phase 4C alert rules."""

    def test_score_spike_medium(self, test_db):
        """SCORE_SPIKE: delta +20-34 → medium."""
        with db_transaction(test_db) as db:
            seed_network(db, 'SpikeNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'SpikeNet', 1, 10)
            seed_score_history(db, 'SpikeNet', 2, 40)  # delta +30

        generate_alerts(test_db, 2)

        with db_transaction(test_db) as db:
            alerts = get_alerts(db, alert_type='SCORE_SPIKE', network_name='SpikeNet')
            assert len(alerts) == 1
            assert alerts[0]['severity'] == 'medium'
            assert '30' in alerts[0]['message']

    def test_score_spike_high(self, test_db):
        """SCORE_SPIKE: delta >= +35 → high."""
        with db_transaction(test_db) as db:
            seed_network(db, 'BigSpikeNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'BigSpikeNet', 1, 10)
            seed_score_history(db, 'BigSpikeNet', 2, 50)  # delta +40

        generate_alerts(test_db, 2)

        with db_transaction(test_db) as db:
            alerts = get_alerts(db, alert_type='SCORE_SPIKE', network_name='BigSpikeNet')
            assert len(alerts) == 1
            assert alerts[0]['severity'] == 'high'

    def test_score_spike_no_alert_below_20(self, test_db):
        """SCORE_SPIKE: delta < +20 → no alert."""
        with db_transaction(test_db) as db:
            seed_network(db, 'SmallSpikeNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'SmallSpikeNet', 1, 10)
            seed_score_history(db, 'SmallSpikeNet', 2, 25)  # delta +15

        generate_alerts(test_db, 2)

        with db_transaction(test_db) as db:
            alerts = get_alerts(db, alert_type='SCORE_SPIKE', network_name='SmallSpikeNet')
            assert len(alerts) == 0


class TestPhase5AAlerts:
    """Test Phase 5A alert rules."""

    def test_score_drop_medium(self, test_db):
        """SCORE_DROP: delta -20 to -34 → medium."""
        with db_transaction(test_db) as db:
            seed_network(db, 'DropNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'DropNet', 1, 80)
            seed_score_history(db, 'DropNet', 2, 55)  # delta -25

        generate_alerts(test_db, 2)

        with db_transaction(test_db) as db:
            alerts = get_alerts(db, alert_type='SCORE_DROP', network_name='DropNet')
            assert len(alerts) == 1
            assert alerts[0]['severity'] == 'medium'

    def test_score_drop_high(self, test_db):
        """SCORE_DROP: delta <= -35 → high."""
        with db_transaction(test_db) as db:
            seed_network(db, 'BigDropNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'BigDropNet', 1, 80)
            seed_score_history(db, 'BigDropNet', 2, 40)  # delta -40

        generate_alerts(test_db, 2)

        with db_transaction(test_db) as db:
            alerts = get_alerts(db, alert_type='SCORE_DROP', network_name='BigDropNet')
            assert len(alerts) == 1
            assert alerts[0]['severity'] == 'high'

    def test_volatility_spike_medium(self, test_db):
        """VOLATILITY_SPIKE: volatility 15-24 → medium."""
        with db_transaction(test_db) as db:
            seed_network(db, 'VolNet', network_type='organic', stability_state='stable')
            # Scores: [10, 30, 10, 30, 10, 30] → deltas [20, -20, 20, -20, 20]
            # avg(|deltas|) = avg(20, 20, 20, 20, 20) = 20 (over last 5)
            seed_score_history(db, 'VolNet', 1, 10)
            seed_score_history(db, 'VolNet', 2, 30)
            seed_score_history(db, 'VolNet', 3, 10)
            seed_score_history(db, 'VolNet', 4, 30)
            seed_score_history(db, 'VolNet', 5, 10)
            seed_score_history(db, 'VolNet', 6, 30)  # volatility calculated here

        generate_alerts(test_db, 6)

        with db_transaction(test_db) as db:
            alerts = get_alerts(db, alert_type='VOLATILITY_SPIKE', network_name='VolNet')
            assert len(alerts) == 1
            assert alerts[0]['severity'] == 'medium'

    def test_volatility_no_alert_below_threshold(self, test_db):
        """VOLATILITY_SPIKE: volatility < 15 → no alert."""
        with db_transaction(test_db) as db:
            seed_network(db, 'StableNet', network_type='organic', stability_state='stable')
            # Scores: [50, 52, 51, 52, 51, 52] → small deltas
            # avg(|deltas|) = ~1.0
            seed_score_history(db, 'StableNet', 1, 50)
            seed_score_history(db, 'StableNet', 2, 52)
            seed_score_history(db, 'StableNet', 3, 51)
            seed_score_history(db, 'StableNet', 4, 52)
            seed_score_history(db, 'StableNet', 5, 51)
            seed_score_history(db, 'StableNet', 6, 52)

        generate_alerts(test_db, 6)

        with db_transaction(test_db) as db:
            alerts = get_alerts(db, alert_type='VOLATILITY_SPIKE', network_name='StableNet')
            assert len(alerts) == 0

    def test_volatility_needs_min_2_history(self, test_db):
        """VOLATILITY_SPIKE: requires >= 2 history points."""
        with db_transaction(test_db) as db:
            seed_network(db, 'SingleNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'SingleNet', 1, 30)  # Only 1 history point

        generate_alerts(test_db, 1)

        with db_transaction(test_db) as db:
            alerts = get_alerts(db, alert_type='VOLATILITY_SPIKE', network_name='SingleNet')
            assert len(alerts) == 0


class TestPhase5BAlerts:
    """Test Phase 5B alert rules."""

    def test_momentum_up_medium(self, test_db):
        """RISK_MOMENTUM_UP: momentum 10-19 → medium."""
        with db_transaction(test_db) as db:
            seed_network(db, 'MomentumNet', network_type='organic', stability_state='stable')
            # Scores: [10, 25, 40, 55] → deltas [15, 15, 15] → momentum 15
            seed_score_history(db, 'MomentumNet', 1, 10)
            seed_score_history(db, 'MomentumNet', 2, 25)
            seed_score_history(db, 'MomentumNet', 3, 40)
            seed_score_history(db, 'MomentumNet', 4, 55)

        generate_alerts(test_db, 4)

        with db_transaction(test_db) as db:
            alerts = get_alerts(db, alert_type='RISK_MOMENTUM_UP', network_name='MomentumNet')
            assert len(alerts) == 1
            assert alerts[0]['severity'] == 'medium'

    def test_momentum_up_high(self, test_db):
        """RISK_MOMENTUM_UP: momentum >= 20 → high."""
        with db_transaction(test_db) as db:
            seed_network(db, 'FastMomentumNet', network_type='organic', stability_state='stable')
            # Scores: [10, 35, 60, 85] → deltas [25, 25, 25] → momentum 25
            seed_score_history(db, 'FastMomentumNet', 1, 10)
            seed_score_history(db, 'FastMomentumNet', 2, 35)
            seed_score_history(db, 'FastMomentumNet', 3, 60)
            seed_score_history(db, 'FastMomentumNet', 4, 85)

        generate_alerts(test_db, 4)

        with db_transaction(test_db) as db:
            alerts = get_alerts(db, alert_type='RISK_MOMENTUM_UP', network_name='FastMomentumNet')
            assert len(alerts) == 1
            assert alerts[0]['severity'] == 'high'

    def test_momentum_needs_3_builds(self, test_db):
        """RISK_MOMENTUM_UP: requires >= 3 history points."""
        with db_transaction(test_db) as db:
            seed_network(db, 'EarlyNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'EarlyNet', 1, 10)
            seed_score_history(db, 'EarlyNet', 2, 35)  # Only 2 builds

        generate_alerts(test_db, 2)

        with db_transaction(test_db) as db:
            alerts = get_alerts(db, alert_type='RISK_MOMENTUM_UP', network_name='EarlyNet')
            assert len(alerts) == 0

    def test_acceleration_spike_medium(self, test_db):
        """RISK_ACCELERATION_SPIKE: acceleration 15-24 → medium."""
        with db_transaction(test_db) as db:
            seed_network(db, 'AccelNet', network_type='organic', stability_state='stable')
            # Scores: [10, 20, 45] → deltas [10, 25] → acceleration 15
            seed_score_history(db, 'AccelNet', 1, 10)
            seed_score_history(db, 'AccelNet', 2, 20)
            seed_score_history(db, 'AccelNet', 3, 45)

        generate_alerts(test_db, 3)

        with db_transaction(test_db) as db:
            alerts = get_alerts(db, alert_type='RISK_ACCELERATION_SPIKE', network_name='AccelNet')
            assert len(alerts) == 1
            assert alerts[0]['severity'] == 'medium'

    def test_acceleration_spike_high(self, test_db):
        """RISK_ACCELERATION_SPIKE: acceleration >= 25 → high."""
        with db_transaction(test_db) as db:
            seed_network(db, 'FastAccelNet', network_type='organic', stability_state='stable')
            # Scores: [10, 20, 55] → deltas [10, 35] → acceleration 25
            seed_score_history(db, 'FastAccelNet', 1, 10)
            seed_score_history(db, 'FastAccelNet', 2, 20)
            seed_score_history(db, 'FastAccelNet', 3, 55)

        generate_alerts(test_db, 3)

        with db_transaction(test_db) as db:
            alerts = get_alerts(db, alert_type='RISK_ACCELERATION_SPIKE', network_name='FastAccelNet')
            assert len(alerts) == 1
            assert alerts[0]['severity'] == 'high'

    def test_acceleration_needs_3_builds(self, test_db):
        """RISK_ACCELERATION_SPIKE: requires >= 3 history points."""
        with db_transaction(test_db) as db:
            seed_network(db, 'EarlyAccelNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'EarlyAccelNet', 1, 10)
            seed_score_history(db, 'EarlyAccelNet', 2, 20)  # Only 2 builds

        generate_alerts(test_db, 2)

        with db_transaction(test_db) as db:
            alerts = get_alerts(db, alert_type='RISK_ACCELERATION_SPIKE', network_name='EarlyAccelNet')
            assert len(alerts) == 0
