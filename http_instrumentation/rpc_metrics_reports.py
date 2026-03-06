"""
RPC Metrics Reporting Module

Generates human-readable reports from wallet_scan_metrics data.

Usage:
    from rpc_metrics_reports import print_daily_report
    print_daily_report('flex_complete_database.db', hours=24)
"""

import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional


def get_total_credits_24h(db_path: str, hours: int = 24) -> int:
    """Get total credits consumed in last N hours."""
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT SUM(credits_estimated)
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
    """, (hours,))

    result = cursor.fetchone()[0] or 0
    conn.close()
    return result


def get_avg_credits_per_token(db_path: str, hours: int = 24) -> float:
    """
    Average credits per analyzed creator/token.
    Goal: < 20 credits per token
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            CASE WHEN COUNT(DISTINCT creator_address) = 0 THEN 0
                 ELSE ROUND(SUM(credits_estimated) / COUNT(DISTINCT creator_address), 1)
            END as avg_credits
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
          AND creator_address IS NOT NULL
    """, (hours,))

    result = cursor.fetchone()[0] or 0
    conn.close()
    return result


def get_cache_hit_rate(db_path: str, hours: int = 24) -> float:
    """
    Overall cache hit rate (wallet cache).
    Healthy: 70-95%
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            CASE WHEN COUNT(*) = 0 THEN 0
                 ELSE ROUND(100.0 * SUM(cache_hit_wallet) / COUNT(*), 1)
            END as hit_rate
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
    """, (hours,))

    result = cursor.fetchone()[0] or 0
    conn.close()
    return result


def get_avg_pages_per_wallet(db_path: str, hours: int = 24) -> float:
    """
    Average pages scanned per wallet.
    Healthy: 1.0-1.5 pages
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT AVG(CASE WHEN wallet_scan_pages > 0 THEN wallet_scan_pages ELSE 1 END)
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
    """, (hours,))

    result = cursor.fetchone()[0] or 0
    conn.close()
    return round(result, 2)


def get_top_expensive_endpoints(db_path: str, hours: int = 24, limit: int = 20) -> List[Dict]:
    """
    Top N most expensive endpoints by credits.

    Returns:
        [
            {
                'provider': 'helius_enhanced',
                'method': 'helius_enhanced_address_transactions',
                'total_credits': 5000,
                'call_count': 50,
                'avg_credits': 100,
            },
            ...
        ]
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            provider,
            method,
            SUM(credits_estimated) as total_credits,
            COUNT(*) as call_count,
            ROUND(AVG(credits_estimated), 0) as avg_credits,
            SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) as error_count,
            ROUND(AVG(latency_ms), 0) as avg_latency_ms
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
        GROUP BY provider, method
        ORDER BY total_credits DESC
        LIMIT ?
    """, (hours, limit))

    results = []
    for row in cursor.fetchall():
        results.append({
            'provider': row[0],
            'method': row[1],
            'total_credits': row[2],
            'call_count': row[3],
            'avg_credits': row[4],
            'error_count': row[5],
            'avg_latency_ms': row[6],
        })

    conn.close()
    return results


def get_credits_by_provider(db_path: str, hours: int = 24) -> Dict[str, Dict]:
    """
    Credits breakdown by provider.

    Returns:
        {
            'helius_enhanced': {
                'total_credits': 2000,
                'call_count': 20,
                'error_count': 1,
            },
            ...
        }
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            provider,
            SUM(credits_estimated) as total_credits,
            COUNT(*) as call_count,
            SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) as error_count,
            ROUND(AVG(latency_ms), 0) as avg_latency_ms,
            SUM(CASE WHEN rate_limited = 1 THEN 1 ELSE 0 END) as rate_limits
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
        GROUP BY provider
        ORDER BY total_credits DESC
    """, (hours,))

    results = {}
    for row in cursor.fetchall():
        results[row[0]] = {
            'total_credits': row[1],
            'call_count': row[2],
            'error_count': row[3],
            'avg_latency_ms': row[4],
            'rate_limits': row[5],
        }

    conn.close()
    return results


def get_credits_per_creator(db_path: str, hours: int = 24, limit: int = 20) -> List[Dict]:
    """
    Top N creators by credit consumption.

    Returns:
        [
            {
                'creator_address': 'xyz...',
                'total_credits': 200,
                'scans': 5,
                'avg_credits': 40,
            },
            ...
        ]
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            creator_address,
            SUM(credits_estimated) as total_credits,
            COUNT(*) as scans,
            ROUND(AVG(credits_estimated), 0) as avg_credits,
            SUM(cache_hit_wallet) as wallet_cache_hits,
            ROUND(AVG(wallet_scan_pages), 1) as avg_pages
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
          AND creator_address IS NOT NULL
        GROUP BY creator_address
        ORDER BY total_credits DESC
        LIMIT ?
    """, (hours, limit))

    results = []
    for row in cursor.fetchall():
        results.append({
            'creator_address': row[0][:16] + '...' if row[0] else 'unknown',
            'total_credits': row[1],
            'scans': row[2],
            'avg_credits': row[3],
            'wallet_cache_hits': row[4],
            'avg_pages': row[5],
        })

    conn.close()
    return results


def get_credits_per_day(db_path: str, days: int = 7) -> List[Dict]:
    """
    Daily credit consumption trend.

    Returns:
        [
            {
                'date': '2026-03-05',
                'total_credits': 5000,
                'call_count': 50,
                'avg_credits_per_call': 100,
            },
            ...
        ]
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            DATE(created_at) as date,
            SUM(credits_estimated) as total_credits,
            COUNT(*) as call_count,
            ROUND(AVG(credits_estimated), 0) as avg_credits_per_call,
            COUNT(DISTINCT creator_address) as unique_creators
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' days')
        GROUP BY DATE(created_at)
        ORDER BY date ASC
    """, (days,))

    results = []
    for row in cursor.fetchall():
        results.append({
            'date': row[0],
            'total_credits': row[1],
            'call_count': row[2],
            'avg_credits_per_call': row[3],
            'unique_creators': row[4],
        })

    conn.close()
    return results


def get_credits_per_funder(db_path: str, hours: int = 24) -> Optional[float]:
    """
    Compute credits spent per funder discovered.

    Healthy ranges:
        < 3: Excellent
        3-6: Good
        6-8: Acceptable
        > 8: Poor
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            SUM(credits_estimated) as total_credits,
            SUM(funder_discovered) as funders_found
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
    """, (hours,))

    row = cursor.fetchone()
    conn.close()

    if not row or row[1] == 0:
        return None

    total_credits, funders_found = row
    return round(total_credits / funders_found, 2)


def get_funder_efficiency_rating(db_path: str, hours: int = 24) -> Dict:
    """
    Get overall funder discovery efficiency rating.

    Returns:
        {
            'credits_per_funder': 4.5,
            'rating': '✅ Good',
            'total_funders': 200,
            'total_credits': 900,
            'status': 'optimal' | 'good' | 'acceptable' | 'poor'
        }
    """
    cpf = get_credits_per_funder(db_path, hours)

    if cpf is None:
        return {
            'credits_per_funder': None,
            'rating': '⚠️ No data',
            'total_funders': 0,
            'total_credits': 0,
            'status': 'unknown',
        }

    if cpf < 3:
        rating = '✅ Excellent'
        status = 'excellent'
    elif cpf < 6:
        rating = '✅ Good'
        status = 'good'
    elif cpf < 8:
        rating = '⚠️ Acceptable'
        status = 'acceptable'
    else:
        rating = '❌ Poor'
        status = 'poor'

    # Get total stats
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            SUM(credits_estimated),
            SUM(funder_discovered)
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
    """, (hours,))

    row = cursor.fetchone()
    conn.close()

    total_credits = row[0] or 0
    total_funders = row[1] or 0

    return {
        'credits_per_funder': cpf,
        'rating': rating,
        'total_credits': total_credits,
        'total_funders': total_funders,
        'status': status,
    }


