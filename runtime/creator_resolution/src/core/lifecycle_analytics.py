"""
Analytical queries for token lifecycle outcomes.

Provides methods to:
- Identify bad vs good clusters
- Track token trajectories
- Detect patterns in launches
- Generate reports
"""

import sqlite3
import logging
from typing import List, Dict, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ClusterOutcome:
    cluster_id: str
    cluster_name: str
    network_name: str
    total_tokens: int
    rug_count: int
    slow_rug_count: int
    success_count: int
    rug_rate: float
    success_rate: float
    median_peak_mc: float


@dataclass
class TokenTrajectory:
    mint: str
    timestamp: int
    price_usd: float
    market_cap_usd: float
    pct_of_peak: float


class LifecycleAnalytics:
    """Analytics and reporting for token outcomes"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _query(self, sql: str, params: tuple = ()) -> List[tuple]:
        """Execute read-only query"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=15)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(sql, params)
            results = cursor.fetchall()
            conn.close()
            return results
        except Exception as e:
            logger.error(f"[ANALYTICS] Query error: {e}")
            return []

    # =====================================================================
    # CLUSTER-LEVEL ANALYTICS
    # =====================================================================

    def worst_performing_clusters(self, limit: int = 20) -> List[ClusterOutcome]:
        """Clusters with highest rug rates"""
        sql = """
            SELECT
                cluster_id,
                cluster_name,
                network_name,
                total_tokens,
                rug_count,
                slow_rug_count,
                success_count,
                rug_rate,
                success_rate,
                median_peak_market_cap
            FROM cluster_outcome_stats
            WHERE total_tokens >= 5
            ORDER BY rug_rate DESC
            LIMIT ?
        """

        results = self._query(sql, (limit,))
        return [ClusterOutcome(*row) for row in results]

    def best_performing_clusters(self, limit: int = 20) -> List[ClusterOutcome]:
        """Clusters with highest success rates"""
        sql = """
            SELECT
                cluster_id,
                cluster_name,
                network_name,
                total_tokens,
                rug_count,
                slow_rug_count,
                success_count,
                rug_rate,
                success_rate,
                median_peak_market_cap
            FROM cluster_outcome_stats
            WHERE total_tokens >= 5
            ORDER BY success_rate DESC
            LIMIT ?
        """

        results = self._query(sql, (limit,))
        return [ClusterOutcome(*row) for row in results]

    def outcome_distribution_by_network(self) -> List[Dict]:
        """Show outcome spread across networks"""
        sql = """
            SELECT
                network_name,
                outcome,
                COUNT(*) as count,
                ROUND(100.0 * COUNT(*) / (
                    SELECT COUNT(*) FROM token_outcomes o2
                    WHERE o2.cluster_id IN (
                        SELECT DISTINCT cluster_id FROM token_analysis
                        WHERE network_name = o1.network_name
                    )
                ), 2) as percentage
            FROM token_outcomes o1
            LEFT JOIN token_analysis ta ON o1.mint = ta.mint
            GROUP BY network_name, outcome
            ORDER BY network_name, outcome
        """

        results = self._query(sql)
        return [dict(row) for row in results]

    # =====================================================================
    # TOKEN-LEVEL ANALYTICS
    # =====================================================================

    def token_trajectory(self, mint: str, limit: int = 1000) -> List[TokenTrajectory]:
        """Get full lifecycle trajectory for a token"""
        sql = """
            SELECT
                timestamp,
                price_usd,
                market_cap_usd,
                ROUND(100.0 * market_cap_usd / (
                    SELECT MAX(market_cap_usd) FROM token_lifecycle_snapshots
                    WHERE mint = ?
                ), 2) as pct_of_peak
            FROM token_lifecycle_snapshots
            WHERE mint = ?
            ORDER BY timestamp ASC
            LIMIT ?
        """

        results = self._query(sql, (mint, mint, limit))
        return [TokenTrajectory(mint, *row) for row in results]

    def fastest_rugs(self, limit: int = 50) -> List[Dict]:
        """Tokens that failed quickest (lowest peak, fastest collapse)"""
        sql = """
            SELECT
                mint,
                outcome,
                peak_market_cap,
                time_to_peak_minutes,
                max_drawdown_pct,
                cluster_name,
                DATETIME(classified_at, 'unixepoch') as classified_at
            FROM token_outcomes
            WHERE outcome IN ('rug', 'slow_rug')
            ORDER BY time_to_peak_minutes ASC, peak_market_cap ASC
            LIMIT ?
        """

        results = self._query(sql, (limit,))
        return [dict(row) for row in results]

    def biggest_winners(self, limit: int = 50) -> List[Dict]:
        """Tokens with highest peak market caps and sustained them"""
        sql = """
            SELECT
                mint,
                outcome,
                peak_market_cap,
                final_market_cap,
                max_drawdown_pct,
                lifecycle_duration_minutes,
                cluster_name,
                DATETIME(classified_at, 'unixepoch') as classified_at
            FROM token_outcomes
            WHERE outcome = 'success'
            ORDER BY peak_market_cap DESC
            LIMIT ?
        """

        results = self._query(sql, (limit,))
        return [dict(row) for row in results]

    def recently_classified(self, limit: int = 100) -> List[Dict]:
        """Most recently completed tokens"""
        sql = """
            SELECT
                mint,
                outcome,
                peak_market_cap,
                final_market_cap,
                max_drawdown_pct,
                lifecycle_duration_minutes,
                cluster_name,
                DATETIME(classified_at, 'unixepoch') as classified_at
            FROM token_outcomes
            ORDER BY classified_at DESC
            LIMIT ?
        """

        results = self._query(sql, (limit,))
        return [dict(row) for row in results]

    # =====================================================================
    # PATTERN DETECTION
    # =====================================================================

    def cluster_consistency(self) -> List[Dict]:
        """Identify clusters with consistent patterns (good or bad)"""
        sql = """
            SELECT
                cluster_name,
                network_name,
                total_tokens,
                rug_rate,
                success_rate,
                (CASE
                    WHEN rug_rate > 0.7 THEN 'BAD (rug farm)'
                    WHEN success_rate > 0.5 THEN 'GOOD (high success)'
                    WHEN success_rate > 0.3 THEN 'OK (some winners)'
                    ELSE 'MIXED'
                END) as consistency_label
            FROM cluster_outcome_stats
            WHERE total_tokens >= 10
            ORDER BY
                (CASE
                    WHEN rug_rate > 0.7 THEN 0
                    WHEN success_rate > 0.5 THEN 1
                    WHEN success_rate > 0.3 THEN 2
                    ELSE 3
                END),
                rug_rate DESC
        """

        results = self._query(sql)
        return [dict(row) for row in results]

    def outcome_timeline(self, days: int = 7) -> List[Dict]:
        """Show outcomes over time"""
        sql = """
            SELECT
                DATE(DATETIME(classified_at, 'unixepoch')) as day,
                outcome,
                COUNT(*) as count
            FROM token_outcomes
            WHERE classified_at > UNIXEPOCH('now') - (? * 86400)
            GROUP BY DATE(DATETIME(classified_at, 'unixepoch')), outcome
            ORDER BY day DESC, outcome
        """

        results = self._query(sql, (days,))
        return [dict(row) for row in results]

    # =====================================================================
    # MARKET CAP METRICS
    # =====================================================================

    def peak_market_cap_distribution(self) -> List[Dict]:
        """Show distribution of peak market caps"""
        sql = """
            SELECT
                (CASE
                    WHEN peak_market_cap < 10_000 THEN '<$10k'
                    WHEN peak_market_cap < 50_000 THEN '$10k-$50k'
                    WHEN peak_market_cap < 100_000 THEN '$50k-$100k'
                    WHEN peak_market_cap < 500_000 THEN '$100k-$500k'
                    WHEN peak_market_cap < 1_000_000 THEN '$500k-$1M'
                    ELSE '>$1M'
                END) as market_cap_range,
                COUNT(*) as count,
                ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM token_outcomes), 2) as percentage
            FROM token_outcomes
            GROUP BY market_cap_range
            ORDER BY peak_market_cap ASC
        """

        results = self._query(sql)
        return [dict(row) for row in results]

    def drawdown_by_outcome(self) -> List[Dict]:
        """Average drawdown statistics by outcome"""
        sql = """
            SELECT
                outcome,
                COUNT(*) as token_count,
                ROUND(AVG(max_drawdown_pct), 2) as avg_drawdown_pct,
                ROUND(MIN(max_drawdown_pct), 2) as min_drawdown_pct,
                ROUND(MAX(max_drawdown_pct), 2) as max_drawdown_pct
            FROM token_outcomes
            GROUP BY outcome
        """

        results = self._query(sql)
        return [dict(row) for row in results]

    # =====================================================================
    # SUMMARY STATS
    # =====================================================================

    def overall_stats(self) -> Dict:
        """Overall system statistics"""
        sql = """
            SELECT
                COUNT(*) as total_classified,
                SUM(CASE WHEN outcome = 'rug' THEN 1 ELSE 0 END) as rug_count,
                SUM(CASE WHEN outcome = 'slow_rug' THEN 1 ELSE 0 END) as slow_rug_count,
                SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN outcome = 'neutral' THEN 1 ELSE 0 END) as neutral_count,
                ROUND(AVG(peak_market_cap), 0) as avg_peak_mc,
                ROUND(AVG(max_drawdown_pct), 2) as avg_drawdown_pct,
                ROUND(AVG(lifecycle_duration_minutes), 0) as avg_lifecycle_min
            FROM token_outcomes
        """

        result = self._query(sql)
        if result:
            row = result[0]
            return {
                'total_classified': row[0],
                'rug_count': row[1],
                'slow_rug_count': row[2],
                'success_count': row[3],
                'neutral_count': row[4],
                'avg_peak_mc': row[5],
                'avg_drawdown_pct': row[6],
                'avg_lifecycle_min': row[7],
                'rug_rate': (row[1] or 0) / (row[0] or 1),
                'success_rate': (row[3] or 0) / (row[0] or 1)
            }
        return {}

    def active_monitoring_stats(self) -> Dict:
        """Statistics on currently monitored tokens"""
        sql = """
            SELECT
                COUNT(*) as total_active,
                COUNT(DISTINCT cluster_id) as clusters_active,
                ROUND(AVG(last_market_cap), 0) as avg_current_mc,
                MIN(last_market_cap) as min_mc,
                MAX(last_market_cap) as max_mc,
                MAX(peak_market_cap) as highest_peak_ever
            FROM token_monitoring_state
            WHERE monitor_status = 'active'
        """

        result = self._query(sql)
        if result:
            row = result[0]
            return {
                'total_active': row[0],
                'clusters_active': row[1],
                'avg_current_mc': row[2],
                'min_mc': row[3],
                'max_mc': row[4],
                'highest_peak_ever': row[5]
            }
        return {}


