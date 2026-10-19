"""
Dev Intelligence Graph v2 — Launch Probability Model & Organization Reputation

Extends v1 (multi-layer wallet→creator→token detection) with:
1. Launch probability prediction (0-100): predicts token launches within 7 days
2. Organization reputation tracking: lifetime success/failure metrics (tokens, rugs, outcomes)

Exit codes:
- 0: Success
- 1: Error
"""

import sqlite3
import json
import logging
import time
from pathlib import Path
from typing import Dict, Optional, List, Tuple
import sys

logger = logging.getLogger(__name__)


class LaunchProbabilityModel:
    """
    Computes launch probability signals for an organization.
    Uses existing SQLite data to predict likelihood of imminent token launch.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        """Get SQLite connection with WAL mode optimization."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA mmap_size=30000000")
        conn.row_factory = sqlite3.Row
        return conn

    def compute_signals(self, org: Dict) -> Dict:
        """
        Compute all 6 launch probability signals for an organization.
        Opens one connection, runs all sub-queries, closes, returns flat dict.

        Args:
            org: Dict with keys: organization_id, token_count, creator_count, wallet_count,
                 total_volume_sol, cluster_strength, farm_cluster_id, token_list,
                 creator_list, wallet_list

        Returns:
            Dict with keys: signal_recency, signal_scale, signal_launch_rate,
                  signal_funding_velocity, signal_coordination, signal_network_risk,
                  plus raw values: days_since_last_funding, org_token_count, etc.
        """
        signals = {
            'signal_recency': 0.0,
            'signal_scale': 0.0,
            'signal_launch_rate': 0.0,
            'signal_funding_velocity': 0.0,
            'signal_coordination': 0.0,
            'signal_network_risk': 0.0,
            'days_since_last_funding': None,
            'org_token_count': org.get('token_count', 0),
            'org_creator_count': org.get('creator_count', 0),
            'org_wallet_count': org.get('wallet_count', 0),
            'avg_tokens_launched': 0.0,
            'funding_velocity_sol': 0.0,
            'avg_composite_weight': 0.0,
            'avg_rug_probability': 0.0,
        }

        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            # Signal 1: Recency (0-30 points) — days since last funding activity
            last_ts = self._fetch_last_activity_ts(cursor, org.get('farm_cluster_id'),
                                                   json.loads(org.get('wallet_list', '[]')))
            if last_ts:
                days_since = (time.time() - last_ts) / 86400.0
                signals['days_since_last_funding'] = days_since
                signals['signal_recency'] = max(0, 30 * (1 - days_since / 14.0))
            else:
                signals['days_since_last_funding'] = None
                signals['signal_recency'] = 0.0

            # Signal 2: Scale (0-20 points) — org size composite
            token_norm = min(org.get('token_count', 0) / 10.0, 1.0)
            creator_norm = min(org.get('creator_count', 0) / 8.0, 1.0)
            wallet_norm = min(org.get('wallet_count', 0) / 15.0, 1.0)
            signals['signal_scale'] = 20 * (token_norm * 0.5 + creator_norm * 0.3 + wallet_norm * 0.2)

            # Signal 3: Launch Rate (0-20 points) — avg tokens launched per creator member
            avg_launched = self._fetch_avg_tokens_launched(cursor, org.get('organization_id'))
            signals['avg_tokens_launched'] = avg_launched
            launch_rate_norm = min(avg_launched / 20.0, 1.0)
            signals['signal_launch_rate'] = 20 * launch_rate_norm

            # Signal 4: Funding Velocity (0-15 points) — SOL per active day
            active_days = self._fetch_farm_active_days(cursor, org.get('farm_cluster_id'))
            total_volume = org.get('total_volume_sol', 0.0)
            if active_days and active_days > 0:
                velocity = total_volume / active_days
            else:
                velocity = 0.0
            signals['funding_velocity_sol'] = velocity
            velocity_norm = min(velocity / 50.0, 1.0)
            signals['signal_funding_velocity'] = 15 * velocity_norm

            # Signal 5: Coordination Strength (0-10 points) — avg_composite_weight
            avg_weight = self._fetch_avg_composite_weight(cursor, org.get('farm_cluster_id'),
                                                          org.get('cluster_strength', 0.0))
            signals['avg_composite_weight'] = avg_weight
            signals['signal_coordination'] = 10 * (avg_weight / 100.0) if avg_weight > 0 else 0.0

            # Signal 6: Network Risk (0-5 points) — avg rug_probability from org tokens
            avg_rug = self._fetch_avg_rug_probability(cursor, json.loads(org.get('token_list', '[]')))
            signals['avg_rug_probability'] = avg_rug
            signals['signal_network_risk'] = 5 * avg_rug

            conn.close()

        except Exception as e:
            logger.warning(f"Error computing signals for org {org.get('organization_id')}: {e}")
            # Return signals dict with defaults (all 0) on error

        return signals

    def _fetch_last_activity_ts_via_hot_cold(self, reader, farm_cluster_id: Optional[int],
                                              wallet_list: List[str], cursor: sqlite3.Cursor = None) -> Optional[int]:
        """STORAGE-LIFECYCLE-P5B-R2.1 (NOT YET WIRED INTO THE LIVE CALL
        SITE -- see _fetch_last_activity_ts() below, which is unmodified
        production code still querying transfer_index directly against
        the monolithic HOT DB).

        Reproduces this method's transfer_index fallback (the
        `SELECT MAX(block_time) ... WHERE source IN (...) OR destination
        IN (...)` unbounded historical-max lookup) via
        UnifiedTransferReader.last_activity_max_block_time(), so the
        result stays correct once history moves out of the HOT table.
        The farm_clusters primary path is untouched -- it is a different
        table, not transfer_index, and stays HOT_ONLY_SAFE as-is; `cursor`
        is accepted only to preserve that primary-path call signature for
        a future caller that wants to try farm_clusters first."""
        if farm_cluster_id and cursor is not None:
            try:
                cursor.execute(
                    "SELECT last_activity_ts FROM farm_clusters WHERE cluster_id = ? LIMIT 1",
                    (farm_cluster_id,)
                )
                row = cursor.fetchone()
                if row and row[0]:
                    return row[0]
            except Exception as e:
                logger.debug(f"Error fetching farm_clusters last_activity_ts: {e}")

        if not wallet_list:
            return None
        try:
            return reader.last_activity_max_block_time(wallet_list)
        except Exception as e:
            logger.debug(f"Error fetching transfer_index last_activity_ts via hot_cold reader: {e}")
            return None

    def _fetch_last_activity_ts(self, cursor: sqlite3.Cursor, farm_cluster_id: Optional[int],
                                wallet_list: List[str]) -> Optional[int]:
        """Get Unix timestamp of last funding activity for org."""
        # Primary path: farm_clusters if linked
        if farm_cluster_id:
            try:
                cursor.execute(
                    "SELECT last_activity_ts FROM farm_clusters WHERE cluster_id = ? LIMIT 1",
                    (farm_cluster_id,)
                )
                row = cursor.fetchone()
                if row and row[0]:
                    return row[0]
            except Exception as e:
                logger.debug(f"Error fetching farm_clusters last_activity_ts: {e}")

        # Fallback: transfer_index
        if not wallet_list:
            return None

        try:
            placeholders = ','.join(['?'] * len(wallet_list))
            query = f"""
                SELECT MAX(block_time) AS last_ts
                FROM transfer_index
                WHERE source IN ({placeholders}) OR destination IN ({placeholders})
            """
            cursor.execute(query, wallet_list + wallet_list)
            row = cursor.fetchone()
            if row and row[0]:
                return row[0]
        except Exception as e:
            logger.debug(f"Error fetching transfer_index last_activity_ts: {e}")

        return None

    def _fetch_avg_tokens_launched(self, cursor: sqlite3.Cursor, organization_id: int) -> float:
        """Get average tokens_launched across creator members in org."""
        try:
            cursor.execute("""
                SELECT AVG(dr.tokens_launched) AS avg_launched
                FROM dev_organization_members dom
                JOIN dev_reputation dr ON dom.member_address = dr.wallet
                WHERE dom.organization_id = ? AND dom.member_type = 'creator'
            """, (organization_id,))
            row = cursor.fetchone()
            return row[0] or 0.0 if row and row[0] is not None else 0.0
        except Exception as e:
            logger.debug(f"Error fetching avg_tokens_launched: {e}")
            return 0.0

    def _fetch_farm_active_days(self, cursor: sqlite3.Cursor, farm_cluster_id: Optional[int]) -> float:
        """Get active_days from farm_clusters if linked."""
        if not farm_cluster_id:
            return 0.0

        try:
            cursor.execute(
                "SELECT active_days FROM farm_clusters WHERE cluster_id = ? LIMIT 1",
                (farm_cluster_id,)
            )
            row = cursor.fetchone()
            return row[0] or 0.0 if row and row[0] is not None else 0.0
        except Exception as e:
            logger.debug(f"Error fetching farm_clusters active_days: {e}")
            return 0.0

    def _fetch_avg_composite_weight(self, cursor: sqlite3.Cursor, farm_cluster_id: Optional[int],
                                    cluster_strength: float) -> float:
        """Get avg_composite_weight from farm_clusters (fallback to cluster_strength)."""
        # Primary: farm_clusters
        if farm_cluster_id:
            try:
                cursor.execute(
                    "SELECT avg_composite_weight FROM farm_clusters WHERE cluster_id = ? LIMIT 1",
                    (farm_cluster_id,)
                )
                row = cursor.fetchone()
                if row and row[0] is not None:
                    return row[0]
            except Exception as e:
                logger.debug(f"Error fetching farm_clusters avg_composite_weight: {e}")

        # Fallback: dev_organizations.cluster_strength (already in org dict)
        return cluster_strength or 0.0

    def _fetch_avg_rug_probability(self, cursor: sqlite3.Cursor, token_list: List[str]) -> float:
        """Get average rug_probability for all tokens in org."""
        if not token_list:
            return 0.0

        try:
            placeholders = ','.join(['?'] * len(token_list))
            query = f"""
                SELECT AVG(rug_probability) AS avg_rug
                FROM token_analysis
                WHERE mint IN ({placeholders}) AND rug_probability IS NOT NULL
            """
            cursor.execute(query, token_list)
            row = cursor.fetchone()
            return row[0] or 0.0 if row and row[0] is not None else 0.0
        except Exception as e:
            logger.debug(f"Error fetching avg rug_probability: {e}")
            return 0.0

    def score(self, signals: Dict) -> float:
        """
        Compute final launch_probability score from signals.
        Pure arithmetic — no DB access.

        Args:
            signals: Dict with all signal_* keys populated

        Returns:
            Float in [0, 100]
        """
        total = (
            signals.get('signal_recency', 0.0) +
            signals.get('signal_scale', 0.0) +
            signals.get('signal_launch_rate', 0.0) +
            signals.get('signal_funding_velocity', 0.0) +
            signals.get('signal_coordination', 0.0) +
            signals.get('signal_network_risk', 0.0)
        )
        return max(0.0, min(100.0, total))


