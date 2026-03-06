"""
Helius Optimization Metrics API

Provides Flask endpoints to expose optimization metrics to the dashboard.

Usage in main.py:
    from http_instrumentation.optimization_api import register_optimization_routes
    register_optimization_routes(app, db_path='flex_complete_database.db')
"""

from flask import Blueprint, jsonify
import sqlite3
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta


class OptimizationMetrics:
    """Query optimization metrics from database"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def get_optimization_efficiency_24h(self) -> Dict[str, Any]:
        """Get overall optimization efficiency metrics for last 24h"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                COUNT(*) as total_scans,
                SUM(CASE WHEN deep_scan_pages = 1 THEN 1 ELSE 0 END) as single_page_scans,
                SUM(CASE WHEN deep_scan_pages > 1 THEN 1 ELSE 0 END) as multi_page_scans,
                SUM(CASE WHEN budget_exhausted = 1 THEN 1 ELSE 0 END) as budget_exhausted_count,
                SUM(CASE WHEN tombstone_skip = 1 THEN 1 ELSE 0 END) as tombstone_skips,
                SUM(CASE WHEN prefilter_shortlist = 1 THEN 1 ELSE 0 END) as shortlisted_scans
            FROM wallet_scan_metrics
            WHERE created_at >= datetime('now', '-24 hours')
              AND section = 'funder_incoming'
        """)

        row = cursor.fetchone()
        conn.close()

        if not row or row[0] == 0:
            return {
                'total_scans': 0,
                'single_page_scans': 0,
                'multi_page_scans': 0,
                'budget_exhausted_count': 0,
                'tombstone_skips': 0,
                'shortlisted_scans': 0,
                'pct_single_page': 0.0,
                'pct_multi_page': 0.0,
                'pct_shortlisted': 0.0,
            }

        total, single, multi, budget_ex, tombstone, shortlist = row

        return {
            'total_scans': total,
            'single_page_scans': single or 0,
            'multi_page_scans': multi or 0,
            'budget_exhausted_count': budget_ex or 0,
            'tombstone_skips': tombstone or 0,
            'shortlisted_scans': shortlist or 0,
            'pct_single_page': round(100.0 * (single or 0) / total, 1),
            'pct_multi_page': round(100.0 * (multi or 0) / total, 1),
            'pct_shortlisted': round(100.0 * (shortlist or 0) / total, 1),
        }

    def get_budget_exhaustion_summary(self) -> Dict[str, Any]:
        """Get budget exhaustion summary"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                COUNT(*) as total_creators,
                SUM(CASE WHEN budget_exhausted = 1 THEN 1 ELSE 0 END) as exhausted_count,
                ROUND(AVG(credits_spent), 0) as avg_credits_spent,
                MAX(credits_spent) as max_credits_spent,
                ROUND(AVG(CAST(credits_spent AS FLOAT) / max_credits * 100), 1) as avg_pct_budget_used
            FROM creator_extraction_budget
            WHERE started_at >= datetime('now', '-24 hours')
        """)

        row = cursor.fetchone()
        conn.close()

        if not row or row[0] == 0:
            return {
                'total_creators': 0,
                'exhausted_count': 0,
                'avg_credits_spent': 0,
                'max_credits_spent': 0,
                'avg_pct_budget_used': 0.0,
                'pct_budget_exhausted': 0.0,
            }

        total, exhausted, avg_spent, max_spent, pct_used = row

        return {
            'total_creators': total,
            'exhausted_count': exhausted or 0,
            'avg_credits_spent': avg_spent or 0,
            'max_credits_spent': max_spent or 0,
            'avg_pct_budget_used': pct_used or 0.0,
            'pct_budget_exhausted': round(100.0 * (exhausted or 0) / total, 1) if total > 0 else 0.0,
        }

    def get_tombstone_stats(self) -> Dict[str, Any]:
        """Get tombstone (empty wallet skip) statistics"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                SUM(CASE WHEN tombstone_type = 'empty' THEN 1 ELSE 0 END) as empty_count,
                SUM(CASE WHEN tombstone_type = 'shallow' THEN 1 ELSE 0 END) as shallow_count,
                COUNT(*) as total_tombstones
            FROM wallet_analysis_state
            WHERE tombstone_type IS NOT NULL
        """)

        row = cursor.fetchone()

        cursor.execute("""
            SELECT
                SUM(CASE WHEN tombstone_skip = 1 THEN 1 ELSE 0 END) as skips_24h
            FROM wallet_scan_metrics
            WHERE created_at >= datetime('now', '-24 hours')
              AND tombstone_skip = 1
        """)

        skip_row = cursor.fetchone()
        conn.close()

        if not row:
            row = (0, 0, 0)
        if not skip_row:
            skip_row = (0,)

        empty, shallow, total = row
        skips_24h = skip_row[0] or 0

        return {
            'empty_tombstones': empty or 0,
            'shallow_tombstones': shallow or 0,
            'total_tombstones': total or 0,
            'skips_in_24h': skips_24h,
            'estimated_credits_saved_24h': (skips_24h * 150) if skips_24h > 0 else 0,
        }

    def get_funder_shortlist_stats(self) -> Dict[str, Any]:
        """Get funder prefilter shortlist statistics"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                COUNT(DISTINCT creator_address) as total_creators,
                COUNT(*) as total_funders,
                SUM(CASE WHEN shortlist_rank IS NOT NULL THEN 1 ELSE 0 END) as shortlisted_funders,
                SUM(CASE WHEN funder_type = 'cex' THEN 1 ELSE 0 END) as cex_count,
                SUM(CASE WHEN funder_type = 'infra' THEN 1 ELSE 0 END) as infra_count,
                ROUND(AVG(inbound_total_sol), 4) as avg_inbound_sol
            FROM creator_funder_summary
        """)

        row = cursor.fetchone()
        conn.close()

        if not row:
            return {
                'total_creators': 0,
                'total_funders': 0,
                'shortlisted_funders': 0,
                'cex_count': 0,
                'infra_count': 0,
                'avg_inbound_sol': 0.0,
                'pct_shortlisted': 0.0,
            }

        total_creators, total_funders, shortlisted, cex, infra, avg_sol = row

        return {
            'total_creators': total_creators or 0,
            'total_funders': total_funders or 0,
            'shortlisted_funders': shortlisted or 0,
            'cex_count': cex or 0,
            'infra_count': infra or 0,
            'avg_inbound_sol': avg_sol or 0.0,
            'pct_shortlisted': round(100.0 * (shortlisted or 0) / (total_funders or 1), 1),
        }

    def get_deep_scan_distribution(self) -> Dict[str, Any]:
        """Get distribution of deep scan pages"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                deep_scan_pages,
                COUNT(*) as count,
                ROUND(AVG(credits_estimated), 0) as avg_credits
            FROM wallet_scan_metrics
            WHERE created_at >= datetime('now', '-24 hours')
              AND section = 'funder_incoming'
            GROUP BY deep_scan_pages
            ORDER BY deep_scan_pages ASC
        """)

        rows = cursor.fetchall()
        conn.close()

        distribution = {}
        for pages, count, avg_credits in rows:
            distribution[f'{pages}_pages'] = {
                'count': count,
                'avg_credits': avg_credits,
            }

        return {
            'distribution': distribution,
            'total_scans': sum(row[1] for row in rows),
        }

    def get_optimization_timeline(self, days: int = 7) -> List[Dict]:
        """Get optimization metrics over time"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                DATE(created_at) as date,
                COUNT(*) as total_scans,
                SUM(CASE WHEN deep_scan_pages = 1 THEN 1 ELSE 0 END) as single_page_scans,
                SUM(CASE WHEN budget_exhausted = 1 THEN 1 ELSE 0 END) as budget_exhausted,
                SUM(CASE WHEN tombstone_skip = 1 THEN 1 ELSE 0 END) as tombstone_skips,
                ROUND(AVG(credits_estimated), 0) as avg_credits_per_scan
            FROM wallet_scan_metrics
            WHERE created_at >= datetime('now', '-' || ? || ' days')
              AND section = 'funder_incoming'
            GROUP BY DATE(created_at)
            ORDER BY date ASC
        """, (days,))

        rows = cursor.fetchall()
        conn.close()

        timeline = []
        for date, total, single, budget, tombstone, avg_cr in rows:
            timeline.append({
                'date': date,
                'total_scans': total,
                'single_page_scans': single or 0,
                'pct_single_page': round(100.0 * (single or 0) / total, 1),
                'budget_exhausted': budget or 0,
                'tombstone_skips': tombstone or 0,
                'avg_credits_per_scan': avg_cr or 0,
            })

        return timeline


def register_optimization_routes(app, db_path: str = 'flex_complete_database.db'):
    """Register optimization API routes with Flask app"""

    metrics = OptimizationMetrics(db_path)

    @app.route('/api/optimization/efficiency-24h')
    def api_optimization_efficiency():
        """Get optimization efficiency metrics for last 24h"""
        try:
            data = metrics.get_optimization_efficiency_24h()
            return jsonify({
                'status': 'success',
                'data': data,
                'timestamp': datetime.now().isoformat(),
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e),
            }), 500

    @app.route('/api/optimization/budget-summary')
    def api_budget_summary():
        """Get budget exhaustion summary"""
        try:
            data = metrics.get_budget_exhaustion_summary()
            return jsonify({
                'status': 'success',
                'data': data,
                'timestamp': datetime.now().isoformat(),
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e),
            }), 500

    @app.route('/api/optimization/tombstone-stats')
    def api_tombstone_stats():
        """Get tombstone (empty wallet skip) statistics"""
        try:
            data = metrics.get_tombstone_stats()
            return jsonify({
                'status': 'success',
                'data': data,
                'timestamp': datetime.now().isoformat(),
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e),
            }), 500

    @app.route('/api/optimization/shortlist-stats')
    def api_shortlist_stats():
        """Get funder prefilter shortlist statistics"""
        try:
            data = metrics.get_funder_shortlist_stats()
            return jsonify({
                'status': 'success',
                'data': data,
                'timestamp': datetime.now().isoformat(),
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e),
            }), 500

    @app.route('/api/optimization/deep-scan-distribution')
    def api_deep_scan_distribution():
        """Get deep scan page distribution"""
        try:
            data = metrics.get_deep_scan_distribution()
            return jsonify({
                'status': 'success',
                'data': data,
                'timestamp': datetime.now().isoformat(),
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e),
            }), 500

    @app.route('/api/optimization/timeline')
    def api_optimization_timeline():
        """Get optimization metrics over time"""
        try:
            days = 7  # Default to last 7 days
            data = metrics.get_optimization_timeline(days)
            return jsonify({
                'status': 'success',
                'data': data,
                'days': days,
                'timestamp': datetime.now().isoformat(),
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e),
            }), 500

    @app.route('/api/optimization/summary')
    def api_optimization_summary():
        """Get complete optimization summary (all metrics)"""
        try:
            data = {
                'efficiency_24h': metrics.get_optimization_efficiency_24h(),
                'budget_summary': metrics.get_budget_exhaustion_summary(),
                'tombstone_stats': metrics.get_tombstone_stats(),
                'shortlist_stats': metrics.get_funder_shortlist_stats(),
                'deep_scan_distribution': metrics.get_deep_scan_distribution(),
                'timeline': metrics.get_optimization_timeline(7),
            }
            return jsonify({
                'status': 'success',
                'data': data,
                'timestamp': datetime.now().isoformat(),
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e),
            }), 500


# Standalone usage example
if __name__ == '__main__':
    metrics = OptimizationMetrics('flex_complete_database.db')

    print("\n📊 OPTIMIZATION METRICS SNAPSHOT")
    print("=" * 80)

    eff = metrics.get_optimization_efficiency_24h()
    print(f"\n🎯 EFFICIENCY (24h):")
    print(f"   Total scans: {eff['total_scans']}")
    print(f"   Single-page: {eff['pct_single_page']}%")
    print(f"   Multi-page: {eff['pct_multi_page']}%")
    print(f"   Shortlisted: {eff['pct_shortlisted']}%")
    print(f"   Budget exhausted: {eff['budget_exhausted_count']}")

    budget = metrics.get_budget_exhaustion_summary()
    print(f"\n💰 BUDGET (last 24h):")
    print(f"   Total creators: {budget['total_creators']}")
    print(f"   Avg credits spent: {budget['avg_credits_spent']}")
    print(f"   Avg budget usage: {budget['avg_pct_budget_used']}%")
    print(f"   Budget exhausted: {budget['exhausted_count']} creators")

    tombstone = metrics.get_tombstone_stats()
    print(f"\n⏭️  TOMBSTONES:")
    print(f"   Empty wallets: {tombstone['empty_tombstones']}")
    print(f"   Shallow classifications: {tombstone['shallow_tombstones']}")
    print(f"   Skips in 24h: {tombstone['skips_in_24h']}")
    print(f"   Est. credits saved: {tombstone['estimated_credits_saved_24h']}")

    shortlist = metrics.get_funder_shortlist_stats()
    print(f"\n📋 PREFILTER SHORTLIST:")
    print(f"   Total creators: {shortlist['total_creators']}")
    print(f"   Total funders: {shortlist['total_funders']}")
    print(f"   Shortlisted: {shortlist['shortlisted_funders']} ({shortlist['pct_shortlisted']}%)")
    print(f"   CEX funders: {shortlist['cex_count']}")
    print(f"   INFRA funders: {shortlist['infra_count']}")

    print("\n" + "=" * 80)