# =========================================================================
# EXAMPLE USAGE
# =========================================================================

if __name__ == "__main__":
    import os

    db_path = os.environ.get('DB_PATH', 'database/flex_complete_database.db')
    analytics = LifecycleAnalytics(db_path)

    print("\n=== WORST CLUSTERS ===")
    for cluster in analytics.worst_performing_clusters(limit=10):
        print(f"{cluster.cluster_name}: {cluster.rug_rate:.1%} rug rate ({cluster.total_tokens} tokens)")

    print("\n=== BEST CLUSTERS ===")
    for cluster in analytics.best_performing_clusters(limit=10):
        print(f"{cluster.cluster_name}: {cluster.success_rate:.1%} success rate ({cluster.total_tokens} tokens)")

    print("\n=== OVERALL STATS ===")
    stats = analytics.overall_stats()
    for k, v in stats.items():
        print(f"{k}: {v}")

    print("\n=== FASTEST RUGS ===")
    for token in analytics.fastest_rugs(limit=5):
        print(f"{token['mint'][:16]}... peaked in {token['time_to_peak_minutes']}min (${token['peak_market_cap']:,.0f})")

    print("\n=== BIGGEST WINNERS ===")
    for token in analytics.biggest_winners(limit=5):
        print(f"{token['mint'][:16]}... → ${token['peak_market_cap']:,.0f} peak")

    print("\n[ANALYTICS] Module loaded successfully")
