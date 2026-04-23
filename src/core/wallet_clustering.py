"""
FLEX Phase 3.3 Dev Farm Detection + Developer Reputation

This module provides wallet clustering for dev farm detection and per-developer
reputation scoring from rug history + token success metrics.

Architecture:
- Dev farm detection via multi-creator funding patterns on transfer_index
- Confidence scoring (0-100) based on creator count, consistency, duration, activity
- Developer reputation merging rug_count + token success metrics
- Burst detection for synchronized funding within 1-hour windows
- Wallet age computed from first block_time in transfer_index
"""

import sqlite3
import time
import logging
import json
import statistics
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

from src.utils.infra_mapping import build_excluded_set

logger = logging.getLogger(__name__)


class WalletClusteringEngine:
    """
    Dev farm detection and developer reputation scoring.

    Implements:
    1. Detection of dev farm wallets (3+ creators, 0.5-5 SOL transfers)
    2. Confidence scoring based on transfer patterns
    3. Burst detection for synchronized funding
    4. Developer reputation from rug history + token success
    """

    def __init__(self, db_path: str):
        """
        Initialize clustering engine.

        Args:
            db_path: Path to flex_complete_database.db
        """
        self.db_path = db_path
        self.min_creators = 3
        self.min_transfer_sol = 0.5
        self.max_transfer_sol = 10.0
        self.min_days_active = 2

    def _get_conn(self) -> sqlite3.Connection:
        """Get optimized SQLite connection for clustering operations."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=30000000")  # 30MB memory map
        conn.execute("PRAGMA query_only=FALSE")
        return conn

    def _ensure_tables(self) -> None:
        """Create wallet_clusters, dev_reputation, and cluster_detection_log if missing."""
        conn = self._get_conn()
        cursor = conn.cursor()

        # wallet_clusters table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wallet_clusters (
                cluster_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                funder_wallet       TEXT NOT NULL UNIQUE,
                creator_addresses   TEXT NOT NULL,
                creator_count       INTEGER NOT NULL,
                confidence_score    REAL DEFAULT 0,
                avg_transfer_sol    REAL DEFAULT 0,
                transfer_stddev     REAL DEFAULT 0,
                days_active         INTEGER DEFAULT 0,
                first_transfer_ts   INTEGER,
                last_transfer_ts    INTEGER,
                has_burst           BOOLEAN DEFAULT 0,
                wallet_age_days     REAL DEFAULT 0,
                detected_at         REAL NOT NULL,
                updated_at          REAL NOT NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_wallet_clusters_confidence
            ON wallet_clusters(confidence_score DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_wallet_clusters_funder
            ON wallet_clusters(funder_wallet)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_wallet_clusters_detected
            ON wallet_clusters(detected_at DESC)
        """)

        # dev_reputation table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dev_reputation (
                wallet              TEXT PRIMARY KEY,
                tokens_launched     INTEGER DEFAULT 0,
                tokens_rugged       INTEGER DEFAULT 0,
                tokens_above_2x     INTEGER DEFAULT 0,
                tokens_above_10x    INTEGER DEFAULT 0,
                rug_rate            REAL DEFAULT 0,
                success_rate        REAL DEFAULT 0,
                reputation_score    REAL DEFAULT 50,
                first_seen_ts       INTEGER,
                wallet_age_days     REAL DEFAULT 0,
                cluster_id          INTEGER,
                last_updated        REAL NOT NULL,
                FOREIGN KEY(cluster_id) REFERENCES wallet_clusters(cluster_id)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_dev_reputation_score
            ON dev_reputation(reputation_score ASC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_dev_reputation_rug
            ON dev_reputation(rug_rate DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_dev_reputation_cluster
            ON dev_reputation(cluster_id)
        """)

        # cluster_detection_log table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cluster_detection_log (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_at         REAL NOT NULL,
                clusters_found      INTEGER DEFAULT 0,
                reputations_updated INTEGER DEFAULT 0,
                duration_ms         REAL DEFAULT 0,
                status              TEXT DEFAULT 'success',
                error_message       TEXT
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_detection_log_time
            ON cluster_detection_log(detected_at DESC)
        """)

        conn.commit()
        conn.close()

    def detect_and_store(self) -> Dict:
        """
        Main entry point: detect dev farms and update reputation scores.

        Returns:
            {
                'clusters_found': int,
                'reputations_updated': int,
                'status': 'success' | 'error',
                'duration_ms': float,
                'message': str
            }
        """
        detect_start = time.time()
        result = {
            'clusters_found': 0,
            'reputations_updated': 0,
            'status': 'pending',
            'duration_ms': 0.0,
            'message': ''
        }

        try:
            # Ensure tables exist
            self._ensure_tables()

            # Step 1: Detect dev farms
            farms = self._detect_dev_farms()
            logger.info(f"[CLUSTERING] Found {len(farms)} potential dev farms")

            # Step 2: Score and detect bursts
            scored_farms = []
            for farm in farms:
                farm['confidence_score'] = self._score_cluster(farm)
                farm['has_burst'] = self._detect_bursts(farm['funder_wallet'])
                scored_farms.append(farm)

            # Step 3: Store clusters
            stored = self._store_clusters(scored_farms)
            result['clusters_found'] = stored

            # Step 4: Update dev reputation
            updated = self._update_dev_reputation()
            result['reputations_updated'] = updated

            result['status'] = 'success'
            result['message'] = f"Detected {stored} clusters, updated {updated} reputations"
            logger.info(f"[CLUSTERING] {result['message']}")

        except Exception as e:
            result['status'] = 'error'
            result['message'] = f"Clustering failed: {str(e)}"
            logger.error(f"[CLUSTERING] {result['message']}", exc_info=True)

        finally:
            result['duration_ms'] = (time.time() - detect_start) * 1000
            self._log_run(result)

        return result

    def _detect_dev_farms(self) -> List[Dict]:
        """
        Detect wallets funding 3+ creators with 0.5-10 SOL amounts.

        Returns:
            List of farm dicts with funder_wallet, creator_addresses, transfers, etc.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Build unified exclusion set: static registry (CEX + infra) + live cex_wallets table
        excluded = build_excluded_set(conn)

        # Main detection query (note: no STDDEV function in SQLite, compute in Python)
        query = """
            SELECT
                source AS funder,
                COUNT(DISTINCT destination) AS creators,
                COUNT(*) AS transfers,
                ROUND(AVG(amount_sol), 3) AS avg_amount,
                COUNT(DISTINCT DATE(datetime(block_time, 'unixepoch'))) AS days_active,
                (MAX(block_time) - MIN(block_time)) / 86400.0 AS span_days,
                MIN(block_time) AS first_ts,
                MAX(block_time) AS last_ts,
                GROUP_CONCAT(DISTINCT destination) AS creator_list,
                GROUP_CONCAT(amount_sol) AS amounts
            FROM transfer_index
            WHERE amount_sol BETWEEN ? AND ?
              AND is_valid = 1
            GROUP BY source
            HAVING creators >= ?
              AND days_active >= ?
            ORDER BY creators DESC
        """

        cursor.execute(
            query,
            (self.min_transfer_sol, self.max_transfer_sol, self.min_creators, self.min_days_active)
        )

        rows = cursor.fetchall()
        conn.close()

        farms = []
        skipped_excluded = 0
        for row in rows:
            funder, creators, transfers, avg_amt, days_active, span_days, first_ts, last_ts, creator_list, amounts_str = row

            # Skip CEX / infra wallets using unified exclusion set
            if funder in excluded:
                skipped_excluded += 1
                continue

            # Compute stddev in Python
            stddev_amt = 0.0
            if amounts_str:
                try:
                    amounts = [float(x) for x in amounts_str.split(',')]
                    if len(amounts) > 1:
                        mean = sum(amounts) / len(amounts)
                        variance = sum((x - mean) ** 2 for x in amounts) / len(amounts)
                        stddev_amt = variance ** 0.5
                except:
                    stddev_amt = 0.0

            wallet_age = self._compute_wallet_age(funder)

            farms.append({
                'funder_wallet': funder,
                'creator_addresses': creator_list.split(',') if creator_list else [],
                'creator_count': creators,
                'transfers': transfers,
                'avg_transfer_sol': avg_amt or 0.0,
                'transfer_stddev': round(stddev_amt, 3),
                'days_active': days_active,
                'span_days': span_days,
                'first_transfer_ts': int(first_ts) if first_ts else None,
                'last_transfer_ts': int(last_ts) if last_ts else None,
                'wallet_age_days': wallet_age
            })

        logger.info(f"[CLUSTERING_DETECT] Found {len(farms)} farms (excluded {skipped_excluded} CEX/infra wallets)")
        return farms

    def _detect_bursts(self, funder_wallet: str) -> bool:
        """
        Check if wallet funded 2+ creators in the same 1-hour window.

        Args:
            funder_wallet: The wallet to check

        Returns:
            True if burst detected, False otherwise
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Group by 1-hour window and check for 2+ creators
        cursor.execute("""
            SELECT COUNT(DISTINCT destination) as creators_in_hour
            FROM transfer_index
            WHERE source = ?
              AND is_valid = 1
            GROUP BY (block_time / 3600) * 3600
            HAVING creators_in_hour >= 2
            LIMIT 1
        """, (funder_wallet,))

        result = cursor.fetchone()
        conn.close()

        return result is not None

    def _compute_wallet_age(self, wallet: str) -> float:
        """
        Compute wallet age in days from first transfer in transfer_index.

        Args:
            wallet: Wallet address

        Returns:
            Age in days (0 if never transferred)
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT MIN(block_time) FROM transfer_index WHERE source = ?",
            (wallet,)
        )
        row = cursor.fetchone()
        conn.close()

        if row and row[0]:
            return (time.time() - row[0]) / 86400.0

        return 0.0

    def _score_cluster(self, farm: Dict) -> float:
        """
        Compute confidence score (0-100) from farm characteristics.

        Components:
        - Creators (0-25): >=10→25, >=5→18, >=3→10
        - Consistency (0-25): stddev<1→25, <2→18, <3→10
        - Duration (0-25): span>=7d→25, >=3d→18, >=1d→10
        - Activity (0-25): transfers>=20→25, >=10→18, >=5→10

        Args:
            farm: Farm dict with creator_count, transfer_stddev, span_days, transfers

        Returns:
            Confidence score 0-100
        """
        score = 0.0

        # Creator count (0-25)
        creators = farm['creator_count']
        if creators >= 10:
            score += 25
        elif creators >= 5:
            score += 18
        elif creators >= 3:
            score += 10

        # Consistency / low stddev (0-25)
        stddev = farm['transfer_stddev']
        if stddev == 0 or stddev < 1:
            score += 25
        elif stddev < 2:
            score += 18
        elif stddev < 3:
            score += 10

        # Duration / active span (0-25)
        span_days = farm['span_days']
        if span_days >= 7:
            score += 25
        elif span_days >= 3:
            score += 18
        elif span_days >= 1:
            score += 10

        # Activity / transfer count (0-25)
        transfers = farm['transfers']
        if transfers >= 20:
            score += 25
        elif transfers >= 10:
            score += 18
        elif transfers >= 5:
            score += 10

        return min(100.0, max(0.0, score))

    def _store_clusters(self, farms: List[Dict]) -> int:
        """
        Store or update wallet clusters in database.

        Args:
            farms: List of scored farm dicts

        Returns:
            Number of clusters stored/updated
        """
        if not farms:
            return 0

        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()

        inserted = 0
        for farm in farms:
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO wallet_clusters (
                        funder_wallet,
                        creator_addresses,
                        creator_count,
                        confidence_score,
                        avg_transfer_sol,
                        transfer_stddev,
                        days_active,
                        first_transfer_ts,
                        last_transfer_ts,
                        has_burst,
                        wallet_age_days,
                        detected_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    farm['funder_wallet'],
                    json.dumps(farm['creator_addresses']),
                    farm['creator_count'],
                    farm['confidence_score'],
                    farm['avg_transfer_sol'],
                    farm['transfer_stddev'],
                    farm['days_active'],
                    farm['first_transfer_ts'],
                    farm['last_transfer_ts'],
                    1 if farm.get('has_burst') else 0,
                    farm['wallet_age_days'],
                    now,
                    now
                ))
                inserted += 1
            except Exception as e:
                logger.error(f"[CLUSTERING_STORE] Error storing {farm['funder_wallet']}: {e}")

        conn.commit()
        conn.close()

        logger.info(f"[CLUSTERING_STORE] Stored {inserted} clusters")
        return inserted

    def _update_dev_reputation(self) -> int:
        """
        Update dev_reputation table merging rug history + token success.

        For each creator in wallet_clusters:
        1. Get tokens_rugged from creator_blocklist (if exists)
        2. Get tokens_launched, above_2x, above_10x from token_analysis
        3. Compute rug_rate and success_rate
        4. Calculate reputation_score with formula

        Returns:
            Number of reputation records updated
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()

        # Get all unique creators from wallet_clusters
        cursor.execute("""
            SELECT DISTINCT json_each.value
            FROM wallet_clusters,
            json_each(wallet_clusters.creator_addresses)
        """)

        creators = [row[0] for row in cursor.fetchall()]
        logger.info(f"[CLUSTERING_REPUTATION] Processing {len(creators)} creators")

        updated = 0

        for creator in creators:
            try:
                # Get rug data (if creator_blocklist exists)
                rug_count = 0
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='creator_blocklist'"
                )
                if cursor.fetchone():
                    cursor.execute(
                        "SELECT rug_count FROM creator_blocklist WHERE wallet = ?",
                        (creator,)
                    )
                    rug_row = cursor.fetchone()
                    if rug_row:
                        rug_count = rug_row[0] or 0

                # Get success data from token_analysis
                tokens_launched = 0
                tokens_above_2x = 0
                tokens_above_10x = 0

                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='token_analysis'"
                )
                if cursor.fetchone():
                    # This is approximate — looks for tokens where creator is in earliest_tx_creator
                    # In real implementation, might need creator funding detection
                    cursor.execute("""
                        SELECT COUNT(*) as launched,
                               SUM(CASE WHEN price_highest >= price_current * 2 THEN 1 ELSE 0 END) as above_2x,
                               SUM(CASE WHEN market_cap_highest >= 1000000 THEN 1 ELSE 0 END) as above_10x
                        FROM token_analysis
                        WHERE earliest_tx_creator = ?
                    """, (creator,))

                    ta_row = cursor.fetchone()
                    if ta_row:
                        tokens_launched = ta_row[0] or 0
                        tokens_above_2x = ta_row[1] or 0
                        tokens_above_10x = ta_row[2] or 0

                # Compute rates (null-safe)
                if tokens_launched > 0:
                    rug_rate = rug_count / tokens_launched
                    success_rate = tokens_above_2x / tokens_launched
                else:
                    rug_rate = 0.0
                    success_rate = 0.0

                # Check if in cluster
                cursor.execute(
                    "SELECT cluster_id FROM wallet_clusters WHERE json_extract(creator_addresses, '$[*]') LIKE ?",
                    (f'%{creator}%',)
                )
                cluster_row = cursor.fetchone()
                cluster_id = cluster_row[0] if cluster_row else None

                # Compute reputation score
                reputation_score = 50.0  # baseline
                reputation_score += (success_rate * 30.0)  # +30 max for success
                reputation_score -= (rug_rate * 50.0)  # -50 max for rugs
                if cluster_id:
                    reputation_score -= 10.0  # -10 if in dev farm

                # Bonus for established wallet (90+ days)
                wallet_age = self._compute_wallet_age(creator)
                if wallet_age > 90:
                    reputation_score += 10.0

                reputation_score = max(0.0, min(100.0, reputation_score))

                # Get first_seen_ts
                cursor.execute(
                    "SELECT MIN(block_time) FROM transfer_index WHERE destination = ?",
                    (creator,)
                )
                first_row = cursor.fetchone()
                first_seen_ts = int(first_row[0]) if first_row and first_row[0] else None

                # Insert or replace
                cursor.execute("""
                    INSERT OR REPLACE INTO dev_reputation (
                        wallet,
                        tokens_launched,
                        tokens_rugged,
                        tokens_above_2x,
                        tokens_above_10x,
                        rug_rate,
                        success_rate,
                        reputation_score,
                        first_seen_ts,
                        wallet_age_days,
                        cluster_id,
                        last_updated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    creator,
                    tokens_launched,
                    rug_count,
                    tokens_above_2x,
                    tokens_above_10x,
                    rug_rate,
                    success_rate,
                    reputation_score,
                    first_seen_ts,
                    wallet_age,
                    cluster_id,
                    now
                ))
                updated += 1

            except Exception as e:
                logger.error(f"[CLUSTERING_REPUTATION] Error for {creator}: {e}")

        conn.commit()
        conn.close()

        logger.info(f"[CLUSTERING_REPUTATION] Updated {updated} reputation records")
        return updated

    def _log_run(self, result: Dict) -> None:
        """Log detection run to cluster_detection_log."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO cluster_detection_log (
                    detected_at,
                    clusters_found,
                    reputations_updated,
                    duration_ms,
                    status,
                    error_message
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                time.time(),
                result['clusters_found'],
                result['reputations_updated'],
                result['duration_ms'],
                result['status'],
                result.get('message', '')
            ))

            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[CLUSTERING_LOG] Failed to log run: {e}")
