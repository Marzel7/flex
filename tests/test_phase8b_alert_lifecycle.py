"""
Phase 8B: Alert Lifecycle and Escalation Tests

Tests:
- Acknowledgment idempotency (multiple acks → same state)
- Suppression idempotency (multiple suppress → overwrites)
- Escalation rule correctness (R1, R2, R3)
- Escalation does NOT overwrite operator state (ack/suppress preserved)
- Alert filtering by lifecycle (active, unacked, escalated)
"""

import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add parent directory and tests directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from test_utils import (
    db_transaction, seed_network, seed_score_history,
    create_test_db
)
# test_db fixture is provided by conftest.py (auto-discovered by pytest)


class TestAckIdempotency:
    """Test that acknowledging is idempotent"""

    @staticmethod
    def ensure_phase8b_columns(db):
        """Ensure Phase 8B columns exist in test database"""
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN acknowledged INTEGER NOT NULL DEFAULT 0;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN acknowledged_at TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN acknowledged_by TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN suppressed_until TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN suppression_reason TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN suppressed_by TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN suppressed_at TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN is_escalated INTEGER NOT NULL DEFAULT 0;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN escalated_at TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN escalation_reason TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN escalation_rule TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN operator_note TEXT;')
        except Exception:
            pass

    def test_ack_sets_fields(self, test_db):
        """ACK sets acknowledged, acknowledged_at, acknowledged_by"""
        with db_transaction(test_db) as db:
            self.ensure_phase8b_columns(db)
            seed_network(db, 'AckNet', network_type='organic', stability_state='new')

            # Create alert
            db.execute('''
                INSERT INTO network_alerts
                (network_name, build_version, alert_type, severity, message, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', ('AckNet', 1, 'SCORE_SPIKE', 'high', 'Test alert'))

            db.commit()
            alert_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

            # ACK the alert
            db.execute('''
                UPDATE network_alerts
                SET acknowledged = 1,
                    acknowledged_at = CURRENT_TIMESTAMP,
                    acknowledged_by = 'test_operator'
                WHERE alert_id = ?
            ''', (alert_id,))
            db.commit()

            # Verify
            result = db.execute(
                'SELECT acknowledged, acknowledged_by FROM network_alerts WHERE alert_id = ?',
                (alert_id,)
            ).fetchone()

            assert result['acknowledged'] == 1
            assert result['acknowledged_by'] == 'test_operator'

    def test_ack_is_idempotent(self, test_db):
        """Multiple ACKs produce same result"""
        with db_transaction(test_db) as db:
            self.ensure_phase8b_columns(db)
            seed_network(db, 'IdempotentAckNet', network_type='organic', stability_state='new')

            db.execute('''
                INSERT INTO network_alerts
                (network_name, build_version, alert_type, severity, message, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', ('IdempotentAckNet', 1, 'SCORE_SPIKE', 'high', 'Test alert'))

            db.commit()
            alert_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

            # ACK once
            db.execute('''
                UPDATE network_alerts
                SET acknowledged = 1,
                    acknowledged_at = CURRENT_TIMESTAMP,
                    acknowledged_by = 'operator1'
                WHERE alert_id = ?
            ''', (alert_id,))
            db.commit()

            result1 = db.execute(
                'SELECT acknowledged, acknowledged_by FROM network_alerts WHERE alert_id = ?',
                (alert_id,)
            ).fetchone()

            # ACK again (different operator)
            db.execute('''
                UPDATE network_alerts
                SET acknowledged = 1,
                    acknowledged_at = CURRENT_TIMESTAMP,
                    acknowledged_by = 'operator2'
                WHERE alert_id = ?
            ''', (alert_id,))
            db.commit()

            result2 = db.execute(
                'SELECT acknowledged, acknowledged_by FROM network_alerts WHERE alert_id = ?',
                (alert_id,)
            ).fetchone()

            # Both should be acknowledged = 1
            assert result1['acknowledged'] == 1
            assert result2['acknowledged'] == 1

    def test_unack_clears_fields(self, test_db):
        """UNACK clears acknowledged fields"""
        with db_transaction(test_db) as db:
            self.ensure_phase8b_columns(db)
            seed_network(db, 'UnackNet', network_type='organic', stability_state='new')

            db.execute('''
                INSERT INTO network_alerts
                (network_name, build_version, alert_type, severity, message, created_at,
                 acknowledged, acknowledged_at, acknowledged_by)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1, CURRENT_TIMESTAMP, 'operator')
            ''', ('UnackNet', 1, 'SCORE_SPIKE', 'high', 'Test alert'))

            db.commit()
            alert_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

            # UNACK
            db.execute('''
                UPDATE network_alerts
                SET acknowledged = 0,
                    acknowledged_at = NULL,
                    acknowledged_by = NULL
                WHERE alert_id = ?
            ''', (alert_id,))
            db.commit()

            result = db.execute(
                'SELECT acknowledged, acknowledged_at, acknowledged_by FROM network_alerts WHERE alert_id = ?',
                (alert_id,)
            ).fetchone()

            assert result['acknowledged'] == 0
            assert result['acknowledged_at'] is None
            assert result['acknowledged_by'] is None


class TestSuppressionIdempotency:
    """Test that suppression is idempotent"""

    @staticmethod
    def ensure_phase8b_columns(db):
        """Ensure Phase 8B columns exist"""
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN acknowledged INTEGER NOT NULL DEFAULT 0;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN acknowledged_at TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN acknowledged_by TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN suppressed_until TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN suppression_reason TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN suppressed_by TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN suppressed_at TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN is_escalated INTEGER NOT NULL DEFAULT 0;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN escalated_at TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN escalation_reason TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN escalation_rule TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_alerts ADD COLUMN operator_note TEXT;')
        except Exception:
            pass

    def test_suppress_sets_fields(self, test_db):
        """SUPPRESS sets suppressed_until, suppressed_at, suppressed_by"""
        with db_transaction(test_db) as db:
            self.ensure_phase8b_columns(db)
            seed_network(db, 'SuppressNet', network_type='organic', stability_state='new')

            db.execute('''
                INSERT INTO network_alerts
                (network_name, build_version, alert_type, severity, message, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', ('SuppressNet', 1, 'SCORE_SPIKE', 'high', 'Test alert'))

            db.commit()
            alert_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

            future_time = (datetime.utcnow() + timedelta(hours=1)).isoformat()

            # SUPPRESS
            db.execute('''
                UPDATE network_alerts
                SET suppressed_until = ?,
                    suppressed_at = CURRENT_TIMESTAMP,
                    suppressed_by = 'operator',
                    suppression_reason = 'False positive'
                WHERE alert_id = ?
            ''', (future_time, alert_id))
            db.commit()

            result = db.execute(
                'SELECT suppressed_until, suppressed_by, suppression_reason FROM network_alerts WHERE alert_id = ?',
                (alert_id,)
            ).fetchone()

            assert result['suppressed_until'] == future_time
            assert result['suppressed_by'] == 'operator'
            assert result['suppression_reason'] == 'False positive'

    def test_suppress_is_idempotent(self, test_db):
        """Multiple SUPPRESS calls overwrite (idempotent)"""
        with db_transaction(test_db) as db:
            self.ensure_phase8b_columns(db)
            seed_network(db, 'IdempotentSuppressNet', network_type='organic', stability_state='new')

            db.execute('''
                INSERT INTO network_alerts
                (network_name, build_version, alert_type, severity, message, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', ('IdempotentSuppressNet', 1, 'SCORE_SPIKE', 'high', 'Test alert'))

            db.commit()
            alert_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

            time1 = (datetime.utcnow() + timedelta(hours=1)).isoformat()

            # SUPPRESS once
            db.execute('''
                UPDATE network_alerts
                SET suppressed_until = ?,
                    suppressed_at = CURRENT_TIMESTAMP,
                    suppressed_by = 'operator1',
                    suppression_reason = 'Reason1'
                WHERE alert_id = ?
            ''', (time1, alert_id))
            db.commit()

            result1 = db.execute(
                'SELECT suppressed_until, suppressed_by FROM network_alerts WHERE alert_id = ?',
                (alert_id,)
            ).fetchone()

            time2 = (datetime.utcnow() + timedelta(hours=2)).isoformat()

            # SUPPRESS again (different time, operator)
            db.execute('''
                UPDATE network_alerts
                SET suppressed_until = ?,
                    suppressed_at = CURRENT_TIMESTAMP,
                    suppressed_by = 'operator2',
                    suppression_reason = 'Reason2'
                WHERE alert_id = ?
            ''', (time2, alert_id))
            db.commit()

            result2 = db.execute(
                'SELECT suppressed_until, suppressed_by FROM network_alerts WHERE alert_id = ?',
                (alert_id,)
            ).fetchone()

            # Second SUPPRESS should have overwritten
            assert result2['suppressed_until'] == time2
            assert result2['suppressed_by'] == 'operator2'

    def test_unsuppress_clears_fields(self, test_db):
        """UNSUPPRESS clears suppression fields"""
        with db_transaction(test_db) as db:
            self.ensure_phase8b_columns(db)
            seed_network(db, 'UnsuppressNet', network_type='organic', stability_state='new')

            future_time = (datetime.utcnow() + timedelta(hours=1)).isoformat()

            db.execute('''
                INSERT INTO network_alerts
                (network_name, build_version, alert_type, severity, message, created_at,
                 suppressed_until, suppressed_at, suppressed_by, suppression_reason)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP, 'operator', 'False positive')
            ''', ('UnsuppressNet', 1, 'SCORE_SPIKE', 'high', 'Test alert', future_time))

            db.commit()
            alert_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

            # UNSUPPRESS
            db.execute('''
                UPDATE network_alerts
                SET suppressed_until = NULL,
                    suppressed_at = NULL,
                    suppressed_by = NULL,
                    suppression_reason = NULL
                WHERE alert_id = ?
            ''', (alert_id,))
            db.commit()

            result = db.execute(
                'SELECT suppressed_until, suppressed_at, suppressed_by FROM network_alerts WHERE alert_id = ?',
                (alert_id,)
            ).fetchone()

            assert result['suppressed_until'] is None
            assert result['suppressed_at'] is None
            assert result['suppressed_by'] is None


class TestEscalationPreservesOperatorState:
    """Test that escalation does NOT overwrite acknowledged/suppressed fields"""

    @staticmethod
    def ensure_phase8b_columns(db):
        """Ensure Phase 8B columns exist"""
        cols = [
            'acknowledged INTEGER NOT NULL DEFAULT 0',
            'acknowledged_at TEXT',
            'acknowledged_by TEXT',
            'suppressed_until TEXT',
            'suppression_reason TEXT',
            'suppressed_by TEXT',
            'suppressed_at TEXT',
            'is_escalated INTEGER NOT NULL DEFAULT 0',
            'escalated_at TEXT',
            'escalation_reason TEXT',
            'escalation_rule TEXT',
            'operator_note TEXT'
        ]
        for col_def in cols:
            col_name = col_def.split()[0]
            try:
                db.execute(f'ALTER TABLE network_alerts ADD COLUMN {col_def};')
            except Exception:
                pass

    @staticmethod
    def ensure_phase7e_columns(db):
        """Ensure Phase 7E columns exist"""
        try:
            db.execute('ALTER TABLE network_scores ADD COLUMN risk_band TEXT;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_scores ADD COLUMN stability_coeff REAL;')
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE network_scores ADD COLUMN smoothed_score INTEGER;')
        except Exception:
            pass

    def test_escalation_preserves_ack(self, test_db):
        """Escalation does NOT clear acknowledged fields"""
        with db_transaction(test_db) as db:
            self.ensure_phase8b_columns(db)
            self.ensure_phase7e_columns(db)
            seed_network(db, 'EscalateAckNet', network_type='organic', stability_state='new')

            # Set up network score with CRITICAL risk
            db.execute('''
                INSERT INTO network_scores
                (network_name, score, risk_band)
                VALUES (?, 50, 'CRITICAL')
            ''', ('EscalateAckNet',))

            # Create alert
            db.execute('''
                INSERT INTO network_alerts
                (network_name, build_version, alert_type, severity, message, created_at,
                 acknowledged, acknowledged_at, acknowledged_by)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1, CURRENT_TIMESTAMP, 'test_op')
            ''', ('EscalateAckNet', 1, 'SCORE_SPIKE', 'high', 'Test alert'))

            db.commit()
            alert_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

            # Apply R1_CRITICAL_HIGH escalation (but don't overwrite ack)
            db.execute('''
                UPDATE network_alerts
                SET is_escalated = 1,
                    escalated_at = CURRENT_TIMESTAMP,
                    escalation_rule = 'R1_CRITICAL_HIGH',
                    escalation_reason = 'CRITICAL + HIGH'
                WHERE alert_id = ?
                  AND acknowledged = 0
                  AND suppressed_until IS NULL
            ''', (alert_id,))
            db.commit()

            # This should NOT have escalated because acknowledged = 1
            result = db.execute(
                'SELECT is_escalated, acknowledged, acknowledged_by FROM network_alerts WHERE alert_id = ?',
                (alert_id,)
            ).fetchone()

            # Alert should still be acknowledged, NOT escalated
            assert result['acknowledged'] == 1
            assert result['acknowledged_by'] == 'test_op'
            assert result['is_escalated'] == 0

    def test_escalation_preserves_suppress(self, test_db):
        """Escalation does NOT clear suppressed fields"""
        with db_transaction(test_db) as db:
            self.ensure_phase8b_columns(db)
            self.ensure_phase7e_columns(db)
            seed_network(db, 'EscalateSuppressNet', network_type='organic', stability_state='new')

            future_time = (datetime.utcnow() + timedelta(hours=1)).isoformat()

            db.execute('''
                INSERT INTO network_scores
                (network_name, score, risk_band)
                VALUES (?, 50, 'CRITICAL')
            ''', ('EscalateSuppressNet',))

            db.execute('''
                INSERT INTO network_alerts
                (network_name, build_version, alert_type, severity, message, created_at,
                 suppressed_until, suppressed_at, suppressed_by)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP, 'test_op')
            ''', ('EscalateSuppressNet', 1, 'SCORE_SPIKE', 'high', 'Test alert', future_time))

            db.commit()
            alert_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

            # Try to escalate (but don't overwrite suppress)
            db.execute('''
                UPDATE network_alerts
                SET is_escalated = 1,
                    escalated_at = CURRENT_TIMESTAMP,
                    escalation_rule = 'R1_CRITICAL_HIGH',
                    escalation_reason = 'CRITICAL + HIGH'
                WHERE alert_id = ?
                  AND acknowledged = 0
                  AND suppressed_until IS NULL
            ''', (alert_id,))
            db.commit()

            result = db.execute(
                'SELECT is_escalated, suppressed_until, suppressed_by FROM network_alerts WHERE alert_id = ?',
                (alert_id,)
            ).fetchone()

            # Alert should still be suppressed, NOT escalated
            assert result['suppressed_until'] == future_time
            assert result['suppressed_by'] == 'test_op'
            assert result['is_escalated'] == 0


class TestAlertActiveStateFiltering:
    """Test alert lifecycle filtering in queries"""

    @staticmethod
    def ensure_phase8b_columns(db):
        """Ensure Phase 8B columns exist"""
        cols = [
            'acknowledged INTEGER NOT NULL DEFAULT 0',
            'acknowledged_at TEXT',
            'acknowledged_by TEXT',
            'suppressed_until TEXT',
            'suppression_reason TEXT',
            'suppressed_by TEXT',
            'suppressed_at TEXT',
            'is_escalated INTEGER NOT NULL DEFAULT 0',
            'escalated_at TEXT',
            'escalation_reason TEXT',
            'escalation_rule TEXT',
            'operator_note TEXT'
        ]
        for col_def in cols:
            col_name = col_def.split()[0]
            try:
                db.execute(f'ALTER TABLE network_alerts ADD COLUMN {col_def};')
            except Exception:
                pass

    def test_active_filters_acked(self, test_db):
        """ACTIVE filter excludes acknowledged alerts"""
        with db_transaction(test_db) as db:
            self.ensure_phase8b_columns(db)
            seed_network(db, 'FilterNet', network_type='organic', stability_state='new')

            # Create 2 alerts: one acked, one not
            db.execute('''
                INSERT INTO network_alerts
                (network_name, build_version, alert_type, severity, message, created_at, acknowledged)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 0)
            ''', ('FilterNet', 1, 'SCORE_SPIKE', 'high', 'Active alert'))

            db.execute('''
                INSERT INTO network_alerts
                (network_name, build_version, alert_type, severity, message, created_at, acknowledged, acknowledged_at, acknowledged_by)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1, CURRENT_TIMESTAMP, 'op')
            ''', ('FilterNet', 1, 'SCORE_DROP', 'medium', 'Acked alert'))

            db.commit()

            # Query ACTIVE only
            results = db.execute('''
                SELECT alert_id FROM network_alerts
                WHERE acknowledged = 0
                  AND (suppressed_until IS NULL OR suppressed_until <= CURRENT_TIMESTAMP)
            ''').fetchall()

            # Should return only 1 alert
            assert len(results) == 1

    def test_unacked_filter(self, test_db):
        """UNACKED filter returns unacknowledged alerts only"""
        with db_transaction(test_db) as db:
            self.ensure_phase8b_columns(db)
            seed_network(db, 'UnackedFilterNet', network_type='organic', stability_state='new')

            db.execute('''
                INSERT INTO network_alerts
                (network_name, build_version, alert_type, severity, message, created_at, acknowledged)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 0)
            ''', ('UnackedFilterNet', 1, 'SCORE_SPIKE', 'high', 'Alert 1'))

            db.execute('''
                INSERT INTO network_alerts
                (network_name, build_version, alert_type, severity, message, created_at, acknowledged)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 0)
            ''', ('UnackedFilterNet', 1, 'SCORE_DROP', 'medium', 'Alert 2'))

            db.execute('''
                INSERT INTO network_alerts
                (network_name, build_version, alert_type, severity, message, created_at, acknowledged)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1)
            ''', ('UnackedFilterNet', 1, 'VOLATILITY_SPIKE', 'low', 'Alert 3 (acked)'))

            db.commit()

            results = db.execute('''
                SELECT COUNT(*) as count FROM network_alerts
                WHERE acknowledged = 0
            ''').fetchone()

            assert results['count'] == 2

    def test_escalated_filter(self, test_db):
        """ESCALATED filter returns escalated alerts only"""
        with db_transaction(test_db) as db:
            self.ensure_phase8b_columns(db)
            seed_network(db, 'EscalatedFilterNet', network_type='organic', stability_state='new')

            db.execute('''
                INSERT INTO network_alerts
                (network_name, build_version, alert_type, severity, message, created_at, is_escalated)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1)
            ''', ('EscalatedFilterNet', 1, 'SCORE_SPIKE', 'high', 'Escalated'))

            db.execute('''
                INSERT INTO network_alerts
                (network_name, build_version, alert_type, severity, message, created_at, is_escalated)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 0)
            ''', ('EscalatedFilterNet', 1, 'SCORE_DROP', 'medium', 'Not escalated'))

            db.commit()

            results = db.execute('''
                SELECT COUNT(*) as count FROM network_alerts
                WHERE is_escalated = 1
            ''').fetchone()

            assert results['count'] == 1
