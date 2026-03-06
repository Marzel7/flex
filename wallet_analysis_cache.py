"""
Global Wallet Analysis Cache - Production Implementation

Prevents redundant API calls by tracking wallet scan state across all creators.
Each wallet is historically scanned only once, then updated incrementally.

Schema tracks:
- newest_signature: Most recent transaction (for future incremental scans)
- oldest_signature: Oldest scanned transaction (historical boundary)
- last_analyzed_at: Unix timestamp of last scan
- wallet_type: Classification (cex, bot, aggregator, creator, retail, unknown)
- error_count: Track problematic wallets for retry logic

Expected savings: ~80-90% reduction in RPC/Helius calls
"""

import sqlite3
import time
import asyncio
import aiohttp
from typing import Dict, Optional, Tuple, List
import os
import logging

# Logging
logger = logging.getLogger(__name__)

DB_PATH = "flex_complete_database.db"

# Configuration
RESCAN_INTERVAL_SECONDS = 30 * 60  # 30 minutes - skip rescan if within this window
MAX_PAGES_PER_SCAN = 50  # Max pages to fetch in one scan
CUTOFF_DAYS_HISTORICAL = 30  # Days of history for first-time full scans
EARLY_STOP_MEANINGFUL_TRANSFERS = 10  # Stop after finding this many relevant transfers
EARLY_STOP_EMPTY_PAGES = 3  # Stop after N consecutive empty pages
MIN_SOL_THRESHOLD = 0.2  # Minimum SOL to consider "meaningful" (funder filtering)

# Wallet types to skip (low signal)
SKIP_WALLET_TYPES = {'cex', 'aggregator'}

# API Configuration
HELIUS_API_KEY = os.getenv('HELIUS_API_KEY', '')
HELIUS_MONITORING_API_KEY = os.getenv('HELIUS_MONITORING_API_KEY', '')
_RPC_KEY = HELIUS_MONITORING_API_KEY or HELIUS_API_KEY


