"""
Wallet Scan Telemetry & Metrics

Measures real RPC and Helius API savings from wallet cache optimization.

Tracks:
- Cache hit/miss rates
- Helius pages fetched per scan
- RPC fallback calls
- Transaction processing volume
- Scan duration and performance

Enables accurate ROI measurement of the wallet cache system.
"""

import sqlite3
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import logging
from dataclasses import dataclass
from enum import Enum
import json

logger = logging.getLogger(__name__)

DB_PATH = "flex_complete_database.db"


class ScanType(Enum):
    """Scan operation types for telemetry"""
    CACHED_SKIP = "cached_skip"      # Cache hit - no API calls
    INCREMENTAL_SCAN = "incremental" # Incremental update from last signature
    FULL_SCAN = "full"               # Historical full scan (first time)
    ERROR = "error"                  # Failed scan
    SKIPPED = "skipped"              # Wallet type skipped (CEX, aggregator)


@dataclass
class ScanMetrics:
    """Container for scan metrics"""
    address: str
    creator_address: Optional[str]
    scan_type: ScanType
    helius_pages: int = 0            # Pages fetched from Helius
    rpc_calls: int = 0               # Fallback RPC calls made
    tx_fetched: int = 0              # Total transactions fetched
    duration_ms: int = 0             # Scan duration in milliseconds
    error: Optional[str] = None      # Error message if failed
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


