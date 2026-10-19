"""
RPC Savings Dashboard - Backend API Layer
Date: 2026-03-05
Purpose: Query rpc_metrics and return dashboard data for frontend visualization
"""

import sqlite3
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
import logging
from src.utils.db_locking import db_connect

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "database/flex_complete_database.db")

# ============================================================================
# DATA CLASSES (for type safety and JSON serialization)
# ============================================================================


@dataclass
class DailySavingsMetric:
    """Single day of savings data"""
    day: str
    credits_spent: int
    credits_saved: int
    credits_baseline: int
    savings_pct: float
    cache_skip_events: int
    cache_refresh_events: int
    full_scan_events: int
    no_cache_events: int
    credits_saved_skip: int
    credits_saved_refresh: int
    total_requests: int
    cache_hit_events: int
    cache_hit_rate_pct: float


@dataclass
class SectionBreakdown:
    """Savings breakdown by section (funder_incoming, creator_funding, etc)"""
    section: str
    day: str
    credits_spent: int
    credits_saved: int
    credits_baseline: int
    savings_pct: float
    cache_skip_events: int
    cache_refresh_events: int
    total_requests: int


@dataclass
class CachePerformance:
    """Performance metrics for each cache action type"""
    cache_action: str
    event_count: int
    total_credits_saved: int
    avg_credits_saved: float
    min_credits_saved: int
    max_credits_saved: int
    efficiency_per_event: float
    first_seen: str
    last_seen: str


@dataclass
class CumulativeSummary:
    """All-time or period-wide summary"""
    period_start: str
    period_end: str
    total_credits_spent: int
    total_credits_saved: int
    total_credits_baseline: int
    overall_savings_pct: float
    total_requests: int
    total_cache_hits: int
    overall_hit_rate_pct: float
    skip_savings: int
    refresh_savings: int
    skip_count: int
    refresh_count: int


@dataclass
class Dashboard24h:
    """Real-time dashboard summary (last 24 hours)"""
    credits_spent_24h: int
    credits_saved_24h: int
    credits_baseline_24h: int
    savings_pct_24h: float
    cache_hit_rate_24h: float
    skip_savings_24h: int
    refresh_savings_24h: int
    skip_count_24h: int
    refresh_count_24h: int
    total_requests_24h: int


@dataclass
class DashboardResponse:
    """Top-level dashboard response structure"""
    query_time: str
    summary_24h: Dashboard24h
    daily_metrics: List[DailySavingsMetric]
    section_breakdown: List[SectionBreakdown]
    cache_performance: List[CachePerformance]
    cumulative_summary: CumulativeSummary


# ============================================================================
# DATABASE QUERIES
# ============================================================================


def get_db_connection():
    """Get a read-only SQLite connection for dashboard queries."""
    conn = db_connect(DB_PATH, timeout=30, read_only=True)
    conn.row_factory = sqlite3.Row  # Return rows as dictionaries
    return conn


def query_daily_savings(days: int = 30) -> List[DailySavingsMetric]:
    """
    Get daily savings metrics for the last N days.

    Args:
        days: Number of days to retrieve (default 30)

    Returns:
        List of DailySavingsMetric objects
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(f"""
            SELECT *
            FROM v_rpc_daily_savings
            WHERE day >= DATE('now', '-{days} days')
            ORDER BY day ASC
        """)

        rows = cur.fetchall()
        conn.close()

        metrics = []
        for row in rows:
            metrics.append(DailySavingsMetric(
                day=row['day'],
                credits_spent=row['credits_spent'] or 0,
                credits_saved=row['credits_saved'] or 0,
                credits_baseline=row['credits_baseline'] or 0,
                savings_pct=row['savings_pct'] or 0.0,
                cache_skip_events=row['cache_skip_events'] or 0,
                cache_refresh_events=row['cache_refresh_events'] or 0,
                full_scan_events=row['full_scan_events'] or 0,
                no_cache_events=row['no_cache_events'] or 0,
                credits_saved_skip=row['credits_saved_skip'] or 0,
                credits_saved_refresh=row['credits_saved_refresh'] or 0,
                total_requests=row['total_requests'] or 0,
                cache_hit_events=row['cache_hit_events'] or 0,
                cache_hit_rate_pct=row['cache_hit_rate_pct'] or 0.0,
            ))

        return metrics

    except Exception as e:
        logger.error(f"[RPC_SAVINGS] Failed to query daily savings: {e}")
        return []


def query_section_breakdown(days: int = 30) -> List[SectionBreakdown]:
    """
    Get savings breakdown by section.

    Args:
        days: Number of days to analyze

    Returns:
        List of SectionBreakdown objects
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(f"""
            SELECT *
            FROM v_rpc_savings_by_section
            WHERE day >= DATE('now', '-{days} days')
            ORDER BY day DESC, credits_saved DESC
        """)

        rows = cur.fetchall()
        conn.close()

        breakdown = []
        for row in rows:
            breakdown.append(SectionBreakdown(
                section=row['section'],
                day=row['day'],
                credits_spent=row['credits_spent'] or 0,
                credits_saved=row['credits_saved'] or 0,
                credits_baseline=row['credits_baseline'] or 0,
                savings_pct=row['savings_pct'] or 0.0,
                cache_skip_events=row['cache_skip_events'] or 0,
                cache_refresh_events=row['cache_refresh_events'] or 0,
                total_requests=row['total_requests'] or 0,
            ))

        return breakdown

    except Exception as e:
        logger.error(f"[RPC_SAVINGS] Failed to query section breakdown: {e}")
        return []