def init_wallet_cache_schema(conn: sqlite3.Connection) -> None:
    """Initialize wallet_analysis_state table and indexes"""
    cursor = conn.cursor()

    # Main state tracking table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wallet_analysis_state (
            address TEXT PRIMARY KEY,
            newest_signature TEXT,              -- Most recent tx (incremental cursor)
            oldest_signature TEXT,              -- Oldest scanned tx (historical boundary)
            last_analyzed_at INTEGER,           -- Unix timestamp
            tx_scanned INTEGER DEFAULT 0,       -- Total transactions processed
            meaningful_transfers_found INTEGER DEFAULT 0,  -- Transfers >= MIN_SOL_THRESHOLD
            wallet_type TEXT DEFAULT 'unknown', -- Classification
            error_count INTEGER DEFAULT 0,      -- Track failures
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Index for staleness queries (is cache fresh?)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_wallet_analysis_state_last_analyzed
        ON wallet_analysis_state(last_analyzed_at)
    """)

    # Index for wallet type queries (skip cex/aggregators)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_wallet_analysis_state_wallet_type
        ON wallet_analysis_state(wallet_type)
    """)

    # Index for identifying stale wallets needing rescan
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_wallet_analysis_state_stale
        ON wallet_analysis_state(last_analyzed_at, error_count)
    """)

    conn.commit()


def get_wallet_scan_state(conn: sqlite3.Connection, address: str) -> Optional[Dict]:
    """
    Get the current scan state for a wallet.

    Returns:
        {
            'newest_signature': str or None,     -- Resume point for incremental scans
            'oldest_signature': str or None,     -- Historical boundary
            'last_analyzed_at': int (unix timestamp),
            'tx_scanned': int,
            'meaningful_transfers': int,
            'wallet_type': str,
            'needs_scan': bool,                  -- Should we rescan?
            'time_since_scan_seconds': int,
            'error_count': int
        }
        or None if wallet not yet scanned
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT newest_signature, oldest_signature, last_analyzed_at,
               tx_scanned, meaningful_transfers_found, wallet_type, error_count
        FROM wallet_analysis_state
        WHERE address = ?
    """, (address,))

    row = cursor.fetchone()
    if not row:
        return None

    newest_sig, oldest_sig, last_analyzed_at, tx_scanned, meaningful, wtype, error_count = row
    current_time = int(time.time())
    time_since_scan = current_time - last_analyzed_at
    needs_scan = time_since_scan > RESCAN_INTERVAL_SECONDS

    return {
        'newest_signature': newest_sig,
        'oldest_signature': oldest_sig,
        'last_analyzed_at': last_analyzed_at,
        'tx_scanned': tx_scanned,
        'meaningful_transfers': meaningful,
        'wallet_type': wtype,
        'needs_scan': needs_scan,
        'time_since_scan_seconds': time_since_scan,
        'error_count': error_count
    }


def update_wallet_scan_state(
    conn: sqlite3.Connection,
    address: str,
    newest_signature: Optional[str],
    oldest_signature: Optional[str],
    tx_scanned: int,
    meaningful_transfers: int = 0,
    wallet_type: str = 'unknown',
    error: bool = False
) -> None:
    """
    Update wallet scan state after processing.

    Args:
        address: Wallet address
        newest_signature: Most recent signature scanned (for next incremental scan)
        oldest_signature: Oldest signature reached (historical boundary)
        tx_scanned: Total transactions processed in this scan
        meaningful_transfers: Transfers >= MIN_SOL_THRESHOLD
        wallet_type: Classification (cex, bot, aggregator, creator, retail, unknown)
        error: Whether an error occurred during scan
    """
    cursor = conn.cursor()
    current_time = int(time.time())

    cursor.execute("""
        INSERT OR REPLACE INTO wallet_analysis_state
        (address, newest_signature, oldest_signature, last_analyzed_at,
         tx_scanned, meaningful_transfers_found, wallet_type, error_count, updated_at)
        VALUES (
            ?,
            CASE WHEN ? THEN ? ELSE COALESCE((SELECT newest_signature FROM wallet_analysis_state WHERE address = ?), ?) END,
            ?,
            ?,
            ?,
            ?,
            ?,
            CASE
                WHEN ? THEN (SELECT COALESCE(error_count, 0) + 1 FROM wallet_analysis_state WHERE address = ?)
                ELSE 0
            END,
            CURRENT_TIMESTAMP
        )
    """, (
        address,
        newest_signature is not None, newest_signature, address, newest_signature,  # newest_signature logic
        oldest_signature,
        current_time,
        tx_scanned,
        meaningful_transfers,
        wallet_type,
        error, address  # error_count logic
    ))

    conn.commit()


async def fetch_helius_transactions_incremental(
    session: aiohttp.ClientSession,
    address: str,
    newest_signature: Optional[str],
    max_pages: int = MAX_PAGES_PER_SCAN,
    cutoff_ts: Optional[int] = None,
    min_sol: float = MIN_SOL_THRESHOLD
) -> Tuple[List[Dict], Optional[str], Optional[str], int, int]:
    """
    Fetch transactions for a wallet incrementally from Helius.

    Incremental scan logic:
    - If newest_signature provided: scan until reaching that signature
    - If None: perform full historical scan up to max_pages/cutoff

    Stops early if:
    - Reached newest_signature (completed incremental scan)
    - EARLY_STOP_MEANINGFUL_TRANSFERS found AND EARLY_STOP_EMPTY_PAGES consecutive empty pages
    - max_pages reached
    - cutoff_ts reached (oldest transaction time)
    - Rate limited (429)

    Args:
        session: aiohttp ClientSession
        address: Wallet address to scan
        newest_signature: Resume point (pagination cursor). If provided, scan until reaching this.
        max_pages: Maximum pages to fetch
        cutoff_ts: Unix timestamp - stop scanning before this
        min_sol: Minimum SOL to count as "meaningful transfer"

    Returns:
        (transactions_list, newest_sig_found, oldest_sig_found, tx_count, meaningful_count)
    """
    if not _RPC_KEY:
        raise ValueError("HELIUS_API_KEY not configured")

    base_url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{address}/transactions"
    transactions = []
    tx_count = 0
    meaningful_count = 0
    newest_sig_found = None
    oldest_sig_found = None
    before_cursor = None
    empty_page_count = 0
    cutoff_ts = cutoff_ts or (int(time.time()) - (CUTOFF_DAYS_HISTORICAL * 24 * 60 * 60))

    scan_type = "incremental" if newest_signature else "full"
    logger.info(
        f"[WALLET_CACHE] 📊 Starting {scan_type} scan for {address[:8]}... | "
        f"Resume from: {newest_signature[:8] if newest_signature else 'START'} | "
        f"Cutoff: {cutoff_ts}"
    )

    for page_num in range(1, max_pages + 1):
        try:
            # Build query URL
            query_url = f"{base_url}?api-key={_RPC_KEY}&limit=100&sort-order=desc&commitment=finalized"
            if before_cursor:
                query_url += f"&before={before_cursor}"

            # Fetch page
            async with session.get(query_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 429:
                    logger.warning(
                        f"[WALLET_CACHE] ⚠️ Rate limited (429) on page {page_num} for {address[:8]}... "
                        f"({tx_count} txs scanned)"
                    )
                    break

                if resp.status != 200:
                    logger.error(
                        f"[WALLET_CACHE] ❌ Error {resp.status} on page {page_num} for {address[:8]}..."
                    )
                    break

                page_data = await resp.json()
                if not isinstance(page_data, list) or len(page_data) == 0:
                    empty_page_count += 1
                    if empty_page_count >= EARLY_STOP_EMPTY_PAGES:
                        logger.info(
                            f"[WALLET_CACHE] ⏹️ Stopping: {EARLY_STOP_EMPTY_PAGES} consecutive empty pages"
                        )
                        break
                    continue

                empty_page_count = 0  # Reset on non-empty page

                # Process transactions on page
                page_meaningful = 0
                for tx in page_data:
                    tx_count += 1
                    sig = tx.get('signature', '')
                    block_time = tx.get('blockTime', 0)

                    # Track newest and oldest signatures
                    if not newest_sig_found and sig:
                        newest_sig_found = sig
                    if sig:
                        oldest_sig_found = sig

                    # Check if we've reached the cursor point (incremental scan complete)
                    if newest_signature and sig == newest_signature:
                        logger.info(
                            f"[WALLET_CACHE] ✅ Reached resume point {newest_signature[:8]}... "
                            f"after {tx_count} txs | Meaningful: {meaningful_count}"
                        )
                        return transactions, newest_sig_found, oldest_sig_found, tx_count, meaningful_count

                    # Check time cutoff (too old)
                    if block_time and block_time < cutoff_ts:
                        logger.info(
                            f"[WALLET_CACHE] ⏹️ Stopping: reached time cutoff "
                            f"({block_time} < {cutoff_ts})"
                        )
                        return transactions, newest_sig_found, oldest_sig_found, tx_count, meaningful_count

                    # Count native transfers >= min_sol as "meaningful"
                    native_transfers = tx.get('nativeTransfers', [])
                    for transfer in native_transfers:
                        amount_sol = transfer.get('amount', 0) / 1_000_000_000
                        if amount_sol >= min_sol:
                            meaningful_count += 1
                            page_meaningful += 1

                    transactions.append(tx)

                # Early stop condition: enough meaningful transfers + consecutive empty pages
                if meaningful_count >= EARLY_STOP_MEANINGFUL_TRANSFERS and empty_page_count >= EARLY_STOP_EMPTY_PAGES:
                    logger.info(
                        f"[WALLET_CACHE] ⏹️ Stopping: found {meaningful_count} meaningful transfers "
                        f"+ {EARLY_STOP_EMPTY_PAGES} empty pages"
                    )
                    break

                # Set cursor for next page
                if len(page_data) > 0:
                    before_cursor = page_data[-1].get('signature')

                logger.debug(
                    f"[WALLET_CACHE]   Page {page_num}: {len(page_data)} txs, {page_meaningful} meaningful | "
                    f"Total meaningful: {meaningful_count}/{EARLY_STOP_MEANINGFUL_TRANSFERS}"
                )

        except asyncio.TimeoutError:
            logger.error(
                f"[WALLET_CACHE] ⚠️ Timeout on page {page_num} for {address[:8]}..."
            )
            break
        except Exception as e:
            logger.error(
                f"[WALLET_CACHE] ⚠️ Error on page {page_num} for {address[:8]}...: {e}"
            )
            break

    logger.info(
        f"[WALLET_CACHE] ✅ {scan_type.upper()} SCAN COMPLETE for {address[:8]}... | "
        f"Pages: {page_num-1} | TXs: {tx_count} | Meaningful: {meaningful_count}"
    )
    return transactions, newest_sig_found, oldest_sig_found, tx_count, meaningful_count


async def analyze_wallet_incremental(
    session: aiohttp.ClientSession,
    conn: sqlite3.Connection,
    address: str,
    force_rescan: bool = False
) -> Dict:
    """
    Analyze a wallet, using cache if within rescan interval.

    Logic:
    1. Check if wallet in skip list (cex, aggregator) - return cached if known
    2. Check if cache is fresh (within 30 minutes) - return cached
    3. Otherwise fetch incremental transactions and update cache

    Returns:
        {
            'address': str,
            'status': 'cached' | 'scanned' | 'skipped' | 'error',
            'tx_scanned': int,
            'meaningful_transfers': int,
            'wallet_type': str,
            'time_since_last_scan': int,  -- seconds
            'newest_signature': str or None,
            'oldest_signature': str or None
        }
    """
    state = get_wallet_scan_state(conn, address)

    # Check if wallet is in skip list (low signal)
    if state and state['wallet_type'] in SKIP_WALLET_TYPES and not force_rescan:
        return {
            'address': address,
            'status': 'skipped',
            'wallet_type': state['wallet_type'],
            'tx_scanned': 0,
            'meaningful_transfers': 0
        }

    # Check if we can use cache
    if state and not force_rescan and not state['needs_scan']:
        return {
            'address': address,
            'status': 'cached',
            'tx_scanned': state['tx_scanned'],
            'meaningful_transfers': state['meaningful_transfers'],
            'wallet_type': state['wallet_type'],
            'time_since_last_scan': state['time_since_scan_seconds'],
            'newest_signature': state['newest_signature'],
            'oldest_signature': state['oldest_signature']
        }

    # Need to scan - fetch incrementally
    try:
        newest_sig_input = state['newest_signature'] if state else None
        transactions, newest_sig_found, oldest_sig_found, tx_count, meaningful_count = await fetch_helius_transactions_incremental(
            session,
            address,
            newest_sig_input,
            max_pages=MAX_PAGES_PER_SCAN
        )

        # Classify wallet type based on analysis (can be enhanced with heuristics)
        wallet_type = _classify_wallet(transactions, tx_count, meaningful_count)

        # Update cache
        update_wallet_scan_state(
            conn,
            address,
            newest_sig_found,
            oldest_sig_found,
            tx_count,
            meaningful_transfers=meaningful_count,
            wallet_type=wallet_type,
            error=False
        )

        return {
            'address': address,
            'status': 'scanned',
            'tx_scanned': tx_count,
            'meaningful_transfers': meaningful_count,
            'wallet_type': wallet_type,
            'newest_signature': newest_sig_found,
            'oldest_signature': oldest_sig_found
        }

    except Exception as e:
        logger.error(f"[WALLET_CACHE] ❌ Error analyzing {address[:8]}...: {e}")

        # Update error state
        if state:
            update_wallet_scan_state(
                conn, address,
                state['newest_signature'],
                state['oldest_signature'],
                0,
                error=True
            )
        else:
            update_wallet_scan_state(conn, address, None, None, 0, error=True)

        return {
            'address': address,
            'status': 'error',
            'error': str(e)
        }


def _classify_wallet(transactions: List[Dict], tx_count: int, meaningful_count: int) -> str:
    """
    Simple wallet classification heuristic.

    Can be enhanced with:
    - Token contract interactions
    - Program calls (Meteora, Orca, etc.)
    - Account creation patterns
    """
    if tx_count == 0:
        return 'unknown'

    meaningful_ratio = meaningful_count / max(tx_count, 1)

    # High transaction volume with low meaningful ratio = bot
    if tx_count > 1000 and meaningful_ratio < 0.05:
        return 'bot'

    # Very high meaningful ratio = retail/creator
    if meaningful_ratio > 0.5:
        return 'creator'

    # Default
    return 'retail'


async def analyze_wallets_batch(
    session: aiohttp.ClientSession,
    conn: sqlite3.Connection,
    addresses: List[str],
    concurrency: int = 4,
    force_rescan: bool = False
) -> List[Dict]:
    """
    Analyze multiple wallets with concurrency control.

    Uses Semaphore to limit concurrent requests (avoid 429 rate limits).

    Args:
        session: aiohttp ClientSession
        conn: SQLite connection
        addresses: List of wallet addresses to analyze
        concurrency: Max concurrent requests (default 4)
        force_rescan: Force rescan even if cache is fresh

    Returns:
        List of analysis results
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def analyze_with_limit(address: str):
        async with semaphore:
            return await analyze_wallet_incremental(session, conn, address, force_rescan)

    tasks = [analyze_with_limit(addr) for addr in addresses]
    return await asyncio.gather(*tasks)