class ReputationTracker:
    """
    Computes organization-level reputation metrics from lifetime token outcomes.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        """Get SQLite connection with WAL mode optimization."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA mmap_size=30000000")
        conn.row_factory = sqlite3.Row
        return conn

    def compute_reputation(self, organization_id: int) -> Dict:
        """
        Compute reputation metrics for an organization.

        Returns:
            Dict with keys: total_tokens_launched, tokens_above_2x, tokens_above_10x,
                  rug_count, success_rate, rug_rate
        """
        metrics = {
            'total_tokens_launched': 0,
            'tokens_above_2x': 0,
            'tokens_above_10x': 0,
            'rug_count': 0,
            'success_rate': 0.0,
            'rug_rate': 0.0,
        }

        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            # Fetch tokens_above_2x/10x from dev_reputation (aggregated per creator member)
            cursor.execute("""
                SELECT SUM(dr.tokens_launched)  AS total_tokens_launched,
                       SUM(dr.tokens_above_2x)  AS tokens_above_2x,
                       SUM(dr.tokens_above_10x) AS tokens_above_10x
                FROM dev_organization_members dom
                JOIN dev_reputation dr ON dom.member_address = dr.wallet
                WHERE dom.organization_id = ? AND dom.member_type = 'creator'
            """, (organization_id,))
            row = cursor.fetchone()
            if row:
                metrics['total_tokens_launched'] = row[0] or 0
                metrics['tokens_above_2x'] = row[1] or 0
                metrics['tokens_above_10x'] = row[2] or 0

            # Fetch rug_count from token_analysis (per token node)
            cursor.execute("""
                SELECT COUNT(*) AS rug_count
                FROM dev_organization_members dom
                JOIN token_analysis ta ON dom.member_address = ta.mint
                WHERE dom.organization_id = ? AND dom.member_type = 'token'
                  AND ta.rug_probability > 0.7
            """, (organization_id,))
            row = cursor.fetchone()
            if row:
                metrics['rug_count'] = row[0] or 0

            # Compute rates
            if metrics['total_tokens_launched'] > 0:
                metrics['success_rate'] = metrics['tokens_above_2x'] / metrics['total_tokens_launched']
                metrics['rug_rate'] = metrics['rug_count'] / metrics['total_tokens_launched']
            else:
                metrics['success_rate'] = 0.0
                metrics['rug_rate'] = 0.0

            conn.close()

        except Exception as e:
            logger.warning(f"Error computing reputation for org {organization_id}: {e}")

        return metrics

    def _score_reputation(self, metrics: Dict) -> float:
        """
        Compute reputation_score (0-1) from metrics.

        Formula:
            success_component = success_rate * 0.50
            rug_penalty       = rug_rate * 0.40
            volume_bonus      = min(total_tokens_launched / 20.0, 1.0) * 0.10
            score             = success_component - rug_penalty + volume_bonus
            Clamped to [0, 1]
        """
        success_component = metrics.get('success_rate', 0.0) * 0.50
        rug_penalty = metrics.get('rug_rate', 0.0) * 0.40
        volume_norm = min(metrics.get('total_tokens_launched', 0) / 20.0, 1.0)
        volume_bonus = volume_norm * 0.10

        score = success_component - rug_penalty + volume_bonus
        return max(0.0, min(1.0, score))


