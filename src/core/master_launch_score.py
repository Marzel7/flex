"""
FLEX Master Launch Score — Unified Launch Prediction Signal

Combines 8 predictive signals into a single normalized launch alert score (0-1).

Signals aggregated:
1. launch_probability (22%)
2. launch_wave_score (18%)
3. seed_concentration (12%)
4. funder_overlap_score (12%)
5. organization_momentum (10%)
6. creator_reuse_score (8%)
7. operator_activity_score (8%)
8. reputation_adjustment (10%)

Output:
- master_launch_score: 0-1 normalized composite
- alert_level: LOW | WATCH | HIGH | CRITICAL
- component breakdown for analysis

Exit codes:
- 0: Success
- 1: Error
"""

import sqlite3
import logging
import time
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


class SignalNormalizer:
    """
    Normalizes heterogeneous signals to 0-1 scale.

    Handles:
    - 0-100 percentage scores → 0-1
    - 0-1 ratio scores → 0-1 (pass-through)
    - Raw activity metrics → normalized via percentile/threshold
    - Reputation scores → 0-1 with clipping
    """

    @staticmethod
    def normalize_percentage(value: float, max_val: float = 100.0) -> float:
        """Normalize 0-100 scale to 0-1."""
        if value is None:
            return 0.0
        return min(1.0, max(0.0, value / max_val))

    @staticmethod
    def normalize_ratio(value: float) -> float:
        """Pass-through normalization for 0-1 ratio scores."""
        if value is None:
            return 0.0
        return min(1.0, max(0.0, value))

    @staticmethod
    def normalize_momentum(value: float) -> float:
        """
        Normalize organization momentum to 0-1.
        Momentum is (activity_24h - avg_7d) / avg_7d.
        Negative = lower activity, Positive = higher activity.
        Range: -1.0 to +∞, normalized to 0-1.
        """
        if value is None or value < -1.0:
            return 0.0
        # Map: -1.0 → 0, 0.0 → 0.5, 1.0 → 0.83, 2.0 → 0.9
        normalized = 0.5 + (value / (2.0 + abs(value)))
        return min(1.0, max(0.0, normalized))