# ============================================================================
# INTEGRATION EXAMPLE
# ============================================================================

async def example_funder_extraction_with_cache(
    creator_address: str,
    funder_addresses: List[str]
) -> Dict:
    """
    Example: Extract funder transfers using wallet cache.

    Before optimization:
    - 10 creators × 50 funders each
    - 80% overlap between creator sets
    - Each creator rescans all 50 wallets independently
    - ~150-300 credits per token

    After optimization:
    - Creator 1: Scans 50 funders (100 credits)
    - Creators 2-10: Hit cache for 40 wallets (0 credits), scan 10 new (10 credits each)
    - Total: ~100 + (9 × 10) = ~190 credits for all 10 creators
    - Per token: ~20 credits (85% reduction)

    With funder filtering (skip < 0.2 SOL):
    - Most wallets hit early stop rules
    - 30-40 credits per wallet instead of 100
    - Total reduction: ~90%
    """
    results = {
        'creator': creator_address,
        'funders_total': len(funder_addresses),
        'funders_cached': 0,
        'funders_skipped': 0,
        'funders_rescanned': 0,
        'total_meaningful_transfers': 0,
        'credits_saved_estimate': 0
    }

    # Initialize cache schema
    conn = sqlite3.connect(DB_PATH, timeout=90)
    conn.execute("PRAGMA busy_timeout = 90000")
    init_wallet_cache_schema(conn)

    async with aiohttp.ClientSession() as session:
        # Analyze all funders with concurrency control
        analyses = await analyze_wallets_batch(
            session, conn,
            funder_addresses,
            concurrency=4
        )

        # Tally results
        for analysis in analyses:
            if analysis['status'] == 'cached':
                results['funders_cached'] += 1
                # Cached = 0 API calls = 0 credits
                results['credits_saved_estimate'] += 100
            elif analysis['status'] == 'scanned':
                results['funders_rescanned'] += 1
                # New scan = ~100 credits (or less with early stopping)
            elif analysis['status'] == 'skipped':
                results['funders_skipped'] += 1
                # Skipped = 0 credits
                results['credits_saved_estimate'] += 100

            results['total_meaningful_transfers'] += analysis.get('meaningful_transfers', 0)

    conn.close()

    logger.info(
        f"[EXAMPLE] Creator {creator_address[:8]}... extraction complete:"
    )
    logger.info(
        f"  Total funders: {results['funders_total']}"
    )
    logger.info(
        f"  From cache: {results['funders_cached']}"
    )
    logger.info(
        f"  Skipped (low-signal): {results['funders_skipped']}"
    )
    logger.info(
        f"  Rescanned: {results['funders_rescanned']}"
    )
    logger.info(
        f"  Total meaningful transfers found: {results['total_meaningful_transfers']}"
    )
    logger.info(
        f"  Estimated credits saved: {results['credits_saved_estimate']}"
    )

    return results


