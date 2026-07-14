"""
RPC Efficiency Score API - Backend Implementation
Date: 2026-03-05
Purpose: Track and visualize RPC optimization efficiency
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum
import logging
from src.utils.db_locking import db_connect

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "database/flex_complete_database.db")

# ============================================================================
# ENUMS & CONSTANTS
# ============================================================================


class HealthStatus(Enum):
    """Health status interpretation of efficiency score"""
    POOR = "POOR"              # < 1: Spending more than saving
    WEAK = "WEAK"              # 1-3: Weak optimization
    GOOD = "GOOD"              # 3-5: Good optimization
    EXCELLENT = "EXCELLENT"    # 5-10: Excellent optimization
    OUTSTANDING = "OUTSTANDING"  # >10: Very strong optimization


class AlertLevel(Enum):
    """Alert levels based on efficiency"""
    ALERT = "ALERT"       # Efficiency < 3 (critical)
    WARNING = "WARNING"   # Efficiency < 5 (warning)
    NORMAL = "NORMAL"     # Efficiency >= 5 (healthy)


EFFICIENCY_THRESHOLDS = {
    'poor': 1.0,
    'weak': 3.0,
    'good': 5.0,
    'excellent': 10.0,
}

# ============================================================================
# DATA CLASSES
# ============================================================================


@dataclass
class DailyEfficiency:
    """Daily efficiency metrics"""
    day: str
    credits_spent: int
    credits_saved: int
    efficiency_score: float
    credits_baseline: int
    total_requests: int
    cache_hits: int
    cache_hit_rate_pct: float


@dataclass
class EfficiencyScore24h:
    """24-hour efficiency summary"""
    efficiency_score_24h: float
    credits_spent_24h: int
    credits_saved_24h: int
    credits_baseline_24h: int
    total_requests_24h: int
    cache_hits_24h: int
    cache_hit_rate_pct_24h: float


@dataclass
class EfficiencyAllTime:
    """All-time efficiency metrics"""
    efficiency_score_all_time: float
    total_credits_spent: int
    total_credits_saved: int
    period_start: str
    period_end: str
    days_tracked: int
    avg_daily_credits_spent: float
    avg_daily_credits_saved: float
    overall_cache_hit_rate_pct: float


@dataclass
class EfficiencyBySection:
    """Efficiency metrics by section"""
    section: str
    day: str
    credits_spent: int
    credits_saved: int
    efficiency_score: float
    request_count: int
    cache_hit_rate_pct: float


@dataclass
class HealthReport:
    """Health status and alert information"""
    day: str
    efficiency_score: float
    health_status: str
    alert_level: str
    credits_spent: int
    credits_saved: int
    request_count: int


# ============================================================================
# DATABASE QUERIES
# ============================================================================


def get_db_connection():
    """Get a read-only SQLite connection for dashboard queries."""
    conn = db_connect(DB_PATH, timeout=30, read_only=True)
    conn.row_factory = sqlite3.Row
    return conn


def query_daily_efficiency(days: int = 30) -> List[DailyEfficiency]:
    """
    Get daily efficiency scores for last N days.

    Args:
        days: Number of days to retrieve

    Returns:
        List of DailyEfficiency objects
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(f"""
            SELECT *
            FROM v_rpc_efficiency_score
            WHERE day >= DATE('now', '-{days} days')
            ORDER BY day ASC
        """)

        rows = cur.fetchall()
        conn.close()

        metrics = []
        for row in rows:
            metrics.append(DailyEfficiency(
                day=row['day'],
                credits_spent=row['credits_spent'] or 0,
                credits_saved=row['credits_saved'] or 0,
                efficiency_score=float(row['efficiency_score'] or 0),
                credits_baseline=row['credits_baseline'] or 0,
                total_requests=row['total_requests'] or 0,
                cache_hits=row['cache_hits'] or 0,
                cache_hit_rate_pct=float(row['cache_hit_rate_pct'] or 0),
            ))

        return metrics

    except Exception as e:
        logger.error(f"[RPC_EFFICIENCY] Failed to query daily efficiency: {e}")
        return []