class DevIntelligenceV2Engine:
    """
    Orchestrator for launch probability prediction and reputation tracking.
    Runs after DevIntelligenceEngine (v1) to add predictive signals to organizations.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.start_time = time.time()

    def _get_conn(self) -> sqlite3.Connection:
        """Get SQLite connection with WAL mode optimization."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA mmap_size=30000000")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        """Ensure org_launch_predictions and org_reputation tables exist."""
        conn = self._get_conn()
        cursor = conn.cursor()

        # Both tables are created by the migration, but inline DDL for idempotency
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS org_launch_predictions (
                prediction_id           INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id         INTEGER NOT NULL,
                prediction_date         TEXT NOT NULL,
                launch_probability      REAL DEFAULT 0,
                signal_recency          REAL DEFAULT 0,
                signal_scale            REAL DEFAULT 0,
                signal_launch_rate      REAL DEFAULT 0,
                signal_funding_velocity REAL DEFAULT 0,
                signal_coordination     REAL DEFAULT 0,
                signal_network_risk     REAL DEFAULT 0,
                days_since_last_funding REAL,
                org_token_count         INTEGER DEFAULT 0,
                org_creator_count       INTEGER DEFAULT 0,
                org_wallet_count        INTEGER DEFAULT 0,
                avg_tokens_launched     REAL DEFAULT 0,
                funding_velocity_sol    REAL DEFAULT 0,
                avg_composite_weight    REAL DEFAULT 0,
                avg_rug_probability     REAL DEFAULT 0,
                computed_at             REAL NOT NULL,
                FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
                UNIQUE(organization_id, prediction_date)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS org_reputation (
                reputation_id           INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id         INTEGER NOT NULL,
                total_tokens_launched   INTEGER DEFAULT 0,
                tokens_above_2x         INTEGER DEFAULT 0,
                tokens_above_10x        INTEGER DEFAULT 0,
                rug_count               INTEGER DEFAULT 0,
                success_rate            REAL DEFAULT 0,
                rug_rate                REAL DEFAULT 0,
                reputation_score        REAL DEFAULT 0,
                computed_at             REAL NOT NULL,
                FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
                UNIQUE(organization_id)
            )
        """)

        # Indexes (IF NOT EXISTS safeguard)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_org_predictions_org_id
            ON org_launch_predictions(organization_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_org_predictions_probability
            ON org_launch_predictions(launch_probability DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_org_reputation_score
            ON org_reputation(reputation_score DESC)
        """)

        conn.commit()
        conn.close()

    def _load_organizations(self, cursor: sqlite3.Cursor) -> List[Dict]:
        """Load all organizations from dev_organizations."""
        cursor.execute("""
            SELECT organization_id, operator_wallet, token_count, creator_count, wallet_count,
                   total_volume_sol, cluster_strength, farm_cluster_id, token_list, creator_list, wallet_list
            FROM dev_organizations
        """)
        orgs = []
        for row in cursor.fetchall():
            org = dict(row)
            # Parse JSON lists
            org['token_list'] = json.loads(org.get('token_list', '[]') or '[]')
            org['creator_list'] = json.loads(org.get('creator_list', '[]') or '[]')
            org['wallet_list'] = json.loads(org.get('wallet_list', '[]') or '[]')
            orgs.append(org)
        return orgs

    def _store_prediction(self, cursor: sqlite3.Cursor, org_id: int, prediction_date: str,
                         signals: Dict, score: float, now: float) -> None:
        """Store prediction for org on given date (INSERT OR REPLACE)."""
        cursor.execute("""
            INSERT OR REPLACE INTO org_launch_predictions (
                organization_id, prediction_date, launch_probability,
                signal_recency, signal_scale, signal_launch_rate,
                signal_funding_velocity, signal_coordination, signal_network_risk,
                days_since_last_funding, org_token_count, org_creator_count, org_wallet_count,
                avg_tokens_launched, funding_velocity_sol, avg_composite_weight, avg_rug_probability,
                computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            org_id, prediction_date, score,
            signals.get('signal_recency', 0.0),
            signals.get('signal_scale', 0.0),
            signals.get('signal_launch_rate', 0.0),
            signals.get('signal_funding_velocity', 0.0),
            signals.get('signal_coordination', 0.0),
            signals.get('signal_network_risk', 0.0),
            signals.get('days_since_last_funding'),
            signals.get('org_token_count', 0),
            signals.get('org_creator_count', 0),
            signals.get('org_wallet_count', 0),
            signals.get('avg_tokens_launched', 0.0),
            signals.get('funding_velocity_sol', 0.0),
            signals.get('avg_composite_weight', 0.0),
            signals.get('avg_rug_probability', 0.0),
            now
        ))

    def _store_reputation(self, cursor: sqlite3.Cursor, org_id: int,
                         metrics: Dict, rep_score: float, now: float) -> None:
        """Store reputation for org (INSERT OR REPLACE)."""
        cursor.execute("""
            INSERT OR REPLACE INTO org_reputation (
                organization_id, total_tokens_launched, tokens_above_2x, tokens_above_10x,
                rug_count, success_rate, rug_rate, reputation_score, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            org_id,
            metrics.get('total_tokens_launched', 0),
            metrics.get('tokens_above_2x', 0),
            metrics.get('tokens_above_10x', 0),
            metrics.get('rug_count', 0),
            metrics.get('success_rate', 0.0),
            metrics.get('rug_rate', 0.0),
            rep_score,
            now
        ))

    def detect_and_store(self) -> Dict:
        """
        Main entry point: detect and store launch predictions + reputation for all orgs.

        Returns:
            Dict with keys: status ('success' or 'error'), message, orgs_processed, duration_ms
        """
        try:
            self._ensure_tables()

            # Load organizations
            conn = self._get_conn()
            cursor = conn.cursor()
            orgs = self._load_organizations(cursor)
            conn.close()

            if not orgs:
                duration_ms = (time.time() - self.start_time) * 1000
                return {
                    'status': 'success',
                    'message': 'No organizations found to process',
                    'orgs_processed': 0,
                    'duration_ms': duration_ms
                }

            # Initialize models
            model = LaunchProbabilityModel(self.db_path)
            tracker = ReputationTracker(self.db_path)
            prediction_date = time.strftime('%Y-%m-%d', time.gmtime())
            now = time.time()

            # Process each org
            conn = self._get_conn()
            cursor = conn.cursor()
            orgs_processed = 0

            for org in orgs:
                try:
                    # Launch probability
                    signals = model.compute_signals(org)
                    score = model.score(signals)
                    self._store_prediction(cursor, org['organization_id'], prediction_date,
                                         signals, score, now)

                    # Reputation
                    metrics = tracker.compute_reputation(org['organization_id'])
                    rep_score = tracker._score_reputation(metrics)
                    self._store_reputation(cursor, org['organization_id'], metrics, rep_score, now)

                    orgs_processed += 1

                except Exception as e:
                    logger.warning(f"Failed to process org {org['organization_id']}: {e}")
                    continue

            conn.commit()
            conn.close()

            duration_ms = (time.time() - self.start_time) * 1000
            return {
                'status': 'success',
                'message': f'Dev intelligence v2: {orgs_processed}/{len(orgs)} organizations processed',
                'orgs_processed': orgs_processed,
                'duration_ms': duration_ms
            }

        except Exception as e:
            logger.error(f"V2 detection failed: {e}", exc_info=True)
            duration_ms = (time.time() - self.start_time) * 1000
            return {
                'status': 'error',
                'message': str(e),
                'orgs_processed': 0,
                'duration_ms': duration_ms
            }