def print_daily_report(db_path: str, hours: int = 24):
    """
    Print a human-readable daily metrics report.
    """
    print("\n" + "=" * 80)
    print("RPC METRICS REPORT")
    print("=" * 80)
    print(f"Period: Last {hours} hours (ending {datetime.now().isoformat()})")
    print()

    # Total Credits
    total_credits = get_total_credits_24h(db_path, hours)
    print(f"📊 TOTAL CREDITS: {total_credits:,}")
    print(f"   Estimated Cost: ${total_credits / 100:.2f} (at $0.01/credit)")
    print()

    # Per-Token Average
    avg_per_token = get_avg_credits_per_token(db_path, hours)
    print(f"📈 AVERAGE CREDITS PER TOKEN: {avg_per_token}")
    if avg_per_token < 20:
        print(f"   ✅ Good (target: < 20)")
    else:
        print(f"   ⚠️ High (target: < 20)")
    print()

    # Cache Hit Rate
    cache_rate = get_cache_hit_rate(db_path, hours)
    print(f"💾 WALLET CACHE HIT RATE: {cache_rate}%")
    if 70 <= cache_rate <= 95:
        print(f"   ✅ Healthy (target: 70-95%)")
    else:
        print(f"   ⚠️ Outside healthy range (target: 70-95%)")
    print()

    # Pages Per Wallet
    avg_pages = get_avg_pages_per_wallet(db_path, hours)
    print(f"📄 AVERAGE PAGES PER WALLET: {avg_pages}")
    if 1.0 <= avg_pages <= 1.5:
        print(f"   ✅ Good (target: 1.0-1.5)")
    else:
        print(f"   ⚠️ High (target: 1.0-1.5)")
    print()

    # Credits Per Funder
    funder_rating = get_funder_efficiency_rating(db_path, hours)
    if funder_rating['credits_per_funder'] is not None:
        print(f"🎯 CREDITS PER FUNDER: {funder_rating['credits_per_funder']}")
        print(f"   {funder_rating['rating']}")
        print(f"   Total: {funder_rating['total_funders']} funders, {funder_rating['total_credits']} credits")
    print()

    # Credits by Provider
    print("💰 CREDITS BY PROVIDER:")
    credits_by_provider = get_credits_by_provider(db_path, hours)
    for provider, stats in credits_by_provider.items():
        print(f"   {provider:20} {stats['total_credits']:6,} credits | "
              f"{stats['call_count']:4} calls | "
              f"{stats['error_count']:2} errors | "
              f"{stats['rate_limits']:2} rate limits")
    print()

    # Top Expensive Endpoints
    print("🔴 TOP 10 MOST EXPENSIVE ENDPOINTS:")
    top_endpoints = get_top_expensive_endpoints(db_path, hours, limit=10)
    for i, ep in enumerate(top_endpoints, 1):
        print(f"   {i:2}. {ep['provider']:20} {ep['method']:45} "
              f"{ep['total_credits']:5,} cr | "
              f"{ep['call_count']:3} calls | "
              f"avg {ep['avg_credits']:3} cr/call")
    print()

    # Top Creators by Cost
    print("👤 TOP 10 MOST EXPENSIVE CREATORS:")
    top_creators = get_credits_per_creator(db_path, hours, limit=10)
    for i, creator in enumerate(top_creators, 1):
        print(f"   {i:2}. {creator['creator_address']:20} "
              f"{creator['total_credits']:5,} credits | "
              f"{creator['scans']:3} scans | "
              f"avg {creator['avg_credits']:3} cr/scan | "
              f"pages {creator['avg_pages']}")
    print()

    # Daily Trend
    print("📅 DAILY TREND (Last 7 days):")
    daily_data = get_credits_per_day(db_path, days=7)
    for day in daily_data:
        bar_length = min(50, day['total_credits'] // 100)
        bar = "█" * bar_length
        print(f"   {day['date']} {bar:50} {day['total_credits']:5,} cr "
              f"({day['unique_creators']} creators)")
    print()

    print("=" * 80)
    print()


def print_efficiency_report(db_path: str, hours: int = 24):
    """
    Print a focused efficiency report highlighting optimization opportunities.
    """
    print("\n" + "=" * 80)
    print("EFFICIENCY OPTIMIZATION REPORT")
    print("=" * 80)
    print(f"Analysis Period: Last {hours} hours")
    print()

    # Identify high-cost creators
    top_creators = get_credits_per_creator(db_path, hours, limit=5)
    print("🎯 OPTIMIZATION TARGETS (Highest Cost Creators):")
    for creator in top_creators:
        print(f"   {creator['creator_address']:20} {creator['total_credits']:5,} credits")
    print()

    # Identify expensive endpoints
    top_endpoints = get_top_expensive_endpoints(db_path, hours, limit=5)
    print("🎯 MOST EXPENSIVE ENDPOINTS:")
    for ep in top_endpoints:
        print(f"   {ep['provider']:20} {ep['method']:40} {ep['total_credits']:5,} credits")
    print()

    # Cache effectiveness
    cache_rate = get_cache_hit_rate(db_path, hours)
    print(f"💾 CACHE EFFECTIVENESS:")
    print(f"   Wallet Cache Hit Rate: {cache_rate}%")
    if cache_rate < 70:
        print(f"   ⚠️  OPPORTUNITY: Increase cache coverage (target 70-95%)")
    print()

    # Early stop effectiveness
    avg_pages = get_avg_pages_per_wallet(db_path, hours)
    print(f"📄 SCAN DEPTH CONTROL:")
    print(f"   Average Pages Per Wallet: {avg_pages}")
    if avg_pages > 1.5:
        print(f"   ⚠️  OPPORTUNITY: Improve early-stop rules (target 1.0-1.5 pages)")
    print()

    # Funder discovery efficiency
    funder_rating = get_funder_efficiency_rating(db_path, hours)
    if funder_rating['credits_per_funder'] is not None:
        print(f"🎯 FUNDER DISCOVERY EFFICIENCY:")
        print(f"   Credits Per Funder: {funder_rating['credits_per_funder']}")
        print(f"   {funder_rating['rating']}")
        if funder_rating['status'] in ['acceptable', 'poor']:
            print(f"   ⚠️  OPPORTUNITY: Optimize funder discovery logic")
    print()

    # Provider comparison
    credits_by_provider = get_credits_by_provider(db_path, hours)
    print(f"📊 PROVIDER COMPARISON:")
    for provider, stats in credits_by_provider.items():
        if stats['rate_limits'] > 0:
            rate = 100.0 * stats['rate_limits'] / stats['call_count']
            print(f"   {provider:20} {rate:.1f}% rate limit rate (429 errors)")
            if rate > 2:
                print(f"   ⚠️  OPPORTUNITY: Reduce concurrency or add backoff")
    print()

    print("=" * 80)
    print()


if __name__ == '__main__':
    # Example usage
    import sys

    db_path = 'flex_complete_database.db'
    if len(sys.argv) > 1:
        db_path = sys.argv[1]

    print_daily_report(db_path, hours=24)
    print_efficiency_report(db_path, hours=24)
