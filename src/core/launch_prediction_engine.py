"""
FLEX Phase 3.3+ Launch Prediction Engine

Extends Phase 3.3 wallet clustering with:
1. Pump.fun dev farm detection (4+ creators, <48h)
2. Creator reuse detection (3+ funders per creator)
3. Launch probability prediction (5-factor model)
4. Daily pipeline integration

This module is designed to be called by cluster_detection.py after Phase 3.3
detection completes.
"""

import sqlite3
import time
import logging
import json
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class LaunchPredictionEngine:
    """
    Phase 3.3+ enhancement: Pump.fun detection, creator reuse, and launch prediction.
    """

    def __init__(self, db_path: str):
        """Initialize prediction engine."""
        self.db_path = db_path
        self.pumpfun_min_creators = 4
        self.pumpfun_max_hours = 48
        self.pumpfun_amount_range = (0.5, 5.0)
        self.reuse_min_funders = 3

    def _get_conn(self) -> sqlite3.Connection:
        """Get WAL-mode SQLite connection."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA mmap_size=30000000")
        return conn

    def _ensure_tables(self) -> None:
        """Create Phase 3.3+ tables if missing."""
        conn = self._get_conn()
        cursor = conn.cursor()

        # creator_reuse table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_reuse (
                creator_wallet TEXT PRIMARY KEY,
                funder_count INTEGER DEFAULT 0,
                transfer_count INTEGER DEFAULT 0,
                avg_funding_sol REAL DEFAULT 0,
                funder_list TEXT,
                first_funded_ts INTEGER,
                last_funded_ts INTEGER,
                active_days REAL DEFAULT 0,
                reuse_score REAL DEFAULT 0,
                is_pump_fun_target BOOLEAN DEFAULT 0,
                cluster_id INTEGER,
                detected_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(cluster_id) REFERENCES wallet_clusters(cluster_id)
            )
        """)

        # launch_watchlist table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS launch_watchlist (
                creator_wallet TEXT PRIMARY KEY,
                cluster_id INTEGER,
                primary_funder TEXT,
                reuse_score REAL DEFAULT 0,
                farm_confidence_score REAL DEFAULT 0,
                recency_score REAL DEFAULT 0,
                reputation_score REAL DEFAULT 0,
                launch_probability REAL DEFAULT 0,
                risk_level TEXT DEFAULT 'LOW',
                funder_count INTEGER DEFAULT 0,
                funding_days_active REAL DEFAULT 0,
                last_funding_ts INTEGER,
                expected_launch_day INTEGER DEFAULT 0,
                signal_count INTEGER DEFAULT 0,
                detected_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(cluster_id) REFERENCES wallet_clusters(cluster_id)
            )
        """)

        # launch_detection_history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS launch_detection_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_wallet TEXT NOT NULL,
                predicted_probability REAL NOT NULL,
                predicted_risk_level TEXT NOT NULL,
                predicted_launch_day INTEGER,
                token_mint TEXT,
                actual_launch_ts INTEGER,
                launch_detected BOOLEAN DEFAULT 0,
                days_to_actual_launch INTEGER,
                prediction_accuracy REAL,
                detected_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(creator_wallet) REFERENCES launch_watchlist(creator_wallet)
            )
        """)

        # Create indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_creator_reuse_funder_count
            ON creator_reuse(funder_count DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_launch_watchlist_probability
            ON launch_watchlist(launch_probability DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_launch_watchlist_risk
            ON launch_watchlist(risk_level)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_launch_history_creator
            ON launch_detection_history(creator_wallet)
        """)

        conn.commit()
        conn.close()

    def detect_and_store(self) -> Dict:
        """
        Main entry point: Detect pump.fun farms, creator reuse, and launch watchlist.

        Returns: {
            'status': 'success'|'error',
            'pumpfun_farms': int,
            'creator_reuses': int,
            'launch_watchlist': int,
            'duration_ms': float
        }
        """
        start_time = time.time()
        self._ensure_tables()

        try:
            # Detect pump.fun farms
            pumpfun_farms = self._detect_pumpfun_farms()
            pumpfun_stored = self._store_pumpfun_farms(pumpfun_farms)

            # Detect creator reuse
            creator_reuses = self._detect_creator_reuse()
            reuse_stored = self._store_creator_reuse(creator_reuses)

            # Update launch watchlist
            watchlist_updated = self._update_launch_watchlist()

            duration_ms = (time.time() - start_time) * 1000

            result = {
                'status': 'success',
                'pumpfun_farms': pumpfun_stored,
                'creator_reuses': reuse_stored,
                'launch_watchlist': watchlist_updated,
                'duration_ms': duration_ms,
                'message': f'Phase 3.3+ detection: {pumpfun_stored} pump.fun farms, {reuse_stored} reused creators, {watchlist_updated} watchlist entries'
            }

            logger.info(result['message'])
            return result

        except Exception as e:
            logger.error(f"Phase 3.3+ detection failed: {e}", exc_info=True)
            return {
                'status': 'error',
                'pumpfun_farms': 0,
                'creator_reuses': 0,
                'launch_watchlist': 0,
                'duration_ms': (time.time() - start_time) * 1000,
                'message': str(e)
            }

    # ========================================================================
    # PUMP.FUN DETECTION (Algorithm 3.1)
    # ========================================================================

    def _detect_pumpfun_farms(self) -> List[Dict]:
        """
        Detect pump.fun-style coordinated funding operations.

        Pattern: 4+ creators, 0.5-5 SOL, <48 hour window
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            # Query: Get potential pump.fun farms
            cursor.execute("""
                SELECT
                    source,
                    COUNT(DISTINCT destination) AS creator_count,
                    COUNT(*) AS transfer_count,
                    ROUND(AVG(amount_sol), 3) AS avg_amount,
                    ROUND(MIN(amount_sol), 3) AS min_amount,
                    ROUND(MAX(amount_sol), 3) AS max_amount,
                    MIN(block_time) AS first_ts,
                    MAX(block_time) AS last_ts,
                    GROUP_CONCAT(amount_sol) AS amounts_str,
                    GROUP_CONCAT(DISTINCT destination) AS creator_list
                FROM transfer_index
                WHERE amount_sol BETWEEN ? AND ?
                  AND is_valid = 1
                  AND source NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
                GROUP BY source
                HAVING creator_count >= ?
                  AND (last_ts - first_ts) < ?
                ORDER BY creator_count DESC
            """, (
                self.pumpfun_amount_range[0],
                self.pumpfun_amount_range[1],
                self.pumpfun_min_creators,
                self.pumpfun_max_hours * 3600  # Convert to seconds
            ))

            farms = []
            for row in cursor.fetchall():
                source, creator_count, transfer_count, avg_amount, min_amount, max_amount, first_ts, last_ts, amounts_str, creators = row

                # Compute standard deviation
                amounts = [float(a) for a in amounts_str.split(',') if a]
                mean = sum(amounts) / len(amounts) if amounts else 0
                variance = sum((a - mean) ** 2 for a in amounts) / len(amounts) if amounts else 0
                stddev = variance ** 0.5

                span_hours = (last_ts - first_ts) / 3600.0

                # Score the farm
                score_result = self._score_pumpfun_farm(
                    source, creator_count, transfer_count, span_hours, avg_amount, stddev
                )

                if score_result['is_pump_fun']:
                    farms.append({
                        'funder_wallet': source,
                        'creator_count': creator_count,
                        'transfer_count': transfer_count,
                        'avg_amount': avg_amount,
                        'min_amount': min_amount,
                        'max_amount': max_amount,
                        'stddev': round(stddev, 3),
                        'span_hours': round(span_hours, 1),
                        'first_ts': first_ts,
                        'last_ts': last_ts,
                        'creators': creators,
                        'confidence': score_result['confidence'],
                        'signals': score_result['signals']
                    })

            logger.info(f"Detected {len(farms)} pump.fun dev farms")
            return farms

        except Exception as e:
            logger.error(f"Pump.fun detection failed: {e}")
            return []
        finally:
            conn.close()

    def _score_pumpfun_farm(
        self,
        funder_wallet: str,
        creator_count: int,
        transfer_count: int,
        span_hours: float,
        avg_amount: float,
        amount_stddev: float
    ) -> Dict:
        """Score a potential pump.fun farm (0-100)."""
        scores = {}
        signals = []

        # Signal 1: Creator count (0-30)
        if creator_count >= 10:
            scores['creator_count'] = 30
            signals.append(f"Very large farm ({creator_count} creators)")
        elif creator_count >= 7:
            scores['creator_count'] = 20
            signals.append(f"Large farm ({creator_count} creators)")
        elif creator_count >= 4:
            scores['creator_count'] = 10
            signals.append(f"Medium farm ({creator_count} creators)")
        else:
            scores['creator_count'] = 0

        # Signal 2: Time window compression (0-25)
        if span_hours < 12:
            scores['time_window'] = 25
            signals.append(f"Compressed window ({span_hours:.1f}h)")
        elif span_hours < 24:
            scores['time_window'] = 18
            signals.append(f"Tight window ({span_hours:.1f}h)")
        elif span_hours < 48:
            scores['time_window'] = 10
            signals.append(f"Short window ({span_hours:.1f}h)")
        else:
            scores['time_window'] = 0

        # Signal 3: Amount consistency (0-20)
        if amount_stddev <= 0.5:
            scores['consistency'] = 20
            signals.append(f"Highly consistent amounts (σ={amount_stddev:.2f})")
        elif amount_stddev <= 1.0:
            scores['consistency'] = 15
            signals.append(f"Consistent amounts (σ={amount_stddev:.2f})")
        elif amount_stddev <= 2.0:
            scores['consistency'] = 10
            signals.append(f"Moderate consistency (σ={amount_stddev:.2f})")
        else:
            scores['consistency'] = 0

        # Signal 4: Activity density (0-25)
        transfers_per_creator = transfer_count / max(creator_count, 1)
        if transfers_per_creator >= 3:
            scores['activity'] = 25
            signals.append(f"High density ({transfers_per_creator:.1f} tx/creator)")
        elif transfers_per_creator >= 2:
            scores['activity'] = 18
            signals.append(f"Good density ({transfers_per_creator:.1f} tx/creator)")
        elif transfers_per_creator >= 1.5:
            scores['activity'] = 10
            signals.append(f"Moderate activity ({transfers_per_creator:.1f} tx/creator)")
        else:
            scores['activity'] = 0

        base_score = sum(scores.values())
        is_pump_fun = base_score >= 50 and creator_count >= self.pumpfun_min_creators

        return {
            'is_pump_fun': is_pump_fun,
            'confidence': min(base_score, 100),
            'signals': signals,
            'scores': scores
        }

    def _store_pumpfun_farms(self, farms: List[Dict]) -> int:
        """Mark creators in pump.fun farms."""
        if not farms:
            return 0

        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()
        stored = 0

        try:
            for farm in farms:
                # Mark each creator in this farm
                creator_list = farm['creators'].split(',') if farm['creators'] else []
                for creator in creator_list:
                    creator = creator.strip()
                    if creator:
                        # Update or create creator_reuse entry
                        cursor.execute("""
                            UPDATE creator_reuse
                            SET is_pump_fun_target = 1, updated_at = ?
                            WHERE creator_wallet = ?
                        """, (now, creator))

                        if cursor.rowcount == 0:
                            # Create minimal entry if doesn't exist
                            cursor.execute("""
                                INSERT OR IGNORE INTO creator_reuse (
                                    creator_wallet, funder_count, is_pump_fun_target,
                                    detected_at, updated_at
                                ) VALUES (?, 0, 1, ?, ?)
                            """, (creator, now, now))

                        if cursor.rowcount > 0:
                            stored += 1

            conn.commit()
            logger.info(f"Marked {stored} creators as pump.fun targets")
            return len(farms)  # Return number of farms detected

        except Exception as e:
            logger.error(f"Pump.fun storage failed: {e}")
            conn.rollback()
            return 0
        finally:
            conn.close()

    # ========================================================================
    # CREATOR REUSE DETECTION (Algorithm 3.2)
    # ========================================================================

    def _detect_creator_reuse(self) -> List[Dict]:
        """
        Detect creators funded by multiple wallets (coordination signal).
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT
                    destination,
                    COUNT(DISTINCT source) AS funder_count,
                    COUNT(*) AS transfer_count,
                    ROUND(AVG(amount_sol), 3) AS avg_amount,
                    MIN(block_time) AS first_ts,
                    MAX(block_time) AS last_ts,
                    GROUP_CONCAT(DISTINCT source) AS funder_list
                FROM transfer_index
                WHERE amount_sol BETWEEN ? AND ?
                  AND is_valid = 1
                  AND destination NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
                GROUP BY destination
                HAVING funder_count >= ?
                ORDER BY funder_count DESC
            """, (0.5, 10.0, self.reuse_min_funders))

            reuses = []
            for row in cursor.fetchall():
                creator, funder_count, transfer_count, avg_amount, first_ts, last_ts, funder_list = row

                active_days = (last_ts - first_ts) / 86400.0

                # Score the reuse
                reuse_result = self._score_creator_reuse(
                    creator, funder_count, transfer_count, active_days
                )

                reuses.append({
                    'creator_wallet': creator,
                    'funder_count': funder_count,
                    'transfer_count': transfer_count,
                    'avg_funding_sol': avg_amount,
                    'first_funded_ts': first_ts,
                    'last_funded_ts': last_ts,
                    'active_days': round(active_days, 1),
                    'funder_list': funder_list,
                    'reuse_score': reuse_result['reuse_score'],
                    'is_high_risk': reuse_result['is_high_risk'],
                    'expected_launch_day': reuse_result['expected_launch_day']
                })

            logger.info(f"Detected {len(reuses)} creators with multiple funders")
            return reuses

        except Exception as e:
            logger.error(f"Creator reuse detection failed: {e}")
            return []
        finally:
            conn.close()

    def _score_creator_reuse(
        self,
        creator_wallet: str,
        funder_count: int,
        transfer_count: int,
        active_days: float
    ) -> Dict:
        """Score creator based on reuse metrics (0-40)."""
        scores = {}
        signals = []

        # Factor 1: Funder diversity (0-20)
        if funder_count >= 7:
            scores['funder_diversity'] = 20
            signals.append(f"Highly coordinated ({funder_count} funders)")
        elif funder_count >= 5:
            scores['funder_diversity'] = 15
            signals.append(f"Well coordinated ({funder_count} funders)")
        elif funder_count >= 3:
            scores['funder_diversity'] = 10
            signals.append(f"Multiple funders ({funder_count})")
        else:
            scores['funder_diversity'] = 0

        # Factor 2: Funding frequency (0-15)
        transfers_per_day = transfer_count / max(active_days, 1)
        if transfers_per_day >= 5:
            scores['frequency'] = 15
            signals.append(f"Rapid funding ({transfers_per_day:.1f}/day)")
        elif transfers_per_day >= 2:
            scores['frequency'] = 10
            signals.append(f"Regular funding ({transfers_per_day:.1f}/day)")
        elif transfers_per_day >= 1:
            scores['frequency'] = 5
            signals.append(f"Periodic funding ({transfers_per_day:.1f}/day)")
        else:
            scores['frequency'] = 0

        reuse_score = sum(scores.values())
        is_high_risk = reuse_score >= 25 and funder_count >= 4

        # Estimate launch window
        if active_days <= 1:
            launch_window = '0-1 days'
            expected_launch_day = 1
        elif active_days <= 3:
            launch_window = '1-3 days'
            expected_launch_day = 2
        elif active_days <= 7:
            launch_window = '3-7 days'
            expected_launch_day = 4
        else:
            launch_window = '7+ days'
            expected_launch_day = 7

        return {
            'reuse_score': reuse_score,
            'is_high_risk': is_high_risk,
            'signals': signals,
            'expected_launch_window': launch_window,
            'expected_launch_day': expected_launch_day
        }

    def _store_creator_reuse(self, reuses: List[Dict]) -> int:
        """Store creator reuse metrics."""
        if not reuses:
            return 0

        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()
        stored = 0

        try:
            for reuse in reuses:
                # Find cluster_id if creator is in a cluster
                cluster_id = None
                cursor.execute("""
                    SELECT cluster_id FROM wallet_clusters
                    WHERE creator_addresses LIKE ?
                """, (f'%{reuse["creator_wallet"]}%',))
                row = cursor.fetchone()
                if row:
                    cluster_id = row[0]

                cursor.execute("""
                    INSERT OR REPLACE INTO creator_reuse (
                        creator_wallet, funder_count, transfer_count,
                        avg_funding_sol, funder_list, first_funded_ts,
                        last_funded_ts, active_days, reuse_score,
                        cluster_id, detected_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    reuse['creator_wallet'],
                    reuse['funder_count'],
                    reuse['transfer_count'],
                    reuse['avg_funding_sol'],
                    reuse['funder_list'],
                    reuse['first_funded_ts'],
                    reuse['last_funded_ts'],
                    reuse['active_days'],
                    reuse['reuse_score'],
                    cluster_id,
                    now,
                    now
                ))

                stored += 1

            conn.commit()
            logger.info(f"Stored {stored} creator reuse records")
            return stored

        except Exception as e:
            logger.error(f"Creator reuse storage failed: {e}")
            conn.rollback()
            return 0
        finally:
            conn.close()

    # ========================================================================
    # LAUNCH PREDICTION (Algorithm 3.3)
    # ========================================================================

    def _update_launch_watchlist(self) -> int:
        """
        Compute and store launch watchlist using multi-factor model.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()

        try:
            # Get all creators with reuse metrics
            cursor.execute("""
                SELECT
                    creator_wallet, funder_count, active_days,
                    cluster_id, last_funded_ts
                FROM creator_reuse
                WHERE funder_count >= ?
            """, (self.reuse_min_funders,))

            creators = cursor.fetchall()
            stored = 0

            for creator_row in creators:
                creator_wallet, funder_count, active_days, cluster_id, last_funded_ts = creator_row

                # Get cluster info
                cluster_info = None
                if cluster_id:
                    cursor.execute("""
                        SELECT confidence_score FROM wallet_clusters
                        WHERE cluster_id = ?
                    """, (cluster_id,))
                    row = cursor.fetchone()
                    if row:
                        cluster_info = {'confidence_score': row[0]}

                # Get reputation info
                reputation_info = None
                cursor.execute("""
                    SELECT reputation_score, wallet_age_days FROM dev_reputation
                    WHERE wallet = ?
                """, (creator_wallet,))
                row = cursor.fetchone()
                if row:
                    reputation_info = {'reputation_score': row[0], 'wallet_age_days': row[1]}

                # Compute launch probability
                reuse_info = {
                    'funder_count': funder_count,
                    'active_days': active_days,
                    'last_funded_ts': last_funded_ts
                }

                prob_result = self._compute_launch_probability(
                    creator_wallet, cluster_info, reuse_info, reputation_info
                )

                # Store in launch_watchlist
                cursor.execute("""
                    INSERT OR REPLACE INTO launch_watchlist (
                        creator_wallet, cluster_id,
                        reuse_score, farm_confidence_score, recency_score,
                        reputation_score, launch_probability, risk_level,
                        funder_count, funding_days_active, last_funding_ts,
                        expected_launch_day, signal_count,
                        detected_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    creator_wallet,
                    cluster_id,
                    prob_result['factor_breakdown'].get('creator_reuse', 0),
                    prob_result['factor_breakdown'].get('cluster_confidence', 0),
                    prob_result['factor_breakdown'].get('recent_activity', 0),
                    prob_result['factor_breakdown'].get('reputation', 0),
                    prob_result['launch_probability'],
                    prob_result['risk_level'],
                    funder_count,
                    active_days,
                    last_funded_ts,
                    prob_result['expected_launch_day'],
                    prob_result['signal_count'],
                    now,
                    now
                ))

                stored += 1

            conn.commit()
            logger.info(f"Updated {stored} launch watchlist entries")
            return stored

        except Exception as e:
            logger.error(f"Launch watchlist update failed: {e}")
            conn.rollback()
            return 0
        finally:
            conn.close()

    def _compute_launch_probability(
        self,
        creator_wallet: str,
        cluster_info: Optional[Dict],
        reuse_info: Dict,
        reputation_info: Optional[Dict]
    ) -> Dict:
        """
        Compute multi-factor launch probability (0-100).
        """
        factors = {}
        signals = []

        # Factor 1: Cluster Confidence (0-25)
        cluster_conf = cluster_info.get('confidence_score', 0) if cluster_info else 0
        if cluster_conf >= 80:
            factors['cluster_confidence'] = 25
            signals.append(f"High-confidence farm ({cluster_conf:.0f})")
        elif cluster_conf >= 60:
            factors['cluster_confidence'] = 18
            signals.append(f"Moderate farm confidence ({cluster_conf:.0f})")
        elif cluster_conf >= 40:
            factors['cluster_confidence'] = 10
            signals.append(f"Farm member ({cluster_conf:.0f})")
        else:
            factors['cluster_confidence'] = 0

        # Factor 2: Creator Reuse (0-25)
        funder_count = reuse_info.get('funder_count', 0)
        if funder_count >= 6:
            factors['creator_reuse'] = 25
            signals.append(f"Highly coordinated ({funder_count} funders)")
        elif funder_count >= 4:
            factors['creator_reuse'] = 18
            signals.append(f"Multiple funders ({funder_count})")
        elif funder_count >= 3:
            factors['creator_reuse'] = 10
            signals.append(f"Coordinated funding ({funder_count})")
        else:
            factors['creator_reuse'] = 0

        # Factor 3: Recent Funding Activity (0-20)
        active_days = reuse_info.get('active_days', 0)
        last_funded_ts = reuse_info.get('last_funded_ts', 0)
        hours_since_funding = (time.time() - last_funded_ts) / 3600.0 if last_funded_ts else float('inf')

        if hours_since_funding < 24 and active_days <= 3:
            factors['recent_activity'] = 20
            signals.append(f"Very recent funding ({hours_since_funding:.1f}h ago)")
        elif hours_since_funding < 72:
            factors['recent_activity'] = 15
            signals.append(f"Recent funding ({hours_since_funding:.1f}h ago)")
        elif hours_since_funding < 168:
            factors['recent_activity'] = 10
            signals.append(f"Recent activity ({hours_since_funding / 24:.1f} days ago)")
        else:
            factors['recent_activity'] = 0

        # Factor 4: Reputation Score (0-20)
        rep_score = reputation_info.get('reputation_score', 50) if reputation_info else 50
        if rep_score >= 70:
            factors['reputation'] = 20
            signals.append(f"Strong reputation ({rep_score:.0f})")
        elif rep_score >= 50:
            factors['reputation'] = 15
            signals.append(f"Neutral reputation ({rep_score:.0f})")
        elif rep_score >= 30:
            factors['reputation'] = 5
            signals.append(f"Weak reputation ({rep_score:.0f})")
        else:
            factors['reputation'] = 0
            signals.append(f"High risk reputation ({rep_score:.0f})")

        # Factor 5: Wallet Age (0-10)
        wallet_age_days = reputation_info.get('wallet_age_days', 0) if reputation_info else 0
        if wallet_age_days >= 90:
            factors['wallet_age'] = 10
            signals.append(f"Established wallet ({wallet_age_days:.0f}d)")
        elif wallet_age_days >= 30:
            factors['wallet_age'] = 5
            signals.append(f"Moderate age ({wallet_age_days:.0f}d)")
        else:
            factors['wallet_age'] = 0

        # Compute probability
        total_probability = sum(factors.values())
        signal_count = len([s for s in signals if s])

        # Determine risk level
        if total_probability >= 75:
            risk_level = 'CRITICAL'
        elif total_probability >= 60:
            risk_level = 'HIGH'
        elif total_probability >= 40:
            risk_level = 'MEDIUM'
        elif total_probability >= 20:
            risk_level = 'LOW'
        else:
            risk_level = 'MINIMAL'

        # Estimate launch day (1-7)
        if active_days <= 1:
            expected_launch_day = 1
        elif active_days <= 3:
            expected_launch_day = 2
        elif active_days <= 7:
            expected_launch_day = 4
        else:
            expected_launch_day = 7

        return {
            'launch_probability': total_probability,
            'risk_level': risk_level,
            'signal_count': signal_count,
            'expected_launch_day': expected_launch_day,
            'factor_breakdown': factors,
            'signals': signals
        }
