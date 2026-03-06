"""
Enhanced RPC Metrics Recording - Extended version of record_request()

This module shows how to update your existing record_request() function
to support the new monitoring fields.

Copy these functions into your metrics recorder module.
"""

import sqlite3
import time
from typing import Optional
from datetime import datetime


def record_request(
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
    # ===== NEW FIELDS =====
    creator_address: Optional[str] = None,
    wallet_scan_pages: Optional[int] = None,
    cache_hit_creator: Optional[int] = None,
    cache_hit_wallet: Optional[int] = None,
    cache_hit_funder: Optional[int] = None,
    rpc_fallback: Optional[int] = None,
    rate_limited: Optional[int] = None,
    empty_wallet_scan: Optional[int] = None,
    # ==================
    db_path: str = 'flex_complete_database.db',
) -> Optional[int]:
    """
    Record an API/RPC request with extended monitoring metrics.

    Args:
        section: Section name (creator_funding, funder_tracing, etc)
        provider: Provider name (helius_api, helius_enhanced, solana_public)
        method: Method name (helius_enhanced_address_transactions, getTransaction)
        status_code: HTTP status code (200, 429, 500, etc)
        latency_ms: Request latency in milliseconds
        mode: Mode (realtime, background, etc)
        retries: Number of retries used
        source_file: Source file that made the call
        error: Error message if failed
        host: Hostname of API endpoint
        path_group: API path without query string
        credits_estimated: Estimated credits consumed

        # NEW FIELDS:
        creator_address: Creator being analyzed (if known)
        wallet_scan_pages: Number of pages scanned for wallet
        cache_hit_creator: 1 if creator was cached, 0 otherwise
        cache_hit_wallet: 1 if wallet was cached, 0 otherwise
        cache_hit_funder: 1 if funder was cached, 0 otherwise
        rpc_fallback: 1 if fell back to RPC, 0 otherwise
        rate_limited: 1 if got 429, 0 otherwise
        empty_wallet_scan: 1 if wallet returned no transfers, 0 otherwise
        db_path: Database path

    Returns:
        Estimated credits consumed, or None if error
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
                rate_limited, empty_wallet_scan, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            datetime.now().isoformat()
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
# HELPER FUNCTIONS FOR COMPUTING METRICS
# ============================================================================

def get_credits_per_creator(db_path: str, hours: int = 24) -> dict:
    """
    Compute credits spent per creator.

    Returns:
        {creator_address: total_credits, ...}
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT creator_address, SUM(credits_estimated) as total
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
          AND creator_address IS NOT NULL
        GROUP BY creator_address
        ORDER BY total DESC
    """, (hours,))

    result = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    return result


def get_avg_pages_per_wallet(db_path: str, hours: int = 24) -> float:
    """
    Average pages scanned per wallet.
    Healthy value: 1.0-1.5 pages
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT AVG(CASE WHEN wallet_scan_pages > 0 THEN wallet_scan_pages ELSE 1 END)
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
          AND provider = 'helius_enhanced'
    """, (hours,))

    result = cursor.fetchone()[0] or 0.0
    conn.close()
    return round(result, 2)


def get_cache_hit_rates(db_path: str, hours: int = 24) -> dict:
    """
    Compute cache hit rates for creator, wallet, and funder caches.

    Returns:
        {
            'creator_cache_hit_rate': 0.75,
            'wallet_cache_hit_rate': 0.82,
            'funder_cache_hit_rate': 0.60,
        }
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            CASE WHEN COUNT(*) = 0 THEN 0
                 ELSE ROUND(100.0 * SUM(cache_hit_creator) / COUNT(*), 1)
            END as creator_rate,
            CASE WHEN COUNT(*) = 0 THEN 0
                 ELSE ROUND(100.0 * SUM(cache_hit_wallet) / COUNT(*), 1)
            END as wallet_rate,
            CASE WHEN COUNT(*) = 0 THEN 0
                 ELSE ROUND(100.0 * SUM(cache_hit_funder) / COUNT(*), 1)
            END as funder_rate
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
    """, (hours,))

    row = cursor.fetchone()
    conn.close()

    return {
        'creator_cache_hit_rate': row[0] if row else 0,
        'wallet_cache_hit_rate': row[1] if row else 0,
        'funder_cache_hit_rate': row[2] if row else 0,
    }


def get_rpc_fallback_rate(db_path: str, hours: int = 24) -> float:
    """
    Percentage of requests that fell back to RPC.
    Should be < 5%
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            CASE WHEN COUNT(*) = 0 THEN 0
                 ELSE ROUND(100.0 * SUM(rpc_fallback) / COUNT(*), 1)
            END as fallback_rate
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
    """, (hours,))

    result = cursor.fetchone()[0] or 0
    conn.close()
    return result


def get_rate_limit_rate(db_path: str, hours: int = 24) -> dict:
    """
    Percentage of 429 rate limit errors per provider.

    Returns:
        {
            'helius_api': 2.5,
            'helius_enhanced': 1.2,
            ...
        }
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            provider,
            CASE WHEN COUNT(*) = 0 THEN 0
                 ELSE ROUND(100.0 * SUM(rate_limited) / COUNT(*), 1)
            END as rate_limit_pct
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
        GROUP BY provider
        ORDER BY rate_limit_pct DESC
    """, (hours,))

    result = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    return result


def get_empty_wallet_rate(db_path: str, hours: int = 24) -> float:
    """
    Percentage of wallets that returned no transfers.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            CASE WHEN COUNT(*) = 0 THEN 0
                 ELSE ROUND(100.0 * SUM(empty_wallet_scan) / COUNT(*), 1)
            END as empty_rate
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
    """, (hours,))

    result = cursor.fetchone()[0] or 0
    conn.close()
    return result
