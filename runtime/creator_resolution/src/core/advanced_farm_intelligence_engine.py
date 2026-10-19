"""
FLEX Phase 4: Advanced Dev Farm Intelligence Engine

Ecosystem-level detection with:
1. Dev farm ecosystem detection (funders sharing creators)
2. Launch wave detection (pump.fun time-windowed launches)
3. Member importance scoring (leader/hub/follower roles)
4. Ecosystem evolution tracking

This module extends Phase 3.3+ with multi-hop coordination detection.
"""

import sqlite3
import time
import logging
import json
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class AdvancedFarmIntelligenceEngine:
    """
    Phase 4: Ecosystem-level dev farm detection.

    Detects:
    - Dev farm ecosystems (funders sharing creators)
    - Launch waves (pump.fun coordinated launches)
    - Member network topology
    - Ecosystem evolution and growth patterns
    """

    def __init__(self, db_path: str):
        """Initialize Phase 4 engine."""
        self.db_path = db_path
        self.min_shared_creators = 2
        self.min_creators_per_wave = 4
        self.min_funders_per_wave = 2
        self.wave_window_hours = 1
        self.pumpfun_amount_range = (0.1, 5.0)

    def _get_conn(self) -> sqlite3.Connection:
        """Get WAL-mode SQLite connection."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA mmap_size=30000000")
        return conn

    def _ensure_tables(self) -> None:
        """Create Phase 4 tables if missing."""
        conn = self._get_conn()
        cursor = conn.cursor()

        # dev_farm_ecosystems
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dev_farm_ecosystems (
                ecosystem_id INTEGER PRIMARY KEY AUTOINCREMENT,
                funder_1 TEXT NOT NULL,
                funder_2 TEXT NOT NULL,
                shared_creators INTEGER DEFAULT 0,
                shared_transfers INTEGER DEFAULT 0,
                avg_amount_f1 REAL DEFAULT 0,
                avg_amount_f2 REAL DEFAULT 0,
                earliest_funding INTEGER,
                latest_funding INTEGER,
                coordination_span_days REAL DEFAULT 0,
                creators_list TEXT,
                coordination_score REAL DEFAULT 0,
                ecosystem_size INTEGER DEFAULT 0,
                is_active_ecosystem BOOLEAN DEFAULT 1,
                detected_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(funder_1, funder_2)
            )
        """)

        # launch_waves
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS launch_waves (
                wave_id INTEGER PRIMARY KEY AUTOINCREMENT,
                wave_hour INTEGER NOT NULL UNIQUE,
                wave_start_ts INTEGER,
                wave_end_ts INTEGER,
                funder_count INTEGER DEFAULT 0,
                creator_count INTEGER DEFAULT 0,
                transfer_count INTEGER DEFAULT 0,
                avg_amount REAL DEFAULT 0,
                min_amount REAL DEFAULT 0,
                max_amount REAL DEFAULT 0,
                amount_stddev REAL DEFAULT 0,
                funders_list TEXT,
                creators_list TEXT,
                wave_intensity REAL DEFAULT 0,
                coordination_signal REAL DEFAULT 0,
                pump_fun_confidence REAL DEFAULT 0,
                is_pump_fun_wave BOOLEAN DEFAULT 0,
                is_verified_launch BOOLEAN DEFAULT 0,
                detected_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)

        # ecosystem_member_tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ecosystem_member_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ecosystem_id INTEGER NOT NULL,
                member_address TEXT NOT NULL,
                member_type TEXT NOT NULL,
                connections_in_ecosystem INTEGER DEFAULT 0,
                transfer_count_in_ecosystem INTEGER DEFAULT 0,
                avg_amount_in_ecosystem REAL DEFAULT 0,
                first_activity_ts INTEGER,
                last_activity_ts INTEGER,
                active_days REAL DEFAULT 0,
                is_ecosystem_leader BOOLEAN DEFAULT 0,
                is_ecosystem_hub BOOLEAN DEFAULT 0,
                ecosystem_importance_score REAL DEFAULT 0,
                detected_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(ecosystem_id) REFERENCES dev_farm_ecosystems(ecosystem_id),
                UNIQUE(ecosystem_id, member_address)
            )
        """)

        # launch_wave_creators
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS launch_wave_creators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wave_id INTEGER NOT NULL,
                creator_wallet TEXT NOT NULL,
                funder_count_in_wave INTEGER DEFAULT 0,
                simultaneous_funders INTEGER DEFAULT 0,
                wave_launch_probability REAL DEFAULT 0,
                predicted_launch_ts INTEGER,
                expected_token_mint TEXT,
                same_hour_funders_count INTEGER DEFAULT 0,
                sequential_funding BOOLEAN DEFAULT 0,
                token_launched BOOLEAN DEFAULT 0,
                actual_launch_ts INTEGER,
                prediction_accuracy REAL,
                detected_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(wave_id) REFERENCES launch_waves(wave_id),
                UNIQUE(wave_id, creator_wallet)
            )
        """)

        # ecosystem_evolution_log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ecosystem_evolution_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ecosystem_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_details TEXT,
                funder_count_at_event INTEGER,
                creator_count_at_event INTEGER,
                coordination_score_at_event REAL,
                event_ts REAL NOT NULL,
                FOREIGN KEY(ecosystem_id) REFERENCES dev_farm_ecosystems(ecosystem_id)
            )
        """)

        # Create indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ecosystems_coordination_score
            ON dev_farm_ecosystems(coordination_score DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ecosystems_shared_creators
            ON dev_farm_ecosystems(shared_creators DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_waves_pump_fun_confidence
            ON launch_waves(pump_fun_confidence DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_waves_creator_count
            ON launch_waves(creator_count DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ecosystem_members_importance
            ON ecosystem_member_tracking(ecosystem_importance_score DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_wave_creators_probability
            ON launch_wave_creators(wave_launch_probability DESC)
        """)

        conn.commit()
        conn.close()

    def detect_and_store(self) -> Dict:
        """
        Main entry point: Detect ecosystems, launch waves, and update watchlist.

        Returns: {
            'status': 'success'|'error',
            'ecosystems_detected': int,
            'launch_waves_detected': int,
            'members_analyzed': int,
            'creators_linked': int,
            'watchlist_enhanced': int,
            'duration_ms': float
        }
        """
        start_time = time.time()
        self._ensure_tables()

        try:
            # Step 1: Ecosystem detection
            ecosystems = self._detect_ecosystems()
            ecosystems_stored = self._store_ecosystems(ecosystems)

            # Step 2: Launch wave detection
            launch_waves = self._detect_launch_waves()
            waves_stored = self._store_launch_waves(launch_waves)

            # Step 3: Ecosystem member analysis
            members_analyzed = self._analyze_ecosystem_members()

            # Step 4: Link creators to waves
            creators_linked = self._link_creators_to_waves()

            # Step 5: Update launch watchlist
            watchlist_enhanced = self._enhance_launch_watchlist_with_ecosystems()

            duration_ms = (time.time() - start_time) * 1000

            result = {
                'status': 'success',
                'ecosystems_detected': ecosystems_stored,
                'launch_waves_detected': waves_stored,
                'members_analyzed': members_analyzed,
                'creators_linked': creators_linked,
                'watchlist_enhanced': watchlist_enhanced,
                'duration_ms': duration_ms,
                'message': f'Phase 4: {ecosystems_stored} ecosystems, {waves_stored} waves, {watchlist_enhanced} watchlist updates'
            }

            logger.info(result['message'])
            return result

        except Exception as e:
            logger.error(f"Phase 4 detection failed: {e}", exc_info=True)
            return {
                'status': 'error',
                'message': str(e),
                'duration_ms': (time.time() - start_time) * 1000
            }

    # ========================================================================
    # ECOSYSTEM DETECTION
    # ========================================================================

    def _detect_ecosystems(self) -> List[Dict]:
        """
        Detect dev farm ecosystems (funders sharing creators).
        Query 1.3: Self-join on transfer_index to find coordinated funders.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT
                    f1.source AS funder_1,
                    f2.source AS funder_2,
                    COUNT(DISTINCT f1.destination) AS shared_creators,
                    COUNT(*) AS shared_transfers,
                    ROUND(AVG(f1.amount_sol), 3) AS avg_amount_f1,
                    ROUND(AVG(f2.amount_sol), 3) AS avg_amount_f2,
                    MIN(LEAST(f1.block_time, f2.block_time)) AS earliest,
                    MAX(GREATEST(f1.block_time, f2.block_time)) AS latest,
                    GROUP_CONCAT(DISTINCT f1.destination) AS creators
                FROM transfer_index f1
                INNER JOIN transfer_index f2 ON f1.destination = f2.destination
                WHERE f1.source < f2.source
                  AND f1.amount_sol BETWEEN 0.5 AND 10.0
                  AND f2.amount_sol BETWEEN 0.5 AND 10.0
                  AND f1.is_valid = 1
                  AND f2.is_valid = 1
                  AND f1.source NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
                  AND f2.source NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
                GROUP BY f1.source, f2.source
                HAVING shared_creators >= ?
                ORDER BY shared_creators DESC
            """, (self.min_shared_creators,))

            ecosystems = []
            for row in cursor.fetchall():
                funder_1, funder_2, shared_creators, shared_transfers, avg_f1, avg_f2, earliest, latest, creators = row

                span_days = (latest - earliest) / 86400.0 if latest and earliest else 0
                ecosystem_size = len(creators.split(',')) if creators else 0

                # Score coordination
                score_result = self._score_ecosystem_coordination(
                    shared_creators, shared_transfers, span_days, avg_f1, avg_f2, ecosystem_size
                )

                ecosystems.append({
                    'funder_1': funder_1,
                    'funder_2': funder_2,
                    'shared_creators': shared_creators,
                    'shared_transfers': shared_transfers,
                    'avg_amount_f1': avg_f1,
                    'avg_amount_f2': avg_f2,
                    'earliest_funding': earliest,
                    'latest_funding': latest,
                    'coordination_span_days': span_days,
                    'creators_list': creators,
                    'coordination_score': score_result['coordination_score'],
                    'ecosystem_size': ecosystem_size,
                    'signals': score_result['signals']
                })

            logger.info(f"Detected {len(ecosystems)} dev farm ecosystems")
            return ecosystems

        except Exception as e:
            logger.error(f"Ecosystem detection failed: {e}")
            return []
        finally:
            conn.close()

    def _score_ecosystem_coordination(
        self,
        shared_creators: int,
        shared_transfers: int,
        coordination_span_days: float,
        avg_amount_f1: float,
        avg_amount_f2: float,
        ecosystem_size: int
    ) -> Dict:
        """Score ecosystem coordination (0-100)."""
        scores = {}
        signals = []

        # Factor 1: Shared creator count (0-30)
        if shared_creators >= 4:
            scores['shared_creators'] = 30
            signals.append(f"4+ shared creators ({shared_creators})")
        elif shared_creators >= 3:
            scores['shared_creators'] = 18
            signals.append(f"3 shared creators")
        elif shared_creators >= 2:
            scores['shared_creators'] = 10
            signals.append(f"2 shared creators")
        else:
            scores['shared_creators'] = 0

        # Factor 2: Amount consistency (0-25)
        amount_diff = abs(avg_amount_f1 - avg_amount_f2)
        if amount_diff < 0.5:
            scores['consistency'] = 25
            signals.append(f"Highly consistent (diff: {amount_diff:.2f})")
        elif amount_diff < 1.0:
            scores['consistency'] = 18
            signals.append(f"Consistent (diff: {amount_diff:.2f})")
        elif amount_diff < 2.0:
            scores['consistency'] = 10
            signals.append(f"Moderate consistency")
        else:
            scores['consistency'] = 0

        # Factor 3: Timing concentration (0-25)
        if coordination_span_days <= 1:
            scores['timing'] = 25
            signals.append("Simultaneous funding (<24h)")
        elif coordination_span_days <= 3:
            scores['timing'] = 18
            signals.append(f"Rapid ({coordination_span_days:.1f} days)")
        elif coordination_span_days <= 7:
            scores['timing'] = 10
            signals.append(f"Coordinated ({coordination_span_days:.1f} days)")
        else:
            scores['timing'] = 0

        # Factor 4: Ecosystem scope (0-20)
        if ecosystem_size >= 5:
            scores['scope'] = 20
            signals.append(f"Large ecosystem ({ecosystem_size})")
        elif ecosystem_size >= 4:
            scores['scope'] = 15
            signals.append(f"Medium ecosystem ({ecosystem_size})")
        elif ecosystem_size >= 3:
            scores['scope'] = 10
            signals.append(f"Small ecosystem ({ecosystem_size})")
        else:
            scores['scope'] = 0

        total = sum(scores.values())

        return {
            'coordination_score': min(total, 100),
            'is_coordinated': total >= 50,
            'signals': signals,
            'scores': scores
        }

    def _store_ecosystems(self, ecosystems: List[Dict]) -> int:
        """Store ecosystems to database."""
        if not ecosystems:
            return 0

        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()
        stored = 0

        try:
            for ecosystem in ecosystems:
                cursor.execute("""
                    INSERT OR REPLACE INTO dev_farm_ecosystems (
                        funder_1, funder_2, shared_creators, shared_transfers,
                        avg_amount_f1, avg_amount_f2, earliest_funding, latest_funding,
                        coordination_span_days, creators_list, coordination_score,
                        ecosystem_size, is_active_ecosystem, detected_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ecosystem['funder_1'],
                    ecosystem['funder_2'],
                    ecosystem['shared_creators'],
                    ecosystem['shared_transfers'],
                    ecosystem['avg_amount_f1'],
                    ecosystem['avg_amount_f2'],
                    ecosystem['earliest_funding'],
                    ecosystem['latest_funding'],
                    ecosystem['coordination_span_days'],
                    ecosystem['creators_list'],
                    ecosystem['coordination_score'],
                    ecosystem['ecosystem_size'],
                    1,  # is_active
                    now,
                    now
                ))
                stored += 1

            conn.commit()
            logger.info(f"Stored {stored} ecosystems")
            return stored

        except Exception as e:
            logger.error(f"Ecosystem storage failed: {e}")
            conn.rollback()
            return 0
        finally:
            conn.close()

    # ========================================================================
    # LAUNCH WAVE DETECTION
    # ========================================================================

    def _detect_launch_waves(self) -> List[Dict]:
        """
        Detect pump.fun launch waves (multiple creators in time window).
        Query 1.4: Group by hour, HAVING 4+ creators from 2+ funders.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT
                    CAST(block_time / 3600 AS INTEGER) * 3600 AS hour_window,
                    COUNT(DISTINCT source) AS funder_count,
                    COUNT(DISTINCT destination) AS creator_count,
                    COUNT(*) AS transfer_count,
                    ROUND(AVG(amount_sol), 3) AS avg_amount,
                    ROUND(MIN(amount_sol), 3) AS min_amount,
                    ROUND(MAX(amount_sol), 3) AS max_amount,
                    GROUP_CONCAT(DISTINCT source) AS funders,
                    GROUP_CONCAT(DISTINCT destination) AS creators,
                    GROUP_CONCAT(amount_sol) AS amounts_str
                FROM transfer_index
                WHERE amount_sol BETWEEN ? AND ?
                  AND is_valid = 1
                  AND source NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
                  AND destination NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
                GROUP BY hour_window
                HAVING creator_count >= ? AND funder_count >= ?
                ORDER BY hour_window DESC
            """, (
                self.pumpfun_amount_range[0],
                self.pumpfun_amount_range[1],
                self.min_creators_per_wave,
                self.min_funders_per_wave
            ))

            waves = []
            for row in cursor.fetchall():
                hour_window, funder_count, creator_count, transfer_count, avg_amount, min_amount, max_amount, funders, creators, amounts_str = row

                # Compute stddev
                amounts = [float(a) for a in amounts_str.split(',') if a]
                mean = sum(amounts) / len(amounts) if amounts else 0
                variance = sum((a - mean) ** 2 for a in amounts) / len(amounts) if amounts else 0
                stddev = variance ** 0.5

                # Score wave
                score_result = self._score_launch_wave_intensity(
                    creator_count, funder_count, transfer_count, 1.0, avg_amount, stddev
                )

                waves.append({
                    'wave_hour': hour_window,
                    'wave_start_ts': hour_window,
                    'wave_end_ts': hour_window + 3600,
                    'funder_count': funder_count,
                    'creator_count': creator_count,
                    'transfer_count': transfer_count,
                    'avg_amount': avg_amount,
                    'min_amount': min_amount,
                    'max_amount': max_amount,
                    'amount_stddev': stddev,
                    'funders_list': funders,
                    'creators_list': creators,
                    'wave_intensity': score_result['wave_intensity'],
                    'is_pump_fun_wave': score_result['is_launch_wave'],
                    'pump_fun_confidence': score_result['wave_intensity'],
                    'signals': score_result['signals']
                })

            logger.info(f"Detected {len(waves)} launch waves")
            return waves

        except Exception as e:
            logger.error(f"Launch wave detection failed: {e}")
            return []
        finally:
            conn.close()

    def _score_launch_wave_intensity(
        self,
        creator_count: int,
        funder_count: int,
        transfer_count: int,
        span_hours: float,
        avg_amount: float,
        stddev_amount: float
    ) -> Dict:
        """Score launch wave intensity (0-100)."""
        scores = {}
        signals = []

        # Factor 1: Creator concentration (0-30)
        creators_per_hour = creator_count / max(span_hours, 0.25)
        if creators_per_hour >= 8:
            scores['concentration'] = 30
            signals.append(f"Extreme concentration ({creators_per_hour:.0f}/h)")
        elif creators_per_hour >= 4:
            scores['concentration'] = 22
            signals.append(f"High concentration ({creators_per_hour:.0f}/h)")
        elif creators_per_hour >= 2:
            scores['concentration'] = 15
            signals.append(f"Moderate concentration")
        else:
            scores['concentration'] = 0

        # Factor 2: Multi-funder participation (0-25)
        if funder_count >= 4:
            scores['multi_funder'] = 25
            signals.append(f"4+ funders")
        elif funder_count >= 3:
            scores['multi_funder'] = 18
            signals.append(f"3 funders")
        elif funder_count >= 2:
            scores['multi_funder'] = 10
            signals.append(f"2 funders")
        else:
            scores['multi_funder'] = 0

        # Factor 3: Transfer density (0-20)
        transfers_per_creator = transfer_count / max(creator_count, 1)
        if transfers_per_creator >= 3:
            scores['density'] = 20
            signals.append(f"High density")
        elif transfers_per_creator >= 2:
            scores['density'] = 12
            signals.append(f"Moderate density")
        else:
            scores['density'] = 0

        # Factor 4: Amount uniformity (0-25)
        if stddev_amount <= 0.5:
            scores['uniformity'] = 25
            signals.append(f"Uniform amounts")
        elif stddev_amount <= 1.0:
            scores['uniformity'] = 18
            signals.append(f"Consistent amounts")
        elif stddev_amount <= 2.0:
            scores['uniformity'] = 10
            signals.append(f"Variable amounts")
        else:
            scores['uniformity'] = 0

        total = sum(scores.values())

        return {
            'wave_intensity': min(total, 100),
            'is_launch_wave': total >= 50,
            'signals': signals,
            'scores': scores
        }

    def _store_launch_waves(self, waves: List[Dict]) -> int:
        """Store launch waves to database."""
        if not waves:
            return 0

        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()
        stored = 0

        try:
            for wave in waves:
                cursor.execute("""
                    INSERT OR REPLACE INTO launch_waves (
                        wave_hour, wave_start_ts, wave_end_ts, funder_count,
                        creator_count, transfer_count, avg_amount, min_amount,
                        max_amount, amount_stddev, funders_list, creators_list,
                        wave_intensity, pump_fun_confidence, is_pump_fun_wave,
                        detected_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    wave['wave_hour'],
                    wave['wave_start_ts'],
                    wave['wave_end_ts'],
                    wave['funder_count'],
                    wave['creator_count'],
                    wave['transfer_count'],
                    wave['avg_amount'],
                    wave['min_amount'],
                    wave['max_amount'],
                    wave['amount_stddev'],
                    wave['funders_list'],
                    wave['creators_list'],
                    wave['wave_intensity'],
                    wave['pump_fun_confidence'],
                    1 if wave['is_pump_fun_wave'] else 0,
                    now,
                    now
                ))
                stored += 1

            conn.commit()
            logger.info(f"Stored {stored} launch waves")
            return stored

        except Exception as e:
            logger.error(f"Launch wave storage failed: {e}")
            conn.rollback()
            return 0
        finally:
            conn.close()

    # ========================================================================
    # MEMBER ANALYSIS & LINKING
    # ========================================================================

    def _analyze_ecosystem_members(self) -> int:
        """Analyze importance of each member in ecosystems."""
        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()
        analyzed = 0

        try:
            # Get all ecosystems
            cursor.execute("""
                SELECT ecosystem_id, funder_1, funder_2, creators_list
                FROM dev_farm_ecosystems WHERE is_active_ecosystem = 1
            """)

            for ecosystem_id, funder_1, funder_2, creators_list in cursor.fetchall():
                creators = creators_list.split(',') if creators_list else []

                # Analyze each funder
                for funder in [funder_1, funder_2]:
                    if not funder:
                        continue

                    # Count connections and transfers
                    cursor.execute("""
                        SELECT COUNT(DISTINCT destination), COUNT(*), ROUND(AVG(amount_sol), 3)
                        FROM transfer_index
                        WHERE source = ? AND destination IN ({})
                    """.format(','.join('?' * len(creators))), [funder] + creators)

                    connections, transfers, avg_amt = cursor.fetchone() or (0, 0, 0)

                    is_leader = funder == funder_1 or connections > 2

                    cursor.execute("""
                        INSERT OR REPLACE INTO ecosystem_member_tracking (
                            ecosystem_id, member_address, member_type, connections_in_ecosystem,
                            transfer_count_in_ecosystem, avg_amount_in_ecosystem,
                            is_ecosystem_leader, ecosystem_importance_score,
                            detected_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        ecosystem_id,
                        funder,
                        'funder',
                        connections,
                        transfers,
                        avg_amt,
                        is_leader,
                        min(connections * 20, 100),  # Simple importance score
                        now,
                        now
                    ))
                    analyzed += 1

                # Analyze each creator
                for creator in creators:
                    cursor.execute("""
                        SELECT COUNT(DISTINCT source), COUNT(*)
                        FROM transfer_index
                        WHERE destination = ? AND source IN (?, ?)
                    """, (creator, funder_1, funder_2))

                    funder_count, transfers = cursor.fetchone() or (0, 0)

                    is_hub = funder_count >= 2

                    cursor.execute("""
                        INSERT OR REPLACE INTO ecosystem_member_tracking (
                            ecosystem_id, member_address, member_type, connections_in_ecosystem,
                            transfer_count_in_ecosystem, is_ecosystem_hub,
                            ecosystem_importance_score, detected_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        ecosystem_id,
                        creator,
                        'creator',
                        funder_count,
                        transfers,
                        is_hub,
                        min(funder_count * 25, 100),
                        now,
                        now
                    ))
                    analyzed += 1

            conn.commit()
            logger.info(f"Analyzed {analyzed} ecosystem members")
            return analyzed

        except Exception as e:
            logger.error(f"Member analysis failed: {e}")
            conn.rollback()
            return 0
        finally:
            conn.close()

    def _link_creators_to_waves(self) -> int:
        """Link creators to launch waves they appear in."""
        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()
        linked = 0

        try:
            # Get all waves with creator lists
            cursor.execute("""
                SELECT wave_id, creators_list, pump_fun_confidence, funder_count
                FROM launch_waves
            """)

            for wave_id, creators_list, confidence, funder_count in cursor.fetchall():
                creators = creators_list.split(',') if creators_list else []

                for creator in creators:
                    creator = creator.strip()
                    if not creator:
                        continue

                    cursor.execute("""
                        INSERT OR REPLACE INTO launch_wave_creators (
                            wave_id, creator_wallet, funder_count_in_wave,
                            wave_launch_probability, detected_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        wave_id,
                        creator,
                        funder_count,
                        confidence,
                        now,
                        now
                    ))
                    linked += 1

            conn.commit()
            logger.info(f"Linked {linked} creators to waves")
            return linked

        except Exception as e:
            logger.error(f"Creator linking failed: {e}")
            conn.rollback()
            return 0
        finally:
            conn.close()

    def _enhance_launch_watchlist_with_ecosystems(self) -> int:
        """Enhance launch_watchlist with ecosystem signals."""
        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()
        enhanced = 0

        try:
            # Check if ecosystem_id column exists in launch_watchlist
            cursor.execute("PRAGMA table_info(launch_watchlist)")
            columns = [row[1] for row in cursor.fetchall()]

            if 'ecosystem_id' not in columns:
                logger.info("ecosystem_id column not in launch_watchlist (Phase 3.3+ only)")
                return 0

            # Update existing watchlist entries with ecosystem data
            cursor.execute("""
                UPDATE launch_watchlist
                SET ecosystem_id = (
                    SELECT ecosystem_id FROM dev_farm_ecosystems
                    WHERE creators_list LIKE '%' || launch_watchlist.creator_wallet || '%'
                    ORDER BY coordination_score DESC
                    LIMIT 1
                ),
                updated_at = ?
                WHERE ecosystem_id IS NULL
            """, (now,))

            enhanced = cursor.rowcount
            conn.commit()
            logger.info(f"Enhanced {enhanced} watchlist entries with ecosystem data")
            return enhanced

        except Exception as e:
            logger.error(f"Watchlist enhancement failed: {e}")
            conn.rollback()
            return 0
        finally:
            conn.close()
