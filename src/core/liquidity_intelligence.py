"""
FLEX Liquidity Intelligence System

Tracks liquidity changes over time and computes liquidity-based risk signals.

Components:
1. Liquidity snapshots (periodic storage)
2. Liquidity health scoring
3. Rug pull detection
4. Liquidity growth signals
5. Risk classification

Benefits:
- Early rug pull detection (>80% drop)
- Liquidity health tracking
- Launch outcome analysis
- Risk-based token filtering
"""

import sqlite3
import logging
import time
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass
from statistics import mean, stdev
import math

logger = logging.getLogger(__name__)


@dataclass
class LiquiditySnapshot:
    """Historical liquidity data point."""
    mint: str
    pair_address: Optional[str]
    liquidity_usd: float
    liquidity_sol: float
    captured_at: int


@dataclass
class LiquidityHealthScore:
    """Liquidity health assessment."""
    mint: str
    health_band: str  # HEALTHY, MODERATE, DANGER
    health_score: float  # 0-100
    liquidity_level_score: float  # 0-100
    liquidity_growth_score: float  # 0-100
    liquidity_stability_score: float  # 0-100
    current_liquidity: float
    liquidity_trend: str  # 'growing', 'stable', 'declining'
    reasons: List[str]


@dataclass
class LiquidityRiskAssessment:
    """Liquidity-based risk detection."""
    mint: str
    liquidity_risk: str  # SAFE, WARNING, DANGER, CRITICAL
    risk_score: float  # 0-100
    drop_percent_24h: float
    drop_percent_7d: float
    rug_pull_likelihood: float  # 0-100
    warning_reasons: List[str]


