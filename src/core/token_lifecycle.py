"""
Token Lifecycle Tracking & Classification System

Tracks tokens from launch → peak → outcome and classifies as:
- rug: rapid failure
- slow_rug: gradual decay
- success: sustained growth
- neutral: unclear outcome

Integrates with existing token_analysis, token_price_snapshots, and cluster tables.
"""

import sqlite3
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)

# =========================================================================
# CONFIGURATION (tunable thresholds)
# =========================================================================

class LifecycleConfig:
    """All tunable parameters for monitoring and classification"""

    # Stop conditions (when to stop monitoring a token)
    RUG_THRESHOLD_MC = 5_000                    # Stop if < $5k for 30+ min
    STALL_THRESHOLD_MC = 50_000                # Stop if < $50k for 2+ hours
    INACTIVITY_THRESHOLD_MIN = 60              # Stop if no updates for 60 min
    MAX_MONITORING_AGE_DAYS = 7                # Stop after 7 days regardless

    # Classification thresholds
    RUG_PEAK_MC = 100_000                       # Peak < $100k
    RUG_TIME_TO_PEAK_MIN = 30                  # Peak within 30 min
    RUG_DRAWDOWN_MIN_PCT = 80                  # Drawdown > 80%

    SLOW_RUG_PEAK_MC = 50_000                  # Peak > $50k
    SLOW_RUG_DRAWDOWN_MIN_PCT = 80             # Drawdown > 80%
    SLOW_RUG_FINAL_MC = 5_000                  # Final < $5k

    SUCCESS_PEAK_MC = 250_000                  # Peak > $250k
    SUCCESS_FINAL_MC = 50_000                  # Final > $50k
    SUCCESS_MAX_DRAWDOWN_PCT = 75              # Max drawdown < 75%

    # Snapshot cadence (minutes)
    CADENCE_EARLY_INTERVAL = 0.5               # 0-30 min: every 30 sec
    CADENCE_EARLY_MAX_AGE_MIN = 30
    CADENCE_NORMAL_INTERVAL = 5                # 30 min-6 hr: every 5 min
    CADENCE_NORMAL_MAX_AGE_MIN = 360
    CADENCE_LATE_INTERVAL = 30                 # 6+ hr: every 30 min

    # Storage
    SNAPSHOT_RETENTION_DAYS = 30               # Archive snapshots after 30 days
    AGGREGATE_AFTER_HOURS = 24                 # Aggregate to hourly after 24 hours

    # Stall detection
    STALL_DETECTION_HOURS = 2                  # If < STALL_THRESHOLD_MC for 2 hours
    STALL_CHECK_SAMPLES_MIN = 4                # Need at least 4 samples


# =========================================================================
# DATA CLASSES
# =========================================================================

class TokenOutcome(Enum):
    RUG = "rug"
    SLOW_RUG = "slow_rug"
    SUCCESS = "success"
    NEUTRAL = "neutral"


class MonitorStatus(Enum):
    ACTIVE = "active"
    STOPPED = "stopped"
    COMPLETED = "completed"


@dataclass
class TokenSnapshot:
    """Single point-in-time observation"""
    mint: str
    timestamp: int
    price_usd: float
    market_cap_usd: float
    liquidity_usd: float = 0
    volume_24h: float = 0
    price_source: str = "unknown"
    cluster_id: str = None
    creator: str = None


@dataclass
class MonitoringState:
    """Current monitoring status of a token"""
    mint: str
    status: MonitorStatus
    started_at: int
    stopped_at: Optional[int] = None
    stop_reason: Optional[str] = None
    peak_market_cap: float = 0
    peak_price: float = 0
    peak_market_cap_at: Optional[int] = None
    last_market_cap: float = 0
    last_price: float = 0
    last_snapshot_at: Optional[int] = None
    snapshot_count: int = 0
    inactivity_minutes: int = 0
    outcome: Optional[str] = None


@dataclass
class TokenOutcomeRecord:
    """Final classification of a token"""
    mint: str
    outcome: TokenOutcome
    outcome_score: float
    peak_market_cap: float
    peak_price: float
    time_to_peak_minutes: int
    final_market_cap: float
    final_price: float
    max_drawdown_pct: float
    time_from_peak_to_finish_minutes: int
    classification_reason: str
    cluster_id: Optional[str]
    cluster_name: Optional[str]
    lifecycle_duration_minutes: int
    classified_at: int