def query_efficiency_24h() -> Optional[EfficiencyScore24h]:
    """
    Get 24-hour efficiency score.

    Returns:
        EfficiencyScore24h object or None if no data
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT * FROM v_rpc_efficiency_24h")
        row = cur.fetchone()
        conn.close()

        if not row:
            return None

        return EfficiencyScore24h(
            efficiency_score_24h=float(row['efficiency_score_24h'] or 0),
            credits_spent_24h=row['credits_spent_24h'] or 0,
            credits_saved_24h=row['credits_saved_24h'] or 0,
            credits_baseline_24h=row['credits_baseline_24h'] or 0,
            total_requests_24h=row['total_requests_24h'] or 0,
            cache_hits_24h=row['cache_hits_24h'] or 0,
            cache_hit_rate_pct_24h=float(row['cache_hit_rate_pct_24h'] or 0),
        )

    except Exception as e:
        logger.error(f"[RPC_EFFICIENCY] Failed to query 24h efficiency: {e}")
        return None


def query_efficiency_all_time() -> Optional[EfficiencyAllTime]:
    """
    Get all-time efficiency metrics.

    Returns:
        EfficiencyAllTime object or None
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT * FROM v_rpc_efficiency_all_time")
        row = cur.fetchone()
        conn.close()

        if not row:
            return None

        return EfficiencyAllTime(
            efficiency_score_all_time=float(row['efficiency_score_all_time'] or 0),
            total_credits_spent=row['total_credits_spent'] or 0,
            total_credits_saved=row['total_credits_saved'] or 0,
            period_start=row['period_start'] or '',
            period_end=row['period_end'] or '',
            days_tracked=row['days_tracked'] or 0,
            avg_daily_credits_spent=float(row['avg_daily_credits_spent'] or 0),
            avg_daily_credits_saved=float(row['avg_daily_credits_saved'] or 0),
            overall_cache_hit_rate_pct=float(row['overall_cache_hit_rate_pct'] or 0),
        )

    except Exception as e:
        logger.error(f"[RPC_EFFICIENCY] Failed to query all-time efficiency: {e}")
        return None


def query_efficiency_by_section(days: int = 30) -> List[EfficiencyBySection]:
    """
    Get efficiency metrics by section.

    Args:
        days: Number of days to analyze

    Returns:
        List of EfficiencyBySection objects
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(f"""
            SELECT *
            FROM v_rpc_efficiency_by_section
            WHERE day >= DATE('now', '-{days} days')
            ORDER BY day DESC, efficiency_score DESC
        """)

        rows = cur.fetchall()
        conn.close()

        metrics = []
        for row in rows:
            metrics.append(EfficiencyBySection(
                section=row['section'],
                day=row['day'],
                credits_spent=row['credits_spent'] or 0,
                credits_saved=row['credits_saved'] or 0,
                efficiency_score=float(row['efficiency_score'] or 0),
                request_count=row['request_count'] or 0,
                cache_hit_rate_pct=float(row['cache_hit_rate_pct'] or 0),
            ))

        return metrics

    except Exception as e:
        logger.error(f"[RPC_EFFICIENCY] Failed to query section efficiency: {e}")
        return []


def query_health_status(days: int = 7) -> List[HealthReport]:
    """
    Get health status and alert levels.

    Args:
        days: Number of days to check

    Returns:
        List of HealthReport objects
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(f"""
            SELECT *
            FROM v_rpc_efficiency_health
            WHERE day >= DATE('now', '-{days} days')
            ORDER BY day DESC
        """)

        rows = cur.fetchall()
        conn.close()

        reports = []
        for row in rows:
            reports.append(HealthReport(
                day=row['day'],
                efficiency_score=float(row['efficiency_score'] or 0),
                health_status=row['health_status'],
                alert_level=row['alert_level'],
                credits_spent=row['credits_spent'] or 0,
                credits_saved=row['credits_saved'] or 0,
                request_count=row['request_count'] or 0,
            ))

        return reports

    except Exception as e:
        logger.error(f"[RPC_EFFICIENCY] Failed to query health status: {e}")
        return []


