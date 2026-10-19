"""
FLEX Launch Wave Detection — Multi-Token Launch Pattern Recognition

Detects when developer organizations are preparing multiple token launches
simultaneously by analyzing:

1. new_creators_last_24h — Fresh team member additions
2. funding_burst_count — Concentrated funding activity
3. organization_momentum — Activity acceleration
4. operator_activity_spike — Lead operator engagement
5. creator_reuse_delta — Existing creator re-engagement

Wave Score Formula:
  wave_score = 0.30*new_creators + 0.25*bursts + 0.20*momentum
             + 0.15*operator_spike + 0.10*creator_reuse
  (0-100 scale)

High wave_score (>70) indicates simultaneous multi-launch preparation:
- Multiple creators funded simultaneously
- Rapid funding bursts (3+ in 24h)
- Activity acceleration
- Operator heavily engaged
- Existing creators mobilized

Integrates after launch_probability_engine in daily pipeline.
"""

import sqlite3
import json
import logging
import time
from typing import Dict, List, Tuple
from statistics import mean, stdev

logger = logging.getLogger(__name__)


class NewCreatorDetector:
    """Tracks newly added creators to organizations."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def detect_new_creators(self, org_id: int, cursor: sqlite3.Cursor) -> Dict:
        """
        Detect creators added in last 24h by analyzing transfer activity.

        Returns:
        {
            'new_creators_24h': int,
            'new_creators_addresses': [list],
            'first_funded_in_24h': int,
            'avg_funding_size': float,
            'signal_strength': float (0-100)
        }
        """
        # Get current creators from org
        cursor.execute("""
            SELECT creator_list FROM dev_organizations
            WHERE organization_id = ?
        """, (org_id,))

        org_row = cursor.fetchone()
        if not org_row:
            return {
                'new_creators_24h': 0,
                'new_creators_addresses': [],
                'first_funded_in_24h': 0,
                'avg_funding_size': 0,
                'signal_strength': 0
            }

        current_creators = set(json.loads(org_row[0]) if org_row[0] else [])
        now_ts = int(time.time())
        since_24h = now_ts - 86400

        # Find creators who received first transfer in last 24h
        cursor.execute(f"""
            SELECT DISTINCT destination FROM transfer_index
            WHERE destination IN ({','.join('?' * len(current_creators)) if current_creators else 'NULL'})
              AND block_time >= ?
            ORDER BY block_time ASC
        """, list(current_creators) + [since_24h] if current_creators else [])

        new_creators = [row[0] for row in cursor.fetchall()]

        # Get average funding size for new creators
        if new_creators:
            ph = ','.join('?' * len(new_creators))
            cursor.execute(f"""
                SELECT COUNT(*), COALESCE(AVG(amount_sol), 0)
                FROM transfer_index
                WHERE destination IN ({ph})
                  AND block_time >= ?
            """, new_creators + [since_24h])

            row = cursor.fetchone()
            count = row[0] or 0
            avg_size = row[1] or 0
        else:
            count = 0
            avg_size = 0

        # Signal strength: weighted by count and funding size
        signal_strength = min(100, (len(new_creators) / 5.0) * 50 + (min(avg_size, 10) / 10.0) * 50)

        return {
            'new_creators_24h': len(new_creators),
            'new_creators_addresses': new_creators,
            'first_funded_in_24h': count,
            'avg_funding_size': float(avg_size),
            'signal_strength': float(signal_strength),
        }


class FundingBurstAnalyzer:
    """Analyzes concentrated funding activity bursts."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def analyze_bursts(self, org_id: int, cursor: sqlite3.Cursor) -> Dict:
        """
        Analyze funding bursts (3+ transfers in same hour).

        Returns:
        {
            'burst_count_24h': int,
            'max_burst_size': int (transfers in single hour),
            'burst_concentration': float (0-1, how concentrated),
            'total_bursts_value': float,
            'signal_strength': float (0-100)
        }
        """
        # Get org wallets
        cursor.execute("""
            SELECT wallet_list FROM dev_organizations
            WHERE organization_id = ?
        """, (org_id,))

        org_row = cursor.fetchone()
        if not org_row:
            return {
                'burst_count_24h': 0,
                'max_burst_size': 0,
                'burst_concentration': 0,
                'total_bursts_value': 0,
                'signal_strength': 0
            }

        wallet_list = json.loads(org_row[0]) if org_row[0] else []
        if not wallet_list:
            return {
                'burst_count_24h': 0,
                'max_burst_size': 0,
                'burst_concentration': 0,
                'total_bursts_value': 0,
                'signal_strength': 0
            }

        now_ts = int(time.time())
        since_24h = now_ts - 86400

        # Find 1-hour windows with 3+ transfers
        ph = ','.join('?' * len(wallet_list))
        cursor.execute(f"""
            SELECT CAST(block_time/3600 AS INTEGER) AS hour_bucket,
                   COUNT(*) AS tx_count,
                   SUM(amount_sol) AS burst_value
            FROM transfer_index
            WHERE source IN ({ph})
              AND block_time >= ?
            GROUP BY hour_bucket
            HAVING tx_count >= 3
            ORDER BY tx_count DESC
        """, wallet_list + [since_24h])

        bursts = cursor.fetchall()
        if not bursts:
            return {
                'burst_count_24h': 0,
                'max_burst_size': 0,
                'burst_concentration': 0,
                'total_bursts_value': 0,
                'signal_strength': 0
            }

        burst_counts = [row[1] for row in bursts]
        burst_values = [row[2] for row in bursts]

        # Concentration: how much activity is in bursts vs total
        cursor.execute(f"""
            SELECT COUNT(*), SUM(amount_sol)
            FROM transfer_index
            WHERE source IN ({ph})
              AND block_time >= ?
        """, wallet_list + [since_24h])

        total_row = cursor.fetchone()
        total_tx = total_row[0] or 1
        total_value = total_row[1] or 0

        burst_tx = sum(burst_counts)
        burst_value = sum(burst_values)

        concentration = (burst_tx / total_tx) if total_tx > 0 else 0
        signal_strength = min(100, (len(bursts) / 3.0) * 50 + concentration * 50)

        return {
            'burst_count_24h': len(bursts),
            'max_burst_size': max(burst_counts),
            'burst_concentration': float(concentration),
            'total_bursts_value': float(burst_value),
            'signal_strength': float(signal_strength),
        }


