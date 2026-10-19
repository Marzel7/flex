"""
FLEX Enhanced Launch Score Model

Extends launch prediction with seed concentration signal.

Previous 7-signal model (implied from Phase 4):
- recent_funding_activity (22%)
- cluster_activity (18%)
- creator_reuse (14%)
- operator_activity (14%)
- dev_reputation (10%)
- organization_momentum (10%)
- cadence_score (7%)
[Total: 95%, leaving 5% for seed_concentration]

Enhanced 8-signal model:
- recent_funding_activity (0.22)
- cluster_activity (0.18)
- creator_reuse (0.14)
- operator_activity (0.14)
- dev_reputation (0.10)
- organization_momentum (0.10)
- cadence_score (0.07)
- seed_concentration (0.05)  ← NEW
---
Total weight: 1.00

Seed concentration indicates:
- High: Creators funded equally (coordinated)
- Low: Variable funding (ad-hoc)

Score 0-100, where:
- 80+: Imminent launch (high-confidence)
- 60-79: Preparation phase
- 40-59: Early signals
- <40: Standard activity
"""

import sqlite3
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class EnhancedLaunchScoreCalculator:
    """
    Computes enhanced launch score with seed concentration integration.

    Weights:
    - recent_funding_activity: 22%
    - cluster_activity: 18%
    - creator_reuse: 14%
    - operator_activity: 14%
    - dev_reputation: 10%
    - organization_momentum: 10%
    - cadence_score: 7%
    - seed_concentration: 5% ← NEW SIGNAL
    """

    WEIGHTS = {
        'recent_funding': 0.22,
        'cluster_activity': 0.18,
        'creator_reuse': 0.14,
        'operator_activity': 0.14,
        'dev_reputation': 0.10,
        'organization_momentum': 0.10,
        'cadence_score': 0.07,
        'seed_concentration': 0.05  # NEW: Coordinated seed funding signal
    }

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_recent_funding_signal(self, org_id: int, cursor: sqlite3.Cursor) -> float:
        """
        Get recent funding activity signal (0-100).

        Measures: SOL volume in last 24-72h, normalized.
        """
        cursor.execute("""
            SELECT
                SUM(amount_sol) as total_volume
            FROM funder_outgoing_transfers
            WHERE recipient IN (
                SELECT creator_wallet FROM dev_organization_members
                WHERE organization_id = ?
            )
            AND tx_timestamp > (? - 259200)  -- Last 72 hours
        """, (org_id, __import__('time').time()))

        result = cursor.fetchone()
        volume = result['total_volume'] or 0 if result else 0

        # Normalize: 100 SOL = signal of 100
        signal = min(100, volume)
        return signal

    def _get_cluster_activity_signal(self, org_id: int, cursor: sqlite3.Cursor) -> float:
        """
        Get cluster activity signal (0-100).

        Measures: Multi-wallet coordination in last 72h.
        """
        cursor.execute("""
            SELECT
                COUNT(DISTINCT source) as unique_senders,
                COUNT(DISTINCT recipient) as unique_recipients,
                COUNT(*) as transfer_count
            FROM funder_outgoing_transfers
            WHERE source IN (
                SELECT org_wallet FROM dev_organization_members
                WHERE organization_id = ?
            )
            AND tx_timestamp > (? - 259200)  -- Last 72 hours
        """, (org_id, __import__('time').time()))

        result = cursor.fetchone()
        if not result:
            return 0

        unique_senders = result['unique_senders'] or 0
        unique_recipients = result['unique_recipients'] or 0
        transfer_count = result['transfer_count'] or 0

        # Signal: diversity of coordination
        coordination = (min(unique_senders / 5, 1.0) * 0.4 +
                       min(unique_recipients / 10, 1.0) * 0.3 +
                       min(transfer_count / 50, 1.0) * 0.3) * 100

        return min(100, coordination)

    def _get_creator_reuse_signal(self, org_id: int, cursor: sqlite3.Cursor) -> float:
        """
        Get creator reuse signal (0-100).

        Measures: How many creators were funded again in last 24h.
        """
        cursor.execute("""
            SELECT
                COUNT(DISTINCT c1.creator_wallet) as reused_creators
            FROM creator_funders c1
            WHERE c1.organization_id = ?
            AND c1.last_funded_timestamp > (? - 86400)  -- Last 24h
            AND EXISTS (
                SELECT 1 FROM creator_funders c2
                WHERE c2.creator_wallet = c1.creator_wallet
                AND c2.organization_id = ?
                AND c2.last_funded_timestamp <= (? - 86400)  -- Funded before today
            )
        """, (org_id, __import__('time').time(), org_id, __import__('time').time()))

        result = cursor.fetchone()
        reused = result['reused_creators'] or 0 if result else 0

        # Normalize: 10+ reused creators = 100
        signal = min(100, reused * 10)
        return signal

    def _get_operator_activity_signal(self, org_id: int, cursor: sqlite3.Cursor) -> float:
        """
        Get operator activity spike signal (0-100).

        Measures: Lead wallet activity in last 24h vs 7d baseline.
        """
        cursor.execute("""
            SELECT
                COUNT(*) as tx_24h
            FROM funder_outgoing_transfers
            WHERE source = (
                SELECT org_wallet FROM dev_organization_members
                WHERE organization_id = ? LIMIT 1
            )
            AND tx_timestamp > (? - 86400)
        """, (org_id, __import__('time').time()))

        result_24h = cursor.fetchone()
        tx_24h = result_24h[0] if result_24h else 0

        cursor.execute("""
            SELECT
                COUNT(*) as tx_7d
            FROM funder_outgoing_transfers
            WHERE source = (
                SELECT org_wallet FROM dev_organization_members
                WHERE organization_id = ? LIMIT 1
            )
            AND tx_timestamp > (? - 604800)
        """, (org_id, __import__('time').time()))

        result_7d = cursor.fetchone()
        tx_7d = result_7d[0] if result_7d else 0

        # Spike detection: 24h vs 7d average
        if tx_7d > 0:
            avg_7d = tx_7d / 7
            spike = (tx_24h / avg_7d - 1) * 100
            signal = min(100, max(0, spike))
        else:
            signal = 0 if tx_24h == 0 else 100

        return signal

    def _get_dev_reputation_signal(self, org_id: int, cursor: sqlite3.Cursor) -> float:
        """
        Get dev reputation signal (0-100).

        Measures: Organization reputation score.
        """
        cursor.execute("""
            SELECT reputation_score
            FROM org_reputation
            WHERE organization_id = ?
        """, (org_id,))

        result = cursor.fetchone()
        if result and result['reputation_score']:
            return min(100, result['reputation_score'] * 100)

        return 50  # Neutral default

    def _get_momentum_signal(self, org_id: int, cursor: sqlite3.Cursor) -> float:
        """
        Get organization momentum signal (0-100).

        Measures: Activity acceleration (24h vs 7d baseline).
        """
        cursor.execute("""
            SELECT
                COUNT(*) as activity_24h
            FROM creator_outgoing_transfers
            WHERE creator_wallet IN (
                SELECT creator_wallet FROM dev_organization_members
                WHERE organization_id = ?
            )
            AND tx_timestamp > (? - 86400)
        """, (org_id, __import__('time').time()))

        result_24h = cursor.fetchone()
        activity_24h = result_24h[0] if result_24h else 0

        cursor.execute("""
            SELECT
                COUNT(*) as activity_7d
            FROM creator_outgoing_transfers
            WHERE creator_wallet IN (
                SELECT creator_wallet FROM dev_organization_members
                WHERE organization_id = ?
            )
            AND tx_timestamp > (? - 604800)
        """, (org_id, __import__('time').time()))

        result_7d = cursor.fetchone()
        activity_7d = result_7d[0] if result_7d else 0

        if activity_7d > 0:
            momentum = ((activity_24h / 1) - (activity_7d / 7)) / (activity_7d / 7) * 100
            signal = min(100, max(0, momentum))
        else:
            signal = 100 if activity_24h > 0 else 0

        return signal

    def _get_cadence_signal(self, org_id: int, cursor: sqlite3.Cursor) -> float:
        """
        Get launch cadence signal (0-100).

        Measures: Time since last launch vs historical interval.
        """
        cursor.execute("""
            SELECT
                MIN(launch_timestamp) as oldest_launch,
                MAX(launch_timestamp) as newest_launch,
                COUNT(*) as launch_count
            FROM token_analysis
            WHERE creator_wallet IN (
                SELECT creator_wallet FROM dev_organization_members
                WHERE organization_id = ?
            )
            AND launch_timestamp > 0
        """, (org_id,))

        result = cursor.fetchone()
        if not result or result['launch_count'] is None:
            return 25  # No history = uncertain

        launch_count = result['launch_count']
        if launch_count < 2:
            return 25

        oldest = result['oldest_launch']
        newest = result['newest_launch']
        days_between = (newest - oldest) / 86400

        # Average interval
        avg_interval = days_between / (launch_count - 1)

        # Time since last launch
        time_since_last = (__import__('time').time() - newest) / 86400

        # Cadence score
        if avg_interval > 0:
            expected_next = time_since_last / avg_interval
            # 0.5 = halfway to next expected launch (50 points)
            # 1.0 = time for next launch (100 points)
            signal = min(100, expected_next * 50)
        else:
            signal = 25

        return signal

    def _get_seed_concentration_signal(self, org_id: int, cursor: sqlite3.Cursor) -> float:
        """
        Get seed concentration signal (0-100).

        Measures: Coordinated seed funding of creators.
        - High concentration (equal amounts) = high signal
        - Multiple wallets funding = high signal
        - Recent funding = high signal
        """
        cursor.execute("""
            SELECT
                AVG(seed_concentration) as avg_concentration,
                AVG(funding_wallet_count) as avg_wallets,
                COUNT(*) as creator_count
            FROM creator_seed_metrics
            WHERE organization_id = ?
            AND seed_count > 0
        """, (org_id,))

        result = cursor.fetchone()
        if not result or result['creator_count'] is None or result['creator_count'] == 0:
            return 0

        avg_concentration = result['avg_concentration'] or 0
        avg_wallets = result['avg_wallets'] or 0
        creator_count = result['creator_count']

        # Signal combines concentration + wallet diversity + creator count
        # Concentration (0-1) weighted 60%
        # Wallet diversity (0-1) weighted 30%
        # Creator spread weighted 10%
        concentration_signal = avg_concentration * 0.6
        wallet_signal = min(1, avg_wallets / 5) * 0.3
        spread_signal = min(1, creator_count / 10) * 0.1

        total_signal = (concentration_signal + wallet_signal + spread_signal) * 100

        return min(100, total_signal)

    def compute_enhanced_launch_score(self, org_id: int, cursor: sqlite3.Cursor) -> Dict:
        """
        Compute enhanced launch score with 8 signals.

        Returns:
        {
            'launch_score': float (0-100),
            'launch_type': str (imminent|preparation|early|standard),
            'confidence': float (0-1),
            'recent_funding': float,
            'cluster_activity': float,
            'creator_reuse': float,
            'operator_activity': float,
            'dev_reputation': float,
            'organization_momentum': float,
            'cadence_score': float,
            'seed_concentration': float
        }
        """
        signals = {
            'recent_funding': self._get_recent_funding_signal(org_id, cursor),
            'cluster_activity': self._get_cluster_activity_signal(org_id, cursor),
            'creator_reuse': self._get_creator_reuse_signal(org_id, cursor),
            'operator_activity': self._get_operator_activity_signal(org_id, cursor),
            'dev_reputation': self._get_dev_reputation_signal(org_id, cursor),
            'organization_momentum': self._get_momentum_signal(org_id, cursor),
            'cadence_score': self._get_cadence_signal(org_id, cursor),
            'seed_concentration': self._get_seed_concentration_signal(org_id, cursor)
        }

        # Weighted sum
        launch_score = sum(
            signals[key] * self.WEIGHTS[key]
            for key in signals
        )

        # Normalize to 0-100
        launch_score = min(100, max(0, launch_score))

        # Classify
        if launch_score >= 80:
            launch_type = 'imminent'
        elif launch_score >= 60:
            launch_type = 'preparation'
        elif launch_score >= 40:
            launch_type = 'early'
        else:
            launch_type = 'standard'

        # Confidence: agreement across signals
        signal_values = list(signals.values())
        mean_signal = sum(signal_values) / len(signal_values)
        variance = sum((s - mean_signal) ** 2 for s in signal_values) / len(signal_values)
        stddev = variance ** 0.5
        max_stddev = 100
        confidence = max(0, 1 - (stddev / max_stddev))

        return {
            'launch_score': launch_score,
            'launch_type': launch_type,
            'confidence': confidence,
            'signals': signals,
            'weights': self.WEIGHTS
        }