# ============================================================================
# DIRECT INTEGRATION INTO FUNDER EXTRACTION
# ============================================================================

async def extract_funder_transfers_with_cache(
    creator_address: str,
    funder_addresses: List[str],
    conn: sqlite3.Connection
) -> Dict:
    """
    Drop-in replacement for existing funder extraction.

    This should replace the existing `extract_for_creator()` calls in:
    - funder_incoming_extractor.py line ~868
    - pumpfun_curve_listener.py line ~1931

    Before:
    ```python
    result = extract_for_creator(creator_address)  # Rescans all funders
    ```

    After:
    ```python
    result = await extract_funder_transfers_with_cache(
        creator_address,
        funder_addresses,
        conn
    )
    ```
    """
    # Initialize cache on first use
    init_wallet_cache_schema(conn)

    async with aiohttp.ClientSession() as session:
        # Analyze all funders concurrently
        analyses = await analyze_wallets_batch(
            session, conn,
            funder_addresses,
            concurrency=4
        )

    # Build result compatible with existing code
    result = {
        'status': 'complete',
        'creator': creator_address,
        'funders_analyzed': len([a for a in analyses if a['status'] != 'error']),
        'funders_with_errors': len([a for a in analyses if a['status'] == 'error']),
        'total_meaningful_transfers': sum(a.get('meaningful_transfers', 0) for a in analyses),
        'analyses': analyses
    }

    return result


if __name__ == "__main__":
    # Test: Initialize cache schema
    test_conn = sqlite3.connect(":memory:")
    init_wallet_cache_schema(test_conn)

    # Insert a test wallet state
    update_wallet_scan_state(
        test_conn,
        "TestWallet123",
        "sig_newest_123",
        "sig_oldest_456",
        tx_scanned=150,
        meaningful_transfers=8,
        wallet_type='retail'
    )

    # Query it back
    state = get_wallet_scan_state(test_conn, "TestWallet123")
    print("[TEST] Wallet state:", state)

    # Test: Query non-existent wallet
    state = get_wallet_scan_state(test_conn, "NonExistent")
    print("[TEST] Non-existent wallet:", state)

    # Test: Update to mark wallet as CEX (will be skipped)
    update_wallet_scan_state(
        test_conn,
        "TestWallet456",
        "sig_1",
        "sig_2",
        tx_scanned=500,
        meaningful_transfers=2,
        wallet_type='cex'
    )

    state = get_wallet_scan_state(test_conn, "TestWallet456")
    print("[TEST] CEX wallet (should be skipped in future):", state)