def query_cache_performance() -> List[CachePerformance]:
    """
    Get performance metrics for each cache action type.

    Returns:
        List of CachePerformance objects
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM v_rpc_cache_performance
            ORDER BY total_credits_saved DESC
        """)

        rows = cur.fetchall()
        conn.close()

        performance = []
        for row in rows:
            performance.append(CachePerformance(
                cache_action=row['cache_action'],
                event_count=row['event_count'] or 0,
                total_credits_saved=row['total_credits_saved'] or 0,
                avg_credits_saved=float(row['avg_credits_saved'] or 0),
                min_credits_saved=row['min_credits_saved'] or 0,
                max_credits_saved=row['max_credits_saved'] or 0,
                efficiency_per_event=float(row['efficiency_per_event'] or 0),
                first_seen=row['first_seen'] or '',
                last_seen=row['last_seen'] or '',
            ))

        return performance

    except Exception as e:
        logger.error(f"[RPC_SAVINGS] Failed to query cache performance: {e}")
        return []


def query_cumulative_summary() -> Optional[CumulativeSummary]:
    """
    Get all-time cumulative savings summary.

    Returns:
        CumulativeSummary object or None if no data
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT * FROM v_rpc_cumulative_savings")
        row = cur.fetchone()
        conn.close()

        if not row:
            return None

        return CumulativeSummary(
            period_start=row['period_start'] or '',
            period_end=row['period_end'] or '',
            total_credits_spent=row['total_credits_spent'] or 0,
            total_credits_saved=row['total_credits_saved'] or 0,
            total_credits_baseline=row['total_credits_baseline'] or 0,
            overall_savings_pct=row['overall_savings_pct'] or 0.0,
            total_requests=row['total_requests'] or 0,
            total_cache_hits=row['total_cache_hits'] or 0,
            overall_hit_rate_pct=row['overall_hit_rate_pct'] or 0.0,
            skip_savings=row['skip_savings'] or 0,
            refresh_savings=row['refresh_savings'] or 0,
            skip_count=row['skip_count'] or 0,
            refresh_count=row['refresh_count'] or 0,
        )

    except Exception as e:
        logger.error(f"[RPC_SAVINGS] Failed to query cumulative summary: {e}")
        return None


def query_dashboard_24h() -> Optional[Dashboard24h]:
    """
    Get real-time dashboard summary for last 24 hours.

    Returns:
        Dashboard24h object or None if no data
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT * FROM v_rpc_dashboard_24h")
        row = cur.fetchone()
        conn.close()

        if not row:
            return None

        return Dashboard24h(
            credits_spent_24h=row['credits_spent_24h'] or 0,
            credits_saved_24h=row['credits_saved_24h'] or 0,
            credits_baseline_24h=row['credits_baseline_24h'] or 0,
            savings_pct_24h=row['savings_pct_24h'] or 0.0,
            cache_hit_rate_24h=row['cache_hit_rate_24h'] or 0.0,
            skip_savings_24h=row['skip_savings_24h'] or 0,
            refresh_savings_24h=row['refresh_savings_24h'] or 0,
            skip_count_24h=row['skip_count_24h'] or 0,
            refresh_count_24h=row['refresh_count_24h'] or 0,
            total_requests_24h=row['total_requests_24h'] or 0,
        )

    except Exception as e:
        logger.error(f"[RPC_SAVINGS] Failed to query 24h dashboard: {e}")
        return None


# ============================================================================
# API ENDPOINTS (Flask examples)
# ============================================================================


def get_dashboard_data(days: int = 30) -> Dict[str, Any]:
    """
    Get complete dashboard data for frontend.

    Args:
        days: Number of days to include in metrics (default 30)

    Returns:
        Dictionary with all dashboard data
    """
    # Query all data
    daily_metrics = query_daily_savings(days)
    section_breakdown = query_section_breakdown(days)
    cache_performance = query_cache_performance()
    cumulative_summary = query_cumulative_summary()
    dashboard_24h = query_dashboard_24h()

    # Build response
    response = {
        'query_time': datetime.utcnow().isoformat(),
        'summary_24h': asdict(dashboard_24h) if dashboard_24h else None,
        'daily_metrics': [asdict(m) for m in daily_metrics],
        'section_breakdown': [asdict(s) for s in section_breakdown],
        'cache_performance': [asdict(c) for c in cache_performance],
        'cumulative_summary': asdict(cumulative_summary) if cumulative_summary else None,
    }

    return response


def get_dashboard_json(days: int = 30) -> str:
    """Get dashboard data as JSON string."""
    data = get_dashboard_data(days)
    return json.dumps(data, indent=2, default=str)


# ============================================================================
# FLASK ROUTE EXAMPLE
# ============================================================================

"""
Usage in Flask app:

@app.route('/api/rpc-savings/dashboard', methods=['GET'])
def api_rpc_dashboard():
    days = request.args.get('days', 30, type=int)
    data = get_dashboard_data(days)
    return jsonify(data)

@app.route('/api/rpc-savings/daily', methods=['GET'])
def api_rpc_daily():
    days = request.args.get('days', 30, type=int)
    metrics = query_daily_savings(days)
    return jsonify([asdict(m) for m in metrics])

@app.route('/api/rpc-savings/24h', methods=['GET'])
def api_rpc_24h():
    data = query_dashboard_24h()
    return jsonify(asdict(data) if data else {})

@app.route('/api/rpc-savings/section-breakdown', methods=['GET'])
def api_rpc_section_breakdown():
    days = request.args.get('days', 30, type=int)
    breakdown = query_section_breakdown(days)
    return jsonify([asdict(s) for s in breakdown])

@app.route('/api/rpc-savings/cache-performance', methods=['GET'])
def api_rpc_cache_performance():
    performance = query_cache_performance()
    return jsonify([asdict(c) for c in performance])
"""
