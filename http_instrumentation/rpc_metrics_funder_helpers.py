"""
RPC Metrics: Credits Per Funder Tracking

Helper functions for tracking funder discovery efficiency.
These functions compute how many credits are spent per funder discovered.

This module is designed to integrate with the existing rpc_metrics_enhanced.py
"""

import sqlite3
from typing import Optional, Dict, Tuple


# ============================================================================
# SCHEMA MIGRATION (Run Once)
# ============================================================================

def migrate_funder_discovery_schema(db_path: str = 'flex_complete_database.db') -> bool:
    """
    Add funder_discovered column to wallet_scan_metrics table.
    Safe to run multiple times (idempotent).

    Returns:
        True if migration successful, False otherwise
    """
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()

        # Add column if it doesn't exist
        cursor.execute("""
            ALTER TABLE wallet_scan_metrics
            ADD COLUMN IF NOT EXISTS funder_discovered INTEGER DEFAULT 0
        """)

        # Add indexes for efficient queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_funder_discovered
            ON wallet_scan_metrics(funder_discovered, created_at)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_creator_funder
            ON wallet_scan_metrics(creator_address, funder_discovered)
        """)

        conn.commit()
        conn.close()

        print("[MIGRATION] ✅ Funder discovery schema migrated successfully")
        return True

    except sqlite3.Error as e:
        print(f"[MIGRATION] ❌ Error migrating schema: {e}")
        return False


# ============================================================================
# UPDATED record_request() SIGNATURE
# ============================================================================

def record_request_with_funder(
    section: str,
    provider: str,
    method: str,
    status_code: int,
    latency_ms: float,
    mode: str,
    retries: int,
    source_file: str,
    error: Optional[str] = None,
    host: Optional[str] = None,
    path_group: Optional[str] = None,
    credits_estimated: Optional[int] = None,
    creator_address: Optional[str] = None,
    wallet_scan_pages: Optional[int] = None,
    cache_hit_creator: Optional[int] = None,
    cache_hit_wallet: Optional[int] = None,
    cache_hit_funder: Optional[int] = None,
    rpc_fallback: Optional[int] = None,
    rate_limited: Optional[int] = None,
    empty_wallet_scan: Optional[int] = None,
    # ===== NEW FIELD =====
    funder_discovered: Optional[int] = None,  # ← 1 when new funder found, 0 otherwise
    # ====================
    db_path: str = 'flex_complete_database.db',
) -> Optional[int]:
    """
    Enhanced version of record_request() with funder discovery tracking.

    This function is a drop-in replacement for the existing record_request().
    It adds support for tracking when new funders are discovered.

    Args:
        ... (all existing parameters)
        funder_discovered: Optional[int] — 1 when a new funder is discovered, 0 otherwise

    Example:
        # When discovering a new funder:
        record_request(
            section="creator_funding",
            provider="internal",
            method="funder_discovered",
            status_code=200,
            latency_ms=0,
            mode="analysis",
            retries=0,
            source_file="realtime_creator_funding_extractor.py",
            creator_address=creator_address,
            funder_discovered=1,  # ← Track the discovery
        )

    Returns:
        Estimated credits consumed
    """

    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()

        # Ensure table exists with all columns
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wallet_scan_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                section TEXT NOT NULL,
                provider TEXT NOT NULL,
                method TEXT NOT NULL,
                status_code INTEGER,
                latency_ms REAL DEFAULT 0,
                mode TEXT,
                retries INTEGER DEFAULT 0,
                source_file TEXT,
                error TEXT,
                host TEXT,
                path_group TEXT,
                credits_estimated INTEGER DEFAULT 0,
                creator_address TEXT,
                wallet_scan_pages INTEGER DEFAULT 0,
                cache_hit_creator INTEGER DEFAULT 0,
                cache_hit_wallet INTEGER DEFAULT 0,
                cache_hit_funder INTEGER DEFAULT 0,
                rpc_fallback INTEGER DEFAULT 0,
                rate_limited INTEGER DEFAULT 0,
                empty_wallet_scan INTEGER DEFAULT 0,
                funder_discovered INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Insert the metric record
        cursor.execute("""
            INSERT INTO wallet_scan_metrics (
                section, provider, method, status_code, latency_ms, mode, retries,
                source_file, error, host, path_group, credits_estimated,
                creator_address, wallet_scan_pages, cache_hit_creator,
                cache_hit_wallet, cache_hit_funder, rpc_fallback,
                rate_limited, empty_wallet_scan, funder_discovered, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            section, provider, method, status_code, latency_ms, mode, retries,
            source_file, error, host, path_group, credits_estimated,
            creator_address, wallet_scan_pages,
            cache_hit_creator or 0,
            cache_hit_wallet or 0,
            cache_hit_funder or 0,
            rpc_fallback or 0,
            rate_limited or 0,
            empty_wallet_scan or 0,
            funder_discovered or 0,
            __import__('datetime').datetime.now().isoformat()
        ))

        conn.commit()
        return credits_estimated or 0

    except sqlite3.Error as e:
        print(f"[ERROR] Database error in record_request: {e}")
        return None

    finally:
        if conn:
            conn.close()


# ============================================================================
# HELPER FUNCTIONS: Credits Per Funder
# ============================================================================

