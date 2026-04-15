"""
FLEX Creator Seed Concentration Metrics

Analyzes creator funding patterns to measure coordination and concentration
of seed funding before token launches. Detects when multiple creators in
an organization receive similar funding amounts in coordinated time windows.

Key Metric: seed_concentration = 1 - (seed_stddev / avg_seed_amount)
- 1.0 = Perfect concentration (all creators funded equally)
- 0.0 = Zero concentration (highly variable amounts)

Integration: Used as 0.05 weight in overall launch_score to indicate
organized, coordinated development preparation.
"""

import sqlite3
import logging
import time
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class CreatorSeedMetricsAnalyzer:
    """
    Analyzes creator seed funding patterns to measure concentration.

    Computes for each creator:
    - avg_seed_amount: Average SOL received in seed funding
    - seed_stddev: Standard deviation of seed amounts
    - seed_concentration: 1 - (stddev / avg) normalized metric (0-1)
    - funding_wallet_count: Number of unique wallets funding the creator
    - funding_time_window: Hours between first and last funding (in seed phase)
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.start_time = time.time()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        """Create creator_seed_metrics table if not exists."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_seed_metrics (
                metric_id           INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_wallet      TEXT NOT NULL,
                organization_id     INTEGER,
                avg_seed_amount     REAL DEFAULT 0,        -- Average SOL per seed transaction
                seed_stddev         REAL DEFAULT 0,        -- Std dev of seed amounts
                seed_concentration  REAL DEFAULT 0,        -- 1 - (stddev/avg), normalized 0-1
                funding_wallet_count INTEGER DEFAULT 0,    -- Unique wallets funding this creator
                funding_time_window INTEGER DEFAULT 0,     -- Hours from first to last seed funding
                seed_count          INTEGER DEFAULT 0,     -- Number of seed funding transactions
                total_seed_amount   REAL DEFAULT 0,        -- Sum of all seed amounts
                created_at          INTEGER NOT NULL,      -- Unix timestamp
                FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
                UNIQUE(creator_wallet, organization_id)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_csm_creator
            ON creator_seed_metrics(creator_wallet)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_csm_org_id
            ON creator_seed_metrics(organization_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_csm_concentration
            ON creator_seed_metrics(seed_concentration DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_csm_wallet_count
            ON creator_seed_metrics(funding_wallet_count DESC)
        """)

        conn.commit()
        conn.close()

    def _get_creator_seed_transfers(
        self, creator_wallet: str, cursor: sqlite3.Cursor, lookback_days: int = 30
    ) -> List[Dict]:
        """
        Get seed funding transfers for a creator.

        Seed phase: small transfers (<10 SOL) from multiple wallets,
        typically within 30 days before first significant activity.
        """
        cutoff_time = time.time() - (lookback_days * 86400)

        # Get all inbound transfers to this creator
        cursor.execute("""
            SELECT
                source,
                amount_sol,
                tx_timestamp,
                tx_signature
            FROM funder_incoming_transfers
            WHERE recipient = ?
            AND tx_timestamp > ?
            AND amount_sol > 0
            ORDER BY tx_timestamp ASC
        """, (creator_wallet, cutoff_time))

        transfers = [dict(row) for row in cursor.fetchall()]

        # Filter to seed-like transfers: small amounts, multiple sources
        # Seed phase indicator: <10 SOL, from different wallets
        seed_transfers = [
            t for t in transfers if t['amount_sol'] < 10.0
        ]

        return seed_transfers

    def _compute_seed_metrics(
        self, creator_wallet: str, org_id: Optional[int], cursor: sqlite3.Cursor
    ) -> Dict:
        """
        Compute seed concentration metrics for a creator.

        Returns:
        {
            'creator_wallet': str,
            'organization_id': int,
            'avg_seed_amount': float,
            'seed_stddev': float,
            'seed_concentration': float (0-1),
            'funding_wallet_count': int,
            'funding_time_window': int,  # hours
            'seed_count': int,
            'total_seed_amount': float,
            'signal_strength': float (0-100)
        }
        """
        seed_transfers = self._get_creator_seed_transfers(creator_wallet, cursor)

        if not seed_transfers:
            return {
                'creator_wallet': creator_wallet,
                'organization_id': org_id,
                'avg_seed_amount': 0,
                'seed_stddev': 0,
                'seed_concentration': 0,
                'funding_wallet_count': 0,
                'funding_time_window': 0,
                'seed_count': 0,
                'total_seed_amount': 0,
                'signal_strength': 0
            }

        # Extract amounts
        amounts = [t['amount_sol'] for t in seed_transfers]
        sources = set(t['source'] for t in seed_transfers)

        # Compute statistics
        avg_amount = sum(amounts) / len(amounts) if amounts else 0

        # Standard deviation
        if len(amounts) > 1:
            variance = sum((x - avg_amount) ** 2 for x in amounts) / (len(amounts) - 1)
            stddev = variance ** 0.5
        else:
            stddev = 0

        # Concentration: 1 - (stddev / avg), clamped to 0-1
        # High concentration (low stddev) = all amounts similar
        if avg_amount > 0:
            concentration = max(0, min(1, 1 - (stddev / avg_amount)))
        else:
            concentration = 0

        # Time window in hours
        timestamps = [t['tx_timestamp'] for t in seed_transfers]
        time_window_hours = (max(timestamps) - min(timestamps)) / 3600 if len(timestamps) > 1 else 0

        # Total funded
        total_amount = sum(amounts)

        # Signal strength: combines concentration and wallet diversity
        # High concentration + multiple wallets = high signal
        wallet_diversity = min(1, len(sources) / 5.0)  # Normalize to 5+ wallets = 1.0
        signal_strength = (concentration * 0.6 + wallet_diversity * 0.4) * 100

        return {
            'creator_wallet': creator_wallet,
            'organization_id': org_id,
            'avg_seed_amount': avg_amount,
            'seed_stddev': stddev,
            'seed_concentration': concentration,
            'funding_wallet_count': len(sources),
            'funding_time_window': int(time_window_hours),
            'seed_count': len(seed_transfers),
            'total_seed_amount': total_amount,
            'signal_strength': signal_strength
        }

    def _get_org_creators(self, org_id: int, cursor: sqlite3.Cursor) -> List[str]:
        """Get all creators in an organization."""
        cursor.execute("""
            SELECT DISTINCT creator_wallet
            FROM dev_organization_members
            WHERE organization_id = ?
        """, (org_id,))
        return [row[0] for row in cursor.fetchall()]

    def compute_and_store(self) -> Dict:
        """
        Compute seed metrics for all creators and store in database.

        Returns:
        {
            'status': 'success' or 'error',
            'message': str,
            'metrics_computed': int,
            'orgs_processed': int,
            'high_concentration_count': int,
            'duration_ms': float
        }
        """
        try:
            self._ensure_tables()

            conn = self._get_conn()
            cursor = conn.cursor()

            # Get all organizations
            cursor.execute("""
                SELECT organization_id FROM dev_organizations
            """)
            orgs = [row[0] for row in cursor.fetchall()]

            if not orgs:
                logger.warning("No organizations found")
                conn.close()
                return {
                    'status': 'success',
                    'message': 'No organizations to process',
                    'metrics_computed': 0,
                    'orgs_processed': 0,
                    'high_concentration_count': 0,
                    'duration_ms': int((time.time() - self.start_time) * 1000)
                }

            now = int(time.time())
            metrics_computed = 0
            high_concentration = 0

            for org_id in orgs:
                creators = self._get_org_creators(org_id, cursor)

                for creator_wallet in creators:
                    try:
                        metrics = self._compute_seed_metrics(creator_wallet, org_id, cursor)

                        # Store metrics
                        cursor.execute("""
                            INSERT OR REPLACE INTO creator_seed_metrics
                            (creator_wallet, organization_id, avg_seed_amount, seed_stddev,
                             seed_concentration, funding_wallet_count, funding_time_window,
                             seed_count, total_seed_amount, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            metrics['creator_wallet'],
                            metrics['organization_id'],
                            metrics['avg_seed_amount'],
                            metrics['seed_stddev'],
                            metrics['seed_concentration'],
                            metrics['funding_wallet_count'],
                            metrics['funding_time_window'],
                            metrics['seed_count'],
                            metrics['total_seed_amount'],
                            now
                        ))

                        if metrics['seed_concentration'] >= 0.6:
                            high_concentration += 1

                        metrics_computed += 1

                    except Exception as e:
                        logger.error(f"Error computing metrics for {creator_wallet}: {e}")
                        continue

            conn.commit()
            conn.close()

            logger.info(
                f"Seed metrics computed: {metrics_computed} creators, "
                f"{high_concentration} with high concentration"
            )

            return {
                'status': 'success',
                'message': f'Seed metrics computed for {metrics_computed} creators',
                'metrics_computed': metrics_computed,
                'orgs_processed': len(orgs),
                'high_concentration_count': high_concentration,
                'duration_ms': int((time.time() - self.start_time) * 1000)
            }

        except Exception as e:
            logger.error(f"Seed metrics computation failed: {e}", exc_info=True)
            return {
                'status': 'error',
                'message': str(e),
                'metrics_computed': 0,
                'orgs_processed': 0,
                'high_concentration_count': 0,
                'duration_ms': int((time.time() - self.start_time) * 1000)
            }


class OrgSeedConcentrationScorer:
    """
    Aggregates creator seed metrics to organization level.

    Computes organization-level seed concentration signal (0-100)
    by averaging creator metrics and weighting by funding amounts.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def get_org_seed_concentration(self, org_id: int, cursor: sqlite3.Cursor) -> Dict:
        """
        Get aggregated seed concentration score for an organization.

        Returns:
        {
            'org_id': int,
            'avg_concentration': float (0-1),
            'weighted_concentration': float (0-1),
            'creators_with_seed_funding': int,
            'high_concentration_creators': int,
            'signal_strength': float (0-100)
        }
        """
        cursor.execute("""
            SELECT
                COUNT(*) as creator_count,
                AVG(seed_concentration) as avg_concentration,
                AVG(CASE WHEN seed_concentration >= 0.6 THEN 1 ELSE 0 END) as high_conc_ratio,
                SUM(total_seed_amount) as total_seed,
                SUM(funding_wallet_count) as total_wallets
            FROM creator_seed_metrics
            WHERE organization_id = ?
            AND seed_count > 0
        """, (org_id,))

        result = cursor.fetchone()

        if not result or result['creator_count'] == 0:
            return {
                'org_id': org_id,
                'avg_concentration': 0,
                'weighted_concentration': 0,
                'creators_with_seed_funding': 0,
                'high_concentration_creators': 0,
                'signal_strength': 0
            }

        creator_count = result['creator_count']
        avg_concentration = result['avg_concentration'] or 0
        high_conc_ratio = result['high_conc_ratio'] or 0
        total_seed = result['total_seed'] or 0
        total_wallets = result['total_wallets'] or 0

        # High concentration + multi-wallet = signal of organized coordination
        coordination_signal = avg_concentration * 0.7 + min(1, total_wallets / 10) * 0.3
        signal_strength = coordination_signal * 100

        return {
            'org_id': org_id,
            'avg_concentration': avg_concentration,
            'weighted_concentration': coordination_signal,
            'creators_with_seed_funding': int(creator_count),
            'high_concentration_creators': int(creator_count * high_conc_ratio),
            'signal_strength': signal_strength
        }