class MasterLaunchScoreCalculator:
    """
    Computes unified launch alert score from component signals.

    Aggregation formula:
    master_launch_score =
        0.22 * launch_probability +
        0.18 * launch_wave_score +
        0.12 * seed_concentration +
        0.12 * funder_overlap_score +
        0.10 * organization_momentum +
        0.08 * creator_reuse_score +
        0.08 * operator_activity_score +
        0.10 * reputation_adjustment

    Alert thresholds:
    - LOW      (0.00-0.39)
    - WATCH    (0.40-0.59)
    - HIGH     (0.60-0.74)
    - CRITICAL (0.75-1.00)
    """

    # Weights (sum = 1.0)
    WEIGHTS = {
        'launch_probability': 0.22,
        'launch_wave_score': 0.18,
        'seed_concentration': 0.12,
        'funder_overlap_score': 0.12,
        'organization_momentum': 0.10,
        'creator_reuse_score': 0.08,
        'operator_activity_score': 0.08,
        'reputation_adjustment': 0.10,
    }

    # Alert thresholds
    ALERT_THRESHOLDS = {
        'LOW': (0.00, 0.39),
        'WATCH': (0.40, 0.59),
        'HIGH': (0.60, 0.74),
        'CRITICAL': (0.75, 1.00),
    }

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.normalizer = SignalNormalizer()

    def _get_conn(self) -> sqlite3.Connection:
        """Get SQLite connection with WAL mode."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA mmap_size=30000000")
        conn.row_factory = sqlite3.Row
        return conn

    def _classify_alert_level(self, score: float) -> str:
        """Classify alert level based on score."""
        for level, (min_val, max_val) in self.ALERT_THRESHOLDS.items():
            if min_val <= score <= max_val:
                return level
        return 'LOW' if score < 0.40 else 'CRITICAL'

    def compute_organization_score(self, org_id: int, cursor: sqlite3.Cursor) -> Dict:
        """
        Compute master launch score for an organization.

        Collects all 8 component signals and combines with weights.

        Returns:
        {
            'organization_id': int,
            'launch_probability': float (0-1),
            'launch_wave_score': float (0-1),
            'seed_concentration': float (0-1),
            'funder_overlap_score': float (0-1),
            'organization_momentum': float (0-1),
            'creator_reuse_score': float (0-1),
            'operator_activity_score': float (0-1),
            'reputation_adjustment': float (0-1),
            'master_launch_score': float (0-1),
            'alert_level': str,
            'components': Dict with individual component breakdown
        }
        """
        components = {}

        # 1. Launch Probability (0-100 → 0-1)
        cursor.execute("""
            SELECT launch_probability FROM org_launch_predictions
            WHERE organization_id = ?
            ORDER BY prediction_date DESC LIMIT 1
        """, (org_id,))
        row = cursor.fetchone()
        launch_prob = self.normalizer.normalize_percentage(row['launch_probability'] if row else 0)
        components['launch_probability'] = launch_prob

        # 2. Launch Wave Score (0-100 → 0-1)
        cursor.execute("""
            SELECT wave_score FROM launch_waves
            WHERE organization_id = ?
            ORDER BY detected_at DESC LIMIT 1
        """, (org_id,))
        row = cursor.fetchone()
        wave_score = self.normalizer.normalize_percentage(row['wave_score'] if row else 0)
        components['launch_wave_score'] = wave_score

        # 3. Seed Concentration (0-1 → 0-1)
        cursor.execute("""
            SELECT AVG(seed_concentration) as avg_concentration
            FROM creator_seed_metrics
            WHERE organization_id = ?
        """, (org_id,))
        row = cursor.fetchone()
        seed_conc = self.normalizer.normalize_ratio(row['avg_concentration'] if row and row['avg_concentration'] else 0)
        components['seed_concentration'] = seed_conc

        # 4. Funder Overlap Score (0-1 → 0-1)
        cursor.execute("""
            SELECT AVG(overlap_ratio) as avg_overlap
            FROM funder_overlap
            WHERE funder_a IN (
                SELECT source FROM transfer_index
                WHERE destination IN (
                    SELECT member_address FROM dev_organization_members
                    WHERE organization_id = ? AND member_type = 'creator'
                ) LIMIT 1
            ) OR funder_b IN (
                SELECT source FROM transfer_index
                WHERE destination IN (
                    SELECT member_address FROM dev_organization_members
                    WHERE organization_id = ? AND member_type = 'creator'
                ) LIMIT 1
            )
        """, (org_id, org_id))
        row = cursor.fetchone()
        overlap_score = self.normalizer.normalize_ratio(row['avg_overlap'] if row and row['avg_overlap'] else 0)
        components['funder_overlap_score'] = overlap_score

        # 5. Organization Momentum (momentum → 0-1)
        momentum = self._compute_organization_momentum(org_id, cursor)
        momentum_norm = self.normalizer.normalize_momentum(momentum)
        components['organization_momentum'] = momentum_norm

        # 6. Creator Reuse Score (0-1 → 0-1)
        reuse_score = self._compute_creator_reuse_score(org_id, cursor)
        components['creator_reuse_score'] = reuse_score

        # 7. Operator Activity Score (0-1 → 0-1)
        operator_score = self._compute_operator_activity_score(org_id, cursor)
        components['operator_activity_score'] = operator_score

        # 8. Reputation Adjustment (0-1 → 0-1)
        cursor.execute("""
            SELECT reputation_score FROM dev_reputation
            WHERE organization_id = ?
        """, (org_id,))
        row = cursor.fetchone()
        rep_score = self.normalizer.normalize_ratio(row['reputation_score'] if row else 0)
        components['reputation_adjustment'] = rep_score

        # Compute weighted master score
        master_score = sum(
            components[signal_name] * weight
            for signal_name, weight in self.WEIGHTS.items()
        )

        alert_level = self._classify_alert_level(master_score)

        return {
            'organization_id': org_id,
            'launch_probability': components['launch_probability'],
            'launch_wave_score': components['launch_wave_score'],
            'seed_concentration': components['seed_concentration'],
            'funder_overlap_score': components['funder_overlap_score'],
            'organization_momentum': components['organization_momentum'],
            'creator_reuse_score': components['creator_reuse_score'],
            'operator_activity_score': components['operator_activity_score'],
            'reputation_adjustment': components['reputation_adjustment'],
            'master_launch_score': master_score,
            'alert_level': alert_level,
            'components': components,
        }

    def _compute_organization_momentum(self, org_id: int, cursor: sqlite3.Cursor) -> float:
        """
        Compute organization momentum: (activity_24h - avg_7d) / avg_7d

        Returns: momentum value (may be negative or > 1.0)
        """
        now = time.time()
        day_ago = now - 86400
        week_ago = now - 604800

        # Activity last 24h
        cursor.execute("""
            SELECT COUNT(*) as tx_count, SUM(amount_sol) as volume_sol
            FROM transfer_index
            WHERE source IN (
                SELECT wallet FROM dev_organization_wallets
                WHERE organization_id = ?
            ) AND block_time >= ?
        """, (org_id, day_ago))
        activity_24h = cursor.fetchone()
        txs_24h = activity_24h['tx_count'] or 0

        # Average activity last 7 days
        cursor.execute("""
            SELECT COUNT(*) / 7.0 as avg_tx_count, SUM(amount_sol) / 7.0 as avg_volume
            FROM transfer_index
            WHERE source IN (
                SELECT wallet FROM dev_organization_wallets
                WHERE organization_id = ?
            ) AND block_time >= ?
        """, (org_id, week_ago))
        activity_7d = cursor.fetchone()
        avg_txs_7d = activity_7d['avg_tx_count'] or 1.0

        if avg_txs_7d == 0:
            return 0.0

        momentum = (txs_24h - avg_txs_7d) / avg_txs_7d
        return momentum

    def _compute_creator_reuse_score(self, org_id: int, cursor: sqlite3.Cursor) -> float:
        """
        Creator reuse score: how frequently creators from this org appear in launches.

        Returns: 0-1 normalized score
        """
        # Get org creators
        cursor.execute("""
            SELECT COUNT(DISTINCT member_address) as creator_count
            FROM dev_organization_members
            WHERE organization_id = ? AND member_type = 'creator'
        """, (org_id,))
        result = cursor.fetchone()
        org_creator_count = result['creator_count'] or 0

        if org_creator_count == 0:
            return 0.0

        # Count launches where org creators are involved
        cursor.execute("""
            SELECT COUNT(DISTINCT ta.mint) as launch_count
            FROM token_analysis ta
            WHERE ta.earliest_tx_creator IN (
                SELECT member_address FROM dev_organization_members
                WHERE organization_id = ?
            )
        """, (org_id,))
        result = cursor.fetchone()
        launch_count = result['launch_count'] or 0

        # Reuse ratio: launches per creator
        reuse_ratio = launch_count / org_creator_count if org_creator_count > 0 else 0
        # Normalize: 0-5 launches per creator maps to 0-1
        return min(1.0, reuse_ratio / 5.0)

    def _compute_operator_activity_score(self, org_id: int, cursor: sqlite3.Cursor) -> float:
        """
        Operator activity score: unusual increases in operator wallet activity.

        Compares last 24h to 7d average.

        Returns: 0-1 normalized score
        """
        now = time.time()
        day_ago = now - 86400
        week_ago = now - 604800

        # Get operator wallets
        cursor.execute("""
            SELECT wallet FROM dev_organization_wallets
            WHERE organization_id = ? AND is_operator = 1
        """, (org_id,))
        operator_wallets = [row[0] for row in cursor.fetchall()]

        if not operator_wallets:
            return 0.0

        placeholders = ','.join(['?' for _ in operator_wallets])

        # Activity last 24h
        cursor.execute(f"""
            SELECT COUNT(*) as tx_count
            FROM transfer_index
            WHERE source IN ({placeholders}) AND block_time >= ?
        """, operator_wallets + [day_ago])
        txs_24h = cursor.fetchone()['tx_count'] or 0

        # Average activity last 7 days
        cursor.execute(f"""
            SELECT COUNT(*) / 7.0 as avg_tx_count
            FROM transfer_index
            WHERE source IN ({placeholders}) AND block_time >= ?
        """, operator_wallets + [week_ago])
        avg_txs_7d = cursor.fetchone()['avg_tx_count'] or 1.0

        if avg_txs_7d == 0:
            return 0.0

        # Spike ratio: how much above average
        spike_ratio = txs_24h / avg_txs_7d if avg_txs_7d > 0 else 0
        # Normalize: 1x → 0, 2x → 0.5, 3x+ → 1.0
        activity_score = min(1.0, max(0.0, (spike_ratio - 1.0) / 2.0))
        return activity_score


class MasterLaunchScoreEngine:
    """
    Orchestrator: computes master launch score for all organizations daily.
    Follows DevIntelligenceV2Engine pattern.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.start_time = time.time()
        self.calculator = MasterLaunchScoreCalculator(db_path)

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA mmap_size=30000000")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        """Create master_launch_signals table if not exists."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS master_launch_signals (
                signal_id               INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id         INTEGER NOT NULL,
                launch_probability      REAL DEFAULT 0,
                launch_wave_score       REAL DEFAULT 0,
                seed_concentration      REAL DEFAULT 0,
                funder_overlap_score    REAL DEFAULT 0,
                organization_momentum   REAL DEFAULT 0,
                creator_reuse_score     REAL DEFAULT 0,
                operator_activity_score REAL DEFAULT 0,
                reputation_adjustment   REAL DEFAULT 0,
                master_launch_score     REAL DEFAULT 0,
                alert_level             TEXT,
                computed_at             REAL NOT NULL,
                FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
                UNIQUE(organization_id)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_mls_org_id
            ON master_launch_signals(organization_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_mls_score
            ON master_launch_signals(master_launch_score DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_mls_alert
            ON master_launch_signals(alert_level)
        """)

        conn.commit()
        conn.close()

    def _load_organizations(self) -> List[Dict]:
        """Load all organizations from database."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT organization_id FROM dev_organizations")
        orgs = [{'organization_id': row[0]} for row in cursor.fetchall()]

        conn.close()
        return orgs

    def _store_score(self, org_id: int, score_data: Dict, cursor: sqlite3.Cursor) -> None:
        """Store computed score in database."""
        now = time.time()

        cursor.execute("""
            INSERT OR REPLACE INTO master_launch_signals
            (organization_id, launch_probability, launch_wave_score,
             seed_concentration, funder_overlap_score, organization_momentum,
             creator_reuse_score, operator_activity_score, reputation_adjustment,
             master_launch_score, alert_level, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            org_id,
            score_data['launch_probability'],
            score_data['launch_wave_score'],
            score_data['seed_concentration'],
            score_data['funder_overlap_score'],
            score_data['organization_momentum'],
            score_data['creator_reuse_score'],
            score_data['operator_activity_score'],
            score_data['reputation_adjustment'],
            score_data['master_launch_score'],
            score_data['alert_level'],
            now
        ))

    def detect_and_store(self) -> Dict:
        """
        Main orchestrator: compute and store master launch scores for all orgs.

        Returns:
        {
            'status': 'success'|'error',
            'message': str,
            'orgs_processed': int,
            'critical_count': int,
            'high_count': int,
            'watch_count': int,
            'duration_ms': float
        }
        """
        try:
            logger.info("Starting master launch score computation")

            self._ensure_tables()

            orgs = self._load_organizations()

            if not orgs:
                logger.warning("No organizations found")
                return {
                    'status': 'success',
                    'message': 'No organizations to process',
                    'orgs_processed': 0,
                    'critical_count': 0,
                    'high_count': 0,
                    'watch_count': 0,
                    'duration_ms': int((time.time() - self.start_time) * 1000)
                }

            conn = self._get_conn()
            cursor = conn.cursor()

            orgs_processed = 0
            alert_counts = {'CRITICAL': 0, 'HIGH': 0, 'WATCH': 0, 'LOW': 0}

            for org in orgs:
                try:
                    score_data = self.calculator.compute_organization_score(
                        org['organization_id'], cursor
                    )

                    alert_counts[score_data['alert_level']] += 1
                    self._store_score(org['organization_id'], score_data, cursor)
                    orgs_processed += 1

                except Exception as e:
                    logger.error(
                        f"Error computing score for org {org['organization_id']}: {e}"
                    )
                    continue

            conn.commit()
            conn.close()

            logger.info(
                f"Master launch score computation complete: {orgs_processed} orgs, "
                f"{alert_counts['CRITICAL']} CRITICAL, {alert_counts['HIGH']} HIGH, "
                f"{alert_counts['WATCH']} WATCH"
            )

            return {
                'status': 'success',
                'message': f'Computed master launch scores for {orgs_processed} organizations',
                'orgs_processed': orgs_processed,
                'critical_count': alert_counts['CRITICAL'],
                'high_count': alert_counts['HIGH'],
                'watch_count': alert_counts['WATCH'],
                'duration_ms': int((time.time() - self.start_time) * 1000)
            }

        except Exception as e:
            logger.error(f"Master launch score computation failed: {e}", exc_info=True)
            return {
                'status': 'error',
                'message': str(e),
                'orgs_processed': 0,
                'critical_count': 0,
                'high_count': 0,
                'watch_count': 0,
                'duration_ms': int((time.time() - self.start_time) * 1000)
            }