def get_credits_per_funder(db_path: str, hours: int = 24) -> Optional[float]:
    """
    Compute credits spent per funder discovered.

    This metric measures extraction efficiency:
    - Higher value = more credits per funder (less efficient)
    - Lower value = fewer credits per funder (more efficient)

    Healthy ranges:
        < 3: Excellent
        3-6: Good
        6-8: Acceptable
        > 8: Poor

    Args:
        db_path: Path to database
        hours: Look back N hours (default 24)

    Returns:
        Credits per funder (float), or None if no funders discovered
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


def get_credits_per_funder_by_creator(
    db_path: str,
    hours: int = 24,
    limit: int = 20
) -> list:
    """
    Get credits per funder breakdown by creator.

    Useful for identifying which creators have expensive funder discovery.

    Args:
        db_path: Path to database
        hours: Look back N hours
        limit: Maximum number of creators to return

    Returns:
        List of dicts:
            {
                'creator_address': '...',
                'total_credits': 500,
                'funders_found': 50,
                'credits_per_funder': 10.0,
                'efficiency': '✅ Good' | '⚠️ Acceptable' | '❌ Poor'
            }
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            creator_address,
            SUM(credits_estimated) as total_credits,
            SUM(funder_discovered) as funders_found,
            CASE
                WHEN SUM(funder_discovered) = 0 THEN NULL
                ELSE ROUND(SUM(credits_estimated) * 1.0 / SUM(funder_discovered), 2)
            END as credits_per_funder
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
          AND creator_address IS NOT NULL
        GROUP BY creator_address
        ORDER BY total_credits DESC
        LIMIT ?
    """, (hours, limit))

    results = []
    for row in cursor.fetchall():
        creator, credits, funders, cpf = row

        # Determine efficiency rating
        if cpf is None:
            efficiency = '⚠️ Unknown'
        elif cpf < 3:
            efficiency = '✅ Excellent'
        elif cpf < 6:
            efficiency = '✅ Good'
        elif cpf < 8:
            efficiency = '⚠️ Acceptable'
        else:
            efficiency = '❌ Poor'

        results.append({
            'creator_address': creator[:16] + '...' if creator else 'unknown',
            'total_credits': credits,
            'funders_found': funders or 0,
            'credits_per_funder': cpf,
            'efficiency': efficiency,
        })

    conn.close()
    return results


def get_funder_discovery_daily_trend(
    db_path: str,
    days: int = 7
) -> list:
    """
    Get daily trend of credits per funder.

    Shows improvement over time as optimizations are applied.

    Returns:
        List of dicts:
            {
                'date': '2026-03-05',
                'total_credits': 5000,
                'funders_found': 100,
                'credits_per_funder': 50.0,
                'unique_creators': 5
            }
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            DATE(created_at) as date,
            SUM(credits_estimated) as total_credits,
            SUM(funder_discovered) as funders_found,
            COUNT(DISTINCT creator_address) as unique_creators,
            CASE
                WHEN SUM(funder_discovered) = 0 THEN NULL
                ELSE ROUND(SUM(credits_estimated) * 1.0 / SUM(funder_discovered), 2)
            END as credits_per_funder
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' days')
          AND (funder_discovered > 0 OR section = 'creator_funding')
        GROUP BY DATE(created_at)
        ORDER BY date ASC
    """, (days,))

    results = []
    for row in cursor.fetchall():
        date, credits, funders, creators, cpf = row
        results.append({
            'date': date,
            'total_credits': credits,
            'funders_found': funders or 0,
            'credits_per_funder': cpf,
            'unique_creators': creators,
        })

    conn.close()
    return results


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


def compare_periods(
    db_path: str,
    before_date: str,
    after_date: str
) -> Dict:
    """
    Compare credits per funder between two time periods.

    Useful for measuring improvement from optimizations.

    Args:
        db_path: Path to database
        before_date: Start date (YYYY-MM-DD)
        after_date: Start of after period (YYYY-MM-DD)

    Returns:
        {
            'before': {'cpf': 6.0, 'credits': 1000, 'funders': 166},
            'after': {'cpf': 3.0, 'credits': 900, 'funders': 300},
            'improvement_pct': 50.0,
            'credits_saved': 100
        }
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    # Before period
    cursor.execute("""
        SELECT
            SUM(credits_estimated),
            SUM(funder_discovered)
        FROM wallet_scan_metrics
        WHERE created_at >= ? AND created_at < ?
    """, (before_date, after_date))

    before_row = cursor.fetchone()
    before_credits = before_row[0] or 0
    before_funders = before_row[1] or 0
    before_cpf = (before_credits / before_funders) if before_funders > 0 else None

    # After period
    cursor.execute("""
        SELECT
            SUM(credits_estimated),
            SUM(funder_discovered)
        FROM wallet_scan_metrics
        WHERE created_at >= ?
    """, (after_date,))

    after_row = cursor.fetchone()
    after_credits = after_row[0] or 0
    after_funders = after_row[1] or 0
    after_cpf = (after_credits / after_funders) if after_funders > 0 else None

    conn.close()

    # Calculate improvement
    if before_cpf and after_cpf:
        improvement_pct = round(100 * (before_cpf - after_cpf) / before_cpf, 1)
        credits_saved = before_credits - after_credits
    else:
        improvement_pct = None
        credits_saved = None

    return {
        'before': {
            'cpf': round(before_cpf, 2) if before_cpf else None,
            'credits': before_credits,
            'funders': before_funders,
        },
        'after': {
            'cpf': round(after_cpf, 2) if after_cpf else None,
            'credits': after_credits,
            'funders': after_funders,
        },
        'improvement_pct': improvement_pct,
        'credits_saved': credits_saved,
    }