class LiquidityIntelligence:
    """Core liquidity intelligence engine."""

    def __init__(self, db_path: str = 'database/flex_complete_database.db'):
        self.db_path = db_path
        # Thresholds
        self.min_healthy_liquidity = 10000  # $10k minimum for HEALTHY
        self.mod_healthy_liquidity = 1000   # $1k minimum for MODERATE
        self.critical_drop = 0.80  # 80% drop = DANGER
        self.severe_drop = 0.95   # 95% drop = CRITICAL
        self.volatility_threshold = 0.50  # 50% volatility
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _ensure_tables(self) -> None:
        """Create liquidity tracking tables."""
        conn = self._get_conn()
        cursor = conn.cursor()

        # Liquidity snapshots table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_liquidity_snapshots (
                snapshot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                mint            TEXT NOT NULL,
                pair_address    TEXT,
                liquidity_usd   REAL NOT NULL,
                liquidity_sol   REAL NOT NULL,
                captured_at     INTEGER NOT NULL,
                created_at      INTEGER NOT NULL
            )
        """)

        # Liquidity health cache (latest assessment)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_liquidity_health (
                mint                        TEXT PRIMARY KEY,
                health_band                 TEXT,
                health_score                REAL,
                current_liquidity           REAL,
                liquidity_trend             TEXT,
                liquidity_24h_change        REAL,
                liquidity_7d_change         REAL,
                assessed_at                 INTEGER NOT NULL
            )
        """)

        # Rug pull risk assessments
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_liquidity_risks (
                mint                    TEXT PRIMARY KEY,
                liquidity_risk          TEXT,
                risk_score              REAL,
                drop_percent_24h        REAL,
                drop_percent_7d         REAL,
                rug_pull_likelihood     REAL,
                last_assessed           INTEGER NOT NULL
            )
        """)

        # Indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tls_mint_time
            ON token_liquidity_snapshots(mint, captured_at DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tlh_score
            ON token_liquidity_health(health_score DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tlr_risk
            ON token_liquidity_risks(risk_score DESC)
        """)

        conn.commit()
        conn.close()
        logger.info("Liquidity intelligence tables initialized")

    def store_snapshot(self, mint: str, liquidity_usd: float,
                      liquidity_sol: float, pair_address: Optional[str] = None) -> bool:
        """Store a liquidity snapshot."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            now = int(time.time())

            cursor.execute("""
                INSERT INTO token_liquidity_snapshots
                (mint, pair_address, liquidity_usd, liquidity_sol, captured_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (mint, pair_address, liquidity_usd, liquidity_sol, now, now))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error storing liquidity snapshot for {mint}: {e}")
            return False

    def get_snapshot_history(self, mint: str, lookback_hours: int = 24) -> List[LiquiditySnapshot]:
        """Get historical liquidity snapshots."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cutoff_time = int(time.time()) - (lookback_hours * 3600)

            cursor.execute("""
                SELECT mint, pair_address, liquidity_usd, liquidity_sol, captured_at
                FROM token_liquidity_snapshots
                WHERE mint = ? AND captured_at > ?
                ORDER BY captured_at ASC
            """, (mint, cutoff_time))

            snapshots = [
                LiquiditySnapshot(
                    mint=row['mint'],
                    pair_address=row['pair_address'],
                    liquidity_usd=row['liquidity_usd'],
                    liquidity_sol=row['liquidity_sol'],
                    captured_at=row['captured_at']
                )
                for row in cursor.fetchall()
            ]

            conn.close()
            return snapshots
        except Exception as e:
            logger.error(f"Error getting liquidity history for {mint}: {e}")
            return []

    def compute_health_score(self, mint: str) -> LiquidityHealthScore:
        """
        Compute liquidity health score.

        Combines:
        - Liquidity level (40%)
        - Liquidity growth (30%)
        - Liquidity stability (30%)
        """
        snapshots_24h = self.get_snapshot_history(mint, lookback_hours=24)
        snapshots_7d = self.get_snapshot_history(mint, lookback_hours=168)

        if not snapshots_24h:
            return LiquidityHealthScore(
                mint=mint,
                health_band='DANGER',
                health_score=0,
                liquidity_level_score=0,
                liquidity_growth_score=0,
                liquidity_stability_score=0,
                current_liquidity=0,
                liquidity_trend='unknown',
                reasons=['No liquidity data available']
            )

        # Current liquidity (last snapshot)
        current_liq = snapshots_24h[-1].liquidity_usd
        earliest_liq = snapshots_24h[0].liquidity_usd if len(snapshots_24h) > 1 else current_liq

        # 1. Liquidity Level Score (40%)
        level_score = self._compute_liquidity_level_score(current_liq)

        # 2. Liquidity Growth Score (30%)
        growth_score = self._compute_liquidity_growth_score(snapshots_24h, snapshots_7d)

        # 3. Liquidity Stability Score (30%)
        stability_score = self._compute_liquidity_stability_score(snapshots_24h)

        # Composite health score
        health_score = (
            level_score * 0.40 +
            growth_score * 0.30 +
            stability_score * 0.30
        )

        # Determine band
        if health_score >= 75:
            band = 'HEALTHY'
        elif health_score >= 50:
            band = 'MODERATE'
        else:
            band = 'DANGER'

        # Determine trend
        if len(snapshots_24h) > 2:
            recent = mean([s.liquidity_usd for s in snapshots_24h[-3:]])
            old = mean([s.liquidity_usd for s in snapshots_24h[:3]])
            if recent > old * 1.1:
                trend = 'growing'
            elif recent < old * 0.9:
                trend = 'declining'
            else:
                trend = 'stable'
        else:
            trend = 'unknown'

        # Reasons
        reasons = []
        if level_score < 40:
            reasons.append(f"Low liquidity: ${current_liq:,.0f}")
        if growth_score < 40:
            reasons.append("Declining liquidity trend")
        if stability_score < 40:
            reasons.append("High liquidity volatility")

        return LiquidityHealthScore(
            mint=mint,
            health_band=band,
            health_score=health_score,
            liquidity_level_score=level_score,
            liquidity_growth_score=growth_score,
            liquidity_stability_score=stability_score,
            current_liquidity=current_liq,
            liquidity_trend=trend,
            reasons=reasons if reasons else ['Adequate liquidity levels']
        )

    def _compute_liquidity_level_score(self, current_liq: float) -> float:
        """Score based on absolute liquidity level."""
        if current_liq >= self.min_healthy_liquidity:  # $10k+
            return 100
        if current_liq >= self.min_healthy_liquidity / 2:  # $5k+
            return 80
        if current_liq >= self.mod_healthy_liquidity:  # $1k+
            return 60
        if current_liq >= self.mod_healthy_liquidity / 2:  # $500+
            return 40
        if current_liq > 0:
            return 20
        return 0

    def _compute_liquidity_growth_score(self, snapshots_24h: List[LiquiditySnapshot],
                                       snapshots_7d: List[LiquiditySnapshot]) -> float:
        """Score based on liquidity growth trend."""
        if not snapshots_24h or len(snapshots_24h) < 2:
            return 50  # Neutral

        # 24h growth
        growth_24h = (snapshots_24h[-1].liquidity_usd - snapshots_24h[0].liquidity_usd) / snapshots_24h[0].liquidity_usd if snapshots_24h[0].liquidity_usd > 0 else 0

        score = 50  # Base score

        # Positive growth
        if growth_24h > 0.2:  # >20% growth
            score += 30
        elif growth_24h > 0.0:  # Any growth
            score += 15

        # Negative growth (declining)
        if growth_24h < -0.2:  # >20% decline
            score -= 30
        elif growth_24h < 0.0:  # Any decline
            score -= 15

        return max(0, min(100, score))

    def _compute_liquidity_stability_score(self, snapshots: List[LiquiditySnapshot]) -> float:
        """Score based on liquidity volatility."""
        if not snapshots or len(snapshots) < 3:
            return 50  # Neutral

        values = [s.liquidity_usd for s in snapshots]
        avg = mean(values)
        if avg == 0:
            return 0

        std_dev = stdev(values) if len(values) > 1 else 0
        coefficient_of_variation = std_dev / avg

        # Low volatility = high score
        if coefficient_of_variation < 0.1:  # <10% CV
            return 100
        if coefficient_of_variation < 0.2:  # <20% CV
            return 85
        if coefficient_of_variation < 0.5:  # <50% CV
            return 70
        if coefficient_of_variation < self.volatility_threshold:  # <50% CV
            return 50
        else:  # >50% CV
            return 30

    def detect_rug_pull_risk(self, mint: str) -> LiquidityRiskAssessment:
        """Detect liquidity-based rug pull risk."""
        snapshots_24h = self.get_snapshot_history(mint, lookback_hours=24)
        snapshots_7d = self.get_snapshot_history(mint, lookback_hours=168)

        if not snapshots_24h:
            return LiquidityRiskAssessment(
                mint=mint,
                liquidity_risk='UNKNOWN',
                risk_score=0,
                drop_percent_24h=0,
                drop_percent_7d=0,
                rug_pull_likelihood=0,
                warning_reasons=['No liquidity data available']
            )

        current_liq = snapshots_24h[-1].liquidity_usd

        # Calculate drops
        drop_24h = (snapshots_24h[-1].liquidity_usd - snapshots_24h[0].liquidity_usd) / snapshots_24h[0].liquidity_usd if snapshots_24h[0].liquidity_usd > 0 else 0
        drop_7d = (snapshots_7d[-1].liquidity_usd - snapshots_7d[0].liquidity_usd) / snapshots_7d[0].liquidity_usd if snapshots_7d[0].liquidity_usd > 0 else 0

        risk_score = 0
        risk_reasons = []
        rug_likelihood = 0

        # Severe drop (>95%)
        if drop_24h < -self.severe_drop:
            risk_score += 50
            rug_likelihood += 40
            risk_reasons.append(f"Critical liquidity drop: {abs(drop_24h)*100:.1f}%")

        # Critical drop (>80%)
        elif drop_24h < -self.critical_drop:
            risk_score += 40
            rug_likelihood += 30
            risk_reasons.append(f"Severe liquidity drop: {abs(drop_24h)*100:.1f}%")

        # Moderate drop (>50%)
        elif drop_24h < -0.5:
            risk_score += 25
            rug_likelihood += 15
            risk_reasons.append(f"Significant liquidity drop: {abs(drop_24h)*100:.1f}%")

        # Low liquidity
        if current_liq < 1000:
            risk_score += 20
            rug_likelihood += 10
            risk_reasons.append(f"Very low liquidity: ${current_liq:,.0f}")

        if current_liq < 100:
            risk_score += 30
            rug_likelihood += 20
            risk_reasons.append(f"Critical low liquidity: ${current_liq:,.0f}")

        # Consistent decline over 7d
        if drop_7d < -0.5 and drop_24h < 0:
            risk_score += 15
            rug_likelihood += 10
            risk_reasons.append("Consistent liquidity decline over 7 days")

        # Normalize scores
        risk_score = min(100, risk_score)
        rug_likelihood = min(100, rug_likelihood)

        # Determine risk band
        if risk_score >= 75:
            risk_band = 'CRITICAL'
        elif risk_score >= 50:
            risk_band = 'DANGER'
        elif risk_score >= 25:
            risk_band = 'WARNING'
        else:
            risk_band = 'SAFE'

        return LiquidityRiskAssessment(
            mint=mint,
            liquidity_risk=risk_band,
            risk_score=risk_score,
            drop_percent_24h=drop_24h,
            drop_percent_7d=drop_7d,
            rug_pull_likelihood=rug_likelihood,
            warning_reasons=risk_reasons if risk_reasons else ['Liquidity stable']
        )

    def cache_health_assessment(self, mint: str, health: LiquidityHealthScore) -> bool:
        """Cache latest health assessment."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            now = int(time.time())

            # Get 24h and 7d changes
            snapshots_24h = self.get_snapshot_history(mint, 24)
            change_24h = ((snapshots_24h[-1].liquidity_usd - snapshots_24h[0].liquidity_usd) / snapshots_24h[0].liquidity_usd * 100) if snapshots_24h else 0

            snapshots_7d = self.get_snapshot_history(mint, 168)
            change_7d = ((snapshots_7d[-1].liquidity_usd - snapshots_7d[0].liquidity_usd) / snapshots_7d[0].liquidity_usd * 100) if snapshots_7d else 0

            cursor.execute("""
                INSERT OR REPLACE INTO token_liquidity_health
                (mint, health_band, health_score, current_liquidity, liquidity_trend,
                 liquidity_24h_change, liquidity_7d_change, assessed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (mint, health.health_band, health.health_score, health.current_liquidity,
                  health.liquidity_trend, change_24h, change_7d, now))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error caching health assessment for {mint}: {e}")
            return False

    def cache_risk_assessment(self, mint: str, risk: LiquidityRiskAssessment) -> bool:
        """Cache latest risk assessment."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            now = int(time.time())

            cursor.execute("""
                INSERT OR REPLACE INTO token_liquidity_risks
                (mint, liquidity_risk, risk_score, drop_percent_24h, drop_percent_7d,
                 rug_pull_likelihood, last_assessed)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (mint, risk.liquidity_risk, risk.risk_score, risk.drop_percent_24h,
                  risk.drop_percent_7d, risk.rug_pull_likelihood, now))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error caching risk assessment for {mint}: {e}")
            return False


# Singleton instance
_liquidity_intelligence: Optional[LiquidityIntelligence] = None


def get_liquidity_intelligence(db_path: str = 'database/flex_complete_database.db') -> LiquidityIntelligence:
    """Get or create singleton liquidity intelligence engine."""
    global _liquidity_intelligence
    if _liquidity_intelligence is None:
        _liquidity_intelligence = LiquidityIntelligence(db_path)
    return _liquidity_intelligence
