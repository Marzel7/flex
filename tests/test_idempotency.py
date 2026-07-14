"""
Phase 6A: Test idempotency of alert generation.

Ensures:
- Running alert generation twice produces same counts (no duplicates)
- UNIQUE constraints prevent duplicate inserts
- INSERT OR IGNORE pattern works correctly
"""

import pytest
import sys
from pathlib import Path

# Add parent directory and tests directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from test_utils import (
    db_transaction, seed_network, seed_network_evidence,
    seed_score_history, get_alert_count, get_alerts
)
from test_alerts_phases import generate_alerts
# test_db fixture is provided by conftest.py (auto-discovered by pytest)


class TestIdempotencyAlerts:
    """Test that alert generation is idempotent."""

    def test_score_spike_no_duplicates(self, test_db):
        """SCORE_SPIKE: running alert generation twice → same count."""
        with db_transaction(test_db) as db:
            seed_network(db, 'DupNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'DupNet', 1, 10)
            seed_score_history(db, 'DupNet', 2, 40)  # delta +30

        # First run
        generate_alerts(test_db, 2)
        with db_transaction(test_db) as db:
            count1 = get_alert_count(db, 'SCORE_SPIKE')

        # Second run
        generate_alerts(test_db, 2)
        with db_transaction(test_db) as db:
            count2 = get_alert_count(db, 'SCORE_SPIKE')

        assert count1 == count2 == 1

    def test_score_drop_no_duplicates(self, test_db):
        """SCORE_DROP: running alert generation twice → same count."""
        with db_transaction(test_db) as db:
            seed_network(db, 'DupDropNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'DupDropNet', 1, 80)
            seed_score_history(db, 'DupDropNet', 2, 55)  # delta -25

        # First run
        generate_alerts(test_db, 2)
        with db_transaction(test_db) as db:
            count1 = get_alert_count(db, 'SCORE_DROP')

        # Second run
        generate_alerts(test_db, 2)
        with db_transaction(test_db) as db:
            count2 = get_alert_count(db, 'SCORE_DROP')

        assert count1 == count2 == 1

    def test_volatility_spike_no_duplicates(self, test_db):
        """VOLATILITY_SPIKE: running alert generation twice → same count."""
        with db_transaction(test_db) as db:
            seed_network(db, 'DupVolNet', network_type='organic', stability_state='stable')
            # High volatility pattern
            seed_score_history(db, 'DupVolNet', 1, 10)
            seed_score_history(db, 'DupVolNet', 2, 30)
            seed_score_history(db, 'DupVolNet', 3, 10)
            seed_score_history(db, 'DupVolNet', 4, 30)
            seed_score_history(db, 'DupVolNet', 5, 10)
            seed_score_history(db, 'DupVolNet', 6, 30)

        # First run
        generate_alerts(test_db, 6)
        with db_transaction(test_db) as db:
            count1 = get_alert_count(db, 'VOLATILITY_SPIKE')

        # Second run
        generate_alerts(test_db, 6)
        with db_transaction(test_db) as db:
            count2 = get_alert_count(db, 'VOLATILITY_SPIKE')

        assert count1 == count2 == 1

    def test_momentum_no_duplicates(self, test_db):
        """RISK_MOMENTUM_UP: running alert generation twice → same count."""
        with db_transaction(test_db) as db:
            seed_network(db, 'DupMomNet', network_type='organic', stability_state='stable')
            # Consistent upward momentum
            seed_score_history(db, 'DupMomNet', 1, 10)
            seed_score_history(db, 'DupMomNet', 2, 25)
            seed_score_history(db, 'DupMomNet', 3, 40)
            seed_score_history(db, 'DupMomNet', 4, 55)

        # First run
        generate_alerts(test_db, 4)
        with db_transaction(test_db) as db:
            count1 = get_alert_count(db, 'RISK_MOMENTUM_UP')

        # Second run
        generate_alerts(test_db, 4)
        with db_transaction(test_db) as db:
            count2 = get_alert_count(db, 'RISK_MOMENTUM_UP')

        assert count1 == count2 == 1

    def test_acceleration_no_duplicates(self, test_db):
        """RISK_ACCELERATION_SPIKE: running alert generation twice → same count."""
        with db_transaction(test_db) as db:
            seed_network(db, 'DupAccelNet', network_type='organic', stability_state='stable')
            # Acceleration pattern
            seed_score_history(db, 'DupAccelNet', 1, 10)
            seed_score_history(db, 'DupAccelNet', 2, 20)
            seed_score_history(db, 'DupAccelNet', 3, 45)

        # First run
        generate_alerts(test_db, 3)
        with db_transaction(test_db) as db:
            count1 = get_alert_count(db, 'RISK_ACCELERATION_SPIKE')

        # Second run
        generate_alerts(test_db, 3)
        with db_transaction(test_db) as db:
            count2 = get_alert_count(db, 'RISK_ACCELERATION_SPIKE')

        assert count1 == count2 == 1

    def test_multiple_networks_no_duplicates(self, test_db):
        """Multiple networks: running alert generation twice → same total count."""
        with db_transaction(test_db) as db:
            # Network 1: spike
            seed_network(db, 'MultiNet1', network_type='organic', stability_state='stable')
            seed_score_history(db, 'MultiNet1', 1, 10)
            seed_score_history(db, 'MultiNet1', 2, 40)

            # Network 2: drop
            seed_network(db, 'MultiNet2', network_type='organic', stability_state='stable')
            seed_score_history(db, 'MultiNet2', 1, 80)
            seed_score_history(db, 'MultiNet2', 2, 55)

        # First run
        generate_alerts(test_db, 2)
        with db_transaction(test_db) as db:
            count1 = get_alert_count(db)

        # Second run
        generate_alerts(test_db, 2)
        with db_transaction(test_db) as db:
            count2 = get_alert_count(db)

        assert count1 == count2  # Idempotent: same count on both runs
        assert count1 >= 2  # At least SPIKE and DROP alerts

    def test_unique_constraint_enforced(self, test_db):
        """UNIQUE(network_name, build_version, alert_type) prevents duplicates."""
        with db_transaction(test_db) as db:
            seed_network(db, 'UniqueNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'UniqueNet', 1, 10)
            seed_score_history(db, 'UniqueNet', 2, 40)

        generate_alerts(test_db, 2)

        with db_transaction(test_db) as db:
            # Try to insert duplicate manually
            db.execute('''
                INSERT OR IGNORE INTO network_alerts
                (network_name, build_version, alert_type, severity, message, details_json)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', ('UniqueNet', 2, 'SCORE_SPIKE', 'medium', 'duplicate', '{}'))

            # Check that count didn't increase
            result = db.execute(
                'SELECT COUNT(*) as cnt FROM network_alerts WHERE network_name = ? AND build_version = ? AND alert_type = ?',
                ('UniqueNet', 2, 'SCORE_SPIKE')
            ).fetchone()
            assert result['cnt'] == 1  # Still only 1, insert was ignored


class TestIdempotencyScoreHistory:
    """Test that score history insertion is idempotent."""

    def test_score_history_primary_key_prevents_duplicates(self, test_db):
        """PRIMARY KEY(network_name, build_version) prevents duplicate history entries."""
        with db_transaction(test_db) as db:
            seed_network(db, 'HistNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'HistNet', 1, 50)

            # Try to insert duplicate
            db.execute('''
                INSERT OR IGNORE INTO network_score_history
                (network_name, build_version, score, score_version, components_json)
                VALUES (?, ?, ?, ?, ?)
            ''', ('HistNet', 1, 75, 2, '{"model": "v2"}'))

            # Check count
            result = db.execute(
                'SELECT COUNT(*) as cnt FROM network_score_history WHERE network_name = ? AND build_version = ?',
                ('HistNet', 1)
            ).fetchone()
            assert result['cnt'] == 1

    def test_multiple_builds_allowed(self, test_db):
        """Same network, different build_version should allow multiple entries."""
        with db_transaction(test_db) as db:
            seed_network(db, 'MultiBuildNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'MultiBuildNet', 1, 50)
            seed_score_history(db, 'MultiBuildNet', 2, 60)
            seed_score_history(db, 'MultiBuildNet', 3, 70)

            result = db.execute(
                'SELECT COUNT(*) as cnt FROM network_score_history WHERE network_name = ?',
                ('MultiBuildNet',)
            ).fetchone()
            assert result['cnt'] == 3


class TestRebuildSafety:
    """Test that rebuilding produces consistent results."""

    def test_rebuild_same_data_same_results(self, test_db):
        """Rebuilding with same data → same alert counts and types."""
        with db_transaction(test_db) as db:
            # Setup: Create networks with various trigger patterns
            seed_network(db, 'RebuildNet1', network_type='cex_and_infra_connected', stability_state='growing')
            seed_score_history(db, 'RebuildNet1', 1, 10)
            seed_score_history(db, 'RebuildNet1', 2, 40)
            seed_score_history(db, 'RebuildNet1', 3, 60)
            seed_score_history(db, 'RebuildNet1', 4, 85)

        # First build
        generate_alerts(test_db, 4)
        with db_transaction(test_db) as db:
            alerts1 = db.execute('SELECT alert_type, severity FROM network_alerts ORDER BY alert_type').fetchall()

        # Second build (rebuild)
        generate_alerts(test_db, 4)
        with db_transaction(test_db) as db:
            alerts2 = db.execute('SELECT alert_type, severity FROM network_alerts ORDER BY alert_type').fetchall()

        # Results should be identical
        assert len(alerts1) == len(alerts2)
        for a1, a2 in zip(alerts1, alerts2):
            assert a1['alert_type'] == a2['alert_type']
            assert a1['severity'] == a2['severity']

    def test_incremental_builds_preserve_history(self, test_db):
        """Adding new build version doesn't affect previous alerts."""
        with db_transaction(test_db) as db:
            seed_network(db, 'IncrNet', network_type='organic', stability_state='stable')
            seed_score_history(db, 'IncrNet', 1, 10)
            seed_score_history(db, 'IncrNet', 2, 40)

        # Build 2
        generate_alerts(test_db, 2)
        with db_transaction(test_db) as db:
            count_after_build2 = get_alert_count(db)
            assert count_after_build2 >= 1

        # Add build 3
        with db_transaction(test_db) as db:
            seed_score_history(db, 'IncrNet', 3, 50)

        # Rebuild (includes new build 3)
        generate_alerts(test_db, 3)
        with db_transaction(test_db) as db:
            count_after_build3 = get_alert_count(db)

        # Build 2 alerts should still exist, plus new ones from build 3
        # (At minimum, we should have alerts from build 2)
        assert count_after_build3 >= count_after_build2