class OperatorActivityMonitor:
    """Tracks operator engagement and activity spikes."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def monitor_operator_spike(self, org_id: int, cursor: sqlite3.Cursor) -> Dict:
        """
        Monitor operator (primary wallet) activity spikes.

        Returns:
        {
            'operator_wallet': str,
            'operator_tx_24h': int,
            'operator_tx_7d_avg': float,
            'activity_spike': float (ratio),
            'signal_strength': float (0-100)
        }
        """
        # Get operator wallet
        cursor.execute("""
            SELECT operator_wallet FROM dev_organizations
            WHERE organization_id = ?
        """, (org_id,))

        org_row = cursor.fetchone()
        if not org_row or not org_row[0]:
            return {
                'operator_wallet': None,
                'operator_tx_24h': 0,
                'operator_tx_7d_avg': 0,
                'activity_spike': 0,
                'signal_strength': 0
            }

        operator = org_row[0]
        now_ts = int(time.time())
        since_24h = now_ts - 86400
        since_7d = now_ts - 604800

        # Operator tx in 24h
        cursor.execute("""
            SELECT COUNT(*) FROM transfer_index
            WHERE source = ?
              AND block_time >= ?
        """, (operator, since_24h))

        tx_24h = cursor.fetchone()[0] or 0

        # Operator tx in 7d (average per day)
        cursor.execute("""
            SELECT COUNT(*) FROM transfer_index
            WHERE source = ?
              AND block_time >= ?
        """, (operator, since_7d))

        tx_7d = cursor.fetchone()[0] or 0
        tx_7d_avg = tx_7d / 7.0

        # Spike ratio
        spike = (tx_24h / max(tx_7d_avg, 1)) if tx_7d_avg > 0 else 0

        # Signal: ratio of 2+ means significant spike
        signal_strength = min(100, max(0, (spike - 1) / 2.0 * 100))

        return {
            'operator_wallet': operator,
            'operator_tx_24h': int(tx_24h),
            'operator_tx_7d_avg': float(tx_7d_avg),
            'activity_spike': float(spike),
            'signal_strength': float(signal_strength),
        }


class CreatorReuseDetector:
    """Detects re-engagement of existing creators (sign of coordinated launches)."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def detect_reuse_delta(self, org_id: int, cursor: sqlite3.Cursor) -> Dict:
        """
        Detect increase in funding to existing creators (re-engagement).

        Returns:
        {
            'creators_reused_24h': int,
            'total_org_creators': int,
            'reuse_rate': float (0-1),
            'avg_refunding_amount': float,
            'signal_strength': float (0-100)
        }
        """
        # Get org creators
        cursor.execute("""
            SELECT creator_list FROM dev_organizations
            WHERE organization_id = ?
        """, (org_id,))

        org_row = cursor.fetchone()
        if not org_row:
            return {
                'creators_reused_24h': 0,
                'total_org_creators': 0,
                'reuse_rate': 0,
                'avg_refunding_amount': 0,
                'signal_strength': 0
            }

        creator_list = json.loads(org_row[0]) if org_row[0] else []
        if not creator_list:
            return {
                'creators_reused_24h': 0,
                'total_org_creators': 0,
                'reuse_rate': 0,
                'avg_refunding_amount': 0,
                'signal_strength': 0
            }

        now_ts = int(time.time())
        since_24h = now_ts - 86400

        # Find creators funded in last 24h
        ph = ','.join('?' * len(creator_list))
        cursor.execute(f"""
            SELECT DISTINCT destination,
                   SUM(amount_sol) as total_amount,
                   COUNT(*) as tx_count
            FROM transfer_index
            WHERE destination IN ({ph})
              AND block_time >= ?
            GROUP BY destination
        """, creator_list + [since_24h])

        reused = cursor.fetchall()
        if not reused:
            return {
                'creators_reused_24h': 0,
                'total_org_creators': len(creator_list),
                'reuse_rate': 0,
                'avg_refunding_amount': 0,
                'signal_strength': 0
            }

        reused_count = len(reused)
        amounts = [row[1] for row in reused]
        avg_amount = mean(amounts) if amounts else 0

        reuse_rate = reused_count / len(creator_list) if creator_list else 0

        # Signal: high reuse rate (>50%) = coordinated multi-launch
        signal_strength = min(100, reuse_rate * 100 * 0.7 + (min(avg_amount, 5) / 5.0) * 30)

        return {
            'creators_reused_24h': int(reused_count),
            'total_org_creators': len(creator_list),
            'reuse_rate': float(reuse_rate),
            'avg_refunding_amount': float(avg_amount),
            'signal_strength': float(signal_strength),
        }