def init_telemetry_schema(conn: sqlite3.Connection) -> None:
    """Initialize telemetry tables and indexes"""
    cursor = conn.cursor()

    # Main metrics table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wallet_scan_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            creator_address TEXT,
            scan_type TEXT NOT NULL,
            helius_pages INTEGER DEFAULT 0,
            rpc_calls INTEGER DEFAULT 0,
            tx_fetched INTEGER DEFAULT 0,
            started_at TEXT,
            finished_at TEXT,
            duration_ms INTEGER DEFAULT 0,
            error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Indexes for common queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_address
        ON wallet_scan_metrics(address)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_creator
        ON wallet_scan_metrics(creator_address)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_scan_type
        ON wallet_scan_metrics(scan_type)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_created_at
        ON wallet_scan_metrics(created_at)
    """)

    # Summary table (pre-computed metrics for faster queries)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wallet_scan_summary (
            period_start TEXT PRIMARY KEY,
            period_end TEXT,
            total_scans INTEGER DEFAULT 0,
            cached_skips INTEGER DEFAULT 0,
            incremental_scans INTEGER DEFAULT 0,
            full_scans INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            total_helius_pages INTEGER DEFAULT 0,
            total_rpc_calls INTEGER DEFAULT 0,
            total_tx_fetched INTEGER DEFAULT 0,
            avg_duration_ms REAL DEFAULT 0,
            cache_hit_rate REAL DEFAULT 0,
            total_credits_saved INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()


def record_scan_metrics(
    conn: sqlite3.Connection,
    metrics: ScanMetrics
) -> int:
    """
    Record a scan operation to telemetry.

    Args:
        conn: SQLite connection
        metrics: ScanMetrics object with scan details

    Returns:
        Row ID of inserted metric
    """
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO wallet_scan_metrics
        (address, creator_address, scan_type, helius_pages, rpc_calls,
         tx_fetched, started_at, finished_at, duration_ms, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        metrics.address,
        metrics.creator_address,
        metrics.scan_type.value,
        metrics.helius_pages,
        metrics.rpc_calls,
        metrics.tx_fetched,
        metrics.started_at,
        metrics.finished_at,
        metrics.duration_ms,
        metrics.error
    ))

    conn.commit()
    return cursor.lastrowid


class ScanTimer:
    """Context manager for timing wallet scans"""

    def __init__(self, conn: sqlite3.Connection, address: str, creator_address: Optional[str] = None):
        self.conn = conn
        self.address = address
        self.creator_address = creator_address
        self.metrics = ScanMetrics(
            address=address,
            creator_address=creator_address,
            scan_type=ScanType.FULL_SCAN,
            started_at=datetime.utcnow().isoformat()
        )
        self.start_time = None

    def __enter__(self):
        self.start_time = time.time()
        return self.metrics

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.metrics.finished_at = datetime.utcnow().isoformat()
        self.metrics.duration_ms = int((time.time() - self.start_time) * 1000)

        if exc_type is not None:
            self.metrics.error = str(exc_val)
            self.metrics.scan_type = ScanType.ERROR

        record_scan_metrics(self.conn, self.metrics)
        return False  # Don't suppress exceptions


# ============================================================================
# METRICS QUERIES
# ============================================================================

def get_cache_hit_rate(
    conn: sqlite3.Connection,
    since_hours: int = 24
) -> Dict:
    """
    Calculate cache hit rate over time period.

    Args:
        conn: SQLite connection
        since_hours: Look back this many hours

    Returns:
        {
            'cache_hits': int,
            'total_scans': int,
            'hit_rate': float (0.0-1.0),
            'hit_rate_pct': float (0-100)
        }
    """
    cursor = conn.cursor()
    cutoff = datetime.utcnow() - timedelta(hours=since_hours)

    cursor.execute("""
        SELECT
            SUM(CASE WHEN scan_type = ? THEN 1 ELSE 0 END) as cache_hits,
            COUNT(*) as total_scans
        FROM wallet_scan_metrics
        WHERE created_at >= ?
    """, (ScanType.CACHED_SKIP.value, cutoff.isoformat()))

    row = cursor.fetchone()
    cache_hits = row[0] or 0
    total_scans = row[1] or 0

    hit_rate = cache_hits / max(total_scans, 1)

    return {
        'cache_hits': cache_hits,
        'total_scans': total_scans,
        'hit_rate': hit_rate,
        'hit_rate_pct': hit_rate * 100,
        'period_hours': since_hours
    }


def get_helius_usage(
    conn: sqlite3.Connection,
    since_hours: int = 24
) -> Dict:
    """
    Calculate Helius API usage metrics.

    Args:
        conn: SQLite connection
        since_hours: Look back this many hours

    Returns:
        {
            'total_pages': int,
            'avg_pages_per_scan': float,
            'incremental_scans': int,
            'full_scans': int,
            'estimated_credits': int
        }
    """
    cursor = conn.cursor()
    cutoff = datetime.utcnow() - timedelta(hours=since_hours)

    cursor.execute("""
        SELECT
            SUM(helius_pages) as total_pages,
            COUNT(*) as total_scans,
            SUM(CASE WHEN scan_type = ? THEN 1 ELSE 0 END) as incremental,
            SUM(CASE WHEN scan_type = ? THEN 1 ELSE 0 END) as full
        FROM wallet_scan_metrics
        WHERE created_at >= ? AND scan_type NOT IN (?, ?)
    """, (
        ScanType.INCREMENTAL_SCAN.value,
        ScanType.FULL_SCAN.value,
        cutoff.isoformat(),
        ScanType.ERROR.value,
        ScanType.CACHED_SKIP.value
    ))

    row = cursor.fetchone()
    total_pages = row[0] or 0
    total_scans = row[1] or 0
    incremental_scans = row[2] or 0
    full_scans = row[3] or 0

    avg_pages_per_scan = total_pages / max(total_scans, 1)
    estimated_credits = total_pages * 100  # 100 credits per page

    return {
        'total_pages': total_pages,
        'avg_pages_per_scan': avg_pages_per_scan,
        'incremental_scans': incremental_scans,
        'full_scans': full_scans,
        'estimated_credits': estimated_credits,
        'period_hours': since_hours
    }


def get_rpc_usage(
    conn: sqlite3.Connection,
    since_hours: int = 24
) -> Dict:
    """
    Calculate RPC fallback usage metrics.

    Args:
        conn: SQLite connection
        since_hours: Look back this many hours

    Returns:
        {
            'total_rpc_calls': int,
            'avg_rpc_per_scan': float,
            'estimated_credits': int,
            'scans_with_rpc': int
        }
    """
    cursor = conn.cursor()
    cutoff = datetime.utcnow() - timedelta(hours=since_hours)

    cursor.execute("""
        SELECT
            SUM(rpc_calls) as total_rpc,
            COUNT(*) as total_scans,
            SUM(CASE WHEN rpc_calls > 0 THEN 1 ELSE 0 END) as scans_with_rpc
        FROM wallet_scan_metrics
        WHERE created_at >= ? AND scan_type NOT IN (?, ?)
    """, (
        cutoff.isoformat(),
        ScanType.ERROR.value,
        ScanType.CACHED_SKIP.value
    ))

    row = cursor.fetchone()
    total_rpc = row[0] or 0
    total_scans = row[1] or 0
    scans_with_rpc = row[2] or 0

    avg_rpc_per_scan = total_rpc / max(total_scans, 1)
    # RPC costs vary: getTransaction=1, getSignaturesForAddress=1, batch=varies
    estimated_credits = total_rpc * 1  # Conservative: 1 credit per RPC call

    return {
        'total_rpc_calls': total_rpc,
        'avg_rpc_per_scan': avg_rpc_per_scan,
        'scans_with_rpc': scans_with_rpc,
        'estimated_credits': estimated_credits,
        'period_hours': since_hours
    }


def get_transaction_stats(
    conn: sqlite3.Connection,
    since_hours: int = 24
) -> Dict:
    """
    Calculate transaction processing statistics.

    Args:
        conn: SQLite connection
        since_hours: Look back this many hours

    Returns:
        {
            'total_tx': int,
            'avg_tx_per_scan': float,
            'min_tx_per_scan': int,
            'max_tx_per_scan': int,
            'scans_with_tx': int
        }
    """
    cursor = conn.cursor()
    cutoff = datetime.utcnow() - timedelta(hours=since_hours)

    cursor.execute("""
        SELECT
            SUM(tx_fetched) as total_tx,
            COUNT(*) as total_scans,
            MIN(tx_fetched) as min_tx,
            MAX(tx_fetched) as max_tx,
            SUM(CASE WHEN tx_fetched > 0 THEN 1 ELSE 0 END) as scans_with_tx
        FROM wallet_scan_metrics
        WHERE created_at >= ? AND scan_type NOT IN (?, ?)
    """, (
        cutoff.isoformat(),
        ScanType.ERROR.value,
        ScanType.CACHED_SKIP.value
    ))

    row = cursor.fetchone()
    total_tx = row[0] or 0
    total_scans = row[1] or 0
    min_tx = row[2] or 0
    max_tx = row[3] or 0
    scans_with_tx = row[4] or 0

    avg_tx_per_scan = total_tx / max(scans_with_tx, 1)

    return {
        'total_tx': total_tx,
        'avg_tx_per_scan': avg_tx_per_scan,
        'min_tx_per_scan': min_tx,
        'max_tx_per_scan': max_tx,
        'scans_with_tx': scans_with_tx,
        'period_hours': since_hours
    }


def get_performance_stats(
    conn: sqlite3.Connection,
    since_hours: int = 24
) -> Dict:
    """
    Calculate scan performance metrics (duration).

    Args:
        conn: SQLite connection
        since_hours: Look back this many hours

    Returns:
        {
            'avg_duration_ms': float,
            'median_duration_ms': float,
            'min_duration_ms': int,
            'max_duration_ms': int,
            'p95_duration_ms': float,
            'p99_duration_ms': float
        }
    """
    cursor = conn.cursor()
    cutoff = datetime.utcnow() - timedelta(hours=since_hours)

    # Get percentiles
    cursor.execute("""
        SELECT
            AVG(duration_ms) as avg_ms,
            PERCENTILE(duration_ms, 50) as median_ms,
            MIN(duration_ms) as min_ms,
            MAX(duration_ms) as max_ms
        FROM wallet_scan_metrics
        WHERE created_at >= ? AND scan_type NOT IN (?)
    """, (cutoff.isoformat(), ScanType.ERROR.value))

    row = cursor.fetchone()
    avg_ms = row[0] or 0
    median_ms = row[1] or 0
    min_ms = row[2] or 0
    max_ms = row[3] or 0

    # Manual percentile calculation (SQLite doesn't have native PERCENTILE)
    cursor.execute("""
        SELECT duration_ms FROM wallet_scan_metrics
        WHERE created_at >= ? AND scan_type NOT IN (?)
        ORDER BY duration_ms
    """, (cutoff.isoformat(), ScanType.ERROR.value))

    durations = [row[0] for row in cursor.fetchall()]
    p95_ms = durations[int(len(durations) * 0.95)] if len(durations) > 0 else 0
    p99_ms = durations[int(len(durations) * 0.99)] if len(durations) > 0 else 0

    return {
        'avg_duration_ms': float(avg_ms),
        'median_duration_ms': float(median_ms),
        'min_duration_ms': min_ms,
        'max_duration_ms': max_ms,
        'p95_duration_ms': float(p95_ms),
        'p99_duration_ms': float(p99_ms),
        'period_hours': since_hours
    }


def get_credit_savings(
    conn: sqlite3.Connection,
    since_hours: int = 24,
    helius_credit_per_page: int = 100,
    rpc_credit_per_call: int = 1
) -> Dict:
    """
    Calculate estimated API credit savings from cache optimization.

    Args:
        conn: SQLite connection
        since_hours: Look back this many hours
        helius_credit_per_page: Credits per Helius Enhanced API page
        rpc_credit_per_call: Credits per RPC call

    Returns:
        {
            'helius_pages_served_from_cache': int,
            'helius_credits_saved': int,
            'rpc_calls_avoided': int,
            'rpc_credits_saved': int,
            'total_credits_saved': int,
            'total_credits_without_cache': int,
            'reduction_pct': float,
            'roi_factor': float
        }
    """
    cursor = conn.cursor()
    cutoff = datetime.utcnow() - timedelta(hours=since_hours)

    # Count cache hits (avoided pages)
    cursor.execute("""
        SELECT COUNT(*) as cache_hits FROM wallet_scan_metrics
        WHERE created_at >= ? AND scan_type = ?
    """, (cutoff.isoformat(), ScanType.CACHED_SKIP.value))

    cache_hits = cursor.fetchone()[0] or 0

    # Get actual API usage
    helius_stats = get_helius_usage(conn, since_hours)
    rpc_stats = get_rpc_usage(conn, since_hours)

    # Estimate what would have been used without cache
    total_scans = helius_stats['incremental_scans'] + helius_stats['full_scans'] + cache_hits
    avg_pages_without_cache = 1  # Assume 1 page per scan if no cache

    # Calculate savings
    helius_pages_served_from_cache = cache_hits * avg_pages_without_cache
    helius_credits_saved = helius_pages_served_from_cache * helius_credit_per_page

    # RPC savings (cache reduces need for RPC fallback)
    rpc_calls_avoided = cache_hits // 2  # Rough estimate: cache hits avoid ~2 RPC calls
    rpc_credits_saved = rpc_calls_avoided * rpc_credit_per_call

    total_credits_saved = helius_credits_saved + rpc_credits_saved
    total_credits_without_cache = (total_scans * helius_credit_per_page) + rpc_stats['estimated_credits']
    total_credits_with_cache = total_credits_without_cache - total_credits_saved

    reduction_pct = (total_credits_saved / max(total_credits_without_cache, 1)) * 100
    roi_factor = total_credits_without_cache / max(total_credits_with_cache, 1)

    return {
        'helius_pages_served_from_cache': helius_pages_served_from_cache,
        'helius_credits_saved': helius_credits_saved,
        'rpc_calls_avoided': rpc_calls_avoided,
        'rpc_credits_saved': rpc_credits_saved,
        'total_credits_saved': total_credits_saved,
        'total_credits_without_cache': total_credits_without_cache,
        'total_credits_with_cache': total_credits_with_cache,
        'reduction_pct': reduction_pct,
        'roi_factor': roi_factor,
        'period_hours': since_hours
    }


def get_summary_report(
    conn: sqlite3.Connection,
    since_hours: int = 24
) -> Dict:
    """
    Generate comprehensive summary report of wallet scan telemetry.

    Args:
        conn: SQLite connection
        since_hours: Look back this many hours

    Returns:
        Complete metrics report dictionary
    """
    return {
        'period_hours': since_hours,
        'cache_metrics': get_cache_hit_rate(conn, since_hours),
        'helius_metrics': get_helius_usage(conn, since_hours),
        'rpc_metrics': get_rpc_usage(conn, since_hours),
        'transaction_metrics': get_transaction_stats(conn, since_hours),
        'performance_metrics': get_performance_stats(conn, since_hours),
        'credit_savings': get_credit_savings(conn, since_hours),
        'timestamp': datetime.utcnow().isoformat()
    }


# ============================================================================
# INTEGRATION EXAMPLE
# ============================================================================

async def example_wallet_scan_with_telemetry(
    session,
    conn: sqlite3.Connection,
    address: str,
    creator_address: str,
    is_cached: bool = False,
    pages_fetched: int = 0,
    rpc_fallback_calls: int = 0,
    tx_count: int = 0
):
    """
    Example: Instrument wallet scanning with telemetry.

    This shows how to use the telemetry system inside the actual
    wallet_analysis_cache.analyze_wallet_incremental() function.

    Before integration:
    ```python
    async def analyze_wallet_incremental(session, conn, address):
        state = get_wallet_scan_state(conn, address)
        if state and not state['needs_scan']:
            return {'status': 'cached'}

        transactions, newest_sig, oldest_sig, tx_count, meaningful = \
            await fetch_helius_transactions_incremental(session, address, state['newest_signature'])

        update_wallet_scan_state(conn, address, newest_sig, oldest_sig, tx_count)
        return {'status': 'scanned', 'tx_scanned': tx_count}
    ```

    After integration (with telemetry):
    ```python
    async def analyze_wallet_incremental(session, conn, address, creator_address):
        state = get_wallet_scan_state(conn, address)

        # CACHE HIT - Record immediately
        if state and not state['needs_scan']:
            metrics = ScanMetrics(
                address=address,
                creator_address=creator_address,
                scan_type=ScanType.CACHED_SKIP
            )
            record_scan_metrics(conn, metrics)
            return {'status': 'cached'}

        # USE TIMER FOR ACTUAL SCAN
        with ScanTimer(conn, address, creator_address) as metrics:
            transactions, newest_sig, oldest_sig, tx_count, meaningful = \
                await fetch_helius_transactions_incremental(session, address, state['newest_signature'])

            # RECORD TELEMETRY DATA
            metrics.scan_type = ScanType.INCREMENTAL_SCAN
            metrics.helius_pages = pages_fetched
            metrics.rpc_calls = rpc_calls_made
            metrics.tx_fetched = tx_count

            update_wallet_scan_state(conn, address, newest_sig, oldest_sig, tx_count)

        return {'status': 'scanned', 'tx_scanned': tx_count}
    ```
    """
    # Initialize telemetry schema
    init_telemetry_schema(conn)

    # Scenario 1: Cache hit - Record immediately
    if is_cached:
        metrics = ScanMetrics(
            address=address,
            creator_address=creator_address,
            scan_type=ScanType.CACHED_SKIP,
            started_at=datetime.utcnow().isoformat(),
            finished_at=datetime.utcnow().isoformat()
        )
        record_scan_metrics(conn, metrics)
        logger.info(f"[TELEMETRY] Cache hit for {address[:8]}... (creator: {creator_address[:8]}...)")
        return {'status': 'cached'}

    # Scenario 2: Actual scan - Use timer for automatic tracking
    with ScanTimer(conn, address, creator_address) as metrics:
        # Simulate scan work (in real code, this is fetch_helius_transactions_incremental)
        await asyncio.sleep(0.1)

        # Record what the scan collected
        metrics.scan_type = ScanType.INCREMENTAL_SCAN
        metrics.helius_pages = pages_fetched
        metrics.rpc_calls = rpc_fallback_calls
        metrics.tx_fetched = tx_count

    logger.info(
        f"[TELEMETRY] Scanned {address[:8]}... (creator: {creator_address[:8]}...) | "
        f"Pages: {pages_fetched} | RPC calls: {rpc_fallback_calls} | TXs: {tx_count}"
    )

    return {'status': 'scanned', 'tx_scanned': tx_count}


async def example_batch_analysis_with_metrics(
    session,
    conn: sqlite3.Connection,
    creator_address: str,
    funder_addresses: List[str]
):
    """
    Example: Analyze batch of funders and report telemetry.

    This shows how metrics are collected across an entire funder
    analysis batch for a single token creator.
    """
    import asyncio

    # Initialize telemetry
    init_telemetry_schema(conn)

    logger.info(f"[METRICS] Starting analysis for creator {creator_address[:8]}...")
    logger.info(f"[METRICS] Analyzing {len(funder_addresses)} funder wallets")

    # Simulate analyzing funders (in real code, this would be analyze_wallets_batch)
    cached_count = 0
    scanned_count = 0

    for i, funder in enumerate(funder_addresses[:10]):  # Simulate 10 funders
        # Simulate cache hit for 70% of wallets
        is_cached = i % 3 != 0

        if is_cached:
            cached_count += 1
        else:
            scanned_count += 1

        # Record scan with telemetry
        await example_wallet_scan_with_telemetry(
            session,
            conn,
            funder,
            creator_address,
            is_cached=is_cached,
            pages_fetched=0 if is_cached else 2,  # New scans use 2 pages
            rpc_fallback_calls=0,
            tx_count=0 if is_cached else 50
        )

    # Generate telemetry report
    logger.info(f"\n[METRICS] ========== TELEMETRY REPORT ==========")
    report = get_summary_report(conn, since_hours=24)

    logger.info(f"[METRICS] Period: Last {report['period_hours']} hours")
    logger.info(f"\n[METRICS] CACHE PERFORMANCE:")
    cache = report['cache_metrics']
    logger.info(f"  Cache hit rate: {cache['hit_rate_pct']:.1f}% ({cache['cache_hits']}/{cache['total_scans']})")

    logger.info(f"\n[METRICS] HELIUS API USAGE:")
    helius = report['helius_metrics']
    logger.info(f"  Total pages fetched: {helius['total_pages']}")
    logger.info(f"  Avg pages per scan: {helius['avg_pages_per_scan']:.2f}")
    logger.info(f"  Estimated credits: {helius['estimated_credits']}")

    logger.info(f"\n[METRICS] RPC FALLBACK USAGE:")
    rpc = report['rpc_metrics']
    logger.info(f"  Total RPC calls: {rpc['total_rpc_calls']}")
    logger.info(f"  Scans requiring RPC: {rpc['scans_with_rpc']}")
    logger.info(f"  Estimated credits: {rpc['estimated_credits']}")

    logger.info(f"\n[METRICS] TRANSACTIONS:")
    tx = report['transaction_metrics']
    logger.info(f"  Total processed: {tx['total_tx']}")
    logger.info(f"  Avg per scan: {tx['avg_tx_per_scan']:.1f}")

    logger.info(f"\n[METRICS] PERFORMANCE:")
    perf = report['performance_metrics']
    logger.info(f"  Avg scan duration: {perf['avg_duration_ms']:.0f}ms")
    logger.info(f"  P95 scan duration: {perf['p95_duration_ms']:.0f}ms")

    logger.info(f"\n[METRICS] CREDIT SAVINGS:")
    savings = report['credit_savings']
    logger.info(f"  Credits saved: {savings['total_credits_saved']}")
    logger.info(f"  Without cache: {savings['total_credits_without_cache']} credits")
    logger.info(f"  With cache: {savings['total_credits_with_cache']} credits")
    logger.info(f"  Reduction: {savings['reduction_pct']:.1f}%")
    logger.info(f"  ROI factor: {savings['roi_factor']:.2f}x")

    logger.info(f"\n[METRICS] ==========================================\n")

    return report


# ============================================================================
# TEST EXAMPLES
# ============================================================================

if __name__ == "__main__":
    import asyncio

    # Initialize test database
    test_conn = sqlite3.connect(":memory:")
    init_telemetry_schema(test_conn)

    # Simulate some metrics
    logger.basicConfig(level=logging.INFO)

    # Record some test scans
    print("[TEST] Recording sample metrics...")

    for i in range(20):
        is_cached = i % 3 == 0

        metrics = ScanMetrics(
            address=f"wallet_{i:02d}",
            creator_address="creator_A",
            scan_type=ScanType.CACHED_SKIP if is_cached else ScanType.INCREMENTAL_SCAN,
            helius_pages=0 if is_cached else 2,
            rpc_calls=0,
            tx_fetched=0 if is_cached else 50,
            duration_ms=10 if is_cached else 500,
            started_at=datetime.utcnow().isoformat(),
            finished_at=datetime.utcnow().isoformat()
        )

        record_scan_metrics(test_conn, metrics)

    # Generate reports
    print("\n[TEST] ========== CACHE HIT RATE ==========")
    cache = get_cache_hit_rate(test_conn)
    print(f"Hit rate: {cache['hit_rate_pct']:.1f}%")
    print(f"Cache hits: {cache['cache_hits']} / Total: {cache['total_scans']}")

    print("\n[TEST] ========== HELIUS USAGE ==========")
    helius = get_helius_usage(test_conn)
    print(f"Total pages: {helius['total_pages']}")
    print(f"Avg per scan: {helius['avg_pages_per_scan']:.2f}")
    print(f"Estimated credits: {helius['estimated_credits']}")

    print("\n[TEST] ========== CREDIT SAVINGS ==========")
    savings = get_credit_savings(test_conn)
    print(f"Total saved: {savings['total_credits_saved']} credits")
    print(f"Reduction: {savings['reduction_pct']:.1f}%")
    print(f"ROI factor: {savings['roi_factor']:.2f}x")

    print("\n[TEST] ========== FULL REPORT ==========")
    report = get_summary_report(test_conn)
    print(json.dumps(report, indent=2, default=str))