def get_efficiency_health(efficiency_score: float) -> Dict[str, str]:
    """
    Interpret efficiency score and return health status.

    Args:
        efficiency_score: The calculated efficiency score

    Returns:
        Dictionary with health_status and alert_level
    """
    if efficiency_score < EFFICIENCY_THRESHOLDS['poor']:
        health = HealthStatus.POOR.value
        alert = AlertLevel.ALERT.value
    elif efficiency_score < EFFICIENCY_THRESHOLDS['weak']:
        health = HealthStatus.WEAK.value
        alert = AlertLevel.ALERT.value
    elif efficiency_score < EFFICIENCY_THRESHOLDS['good']:
        health = HealthStatus.GOOD.value
        alert = AlertLevel.WARNING.value
    elif efficiency_score < EFFICIENCY_THRESHOLDS['excellent']:
        health = HealthStatus.EXCELLENT.value
        alert = AlertLevel.NORMAL.value
    else:
        health = HealthStatus.OUTSTANDING.value
        alert = AlertLevel.NORMAL.value

    return {
        'health_status': health,
        'alert_level': alert,
    }


# ============================================================================
# AGGREGATION FUNCTIONS
# ============================================================================


def get_efficiency_dashboard(days: int = 30) -> Dict[str, Any]:
    """
    Get complete efficiency dashboard data.

    Args:
        days: Number of days to include

    Returns:
        Dictionary with all efficiency metrics
    """
    daily_metrics = query_daily_efficiency(days)
    efficiency_24h = query_efficiency_24h()
    efficiency_all_time = query_efficiency_all_time()
    by_section = query_efficiency_by_section(days)
    health_status = query_health_status(7)

    response = {
        'query_time': datetime.utcnow().isoformat(),
        'period_days': days,
        'efficiency_24h': asdict(efficiency_24h) if efficiency_24h else None,
        'efficiency_all_time': asdict(efficiency_all_time) if efficiency_all_time else None,
        'daily_metrics': [asdict(m) for m in daily_metrics],
        'by_section': [asdict(s) for s in by_section],
        'health_status': [asdict(h) for h in health_status],
    }

    return response


# ============================================================================
# FLASK ROUTE EXAMPLES
# ============================================================================

"""
Usage in Flask app:

@app.route('/api/rpc-efficiency/dashboard', methods=['GET'])
def api_efficiency_dashboard():
    days = request.args.get('days', 30, type=int)
    data = get_efficiency_dashboard(days)
    return jsonify(data)

@app.route('/api/rpc-efficiency/daily', methods=['GET'])
def api_efficiency_daily():
    days = request.args.get('days', 30, type=int)
    metrics = query_daily_efficiency(days)
    return jsonify([asdict(m) for m in metrics])

@app.route('/api/rpc-efficiency/24h', methods=['GET'])
def api_efficiency_24h():
    data = query_efficiency_24h()
    return jsonify(asdict(data) if data else {})

@app.route('/api/rpc-efficiency/all-time', methods=['GET'])
def api_efficiency_all_time():
    data = query_efficiency_all_time()
    return jsonify(asdict(data) if data else {})

@app.route('/api/rpc-efficiency/by-section', methods=['GET'])
def api_efficiency_section():
    days = request.args.get('days', 30, type=int)
    metrics = query_efficiency_by_section(days)
    return jsonify([asdict(m) for m in metrics])

@app.route('/api/rpc-efficiency/health', methods=['GET'])
def api_efficiency_health():
    days = request.args.get('days', 7, type=int)
    reports = query_health_status(days)
    return jsonify([asdict(r) for r in reports])
"""