# =========================================================================
# LIFECYCLE MANAGER
# =========================================================================

class TokenLifecycleManager:
    """Core monitoring and classification logic"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self):
        """Create tables if they don't exist"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # token_monitoring_state
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_monitoring_state (
                mint TEXT PRIMARY KEY,
                monitor_status TEXT CHECK(monitor_status IN ('active', 'stopped', 'completed')),
                started_at INTEGER NOT NULL,
                stopped_at INTEGER,
                stop_reason TEXT,
                peak_market_cap REAL DEFAULT 0,
                peak_market_cap_at INTEGER,
                peak_price REAL DEFAULT 0,
                last_market_cap REAL DEFAULT 0,
                last_price REAL DEFAULT 0,
                last_snapshot_at INTEGER,
                snapshot_count INTEGER DEFAULT 0,
                inactivity_minutes INTEGER DEFAULT 0,
                outcome TEXT,
                outcome_computed_at INTEGER,
                FOREIGN KEY(mint) REFERENCES tracked_tokens(mint)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_monitoring_status
            ON token_monitoring_state(monitor_status)
        """)

        # token_lifecycle_snapshots
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_lifecycle_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mint TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                price_usd REAL,
                market_cap_usd REAL,
                liquidity_usd REAL DEFAULT 0,
                volume_24h REAL DEFAULT 0,
                price_source TEXT,
                cluster_id TEXT,
                creator TEXT,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(mint) REFERENCES tracked_tokens(mint)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_lcs_mint_time
            ON token_lifecycle_snapshots(mint, timestamp DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_lcs_cluster_time
            ON token_lifecycle_snapshots(cluster_id, timestamp DESC)
        """)

        # token_outcomes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_outcomes (
                mint TEXT PRIMARY KEY,
                outcome TEXT NOT NULL CHECK(outcome IN ('rug', 'slow_rug', 'success', 'neutral')),
                outcome_score REAL,
                peak_market_cap REAL DEFAULT 0,
                peak_price REAL DEFAULT 0,
                time_to_peak_minutes INTEGER DEFAULT 0,
                final_market_cap REAL DEFAULT 0,
                final_price REAL DEFAULT 0,
                max_drawdown_pct REAL DEFAULT 0,
                time_from_peak_to_finish_minutes INTEGER DEFAULT 0,
                classification_reason TEXT,
                cluster_id TEXT,
                cluster_name TEXT,
                classified_at INTEGER NOT NULL,
                lifecycle_duration_minutes INTEGER,
                FOREIGN KEY(mint) REFERENCES tracked_tokens(mint)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_outcomes_outcome
            ON token_outcomes(outcome)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_outcomes_cluster
            ON token_outcomes(cluster_id)
        """)

        # cluster_outcome_stats
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cluster_outcome_stats (
                cluster_id TEXT PRIMARY KEY,
                cluster_name TEXT,
                network_name TEXT,
                total_tokens INTEGER DEFAULT 0,
                rug_count INTEGER DEFAULT 0,
                slow_rug_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                neutral_count INTEGER DEFAULT 0,
                rug_rate REAL DEFAULT 0,
                success_rate REAL DEFAULT 0,
                median_peak_market_cap REAL DEFAULT 0,
                median_final_market_cap REAL DEFAULT 0,
                median_max_drawdown_pct REAL DEFAULT 0,
                median_time_to_peak_minutes REAL DEFAULT 0,
                computed_at INTEGER NOT NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cluster_stats_network
            ON cluster_outcome_stats(network_name)
        """)

        conn.commit()
        conn.close()
        logger.info("[LIFECYCLE] Schema initialized")

    def start_monitoring(self, mint: str, cluster_id: str = None, creator: str = None) -> bool:
        """Begin monitoring a new token"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            now = int(datetime.now().timestamp())

            cursor.execute("""
                INSERT OR IGNORE INTO token_monitoring_state
                (mint, monitor_status, started_at)
                VALUES (?, ?, ?)
            """, (mint, MonitorStatus.ACTIVE.value, now))

            conn.commit()
            conn.close()
            logger.info(f"[LIFECYCLE] Started monitoring {mint[:16]}... (cluster: {cluster_id})")
            return True
        except Exception as e:
            logger.error(f"[LIFECYCLE] Error starting monitoring: {e}")
            return False

    def record_snapshot(self, snapshot: TokenSnapshot) -> bool:
        """Record a price/market cap snapshot"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            now = int(datetime.now().timestamp())

            # Insert snapshot
            cursor.execute("""
                INSERT INTO token_lifecycle_snapshots
                (mint, timestamp, price_usd, market_cap_usd, liquidity_usd, volume_24h,
                 price_source, cluster_id, creator, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                snapshot.mint,
                snapshot.timestamp,
                snapshot.price_usd,
                snapshot.market_cap_usd,
                snapshot.liquidity_usd,
                snapshot.volume_24h,
                snapshot.price_source,
                snapshot.cluster_id,
                snapshot.creator,
                now
            ))

            # Update monitoring state
            cursor.execute("""
                UPDATE token_monitoring_state
                SET
                    last_market_cap = ?,
                    last_price = ?,
                    last_snapshot_at = ?,
                    snapshot_count = snapshot_count + 1,
                    peak_market_cap = MAX(peak_market_cap, ?),
                    peak_price = MAX(peak_price, ?),
                    peak_market_cap_at = CASE
                        WHEN ? > peak_market_cap THEN ?
                        ELSE peak_market_cap_at
                    END,
                    inactivity_minutes = 0
                WHERE mint = ?
            """, (
                snapshot.market_cap_usd,
                snapshot.price_usd,
                snapshot.timestamp,
                snapshot.market_cap_usd,
                snapshot.price_usd,
                snapshot.market_cap_usd,
                snapshot.timestamp,
                snapshot.mint
            ))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"[LIFECYCLE] Error recording snapshot: {e}")
            return False

    def evaluate_stop_conditions(self, mint: str) -> Optional[Tuple[bool, str]]:
        """
        Check if token should stop monitoring.
        Returns (should_stop, reason) or (False, None)
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get current state
            cursor.execute("""
                SELECT started_at, last_market_cap, last_snapshot_at, snapshot_count
                FROM token_monitoring_state
                WHERE mint = ?
            """, (mint,))

            row = cursor.fetchone()
            if not row:
                return False, None

            started_at, last_mc, last_snap_at, snap_count = row

            now = int(datetime.now().timestamp())
            age_seconds = now - started_at
            age_minutes = age_seconds / 60

            # Condition 1: Age exceeded
            if age_minutes > LifecycleConfig.MAX_MONITORING_AGE_DAYS * 24 * 60:
                return True, "max_age_exceeded"

            # Condition 2: No updates (inactivity)
            if last_snap_at:
                inactivity_sec = now - last_snap_at
                if inactivity_sec > LifecycleConfig.INACTIVITY_THRESHOLD_MIN * 60:
                    return True, "inactivity"

            # Condition 3: Market cap collapse (rug)
            if last_mc < LifecycleConfig.RUG_THRESHOLD_MC and snap_count > 2:
                return True, "rug_detected"

            # Condition 4: Stall (low market cap for sustained period)
            if last_mc < LifecycleConfig.STALL_THRESHOLD_MC:
                # Check if it's been low for >= 2 hours
                cutoff_time = now - (LifecycleConfig.STALL_DETECTION_HOURS * 3600)

                cursor.execute("""
                    SELECT COUNT(*) FROM token_lifecycle_snapshots
                    WHERE mint = ?
                    AND timestamp >= ?
                    AND market_cap_usd < ?
                """, (mint, cutoff_time, LifecycleConfig.STALL_THRESHOLD_MC))

                low_sample_count = cursor.fetchone()[0]

                if low_sample_count >= LifecycleConfig.STALL_CHECK_SAMPLES_MIN:
                    return True, "stalled"

            conn.close()
            return False, None

        except Exception as e:
            logger.error(f"[LIFECYCLE] Error evaluating stop conditions: {e}")
            return False, None

    def classify_outcome(self, mint: str) -> Optional[TokenOutcomeRecord]:
        """Classify a token's outcome based on its lifecycle"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get monitoring state
            cursor.execute("""
                SELECT
                    started_at, last_snapshot_at, peak_market_cap, peak_market_cap_at,
                    peak_price, last_market_cap, last_price, snapshot_count
                FROM token_monitoring_state
                WHERE mint = ?
            """, (mint,))

            mon_row = cursor.fetchone()
            if not mon_row:
                return None

            (started_at, last_snap_at, peak_mc, peak_at,
             peak_price, final_mc, final_price, snap_count) = mon_row

            # Get first snapshot time and price
            cursor.execute("""
                SELECT timestamp, price_usd FROM token_lifecycle_snapshots
                WHERE mint = ?
                ORDER BY timestamp ASC
                LIMIT 1
            """, (mint,))

            first_row = cursor.fetchone()
            if not first_row:
                return None

            first_time, first_price = first_row

            # Get cluster info
            cursor.execute("""
                SELECT cluster_id, cluster_name FROM token_analysis
                WHERE mint = ?
            """, (mint,))

            cluster_row = cursor.fetchone()
            cluster_id = cluster_row[0] if cluster_row else None
            cluster_name = cluster_row[1] if cluster_row else None

            # Calculate metrics
            now = int(datetime.now().timestamp())
            lifecycle_minutes = (now - started_at) // 60
            time_to_peak_min = (peak_at - started_at) // 60 if peak_at else 0
            time_from_peak_to_finish_min = (last_snap_at - peak_at) // 60 if peak_at else 0

            max_drawdown_pct = (
                ((peak_mc - final_mc) / peak_mc * 100) if peak_mc > 0 else 0
            )

            # Classification logic
            outcome, score, reason = self._classify(
                peak_mc=peak_mc,
                final_mc=final_mc,
                time_to_peak_min=time_to_peak_min,
                max_drawdown_pct=max_drawdown_pct,
                first_price=first_price,
                peak_price=peak_price,
                final_price=final_price,
                snap_count=snap_count
            )

            result = TokenOutcomeRecord(
                mint=mint,
                outcome=outcome,
                outcome_score=score,
                peak_market_cap=peak_mc,
                peak_price=peak_price,
                time_to_peak_minutes=time_to_peak_min,
                final_market_cap=final_mc,
                final_price=final_price,
                max_drawdown_pct=max_drawdown_pct,
                time_from_peak_to_finish_minutes=time_from_peak_to_finish_min,
                classification_reason=reason,
                cluster_id=cluster_id,
                cluster_name=cluster_name,
                lifecycle_duration_minutes=lifecycle_minutes,
                classified_at=now
            )

            conn.close()
            return result

        except Exception as e:
            logger.error(f"[LIFECYCLE] Error classifying outcome: {e}")
            return None

    @staticmethod
    def _classify(
        peak_mc: float,
        final_mc: float,
        time_to_peak_min: int,
        max_drawdown_pct: float,
        first_price: float,
        peak_price: float,
        final_price: float,
        snap_count: int
    ) -> Tuple[TokenOutcome, float, str]:
        """
        Classify token outcome based on lifecycle metrics.
        Returns (outcome, confidence_score, reason)
        """

        reason_parts = []

        # RUG: Fast failure with low peak
        if (peak_mc < LifecycleConfig.RUG_PEAK_MC and
            time_to_peak_min <= LifecycleConfig.RUG_TIME_TO_PEAK_MIN and
            max_drawdown_pct >= LifecycleConfig.RUG_DRAWDOWN_MIN_PCT):
            reason_parts.append(f"peak_mc={peak_mc:.0f}<{LifecycleConfig.RUG_PEAK_MC}")
            reason_parts.append(f"ttp={time_to_peak_min}min<={LifecycleConfig.RUG_TIME_TO_PEAK_MIN}min")
            reason_parts.append(f"dd={max_drawdown_pct:.1f}%>={LifecycleConfig.RUG_DRAWDOWN_MIN_PCT}%")
            return TokenOutcome.RUG, 1.0, " && ".join(reason_parts)

        # SLOW_RUG: High peak but severe drawdown + low final
        if (peak_mc >= LifecycleConfig.SLOW_RUG_PEAK_MC and
            max_drawdown_pct >= LifecycleConfig.SLOW_RUG_DRAWDOWN_MIN_PCT and
            final_mc < LifecycleConfig.SLOW_RUG_FINAL_MC):
            reason_parts.append(f"peak_mc={peak_mc:.0f}>={LifecycleConfig.SLOW_RUG_PEAK_MC}")
            reason_parts.append(f"dd={max_drawdown_pct:.1f}%>={LifecycleConfig.SLOW_RUG_DRAWDOWN_MIN_PCT}%")
            reason_parts.append(f"final_mc={final_mc:.0f}<{LifecycleConfig.SLOW_RUG_FINAL_MC}")
            return TokenOutcome.SLOW_RUG, 1.0, " && ".join(reason_parts)

        # SUCCESS: High peak, sustained, limited drawdown
        if (peak_mc >= LifecycleConfig.SUCCESS_PEAK_MC and
            final_mc >= LifecycleConfig.SUCCESS_FINAL_MC and
            max_drawdown_pct <= LifecycleConfig.SUCCESS_MAX_DRAWDOWN_PCT):
            reason_parts.append(f"peak_mc={peak_mc:.0f}>={LifecycleConfig.SUCCESS_PEAK_MC}")
            reason_parts.append(f"final_mc={final_mc:.0f}>={LifecycleConfig.SUCCESS_FINAL_MC}")
            reason_parts.append(f"dd={max_drawdown_pct:.1f}%<={LifecycleConfig.SUCCESS_MAX_DRAWDOWN_PCT}%")
            return TokenOutcome.SUCCESS, 1.0, " && ".join(reason_parts)

        # NEUTRAL: Everything else
        reason_parts.append("no_rule_matched")
        return TokenOutcome.NEUTRAL, 0.5, " && ".join(reason_parts)

    def stop_monitoring(self, mint: str, outcome: TokenOutcomeRecord) -> bool:
        """Stop monitoring and record outcome"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            now = int(datetime.now().timestamp())

            # Update monitoring state
            cursor.execute("""
                UPDATE token_monitoring_state
                SET
                    monitor_status = ?,
                    stopped_at = ?,
                    outcome = ?,
                    outcome_computed_at = ?
                WHERE mint = ?
            """, (
                MonitorStatus.STOPPED.value,
                now,
                outcome.outcome.value,
                now,
                mint
            ))

            # Insert outcome record
            cursor.execute("""
                INSERT OR REPLACE INTO token_outcomes
                (mint, outcome, outcome_score, peak_market_cap, peak_price,
                 time_to_peak_minutes, final_market_cap, final_price,
                 max_drawdown_pct, time_from_peak_to_finish_minutes,
                 classification_reason, cluster_id, cluster_name,
                 classified_at, lifecycle_duration_minutes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                outcome.mint,
                outcome.outcome.value,
                outcome.outcome_score,
                outcome.peak_market_cap,
                outcome.peak_price,
                outcome.time_to_peak_minutes,
                outcome.final_market_cap,
                outcome.final_price,
                outcome.max_drawdown_pct,
                outcome.time_from_peak_to_finish_minutes,
                outcome.classification_reason,
                outcome.cluster_id,
                outcome.cluster_name,
                outcome.classified_at,
                outcome.lifecycle_duration_minutes
            ))

            conn.commit()
            conn.close()
            logger.info(f"[LIFECYCLE] Stopped monitoring {mint[:16]}... → {outcome.outcome.value}")
            return True

        except Exception as e:
            logger.error(f"[LIFECYCLE] Error stopping monitoring: {e}")
            return False

    def compute_cluster_stats(self, cluster_id: str = None) -> bool:
        """Compute aggregate cluster statistics"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            now = int(datetime.now().timestamp())

            # If specific cluster, update only that; else update all
            if cluster_id:
                cursor.execute("""
                    SELECT cluster_id FROM super_clusters
                    WHERE cluster_id = ?
                """, (cluster_id,))
                if not cursor.fetchone():
                    return False
                clusters = [cluster_id]
            else:
                cursor.execute("SELECT DISTINCT cluster_id FROM token_outcomes")
                clusters = [row[0] for row in cursor.fetchall()]

            for cid in clusters:
                # Get cluster name and network
                cursor.execute("""
                    SELECT cluster_name, network_name FROM token_analysis
                    WHERE cluster_id = ?
                    LIMIT 1
                """, (cid,))

                cluster_row = cursor.fetchone()
                cluster_name = cluster_row[0] if cluster_row else None
                network_name = cluster_row[1] if cluster_row else None

                # Count outcomes
                cursor.execute("""
                    SELECT outcome, COUNT(*) FROM token_outcomes
                    WHERE cluster_id = ?
                    GROUP BY outcome
                """, (cid,))

                outcome_counts = {row[0]: row[1] for row in cursor.fetchall()}

                total = sum(outcome_counts.values())
                if total == 0:
                    continue

                rug_count = outcome_counts.get('rug', 0)
                slow_rug_count = outcome_counts.get('slow_rug', 0)
                success_count = outcome_counts.get('success', 0)
                neutral_count = outcome_counts.get('neutral', 0)

                rug_rate = rug_count / total
                success_rate = success_count / total

                # Compute medians
                cursor.execute("""
                    SELECT
                        percentile_cont(0.5) WITHIN GROUP (ORDER BY peak_market_cap) as median_peak_mc,
                        percentile_cont(0.5) WITHIN GROUP (ORDER BY final_market_cap) as median_final_mc,
                        percentile_cont(0.5) WITHIN GROUP (ORDER BY max_drawdown_pct) as median_dd,
                        percentile_cont(0.5) WITHIN GROUP (ORDER BY time_to_peak_minutes) as median_ttp
                    FROM token_outcomes
                    WHERE cluster_id = ?
                """, (cid,))

                # SQLite doesn't have percentile_cont; use approximate instead
                cursor.execute("""
                    SELECT
                        (SELECT peak_market_cap FROM token_outcomes
                         WHERE cluster_id = ?
                         ORDER BY peak_market_cap LIMIT 1 OFFSET
                         (SELECT COUNT(*)/2 FROM token_outcomes WHERE cluster_id = ?))
                    as median_peak_mc
                """, (cid, cid))

                median_peak_mc = cursor.fetchone()[0] or 0

                # Upsert into cluster stats
                cursor.execute("""
                    INSERT OR REPLACE INTO cluster_outcome_stats
                    (cluster_id, cluster_name, network_name,
                     total_tokens, rug_count, slow_rug_count, success_count, neutral_count,
                     rug_rate, success_rate, median_peak_market_cap, computed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    cid, cluster_name, network_name,
                    total, rug_count, slow_rug_count, success_count, neutral_count,
                    rug_rate, success_rate, median_peak_mc, now
                ))

            conn.commit()
            conn.close()
            logger.info(f"[LIFECYCLE] Computed cluster stats for {len(clusters)} clusters")
            return True

        except Exception as e:
            logger.error(f"[LIFECYCLE] Error computing cluster stats: {e}")
            return False

    def get_active_tokens(self) -> List[str]:
        """Get list of tokens currently being monitored"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT mint FROM token_monitoring_state
                WHERE monitor_status = ?
            """, (MonitorStatus.ACTIVE.value,))

            mints = [row[0] for row in cursor.fetchall()]
            conn.close()
            return mints

        except Exception as e:
            logger.error(f"[LIFECYCLE] Error getting active tokens: {e}")
            return []

    def get_outcome_summary(self, limit: int = 100) -> List[Dict]:
        """Get recently completed tokens with outcomes"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    o.mint, o.outcome, o.peak_market_cap, o.final_market_cap,
                    o.max_drawdown_pct, o.time_to_peak_minutes,
                    o.cluster_name, o.lifecycle_duration_minutes,
                    DATETIME(o.classified_at, 'unixepoch') as completed_at
                FROM token_outcomes o
                ORDER BY o.classified_at DESC
                LIMIT ?
            """, (limit,))

            results = []
            for row in cursor.fetchall():
                results.append({
                    'mint': row[0],
                    'outcome': row[1],
                    'peak_mc': row[2],
                    'final_mc': row[3],
                    'max_drawdown_pct': row[4],
                    'time_to_peak_min': row[5],
                    'cluster': row[6],
                    'lifecycle_min': row[7],
                    'completed_at': row[8]
                })

            conn.close()
            return results

        except Exception as e:
            logger.error(f"[LIFECYCLE] Error getting outcome summary: {e}")
            return []

    def get_token_age_minutes(self, mint: str) -> int:
        """Get token age in minutes"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                "SELECT started_at FROM token_monitoring_state WHERE mint = ?",
                (mint,)
            )
            row = cursor.fetchone()
            conn.close()

            if not row:
                return 0

            started_at = row[0]
            now = int(datetime.now().timestamp())
            age_seconds = now - started_at
            age_minutes = int(age_seconds / 60)

            return age_minutes

        except Exception as e:
            logger.error(f"[LIFECYCLE] Error getting token age: {e}")
            return 0


# =========================================================================
# MONITORING WORKER (async loop)
# =========================================================================

class LifecycleMonitoringWorker:
    """Background worker that monitors tokens and classifies outcomes"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.manager = TokenLifecycleManager(db_path)
        self.processed_count = 0
        self.classified_count = 0

    def run_cycle(self) -> Dict:
        """Run one monitoring cycle"""
        results = {
            'mints_checked': 0,
            'mints_stopped': 0,
            'mints_classified': 0,
            'mints_early_scored': 0,
            'errors': []
        }

        try:
            active_mints = self.manager.get_active_tokens()
            results['mints_checked'] = len(active_mints)

            # Import early signal engine
            from src.core.lifecycle_early_signals import EarlySignalEngine
            early_signal_engine = EarlySignalEngine(self.db_path)

            for mint in active_mints:
                try:
                    # Calculate token age in minutes
                    age_minutes = self.manager.get_token_age_minutes(mint)
                    
                    # Compute early signal for tokens aged 5-15 minutes
                    if 5 <= age_minutes <= 15:
                        early_signal = early_signal_engine.compute_early_score(mint, age_minutes)
                        if early_signal:
                            early_signal_engine.record_early_signal(early_signal)
                            results['mints_early_scored'] += 1
                            logger.info(f"[WORKER] Early signal for {mint[:16]}... → {early_signal.early_label.value} (conf: {early_signal.confidence:.2f})")

                    # Evaluate stop conditions
                    should_stop, reason = self.manager.evaluate_stop_conditions(mint)

                    if should_stop:
                        logger.info(f"[WORKER] Token {mint[:16]}... should stop: {reason}")

                        # Classify outcome
                        outcome = self.manager.classify_outcome(mint)
                        if outcome:
                            self.manager.stop_monitoring(mint, outcome)
                            results['mints_stopped'] += 1
                            results['mints_classified'] += 1
                            self.classified_count += 1
                        else:
                            logger.warning(f"[WORKER] Could not classify {mint[:16]}...")

                except Exception as e:
                    logger.error(f"[WORKER] Error processing {mint[:16]}...: {e}")
                    results['errors'].append(str(e))

        except Exception as e:
            logger.error(f"[WORKER] Error in monitoring cycle: {e}")
            results['errors'].append(str(e))

        return results

    def backfill_monitoring_state(self) -> Dict:
        """Initialize monitoring_state for all existing tracked tokens"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get all tracked tokens not yet in monitoring
            cursor.execute("""
                SELECT mint, created_at FROM tracked_tokens
                WHERE mint NOT IN (SELECT mint FROM token_monitoring_state)
                LIMIT 1000
            """)

            mints = [(row[0], row[1]) for row in cursor.fetchall()]

            for mint, created_at in mints:
                self.manager.start_monitoring(mint)

            conn.close()

            logger.info(f"[WORKER] Backfilled {len(mints)} tokens into monitoring_state")
            return {'backfilled': len(mints)}

        except Exception as e:
            logger.error(f"[WORKER] Error backfilling: {e}")
            return {'backfilled': 0, 'error': str(e)}


# =========================================================================
# EXAMPLE USAGE
# =========================================================================

if __name__ == "__main__":
    import os

    db_path = os.environ.get('DB_PATH', 'database/flex_complete_database.db')
    manager = TokenLifecycleManager(db_path)

    # Example: Start monitoring a token
    # manager.start_monitoring("EPjFWdd5Au17hunZf0LCU5gS43sPUkAeP89SUNqmjV6", cluster_id="cluster_1")

    # Example: Record a snapshot
    # snapshot = TokenSnapshot(
    #     mint="EPjFWdd5Au17hunZf0LCU5gS43sPUkAeP89SUNqmjV6",
    #     timestamp=int(datetime.now().timestamp()),
    #     price_usd=0.999,
    #     market_cap_usd=50_000_000,
    #     cluster_id="cluster_1"
    # )
    # manager.record_snapshot(snapshot)

    # Example: Get active tokens
    # active = manager.get_active_tokens()
    # print(f"Active tokens: {len(active)}")

    # Example: Get recent outcomes
    # outcomes = manager.get_outcome_summary(limit=20)
    # for o in outcomes:
    #     print(f"{o['mint'][:16]}... → {o['outcome']} (peak: ${o['peak_mc']:,.0f})")

    print("[LIFECYCLE] Module loaded successfully")