class LaunchWaveScorer:
    """Combines all signals into composite wave_score."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.new_creator_detector = NewCreatorDetector(db_path)
        self.funding_burst_analyzer = FundingBurstAnalyzer(db_path)
        self.operator_monitor = OperatorActivityMonitor(db_path)
        self.creator_reuse_detector = CreatorReuseDetector(db_path)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def score_launch_wave(self, org_id: int, cursor: sqlite3.Cursor) -> Dict:
        """
        Compute composite wave_score combining all signals.

        Formula:
        wave_score = 0.30*new_creators + 0.25*bursts + 0.20*momentum
                   + 0.15*operator_spike + 0.10*creator_reuse
        """
        # Get all signals
        new_creators = self.new_creator_detector.detect_new_creators(org_id, cursor)
        funding_bursts = self.funding_burst_analyzer.analyze_bursts(org_id, cursor)
        operator_spike = self.operator_monitor.monitor_operator_spike(org_id, cursor)

        # Get momentum from org_momentum_history if available
        cursor.execute("""
            SELECT momentum_signal FROM org_momentum_history
            WHERE organization_id = ?
            ORDER BY recorded_date DESC
            LIMIT 1
        """, (org_id,))

        momentum_row = cursor.fetchone()
        momentum_signal = (momentum_row[0] + 100) / 2 if momentum_row else 50  # Normalize to 0-100

        creator_reuse = self.creator_reuse_detector.detect_reuse_delta(org_id, cursor)

        # Normalize signals to 0-100 scale
        new_creators_norm = min(100, new_creators['signal_strength'])
        bursts_norm = min(100, funding_bursts['signal_strength'])
        momentum_norm = min(100, max(0, momentum_signal))
        operator_norm = min(100, operator_spike['signal_strength'])
        reuse_norm = min(100, creator_reuse['signal_strength'])

        # Compute wave_score
        wave_score = (
            0.30 * new_creators_norm +
            0.25 * bursts_norm +
            0.20 * momentum_norm +
            0.15 * operator_norm +
            0.10 * reuse_norm
        )

        # Wave confidence based on signal agreement
        signals = [new_creators_norm, bursts_norm, momentum_norm, operator_norm, reuse_norm]
        if len(signals) > 1:
            avg_signal = mean(signals)
            variance = sum((s - avg_signal) ** 2 for s in signals) / len(signals)
            convergence = max(0, 1 - (variance / 2500))  # Normalize variance
        else:
            convergence = 0.5

        # Wave classification
        if wave_score >= 80:
            wave_type = 'imminent_multi_launch'
        elif wave_score >= 60:
            wave_type = 'preparation_phase'
        elif wave_score >= 40:
            wave_type = 'early_signals'
        else:
            wave_type = 'no_wave'

        return {
            'wave_score': float(wave_score),
            'wave_type': wave_type,
            'wave_confidence': float(convergence),
            'new_creators_signal': float(new_creators_norm),
            'funding_burst_signal': float(bursts_norm),
            'momentum_signal': float(momentum_norm),
            'operator_spike_signal': float(operator_norm),
            'creator_reuse_signal': float(reuse_norm),
            'details': {
                'new_creators': new_creators,
                'funding_bursts': funding_bursts,
                'operator_activity': operator_spike,
                'creator_reuse': creator_reuse,
            }
        }


class LaunchWaveDetectionEngine:
    """Main orchestrator for launch wave detection."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.start_time = time.time()
        self.wave_scorer = LaunchWaveScorer(db_path)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        """Create organization_launch_waves table if not exists."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS organization_launch_waves (
                wave_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id         INTEGER NOT NULL,
                wave_date               TEXT NOT NULL,
                wave_score              REAL DEFAULT 0,
                wave_type               TEXT,
                wave_confidence         REAL DEFAULT 0,
                new_creators_signal     REAL DEFAULT 0,
                funding_burst_signal    REAL DEFAULT 0,
                momentum_signal         REAL DEFAULT 0,
                operator_spike_signal   REAL DEFAULT 0,
                creator_reuse_signal    REAL DEFAULT 0,
                new_creators_count      INTEGER DEFAULT 0,
                burst_count             INTEGER DEFAULT 0,
                operator_activity_spike REAL DEFAULT 0,
                creator_reuse_rate      REAL DEFAULT 0,
                detected_at             REAL NOT NULL,
                FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
                UNIQUE(organization_id, wave_date)
            )
        """)

        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_olw_org_date ON organization_launch_waves(organization_id, wave_date DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_olw_wave_score ON organization_launch_waves(wave_score DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_olw_wave_type ON organization_launch_waves(wave_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_olw_confidence ON organization_launch_waves(wave_confidence DESC)")

        conn.commit()
        conn.close()

    def _load_organizations(self, cursor) -> List[Dict]:
        """Load all organizations."""
        cursor.execute("SELECT organization_id FROM dev_organizations")
        return [{'organization_id': row[0]} for row in cursor.fetchall()]

    def detect_and_store(self) -> Dict:
        """
        Main detection flow: analyze all orgs for launch waves.

        Returns: {status, message, orgs_processed, waves_detected, duration_ms}
        """
        try:
            self._ensure_tables()

            conn = self._get_conn()
            cursor = conn.cursor()

            # Load orgs
            orgs = self._load_organizations(cursor)
            if not orgs:
                logger.warning("No organizations found")
                conn.close()
                return {
                    'status': 'success',
                    'message': 'No organizations to process',
                    'orgs_processed': 0,
                    'waves_detected': 0,
                    'duration_ms': int((time.time() - self.start_time) * 1000)
                }

            now = time.time()
            waves_detected = 0

            for org in orgs:
                org_id = org['organization_id']

                try:
                    # Score this org
                    wave_result = self.wave_scorer.score_launch_wave(org_id, cursor)

                    # Store result
                    cursor.execute("""
                        INSERT OR REPLACE INTO organization_launch_waves
                        (organization_id, wave_date, wave_score, wave_type, wave_confidence,
                         new_creators_signal, funding_burst_signal, momentum_signal,
                         operator_spike_signal, creator_reuse_signal,
                         new_creators_count, burst_count, operator_activity_spike, creator_reuse_rate,
                         detected_at)
                        VALUES (?, date('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        org_id,
                        wave_result['wave_score'],
                        wave_result['wave_type'],
                        wave_result['wave_confidence'],
                        wave_result['new_creators_signal'],
                        wave_result['funding_burst_signal'],
                        wave_result['momentum_signal'],
                        wave_result['operator_spike_signal'],
                        wave_result['creator_reuse_signal'],
                        wave_result['details']['new_creators']['new_creators_24h'],
                        wave_result['details']['funding_bursts']['burst_count_24h'],
                        wave_result['details']['operator_activity']['activity_spike'],
                        wave_result['details']['creator_reuse']['reuse_rate'],
                        now
                    ))

                    if wave_result['wave_score'] >= 70:
                        waves_detected += 1
                        logger.info(
                            f"Launch wave detected: org_id={org_id}, "
                            f"wave_score={wave_result['wave_score']:.1f}, "
                            f"type={wave_result['wave_type']}"
                        )

                except Exception as e:
                    logger.error(f"Error scoring org {org_id}: {e}", exc_info=True)
                    continue

            conn.commit()
            conn.close()

            duration_ms = int((time.time() - self.start_time) * 1000)

            return {
                'status': 'success',
                'message': f'Launch wave detection complete',
                'orgs_processed': len(orgs),
                'waves_detected': waves_detected,
                'duration_ms': duration_ms
            }

        except Exception as e:
            logger.error(f"Launch wave detection failed: {e}", exc_info=True)
            return {
                'status': 'error',
                'message': str(e),
                'orgs_processed': 0,
                'waves_detected': 0,
                'duration_ms': int((time.time() - self.start_time) * 1000)
            }
