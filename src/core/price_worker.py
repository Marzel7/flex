"""
FLEX Background Price Prefetch Worker

Continuously refreshes prices for tracked tokens every 10 seconds.
Reduces external API load by caching prices in memory.
Implements priority-based prefetching for launch radar tokens.

Architecture:
1. Read tracked tokens from registry
2. Group by priority (HIGH, MEDIUM, LOW)
3. Batch fetch prices
4. Update cache
5. Store snapshots
"""

import sqlite3
from src.utils.db_locking import db_connect
import logging
import time
import threading
import asyncio
from datetime import datetime
from typing import List, Dict, Optional
from src.core.price_service import get_price_service, TokenPrice
from src.core.price_fetch_queue import get_price_queue, FetchTask, start_price_queue_worker

logger = logging.getLogger(__name__)

# Track which mints have had their first price broadcast logged
_first_price_logged: set[str] = set()
_first_price_lock = threading.Lock()

def _log_first_price(mint: str, price_usd: float, market_cap: float, source: str) -> None:
    """Fire-once: log the gap between detection and first price for a mint."""
    with _first_price_lock:
        if mint in _first_price_logged:
            return
        _first_price_logged.add(mint)
    try:
        from src.core.launch_price_logger import record_first_price
        record_first_price(mint, price_usd, market_cap, source)
    except Exception:
        pass
logger.setLevel(logging.WARNING)  # Suppress DEBUG and INFO logs

# Debug flag - set to False to disable verbose debug logging
DEBUG_LOGGING = False

_MIGRATION_SIGNAL_COLUMNS = {
    "is_about_to_migrate": "BOOLEAN DEFAULT 0",
    "migration_progress_pct": "REAL",
    "migration_band": "TEXT",
    "migration_signal_updated_at": "INTEGER",
    "lifecycle_stage": "TEXT DEFAULT 'migration_pending'",
    "migrated_at": "INTEGER",
    "dex": "TEXT",
    "pumpswap_pool_address": "TEXT",
    "source_platform": "TEXT",
    "is_new": "INTEGER DEFAULT 0",
}


def _ensure_token_analysis_migration_columns(db_path: str) -> set[str]:
    """Ensure the precomputed migration signal columns exist on token_analysis."""
    conn = db_connect(db_path, timeout=15)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(token_analysis)")
        columns = {row[1] for row in cursor.fetchall()}
        for column_name, column_def in _MIGRATION_SIGNAL_COLUMNS.items():
            if column_name not in columns:
                cursor.execute(f"ALTER TABLE token_analysis ADD COLUMN {column_name} {column_def}")
                columns.add(column_name)
        conn.commit()
        return columns
    finally:
        conn.close()

# Mints with recent WS compute-fail (low liquidity). TTL = 5 min.
import threading as _threading
_low_liquidity_mints: dict = {}
_low_liquidity_lock = _threading.Lock()
_LOW_LIQUIDITY_TTL = 300

def record_low_liquidity(
    mint: str,
    quote_sol: float,
    db_path: str = 'database/flex_complete_database.db',
    *,
    quote_usd: float | None = None,
    source: str = "unknown",
) -> None:
    """Sticky rug signal. Sets liquidity_removed=1 permanently, then broadcasts SSE after commit.

    Guard against false positives: if quote SOL is clearly healthy, do not mark LIQ-removed
    even if an upstream USD conversion was wrong.
    """
    now = int(time.time())
    # At normal SOL prices, anything above ~5 SOL is well beyond the low-liquidity threshold.
    # This protects against transient bad USD conversions falsely stickying LIQ.
    if quote_sol and quote_sol >= 5.0:
        logger.warning(
            f"[LOW_LIQUIDITY_SKIP] mint={mint[:16]} quote_sol={quote_sol:.4f} "
            f"quote_usd={quote_usd if quote_usd is not None else 'n/a'} source={source}"
        )
        return
    with _low_liquidity_lock:
        _low_liquidity_mints[mint] = (quote_sol, now)
    logger.warning(
        f"[LOW_LIQUIDITY_FLAG] mint={mint[:16]} quote_sol={quote_sol:.4f} "
        f"quote_usd={quote_usd if quote_usd is not None else 'n/a'} source={source}"
    )
    try:
        import sqlite3 as _sq
        conn = _sq.connect(db_path, timeout=2)
        conn.execute("""
            UPDATE token_pool_accounts
            SET quote_liquidity      = ?,
                updated_at           = ?,
                liquidity_removed    = 1,
                liquidity_removed_at = COALESCE(liquidity_removed_at, ?)
            WHERE mint = ? AND is_active = 1
        """, (quote_sol, now, now, mint))
        conn.commit()
        conn.close()
    except Exception:
        pass
    # Broadcast after DB commit — Flask reads consistent state
    try:
        import requests as _requests
        _requests.post(
            "http://127.0.0.1:5002/api/internal/broadcast",
            json={"type": "liquidity_removed", "mint": mint, "quote_sol": quote_sol, "quote_usd": quote_usd, "timestamp": now},
            timeout=0.5,
        )
    except Exception:
        pass

def get_low_liquidity_mints() -> dict:
    """Return {mint: quote_sol} for mints with a recent WS compute-fail signal."""
    now = time.time()
    with _low_liquidity_lock:
        return {m: v[0] for m, v in _low_liquidity_mints.items() if now - v[1] < _LOW_LIQUIDITY_TTL}


class PriceWorkerRegistry:
    """Manages the tracked tokens registry."""

    def __init__(self, db_path: str = 'database/flex_complete_database.db'):
        self.db_path = db_path
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = db_connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _ensure_tables(self) -> None:
        """Create tracked_tokens table if not exists."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tracked_tokens (
                mint                TEXT PRIMARY KEY,
                symbol              TEXT,
                pair_address        TEXT,
                priority_level      TEXT DEFAULT 'MEDIUM',
                last_price_update   INTEGER DEFAULT 0,
                is_active           BOOLEAN DEFAULT 1,
                created_at          INTEGER NOT NULL,
                updated_at          INTEGER NOT NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tt_priority
            ON tracked_tokens(priority_level, is_active)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tt_last_update
            ON tracked_tokens(last_price_update ASC)
        """)

        conn.commit()
        conn.close()
        logger.info("Tracked tokens registry initialized")

    def register_token(self, mint: str, symbol: str = None,
                      pair_address: str = None, priority_level: str = 'MEDIUM') -> bool:
        """Register a token for price tracking."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            now = int(time.time())

            cursor.execute("""
                INSERT INTO tracked_tokens
                (mint, symbol, pair_address, priority_level, last_price_update, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, 1, ?, ?)
                ON CONFLICT(mint) DO UPDATE SET
                    symbol = COALESCE(excluded.symbol, tracked_tokens.symbol),
                    pair_address = COALESCE(excluded.pair_address, tracked_tokens.pair_address),
                    priority_level = CASE
                        WHEN tracked_tokens.priority_level = 'HIGH' AND excluded.priority_level != 'HIGH'
                            THEN tracked_tokens.priority_level
                        ELSE excluded.priority_level
                    END,
                    is_active = 1,
                    updated_at = excluded.updated_at
            """, (mint, symbol, pair_address, priority_level, now, now))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error registering token {mint}: {e}")
            return False

    def get_tracked_tokens(self, priority_level: str = None, active_only: bool = True) -> List[Dict]:
        """Get tracked tokens, optionally filtered by priority."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            if priority_level:
                cursor.execute("""
                    SELECT * FROM tracked_tokens
                    WHERE priority_level = ? AND is_active = ?
                    ORDER BY last_price_update ASC
                """, (priority_level, 1 if active_only else 0))
            else:
                cursor.execute("""
                    SELECT * FROM tracked_tokens
                    WHERE is_active = ?
                    ORDER BY priority_level DESC, last_price_update ASC
                """, (1 if active_only else 0,))

            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting tracked tokens: {e}")
            return []

    def update_price_timestamp(self, mint: str) -> bool:
        """Update last_price_update timestamp for a token."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE tracked_tokens
                SET last_price_update = ?
                WHERE mint = ?
            """, (int(time.time()), mint))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error updating price timestamp for {mint}: {e}")
            return False

    def deactivate_token(self, mint: str) -> bool:
        """Mark a token as inactive."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE tracked_tokens
                SET is_active = 0, updated_at = ?
                WHERE mint = ?
            """, (int(time.time()), mint))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error deactivating token {mint}: {e}")
            return False

    def boost_tokens(self, mints: list, ttl: int = 30) -> int:
        """Elevate tokens to HIGH priority. Inserts if not present. ttl accepted for API compat."""
        if not mints:
            return 0
        unique = list(dict.fromkeys(mints))  # deduplicate, preserve order
        now = int(time.time())
        count = 0
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            for mint in unique:
                cursor.execute("""
                    INSERT INTO tracked_tokens (mint, priority_level, is_active, created_at, updated_at)
                    VALUES (?, 'HIGH', 1, ?, ?)
                    ON CONFLICT(mint) DO UPDATE SET
                        priority_level = 'HIGH',
                        is_active = 1,
                        updated_at = excluded.updated_at
                """, (mint, now, now))
                count += 1
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error boosting tokens: {e}")
        return count

    def get_stats(self) -> Dict:
        """Get registry statistics."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) as total FROM tracked_tokens")
            total = cursor.fetchone()['total']

            cursor.execute("SELECT COUNT(*) as active FROM tracked_tokens WHERE is_active = 1")
            active = cursor.fetchone()['active']

            cursor.execute("""
                SELECT priority_level, COUNT(*) as count
                FROM tracked_tokens WHERE is_active = 1
                GROUP BY priority_level
            """)
            by_priority = {row['priority_level']: row['count'] for row in cursor.fetchall()}

            conn.close()

            return {
                'total_tracked': total,
                'active': active,
                'by_priority': by_priority
            }
        except Exception as e:
            logger.error(f"Error getting registry stats: {e}")
            return {}


class BackgroundPriceWorker:
    """Background worker that continuously refreshes prices."""

    def __init__(self, db_path: str = 'database/flex_complete_database.db',
                 interval: int = 3, batch_size: int = 20):
        """
        Initialize worker.

        Args:
            db_path: Path to database
            interval: Refresh interval in seconds (default 10)
            batch_size: Legacy parameter (no longer used; queue handles batching)
        """
        self.db_path = db_path
        self._token_analysis_columns = _ensure_token_analysis_migration_columns(db_path)
        self._has_curve_progress = 'curve_progress' in self._token_analysis_columns
        self.interval = interval
        self.batch_size = batch_size  # No longer used; for backwards compatibility
        self.price_service = get_price_service(db_path)
        self.registry = PriceWorkerRegistry(db_path)
        self.running = False
        self.thread = None
        self.queue = get_price_queue()

        # Pool WebSocket client lifecycle
        from src.core.pool_price_engine import PoolStateStore, PoolWebSocketClient, get_pool_state
        from src.core.sol_price_cache import get_sol_price_cache
        from src.core.websocket_manager_sharded import get_websocket_manager_sharded
        from src.core.market_cap_calculator import get_market_cap_calculator
        from src.core.token_inactivity_manager import TokenInactivityManager

        self._pool_state = get_pool_state()  # Use singleton shared with listener
        self._ws_client: Optional[PoolWebSocketClient] = None
        self._ws_manager = get_websocket_manager_sharded(self._pool_state, db_path)
        self._ws_started = False
        # Set to now so the first cycle skips the RPC fallback poll.
        # Bootstrap already populated PoolStateStore with fresh reserves via getMultipleAccounts.
        # Running _fetch_pool_prices_async() immediately would block the worker thread for ~5 minutes
        # (one RPC batch per ~316 pools), delaying all snapshot writes until the batch completes.
        # The fallback poll will begin normally after the configured interval (60s / 30s if WS stale).
        self._last_fallback_poll = time.time()

        # Debounce WebSocket refresh to prevent reconnect storms
        self._last_pool_refresh = 0
        self._refresh_debounce_seconds = 5

        # Use new SOL price cache (20s TTL) instead of manual caching
        self._sol_price_cache = get_sol_price_cache()

        # Market cap calculator with supply caching
        self._market_cap_calc = get_market_cap_calculator(db_path)

        # Token inactivity manager (detects and stops monitoring dead tokens)
        self._inactivity_manager = TokenInactivityManager(db_path)

        # Filtered peak MC tracker state: mint → {raw, effective, candidate, count}
        # Kept in-memory; persisted to token_market_cap_peaks on each update.
        self._peak_state: dict = {}  # mint -> dict

        # Priority queue: top tokens for WebSocket, others use Dexscreener fallback
        self._top_mints = set()  # Top 20-25 most recent tokens (from UI)
        self._top_mints_updated = 0  # Last time we refreshed this list

        # Cached pool map for WS fast path — refreshed each worker cycle, not per-event
        self._pool_map_cache: dict = {}  # (mint, base_account) -> pool dict
        self._pool_map_updated: float = 0

        # Snapshot retention cleanup — runs every hour
        self._last_cleanup_run: float = 0

        # Single-writer snapshot queue — WS fast path enqueues, one thread drains
        import queue as _queue
        self._snapshot_queue: _queue.Queue = _queue.Queue(maxsize=2000)

        self.stats = {
            'cycles': 0,
            'tokens_prefetched': 0,
            'api_calls': 0,
            'cache_hits': 0,
            'errors': 0,
            'last_run': None,
            'last_error': None,
            'queue_stats': {},
            'pool_prices_fetched': 0,
            'ws_stats': {},
            'top_mints_count': 0,  # Track number of priority tokens
            'activity_distribution': {
                'high': 0,
                'medium': 0,
                'low': 0,
                'dormant': 0
            }
        }

    def _build_migration_signal_sql(self, market_cap: float, now: int) -> tuple[str, list]:
        """Build a token_analysis SET fragment that keeps migration signals precomputed."""
        if self._has_curve_progress:
            about_expr = "CASE WHEN lifecycle_stage = 'migrated' THEN 0 WHEN curve_progress >= 85 THEN 1 WHEN curve_progress IS NULL AND ? >= 58000 THEN 1 ELSE 0 END"
            progress_expr = "CASE WHEN lifecycle_stage = 'migrated' THEN COALESCE(migration_progress_pct, 100) ELSE curve_progress END"
            band_expr = "CASE WHEN lifecycle_stage = 'migrated' THEN NULL WHEN curve_progress >= 90 OR ? >= 62000 THEN 'hot' WHEN curve_progress >= 75 OR ? >= 52000 THEN 'warm' ELSE NULL END"
            change_expr = (
                f"COALESCE(is_about_to_migrate, -1) IS NOT ({about_expr}) "
                f"OR migration_progress_pct IS NOT ({progress_expr}) "
                f"OR COALESCE(migration_band, '') IS NOT COALESCE(({band_expr}), '')"
            )
            return (
                f"""
                    is_about_to_migrate = {about_expr},
                    migration_progress_pct = {progress_expr},
                    migration_band = {band_expr},
                    migration_signal_updated_at = CASE
                        WHEN {change_expr} THEN ?
                        ELSE migration_signal_updated_at
                    END
                """,
                [
                    market_cap,
                    market_cap, market_cap,
                    market_cap,
                    market_cap, market_cap,
                    now,
                ],
            )

        about_expr = "CASE WHEN ? >= 58000 THEN 1 ELSE 0 END"
        about_expr = f"CASE WHEN lifecycle_stage = 'migrated' THEN 0 ELSE ({about_expr}) END"
        progress_expr = "CASE WHEN lifecycle_stage = 'migrated' THEN COALESCE(migration_progress_pct, 100) ELSE NULL END"
        band_expr = "CASE WHEN lifecycle_stage = 'migrated' THEN NULL WHEN ? >= 62000 THEN 'hot' WHEN ? >= 52000 THEN 'warm' ELSE NULL END"
        change_expr = (
            f"COALESCE(is_about_to_migrate, -1) IS NOT ({about_expr}) "
            f"OR migration_progress_pct IS NOT ({progress_expr}) "
            f"OR COALESCE(migration_band, '') IS NOT COALESCE(({band_expr}), '')"
        )
        return (
            f"""
                is_about_to_migrate = {about_expr},
                migration_progress_pct = {progress_expr},
                migration_band = {band_expr},
                migration_signal_updated_at = CASE
                    WHEN {change_expr} THEN ?
                    ELSE migration_signal_updated_at
                END
            """,
            [
                market_cap,
                market_cap, market_cap,
                market_cap,
                market_cap, market_cap,
                now,
            ],
        )

    def start(self) -> None:
        """Start the background worker."""
        if self.running:
            logger.warning("Worker already running")
            return

        # Start the price fetch queue worker
        start_price_queue_worker(self._fetch_single_price)

        self.running = True
        
        # ✅ CRITICAL FIX: Bootstrap PoolStateStore with REAL reserves from RPC
        # Since start() is called from async context (listener), we must run bootstrap
        # in a background thread, but BEFORE the worker thread starts pricing
        def bootstrap_task():
            """Background bootstrap task - populates PoolStateStore with real reserves."""
            logger.info("[PRICE_WORKER] Bootstrapping pool reserves from RPC...")
            try:
                from src.core.pool_price_engine import get_pool_fetcher
                
                fetcher = get_pool_fetcher(self.db_path)
                pools = fetcher.get_active_pools()
                
                if pools:
                    logger.info(f"[PRICE_WORKER] Fetching reserves for {len(pools)} pools...")
                    # Create new event loop for this thread
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        reserves_dict = loop.run_until_complete(fetcher.fetch_reserves(pools))
                    finally:
                        loop.close()
                    
                    logger.info(f"[PRICE_WORKER] ✅ RPC returned {len(reserves_dict)} pools with reserves")
                    
                    # Populate PoolStateStore with real reserves
                    # ✅ CRITICAL: Skip pools with zero or missing liquidity
                    populated_count = 0
                    skipped_count = 0
                    skipped_none = 0
                    skipped_zero = 0
                    
                    for pool in pools:
                        mint = pool.get("mint")
                        base_account = pool.get("base_account")
                        if mint and base_account:
                            # ✅ Get reserves, but check validity first
                            reserve_pair = reserves_dict.get((mint, base_account), (None, None))
                            base_raw, quote_raw = reserve_pair
                            
                            # ✅ Skip if RPC didn't return data (None)
                            if base_raw is None or quote_raw is None:
                                skipped_count += 1
                                skipped_none += 1
                                continue
                            
                            # ✅ Skip if pool has zero liquidity (invalid for pricing)
                            if base_raw == 0 or quote_raw == 0:
                                skipped_count += 1
                                skipped_zero += 1
                                continue
                            
                            # ✅ Only store valid pools with real liquidity
                            self._pool_state.update_reserve(mint, base_account, "base", base_raw)
                            self._pool_state.update_reserve(mint, base_account, "quote", quote_raw)
                            populated_count += 1
                    
                    all_mints = self._pool_state.get_all_mints()
                    logger.info(
                        f"[PRICE_WORKER] ✅ Bootstrapped {len(all_mints)} mints "
                        f"({populated_count} with liquidity, {skipped_none} missing RPC data, {skipped_zero} zero-liquidity)"
                    )
                    print(
                        f"[PRICE_WORKER] ✅ Bootstrapped {len(all_mints)} mints "
                        f"({populated_count} with liquidity, {skipped_none} missing RPC data, {skipped_zero} zero-liquidity)",
                        flush=True
                    )
                else:
                    logger.info("[PRICE_WORKER] No active pools found, skipping bootstrap")
                    
            except Exception as e:
                logger.error(f"[PRICE_WORKER] ❌ Bootstrap failed: {e}", exc_info=True)
                print(f"[PRICE_WORKER] ❌ Bootstrap failed: {e}", flush=True)
        
        # Start bootstrap in background thread
        bootstrap_thread = threading.Thread(target=bootstrap_task, daemon=True, name="PriceWorkerBootstrap")
        bootstrap_thread.start()

        # Wait for bootstrap to complete before starting worker loop.
        # 696 pools = 14 RPC batches × ~0.5s each = ~7s typical.
        # Allow up to 30s before giving up and letting the fallback poll fill gaps.
        logger.info("[PRICE_WORKER] Waiting for bootstrap to complete...")
        print("[PRICE_WORKER] Waiting for bootstrap to complete...", flush=True)
        bootstrap_thread.join(timeout=30.0)
        if bootstrap_thread.is_alive():
            logger.warning("[PRICE_WORKER] ⚠️  Bootstrap still running after 30s (continuing anyway)")
            print("[PRICE_WORKER] ⚠️  Bootstrap still running after 30s (continuing anyway)", flush=True)
        else:
            logger.info("[PRICE_WORKER] ✅ Bootstrap complete, starting worker loop")
            print("[PRICE_WORKER] ✅ Bootstrap complete, starting worker loop", flush=True)

        logger.info(
            "[PRICE_WORKER] First-cycle RPC fallback poll skipped — reserves already populated by bootstrap. "
            "Fallback poll begins after normal interval (60s, or 30s if WS stale)."
        )
        print(
            "[PRICE_WORKER] First-cycle RPC fallback poll skipped — using bootstrap reserves. "
            "Fallback begins in ~60s.",
            flush=True,
        )

        # Start worker thread (PoolStateStore now has reserves from bootstrap)
        logger.info("[PRICE_WORKER] Creating worker thread")
        print("[PRICE_WORKER] Creating worker thread", flush=True)
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        logger.info("[PRICE_WORKER] thread created")
        print("[PRICE_WORKER] thread created", flush=True)
        self.thread.start()
        logger.info("[PRICE_WORKER] thread.start() executed")
        print("[PRICE_WORKER] thread.start() executed", flush=True)
        logger.info(f"[PRICE_WORKER] thread alive: {self.thread.is_alive()}")
        print(f"[PRICE_WORKER] thread alive: {self.thread.is_alive()}", flush=True)

        # NOTE: WebSocket client is started by the listener (pumpfun_curve_listener.py)
        # and shares the singleton PoolStateStore. Flask should NOT start its own WS client
        # to avoid competing subscriptions on Helius.
        # This method still exists for backward compatibility, but is disabled for Flask.
        # logger.info("[PRICE_WORKER] Starting WebSocket client")
        # self._start_ws_client()
        # logger.info("[PRICE_WORKER] WebSocket client started")

        # Start single-writer snapshot drainer thread
        snapshot_drainer = threading.Thread(target=self._drain_snapshot_queue, daemon=True, name="SnapshotDrainer")
        snapshot_drainer.start()

        # Run retention cleanup once on startup (clears accumulated stale registry entries)
        threading.Thread(target=self._run_retention_cleanup, daemon=True, name="SnapshotRetentionStartup").start()

        logger.info(f"Background price worker started (interval={self.interval}s, using request queue)")

    def _initialize_pool_state_sync(self) -> None:
        """
        Legacy bootstrap method - DEPRECATED.
        The actual bootstrap now happens synchronously in start() method.
        This method is kept for reference/fallback but is no longer called.
        See start() for the active bootstrap logic.
        """
        def init_task():
            try:
                print("[PRICE_INIT] Starting...", flush=True)
                logger.info("[PRICE_INIT] Starting pool state initialization")
                
                from src.core.pool_price_engine import get_pool_fetcher
                
                print("[PRICE_INIT] Getting fetcher...", flush=True)
                fetcher = get_pool_fetcher(self.db_path)
                
                print("[PRICE_INIT] Fetching active pools...", flush=True)
                pools = fetcher.get_active_pools()
                print(f"[PRICE_INIT] Found {len(pools)} pools", flush=True)
                logger.info(f"[PRICE_INIT] Found {len(pools)} active pools")
                
                if not pools:
                    print("[PRICE_INIT] No pools, skipping", flush=True)
                    return

                # Fetch real reserves from RPC via fetcher
                print(f"[PRICE_INIT] Fetching real reserves for {len(pools)} pools from RPC...", flush=True)
                try:
                    reserves_dict = asyncio.run(fetcher.fetch_reserves(pools))
                    print(f"[PRICE_INIT] ✅ Fetched reserves for {len(reserves_dict)} pool pairs", flush=True)
                except Exception as e:
                    print(f"[PRICE_INIT] ⚠️  RPC fetch failed ({e}), falling back to zero initialization", flush=True)
                    reserves_dict = {}

                # Initialize PoolStateStore with fetched reserves
                # ✅ Apply same three-layer filtering as start() method
                print(f"[PRICE_INIT] Initializing {len(pools)} pools in PoolStateStore...", flush=True)
                populated_count = 0
                skipped_count = 0
                
                for i, pool in enumerate(pools):
                    mint = pool.get("mint")
                    base_account = pool.get("base_account")
                    quote_account = pool.get("quote_account")
                    
                    if mint and base_account and quote_account:
                        # ✅ Layer 1 filter: Check if RPC returned data
                        reserve_pair = reserves_dict.get((mint, base_account), (None, None))
                        base_raw, quote_raw = reserve_pair
                        
                        # ✅ Skip if RPC didn't return data
                        if base_raw is None or quote_raw is None:
                            skipped_count += 1
                            logger.debug(f"[PRICE_INIT] Skipping {mint[:12]}... (no RPC data)")
                            continue
                        
                        # ✅ Skip if pool has zero liquidity
                        if base_raw == 0 or quote_raw == 0:
                            skipped_count += 1
                            logger.debug(f"[PRICE_INIT] Skipping {mint[:12]}... (zero liquidity: base={base_raw}, quote={quote_raw})")
                            continue
                        
                        # ✅ Only store valid pools with real liquidity
                        self._pool_state.update_reserve(mint, base_account, "base", base_raw)
                        self._pool_state.update_reserve(mint, base_account, "quote", quote_raw)
                        populated_count += 1
                        logger.debug(f"[PRICE_INIT] Pool {mint[:12]}...: base={base_raw}, quote={quote_raw}")
                    
                    if (i + 1) % 20 == 0:
                        print(f"[PRICE_INIT] Processed {i + 1}/{len(pools)} pools ({populated_count} valid, {skipped_count} skipped)...", flush=True)
                
                all_mints = self._pool_state.get_all_mints()
                print(
                    f"[PRICE_INIT] ✅ Done! {len(all_mints)} mints ready "
                    f"({populated_count} pools with liquidity, {skipped_count} skipped)",
                    flush=True
                )
                logger.info(
                    f"[PRICE_INIT] ✅ Initialized {len(all_mints)} mints "
                    f"({populated_count} with liquidity, {skipped_count} skipped)"
                )
                
            except Exception as e:
                print(f"[PRICE_INIT] ERROR: {e}", flush=True)
                logger.error(f"[PRICE_INIT] Failed: {e}", exc_info=True)
                import traceback
                traceback.print_exc()
        
        # Run in background thread
        init_thread = threading.Thread(target=init_task, daemon=True)
        init_thread.start()

    async def _periodic_pool_resync(self) -> None:
        """
        Periodically re-fetch reserves from RPC to repair any stale state.
        Runs every 3 minutes. Guarantees pools stay fresh even if WebSocket is idle.
        """
        from src.core.pool_price_engine import get_pool_fetcher

        while self.running:
            try:
                await asyncio.sleep(180)  # 3 minutes

                fetcher = get_pool_fetcher(self.db_path)
                pools = fetcher.get_active_pools()

                if not pools:
                    continue

                logger.debug(f"[POOL_RESYNC] Running periodic resync ({len(pools)} pools)...")

                reserves_dict = await fetcher.fetch_reserves(pools)
                repaired_count = 0
                zero_count = 0

                for pool in pools:
                    mint = pool.get("mint")
                    base_account = pool.get("base_account")

                    if not (mint and base_account):
                        continue

                    (base_raw, quote_raw) = reserves_dict.get((mint, base_account), (0, 0))

                    # Update reserves
                    self._pool_state.update_reserve(mint, base_account, "base", base_raw)
                    self._pool_state.update_reserve(mint, base_account, "quote", quote_raw)

                    if base_raw > 0 and quote_raw > 0:
                        repaired_count += 1
                    else:
                        zero_count += 1

                if repaired_count > 0:
                    logger.info(
                        f"[POOL_RESYNC] ✅ Resync complete: {repaired_count} active pools, "
                        f"{zero_count} with zero liquidity"
                    )
                else:
                    logger.debug(f"[POOL_RESYNC] ✅ All {len(pools)} pools in sync")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[POOL_RESYNC] ❌ Error: {e}", exc_info=True)
                # Continue on error (don't crash background task)

    def stop(self) -> None:
        """Stop the background worker."""
        if self._ws_client:
            self._ws_client.stop()
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Background price worker stopped")

    def _refresh_top_mints(self) -> None:
        """
        Refresh list of top 20-25 most recent tokens (those shown on main UI page).
        Updated every 30 seconds.
        """
        now = time.time()
        if now - self._top_mints_updated < 30:
            return  # Update at most every 30 seconds

        try:
            conn = db_connect(self.db_path, timeout=15)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Get top 100 most recently arrived tokens — use migrated_at for migrations
            # so fresh migrations rank above old bonding-curve creation times
            cursor.execute("""
                SELECT mint FROM token_analysis
                ORDER BY COALESCE(migrated_at, created_at) DESC
                LIMIT 100
            """)
            rows = cursor.fetchall()
            self._top_mints = {row['mint'] for row in rows}
            self._top_mints_updated = now
            self.stats['top_mints_count'] = len(self._top_mints)
            conn.close()
        except Exception as e:
            logger.debug(f"Error refreshing top mints: {e}")

    def _drain_snapshot_queue(self) -> None:
        """Single-writer thread that serializes all snapshot DB writes.

        WS fast path enqueues TokenPrice objects here instead of writing directly,
        eliminating SQLite lock contention from concurrent daemon threads.
        """
        import queue as _queue
        while True:
            try:
                token_price = self._snapshot_queue.get(timeout=5)
                try:
                    self.price_service._store_snapshot(token_price)
                except Exception as e:
                    logger.warning(f"[SNAPSHOT_DRAINER] write failed for {token_price.mint[:16]}: {e}")
                finally:
                    self._snapshot_queue.task_done()
            except _queue.Empty:
                continue
            except Exception as e:
                logger.error(f"[SNAPSHOT_DRAINER] unexpected error: {e}")
                continue

    def _on_ws_reserve_update(self, mint: str, base_account: str, reserves: tuple) -> None:
        """Called by PoolWebSocketClient on every dual-reserve update.

        Fast path: recompute price from cached pool map (no RPC, no DB), update
        in-memory cache, enqueue snapshot for single-writer drainer, broadcast SSE.
        Pool map is cached on the worker and refreshed each cycle — not per-event.
        """
        try:
            from src.core.ws_price_tracer import trace as _wst
            _wst('WS_CALLBACK_ENTRY', mint, f"worker_ws_client_id={id(self._ws_client)} cache_size={len(self._pool_map_cache)}")
            pool = self._pool_map_cache.get((mint, base_account))
            if not pool:
                _wst('WS_CACHE_MISS', mint, f"base={base_account[:16]} cache_size={len(self._pool_map_cache)}")
                return

            base_raw, quote_raw = reserves
            if not base_raw or not quote_raw:
                _wst('WS_ZERO_RESERVES', mint, f"base={base_raw} quote={quote_raw}")
                return

            sol_price_usd = self._sol_price_cache.price  # set each cycle by price loop
            if not sol_price_usd:
                _wst('WS_NO_SOL_PRICE', mint, f"sol_price_cache.price is None/zero")
                return

            from src.core.pool_price_engine import PoolPriceCalculator, PoolReserveFetcher
            token_price = PoolPriceCalculator.compute_price(
                mint=mint,
                base_reserve_raw=base_raw,
                quote_reserve_raw=quote_raw,
                base_decimals=pool["base_decimals"],
                quote_decimals=pool["quote_decimals"],
                quote_is_sol=(pool["quote_token"] == PoolReserveFetcher.SOL_MINT),
                sol_price_usd=sol_price_usd,
                last_cached_price=None,
                base_account=base_account,
                total_supply=pool.get("token_supply", 0),
            )
            quote_sol = quote_raw / (10 ** pool.get("quote_decimals", 9))
            quote_usd = quote_sol * sol_price_usd if sol_price_usd else 0

            if not token_price:
                from src.core.ws_price_tracer import trace as _wst
                _wst('WS_PRICE_COMPUTE_FAIL', mint, f"base={base_raw} quote={quote_raw} sol={sol_price_usd}")
                record_low_liquidity(mint, quote_sol, db_path=self.db_path, quote_usd=quote_usd, source="ws_compute_fail")
                return

            # Liquidity below $750 USD threshold → flag as removed even if price computed
            _MIN_LIQUIDITY_USD = 750
            if quote_usd < _MIN_LIQUIDITY_USD:
                record_low_liquidity(mint, quote_sol, db_path=self.db_path, quote_usd=quote_usd, source="ws_threshold")

            from src.core.ws_price_tracer import trace as _wst
            _wst('WS_PRICE_COMPUTED', mint, f"price=${token_price.price_usd:.8f} source={token_price.source}")
            # Update in-memory cache (no lock needed — dict assignment is atomic in CPython)
            self.price_service.pool_price_cache[mint] = token_price

            # Enqueue for single-writer drainer (non-blocking, drop if queue full)
            try:
                self._snapshot_queue.put_nowait(token_price)
            except Exception:
                pass  # Queue full — worker cycle will persist on next pass

            # Broadcast to SSE subscribers via internal HTTP endpoint
            # (avoids creating a nested asyncio loop inside the WS thread)
            try:
                import requests as _requests
                _requests.post(
                    "http://127.0.0.1:5002/api/internal/broadcast",
                    json={
                        "type": "price_update",
                        "mint": mint,
                        "price_usd": token_price.price_usd,
                        "price_sol": token_price.price_sol,
                        "market_cap": token_price.market_cap,
                        "liquidity_usd": token_price.liquidity_usd,
                        "source": token_price.source,
                        "timestamp": token_price.timestamp,
                    },
                    timeout=0.5,
                )
            except Exception:
                pass
            _log_first_price(mint, token_price.price_usd, token_price.market_cap or 0, token_price.source or "ws_fast_path")

        except Exception as e:
            from src.core.ws_price_tracer import trace as _wst
            _wst('WS_FAST_PATH_ERROR', mint, f"{type(e).__name__}: {e}")
            logger.debug(f"[WS_FAST_PATH] {mint[:16]}: {e}")

    def _start_ws_client(self) -> None:
        """Start WebSocket client for pool subscriptions."""
        try:
            from src.core.pool_price_engine import get_pool_fetcher

            fetcher = get_pool_fetcher(self.db_path)
            pools = fetcher.get_active_pools()

            if not pools:
                logger.info("[PRICE_WORKER] No pools to subscribe to")
                return

            # Pre-fetch SOL price so WS fast path has it from first reserve update
            try:
                import asyncio as _aio
                from src.core.pool_price_engine import PoolPriceCalculator as _PPC
                sol_now = _aio.run(_PPC.fetch_sol_price_usd())
                if sol_now:
                    self._sol_price_cache.price = sol_now
                    self._sol_price_cache.last_update = time.time()
                    logger.info(f"[PRICE_WORKER] SOL price pre-fetched: ${sol_now:.2f}")
            except Exception as _e:
                logger.warning(f"[PRICE_WORKER] SOL price pre-fetch failed: {_e}")

            logger.info(f"[PRICE_WORKER] Creating WebSocket client for {len(pools)} pools")
            self._ws_client = __import__('src.core.pool_price_engine', fromlist=['PoolWebSocketClient']).PoolWebSocketClient(
                self._pool_state, self.db_path, on_dual_update=self._on_ws_reserve_update
            )
            from src.core.ws_price_tracer import trace as _wst
            _wst('WS_CLIENT_CREATED', 'system', f"id={id(self._ws_client)}")

            logger.info(f"[PRICE_WORKER] Starting WebSocket subscriptions")
            self._ws_client.start(pools)

            self._ws_started = True
            logger.info(f"[PRICE_WORKER] ✅ WebSocket client started")

        except Exception as e:
            logger.error(f"[PRICE_WORKER] Failed to start WebSocket: {e}", exc_info=True)

    def _run_loop(self) -> None:
        """Main worker loop."""
        print("[PRICE_WORKER] _run_loop THREAD STARTED", flush=True)
        logger.info("[PRICE_WORKER] _run_loop started")
        print(f"[PRICE_WORKER] self.running = {self.running}", flush=True)

        # Start periodic resync in separate thread (3-min interval repair loop)
        resync_thread = threading.Thread(
            target=lambda: asyncio.run(self._periodic_pool_resync()),
            daemon=True,
            name="PriceWorkerResync"
        )
        resync_thread.start()
        logger.info("[PRICE_WORKER] ✅ Periodic resync task started")

        try:
            while self.running:
                print("[PRICE_WORKER] CYCLE LOOP ENTERED", flush=True)
                try:
                    logger.info(f"[PRICE_WORKER] cycle at {time.time()}")
                    print(f"[PRICE_WORKER] cycle at {time.time()}", flush=True)
                    self._refresh_cycle()
                    time.sleep(self.interval)
                except Exception as e:
                    logger.error(f"Worker cycle error: {e}", exc_info=True)
                    self.stats['last_error'] = str(e)
                    self.stats['errors'] += 1
                    time.sleep(self.interval)
        except Exception as e:
            print(f"[PRICE_WORKER] THREAD CRASHED: {e}", flush=True)
            logger.exception("[PRICE_WORKER] THREAD CRASHED")

    def _refresh_cycle(self) -> None:
        """One complete refresh cycle with activity-based scheduling."""
        if DEBUG_LOGGING: print("[PRICE_DEBUG] refresh_cycle START", flush=True)
        logger.info("[PRICE_DEBUG] refresh_cycle START")
        cycle_start = time.time()
        self.stats['cycles'] += 1

        # Start WebSocket if not running but pools exist (handles post-startup pool registration)
        if not self._ws_started:
            from src.core.pool_price_engine import get_pool_fetcher
            fetcher = get_pool_fetcher(self.db_path)
            pools = fetcher.get_active_pools()
            if pools:
                self._start_ws_client()

        # Frequently reload pool subscriptions from database (every cycle = ~10s)
        # This ensures new tokens discovered by the listener get subscribed quickly
        try:
            fetcher = get_pool_fetcher(self.db_path)
            pools = fetcher.get_active_pools()

            if not self._ws_client and pools:
                # WebSocket not started but pools exist — start it now
                logger.info(f"[PRICE_WORKER] 🚀 New pools detected, starting WebSocket")
                self._start_ws_client()
            elif self._ws_client and pools:
                # WebSocket running — refresh subscriptions with latest pools
                # This picks up newly discovered pools within ~10 seconds
                self._ws_client.refresh_pools(pools)

            # Refresh pool map cache used by WS fast path (avoids per-event DB query)
            if pools:
                self._pool_map_cache = {(p["mint"], p["base_account"]): p for p in pools}
        except Exception as e:
            logger.debug(f"Error refreshing WebSocket pools: {e}")

        # Reset activity distribution for this cycle
        self.stats['activity_distribution'] = {
            'high': 0,
            'medium': 0,
            'low': 0,
            'dormant': 0
        }

        # Periodically retry vault validation for pending pools
        if self.stats['cycles'] % 10 == 0:
            logger.debug("Running periodic vault validation retry")
            try:
                asyncio.run(self._retry_pending_vault_validations())
            except Exception as e:
                logger.error(f"Error retrying vault validations: {e}")

        # First, fetch all pool prices into cache (primary source)
        self._fetch_pool_prices()

        # Then, sync new tokens from token_analysis to tracked_tokens
        self._sync_new_tokens()

        # Get tokens to refresh based on activity
        tokens_to_fetch = self._get_tokens_for_refresh()

        if not tokens_to_fetch:
            logger.debug("No tracked tokens to refresh")
            # Update queue stats even if nothing to fetch
            self.stats['queue_stats'] = self.queue.get_stats()
            self.sync_source_metrics()
            # ✅ Log system health metrics every cycle
            if self.stats['cycles'] % 3 == 0:  # Every ~30 seconds
                self.log_system_health_metrics()
            return

        # Enqueue tokens to fetch (instead of batch fetching directly)
        tasks = [
            FetchTask(
                mint=t['mint'],
                priority=t['priority_level'],
                enqueued_at=time.time(),
                callback=self._on_price_fetched
            )
            for t in tokens_to_fetch
        ]
        self.queue.enqueue_batch(tasks)

        # Warm snapshot cache with fresh prices for dashboard reads
        self._warm_snapshot_cache(tokens_to_fetch)

        duration = time.time() - cycle_start
        self.stats['last_run'] = duration
        self.stats['queue_stats'] = self.queue.get_stats()
        self.sync_source_metrics()

        # Run inactivity detection (marks soft inactive, stops hard inactive)
        from src.core.token_inactivity_manager import inactivity_detection
        inactivity_stats = inactivity_detection(self._inactivity_manager, self)

        # Log if tokens were stopped and WebSocket refresh is needed
        if inactivity_stats['tokens_stopped'] > 0:
            logger.info(f"[INACTIVITY] Stopped {inactivity_stats['tokens_stopped']} inactive tokens, WebSocket will refresh on next cycle")

        # ✅ Log system health metrics every cycle
        if self.stats['cycles'] % 3 == 0:  # Every ~30 seconds
            self.log_system_health_metrics()

        # 🧹 Run snapshot retention cleanup every hour
        if time.time() - self._last_cleanup_run >= 3600:
            self._last_cleanup_run = time.time()
            threading.Thread(
                target=self._run_retention_cleanup,
                daemon=True,
                name="SnapshotRetentionCleanup",
            ).start()

        logger.debug(
            f"Prefetch cycle {self.stats['cycles']}: "
            f"enqueued {len(tasks)} tokens (activity-based), "
            f"queue depth {self.queue.get_stats()['queue_depth']}, "
            f"activity: high={self.stats['activity_distribution']['high']} "
            f"medium={self.stats['activity_distribution']['medium']} "
            f"low={self.stats['activity_distribution']['low']} "
            f"dormant={self.stats['activity_distribution']['dormant']}"
        )

    async def _retry_pending_vault_validations(self) -> None:
        """
        Retry vault validation for pending pools.

        For pools with broken quotes (MINT stored as account), re-discover vaults.
        For other pending pools, check if vaults have been created on-chain.
        """
        try:
            import sqlite3
            import asyncio
            import aiohttp
            from src.core.vault_discovery import discover_and_register_vaults_rpc

            # Skip if no RPC URL
            import os
            rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
            if not rpc_url:
                logger.warning("[VAULT_RETRY] No RPC URL configured, skipping vault retry validation")
                return

            conn = db_connect(self.db_path, timeout=15)
            conn.execute("PRAGMA busy_timeout=15000")
            cursor = conn.cursor()

            # Find all pending pools, prioritize those with broken quotes (MINT as account)
            cursor.execute("""
                SELECT mint, base_account, quote_account FROM token_pool_accounts
                WHERE vault_validation_status = 'pending' AND is_active = 1
                ORDER BY CASE WHEN quote_account = 'So11111111111111111111111111111111111111112' THEN 0 ELSE 1 END,
                         last_vault_validation_at ASC
                LIMIT 10
            """)
            pending_pools = cursor.fetchall()
            conn.close()

            if not pending_pools:
                return

            logger.info(f"[VAULT_RETRY] Retrying validation for {len(pending_pools)} pending pools")

            # Create simple RPC client for re-discovery
            class SimpleRPCClient:
                def __init__(self, url):
                    self.url = url

                async def call_async(self, method, params):
                    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.post(self.url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                                data = await resp.json()
                                return data.get("result") if data else None
                    except:
                        return None

                async def close(self):
                    """No-op for compatibility with clients that call close()"""
                    pass

            rpc_client = SimpleRPCClient(rpc_url)

            for mint, pool_account, quote_account in pending_pools:
                is_broken_mint = quote_account == 'So11111111111111111111111111111111111111112'

                if is_broken_mint:
                    # This pool has MINT as account - re-discover to get real vaults
                    logger.info(f"[VAULT_RETRY] Re-discovering vaults for {mint[:16]}... (has MINT bug)")
                    try:
                        success = await discover_and_register_vaults_rpc(
                            token_mint=mint,
                            rpc_client=rpc_client,
                            db=self.db_path,
                            price_worker=None,
                            max_retries=1
                        )
                        if success:
                            logger.info(f"[VAULT_RETRY] ✅ Pool {mint[:16]}... re-discovered with real vaults")
                            self._ws_started = False  # Restart WS to pick up corrected pools
                    except Exception as e:
                        logger.debug(f"[VAULT_RETRY] Re-discovery failed for {mint[:16]}...: {e}")
                else:
                    # Pool has real accounts - just validate them
                    from src.core.pool_discovery import PoolDiscovery
                    discovery = PoolDiscovery(self.db_path, None)
                    validated = await discovery.retry_vault_validation(mint, pool_account)
                    if validated:
                        logger.info(f"[VAULT_RETRY] ✅ Pool {mint[:16]}... vaults now validated")
                        self._ws_started = False

                await asyncio.sleep(0.1)

            await rpc_client.close()

        except Exception as e:
            logger.error(f"[VAULT_RETRY] Error: {e}")


    def sync_source_metrics(self) -> None:
        """Sync source attempt metrics from price_service to worker stats."""
        if hasattr(self.price_service, 'stats'):
            self.stats['source_stats'] = self.price_service.stats.copy()

    def log_system_health_metrics(self) -> None:
        """
        Log system health metrics: pool vs fallback price ratio.
        Called every refresh cycle to show operator real-time system status.
        """
        try:
            import sqlite3
            
            # Query last 100 price updates to calculate ratio
            conn = db_connect(self.db_path, timeout=15)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT price_source, COUNT(*) as count
                FROM token_analysis
                WHERE price_current > 0 AND updated_at > datetime('now', '-5 minutes')
                GROUP BY price_source
                ORDER BY count DESC
            """)
            
            source_counts = {row[0]: row[1] for row in cursor.fetchall()}
            conn.close()
            
            pool_count = source_counts.get('pool', 0)
            fallback_count = source_counts.get('dexscreener_fallback', 0)
            other_count = sum(v for k, v in source_counts.items() if k not in ('pool', 'dexscreener_fallback'))
            
            total = pool_count + fallback_count + other_count
            
            if total > 0:
                pool_pct = (pool_count / total) * 100
                fallback_pct = (fallback_count / total) * 100
                
                # Health status based on pool ratio
                if pool_pct >= 90:
                    health = "✅ HEALTHY"
                elif pool_pct >= 70:
                    health = "⚠️  DEGRADED"
                elif pool_pct >= 50:
                    health = "⚠️  CONCERNING"
                else:
                    health = "❌ CRITICAL"
                
                logger.info(
                    f"[SYSTEM_HEALTH] {health} | Pool: {pool_pct:.1f}% ({pool_count}) | "
                    f"Fallback: {fallback_pct:.1f}% ({fallback_count}) | "
                    f"Other: {(other_count/total)*100:.1f}% ({other_count}) | "
                    f"Last 5min: {total} prices"
                )
                
                # Store for later querying
                self.stats['health_metrics'] = {
                    'pool_pct': pool_pct,
                    'fallback_pct': fallback_pct,
                    'pool_count': pool_count,
                    'fallback_count': fallback_count,
                    'total_count': total,
                    'health_status': health,
                }
            else:
                logger.debug("[SYSTEM_HEALTH] No prices in last 5 minutes")
                
        except Exception as e:
            logger.debug(f"Failed to log health metrics: {e}")

    def _warm_snapshot_cache(self, tokens: list) -> None:
        """
        Pre-warm snapshot cache tier with fresh prices from hot cache.
        
        Dashboard requests can read snapshot tier to avoid triggering live price fetches
        between worker refresh cycles.
        """
        cache_warmed = 0
        try:
            for token in tokens:
                mint = token.get('mint')
                if not mint:
                    continue
                
                # Get price from hot cache (most recent)
                price = self.price_service.cache.get(mint, 'hot')
                if price and not price.is_stale:
                    # Also store in snapshot cache
                    self.price_service.cache.set(mint, price, cache_type='snapshot')
                    cache_warmed += 1
            
            if cache_warmed > 0:
                logger.debug(f"Snapshot cache warmed: {cache_warmed} tokens")
        except Exception as e:
            logger.error(f"Error warming snapshot cache: {e}")

    def _run_retention_cleanup(self) -> None:
        """Run snapshot retention cleanup in a background thread once per hour."""
        try:
            from src.core.snapshot_retention_manager import SnapshotRetentionManager
            mgr = SnapshotRetentionManager(self.db_path)
            stats = mgr.run_cleanup()
            logger.info(
                f"[RETENTION] cleanup done — tokens_deactivated={stats.get('tokens_deleted', 0)} "
                f"snaps_deleted={stats.get('snapshots_deleted', 0)} "
                f"downsampled={stats.get('snapshots_downsampled', 0)}"
            )
        except Exception as e:
            logger.error(f"[RETENTION] cleanup error: {e}", exc_info=True)

    def _fetch_pool_prices(self) -> None:
        """
        Primary: compute prices from PoolStateStore (updated in real-time by WebSocket).
        Fallback: run full getMultipleAccounts batch poll every 60s, or every 30s if WS is stale.
        """
        # Ensure WS client is started if pools were registered after startup
        if not self._ws_started:
            self._start_ws_client()

        now = time.time()

        # Check if any tokens are in critical discovery window
        # If so, suppress stale fallback polls to avoid RPC contention during critical path
        in_critical_window = False
        try:
            if hasattr(self, 'listener') and self.listener:
                in_critical_window = self.listener.any_token_in_critical_window()
        except Exception:
            pass

        # Track WS staleness for health reporting only.
        if self._ws_client:
            time_since_last_event = now - self._ws_client._last_event_received
            if time_since_last_event > self._ws_client.WS_STALE_THRESHOLD:
                self._ws_client.stats["is_stale"] = True

        # Check for pools marked as stale by PoolStateStore (no updates >5 min)
        stale_mints = self._pool_state.mark_stale_pools(now)

        # Primary: compute prices from WebSocket-maintained reserve state
        self._recompute_prices_from_ws_state()

        # Sync WS stats to worker stats and persist to disk for Flask to read
        if self._ws_client:
            self.stats['ws_stats'] = dict(self._ws_client.stats)
            try:
                import json as _json, os as _os
                _ws_stats_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', '..', 'logs', 'ws_stats.json')
                _ws_stats_path = _os.path.normpath(_ws_stats_path)
                _payload = dict(self._ws_client.stats)
                _payload['written_at'] = int(time.time())
                _tmp = _ws_stats_path + '.tmp'
                with open(_tmp, 'w') as _f:
                    _json.dump(_payload, _f)
                _os.replace(_tmp, _ws_stats_path)
            except Exception:
                pass

    async def _fetch_pool_prices_async(self) -> None:
        """Async implementation of pool price fetching with multi-pool aggregation and peak tracking."""
        from collections import defaultdict
        from src.core.pool_price_engine import (
            get_pool_fetcher,
            PoolPriceCalculator,
            PoolReserveFetcher,
            PoolAggregator,
        )
        import sqlite3

        fetcher = get_pool_fetcher(self.db_path)
        pools = fetcher.get_active_pools()
        if not pools:
            return

        # Fetch SOL price once per cycle, shared across all compute_price() calls
        sol_price_usd = await PoolPriceCalculator.fetch_sol_price_usd()
        if sol_price_usd == 0:
            logger.warning("Skipping pool price fetch — SOL price unavailable")
            return
        # Store in shared cache so WS fast path can use it without a separate fetch
        self._sol_price_cache.price = sol_price_usd
        self._sol_price_cache.last_update = time.time()

        # Batch-fetch all reserves: now keyed by (mint, base_account)
        reserves = await fetcher.fetch_reserves(pools)

        # Seed PoolStateStore with fresh RPC reserves — un-stales pools that had no WS events
        # and keeps bootstrapped data current for quiet pools.
        for (mint, base_account), (base_raw, quote_raw) in reserves.items():
            if base_raw and quote_raw and base_raw > 0 and quote_raw > 0:
                self._pool_state.update_reserve(mint, base_account, "base", base_raw)
                self._pool_state.update_reserve(mint, base_account, "quote", quote_raw)

        # Key pool metadata by (mint, base_account)
        pool_map = {(p["mint"], p["base_account"]): p for p in pools}

        # Group reserves by mint for aggregation
        pools_by_mint = defaultdict(list)
        for (mint, base_account), (base_raw, quote_raw) in reserves.items():
            pools_by_mint[mint].append((base_account, base_raw, quote_raw))

        new_cache: Dict[str, TokenPrice] = {}
        now = int(time.time())

        for mint, pool_list in pools_by_mint.items():
            last_price = None
            if mint in self.price_service.pool_price_cache:
                last_price = self.price_service.pool_price_cache[mint].price_usd

            # Compute price for each pool
            candidate_prices = []
            for base_account, base_raw, quote_raw in pool_list:
                pool = pool_map.get((mint, base_account))
                if not pool:
                    continue

                token_price = PoolPriceCalculator.compute_price(
                    mint=mint,
                    base_reserve_raw=base_raw,
                    quote_reserve_raw=quote_raw,
                    base_decimals=pool["base_decimals"],
                    quote_decimals=pool["quote_decimals"],
                    quote_is_sol=(
                        pool["quote_token"] == PoolReserveFetcher.SOL_MINT
                    ),
                    sol_price_usd=sol_price_usd,
                    last_cached_price=last_price,
                    base_account=base_account,
                )
                if token_price:
                    candidate_prices.append(token_price)

            # Aggregate prices from all pools for this mint
            aggregated = PoolAggregator.aggregate(candidate_prices)
            if aggregated:
                # Track peak market cap
                if aggregated.market_cap > 0:
                    self._update_peak_market_cap(mint, aggregated.market_cap, now,
                                                 liquidity_usd=aggregated.liquidity_usd or 0.0)
                    # Store peak info in token price for API response
                    peak_info = self._get_peak_market_cap(mint)
                    if peak_info:
                        aggregated.peak_market_cap = peak_info[0]
                        aggregated.peak_market_cap_at = peak_info[1]
                
                new_cache[mint] = aggregated

        # Atomic swap — GIL-safe for dict reference reassignment
        self.price_service.pool_price_cache = new_cache
        self.stats["pool_prices_fetched"] = len(new_cache)
        logger.info(f"Pool prices fetched: {len(new_cache)} tokens from {len(pools)} pool registrations")
        
        # Update token_analysis table with fresh pool prices for tokens that exist there
        if new_cache:
            try:
                conn = db_connect(self.db_path, timeout=15)
                cursor = conn.cursor()
                for mint, token_price in new_cache.items():
                    migration_sql, migration_params = self._build_migration_signal_sql(token_price.market_cap or 0, now)
                    cursor.execute(f"""
                        UPDATE token_analysis
                        SET price_current = ?,
                            market_cap_current = ?,
                            price_source = ?,
                            price_updated_at = ?,
                            {migration_sql}
                        WHERE mint = ?
                    """, (token_price.price_usd, token_price.market_cap, token_price.source, now, *migration_params, mint))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.debug(f"Failed to update token_analysis with pool prices: {e}")

    def _recompute_prices_from_ws_state(self) -> None:
        """
        Recompute pool_price_cache from current PoolStateStore reserves.
        Called every refresh cycle (10s) — no RPC calls.
        Handles multiple pools per mint via aggregation.
        SOL price is fetched at most once per 30s (cached on self).
        Tracks peak market cap for each token.
        Fetches token supplies to compute accurate market caps.
        """
        try:
            from src.core.pool_price_engine import (
                get_pool_fetcher,
                PoolPriceCalculator,
                PoolReserveFetcher,
                PoolAggregator,
            )
            import sqlite3

            fetcher = get_pool_fetcher(self.db_path)
            pools = fetcher.get_active_pools()
            if not pools:
                if DEBUG_LOGGING: print("[PRICE_DEBUG] No active pools found", flush=True)
                return

            # Key by (mint, base_account) to support multiple pools per token
            pool_map = {(p["mint"], p["base_account"]): p for p in pools}
            if DEBUG_LOGGING: print(f"[PRICE_DEBUG] Built pool_map with {len(pool_map)} pool entries", flush=True)

            # Get all distinct mints
            mints = self._pool_state.get_all_mints()
            if DEBUG_LOGGING: print(f"[PRICE_DEBUG] Mints in PoolStateStore: {len(mints)}", flush=True)

            # Get SOL price from cache (20s TTL, reduces API calls by ~95%)
            # NOTE: Don't use asyncio.run() here — we're in a thread, not async context
            # Instead, directly get from cache without async
            try:
                # Try to get cached SOL price or fetch synchronously
                sol_price_usd = self._sol_price_cache.get_price_sync()
                if not sol_price_usd:
                    if DEBUG_LOGGING: print("[PRICE_DEBUG] SOL price cache empty, attempting async fetch", flush=True)
                    # Fallback: create a new event loop for this thread
                    import asyncio
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        sol_price_usd = loop.run_until_complete(
                            PoolPriceCalculator.fetch_sol_price_usd()
                        )
                    finally:
                        loop.close()
            except Exception as e:
                logger.error(f"Failed to get SOL price: {e}")
                if DEBUG_LOGGING: print(f"[PRICE_DEBUG] SOL price fetch error: {e}", flush=True)
                return

            if not sol_price_usd or sol_price_usd <= 0:
                if DEBUG_LOGGING: print(f"[PRICE_DEBUG] Invalid SOL price: {sol_price_usd}", flush=True)
                return

            if DEBUG_LOGGING: print(f"[PRICE_DEBUG] SOL price valid: ${sol_price_usd:.2f}", flush=True)

            # Fetch token supplies for all mints (cached by MarketCapCalculator)
            # NOTE: Skipping supply fetch to avoid slowdown — use pool token_supply instead
            if DEBUG_LOGGING: print(f"[PRICE_DEBUG] Skipping supply fetch (using pool defaults)", flush=True)
            supply_cache = {}

            new_cache: Dict[str, TokenPrice] = {}
            now = int(time.time())

            if DEBUG_LOGGING: print(f"[PRICE_DEBUG] Starting mint loop for {len(mints)} mints", flush=True)
            processed = 0
            for mint in mints:
                # Get all pools for this mint
                pool_reserves = self._pool_state.get_pools_for_mint(mint)
                processed += 1
                if processed % 10 == 1:
                    if DEBUG_LOGGING: print(f"[PRICE_DEBUG] Processing mint {processed}/{len(mints)}: {mint[:16]}... reserves={len(pool_reserves) if pool_reserves else 0}", flush=True)
                if not pool_reserves:
                    continue

                if DEBUG_LOGGING: print(f"[PRICE_DEBUG] {mint[:16]}... ✓ reserves present: {len(pool_reserves)} pools", flush=True)

                last_price = self.price_service.pool_price_cache.get(mint)
                last_price_usd = last_price.price_usd if last_price else None

                # Use cached supply or fallback to pool's token_supply
                supply = supply_cache.get(mint, 0)

                # Compute price for each pool
                candidate_prices = []
                for base_account, base_raw, quote_raw in pool_reserves:
                    # ✅ HYDRATION GUARD: Require both base AND quote reserves to be fully hydrated
                    # Zero or missing reserves indicate pool not yet ready for pricing (race condition)
                    if not base_raw or not quote_raw or base_raw <= 0 or quote_raw <= 0:
                        logger.debug(f"[PRICE_DEBUG] {mint[:16]}... ✗ skipping unhydrated reserves: base={base_raw}, quote={quote_raw}")
                        continue
                    
                    pool = pool_map.get((mint, base_account))
                    if not pool:
                        if DEBUG_LOGGING: print(f"[PRICE_DEBUG] {mint[:16]}... ✗ pool metadata MISSING for base_account={base_account[:16]}... (looked in {len(pool_map)} pool entries)", flush=True)
                        continue

                    if DEBUG_LOGGING: print(f"[PRICE_DEBUG] {mint[:16]}... ✓ pool metadata loaded: decimals={pool.get('base_decimals')}/{pool.get('quote_decimals')}, quote={pool.get('quote_token')[:16]}", flush=True)

                    # Use fetched supply, fallback to pool value, then default
                    total_supply = supply or pool.get("token_supply", 0)

                    if DEBUG_LOGGING: print(f"[PRICE_DEBUG] {mint[:16]}... Computing price: base_raw={base_raw}, quote_raw={quote_raw}, total_supply={total_supply}", flush=True)
                    token_price = PoolPriceCalculator.compute_price(
                        mint=mint,
                        base_reserve_raw=base_raw,
                        quote_reserve_raw=quote_raw,
                        base_decimals=pool["base_decimals"],
                        quote_decimals=pool["quote_decimals"],
                        quote_is_sol=(
                            pool["quote_token"] == PoolReserveFetcher.SOL_MINT
                        ),
                        sol_price_usd=sol_price_usd,
                        last_cached_price=last_price_usd,
                        base_account=base_account,
                        total_supply=total_supply,
                    )
                    if token_price:
                        if DEBUG_LOGGING: print(f"[PRICE_DEBUG] {mint[:16]}... ✓ price computed: ${token_price.price_usd:.8f}", flush=True)
                        candidate_prices.append(token_price)
                    else:
                        if DEBUG_LOGGING: print(f"[PRICE_DEBUG] {mint[:16]}... ✗ price calculation returned None", flush=True)

                # Aggregate prices from all pools for this mint
                if DEBUG_LOGGING: print(f"[PRICE_DEBUG] {mint[:16]}... Aggregating {len(candidate_prices)} candidate prices", flush=True)
                aggregated = PoolAggregator.aggregate(candidate_prices)
                if aggregated:
                    if DEBUG_LOGGING: print(f"[PRICE_DEBUG] {mint[:16]}... ✓ aggregated price: ${aggregated.price_usd:.8f}", flush=True)
                    
                    # ✅ OPTIONAL: Log price source for visibility into system health
                    logger.info(
                        f"[PRICE_SOURCE] mint={mint[:16]}... source={aggregated.source} "
                        f"price=${aggregated.price_usd:.8f} liquidity=${aggregated.liquidity_usd:.2f}"
                    )
                    
                    # Track peak market cap
                    if aggregated.market_cap > 0:
                        self._update_peak_market_cap(mint, aggregated.market_cap, now,
                                                     liquidity_usd=aggregated.liquidity_usd or 0.0)
                        # Store peak info in token price for API response
                        peak_info = self._get_peak_market_cap(mint)
                        if peak_info:
                            aggregated.peak_market_cap = peak_info[0]
                            aggregated.peak_market_cap_at = peak_info[1]

                    new_cache[mint] = aggregated
                else:
                    if DEBUG_LOGGING: print(f"[PRICE_DEBUG] {mint[:16]}... ✗ no candidate prices to aggregate", flush=True)

            self.price_service.pool_price_cache = new_cache
            self.stats["pool_prices_fetched"] = len(new_cache)

            # Store pool prices to database snapshots (for UI and historical tracking)
            for mint, token_price in new_cache.items():
                try:
                    logger.info(f"[PRICE_DEBUG] {mint[:16]}... ✓ calling _store_snapshot()")
                    self.price_service._store_snapshot(token_price)
                    logger.info(f"[PRICE_DEBUG] {mint[:16]}... ✓ snapshot stored")
                except Exception as e:
                    logger.error(f"[PRICE_DEBUG] {mint[:16]}... ✗ snapshot store failed: {e}")
            
            # Update token_analysis table with fresh pool prices for tokens that exist there
            if DEBUG_LOGGING: print(f"[PRICE_CYCLE] new_cache has {len(new_cache)} prices, about to update DB", flush=True)
            if new_cache:
                try:
                    conn = db_connect(self.db_path, timeout=15)
                    cursor = conn.cursor()
                    for mint, token_price in new_cache.items():
                        migration_sql, migration_params = self._build_migration_signal_sql(token_price.market_cap or 0, now)
                        cursor.execute(f"""
                            UPDATE token_analysis
                            SET price_current = ?,
                                market_cap_current = ?,
                                price_source = ?,
                                price_updated_at = ?,
                                {migration_sql}
                            WHERE mint = ?
                        """, (token_price.price_usd, token_price.market_cap, token_price.source, now, *migration_params, mint))
                    conn.commit()
                    conn.close()
                    if DEBUG_LOGGING: print(f"[PRICE_CYCLE] DB updated, about to broadcast {len(new_cache)} prices", flush=True)

                    # 🚀 BROADCAST TO UI VIA SSE (real-time price updates)
                    try:
                        from src.core.price_stream import get_price_stream
                        price_stream = get_price_stream()
                        subscriber_count = price_stream.get_subscriber_count()

                        if DEBUG_LOGGING: print(f"[BROADCAST_DEBUG] Have {len(new_cache)} prices, {subscriber_count} subscribers", flush=True)
                        logger.info(f"[BROADCAST_DEBUG] Have {len(new_cache)} prices, {subscriber_count} subscribers")

                        # Broadcast each price update
                        if subscriber_count > 0:
                            import asyncio

                            for mint, token_price in new_cache.items():
                                event = {
                                    "type": "price_update",
                                    "mint": mint,
                                    "price_usd": token_price.price_usd,
                                    "price_sol": token_price.price_sol,
                                    "market_cap": token_price.market_cap,
                                    "liquidity_usd": token_price.liquidity_usd,
                                    "source": token_price.source,
                                    "updated_at": int(time.time())
                                }
                                _log_first_price(mint, token_price.price_usd, token_price.market_cap or 0, token_price.source or "pool_cycle")

                                # Broadcast asynchronously
                                try:
                                    if DEBUG_LOGGING: print(f"[BROADCAST_DEBUG] Broadcasting {mint[:16]}...", flush=True)
                                    asyncio.create_task(price_stream.broadcast(event))
                                except RuntimeError:
                                    loop = asyncio.new_event_loop()
                                    asyncio.set_event_loop(loop)
                                    try:
                                        loop.run_until_complete(price_stream.broadcast(event))
                                        pending = asyncio.all_tasks(loop)
                                        if pending:
                                            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                                    finally:
                                        loop.close()
                        else:
                            if DEBUG_LOGGING: print(f"[BROADCAST_DEBUG] No subscribers connected, skipping broadcast", flush=True)

                    except Exception as e:
                        logger.error(f"[PRICE_STREAM] Failed to broadcast: {e}", exc_info=True)
                        print(f"[BROADCAST_ERROR] {e}", flush=True)

                except Exception as e:
                    logger.debug(f"Failed to update token_analysis with pool prices: {e}")

        except Exception as e:
            logger.error(f"Error recomputing prices from WS state: {e}")
    
    def _update_peak_market_cap(self, mint: str, market_cap: float, timestamp: int,
                                liquidity_usd: float = 0.0) -> None:
        """Monotonic peak MC update. Peak only advances on a new high, never regresses."""
        if market_cap <= 0:
            return
        try:
            # Load or initialise in-memory state for this mint
            if mint not in self._peak_state:
                import sqlite3 as _sq
                conn = _sq.connect(self.db_path, timeout=5)
                row = conn.execute(
                    "SELECT raw_peak_mc, peak_market_cap, peak_market_cap_at "
                    "FROM token_market_cap_peaks WHERE mint = ?", (mint,)
                ).fetchone()
                conn.close()
                if row:
                    self._peak_state[mint] = {
                        'raw': row[0] or 0.0,
                        'peak': row[1] or 0.0,
                        'peak_at': row[2],
                    }
                else:
                    self._peak_state[mint] = {
                        'raw': 0.0,
                        'peak': 0.0,
                        'peak_at': None,
                    }

            s = self._peak_state[mint]

            # Always update raw peak
            s['raw'] = max(s['raw'], market_cap)
            if market_cap > s['peak']:
                s['peak'] = market_cap
                s['peak_at'] = timestamp

            peak_iso = datetime.utcfromtimestamp(s['peak_at']).isoformat() + "Z" if s.get('peak_at') else None

            import sqlite3 as _sq
            conn = _sq.connect(self.db_path, timeout=5)
            conn.execute("""
                INSERT INTO token_market_cap_peaks
                    (mint, peak_market_cap, peak_market_cap_at,
                     raw_peak_mc, effective_peak_mc, candidate_peak_mc, candidate_peak_count, raw_peak_mc_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mint) DO UPDATE SET
                    peak_market_cap      = MAX(COALESCE(peak_market_cap, 0), excluded.peak_market_cap),
                    peak_market_cap_at   = CASE
                                               WHEN excluded.peak_market_cap > COALESCE(peak_market_cap, 0) THEN excluded.peak_market_cap_at
                                               WHEN peak_market_cap_at IS NULL OR peak_market_cap_at = 0 THEN excluded.peak_market_cap_at
                                               ELSE peak_market_cap_at
                                           END,
                    raw_peak_mc          = MAX(COALESCE(raw_peak_mc, 0), excluded.raw_peak_mc),
                    raw_peak_mc_at       = CASE
                                               WHEN excluded.raw_peak_mc > COALESCE(raw_peak_mc, 0) THEN excluded.raw_peak_mc_at
                                               WHEN raw_peak_mc_at IS NULL OR raw_peak_mc_at = 0 THEN excluded.raw_peak_mc_at
                                               ELSE raw_peak_mc_at
                                           END,
                    effective_peak_mc    = MAX(COALESCE(effective_peak_mc, 0), excluded.effective_peak_mc),
                    candidate_peak_mc    = MAX(COALESCE(candidate_peak_mc, 0), excluded.candidate_peak_mc),
                    candidate_peak_count = MAX(COALESCE(candidate_peak_count, 0), excluded.candidate_peak_count)
            """, (mint, s['peak'], s.get('peak_at'),
                  s['raw'], s['peak'], s['peak'], 1, timestamp))
            conn.execute("""
                UPDATE token_analysis
                SET market_cap_highest = MAX(COALESCE(market_cap_highest, 0), ?),
                    market_cap_highest_at_ts = CASE
                        WHEN ? > COALESCE(market_cap_highest, 0) THEN ?
                        WHEN market_cap_highest_at_ts IS NULL OR market_cap_highest_at_ts = 0 THEN ?
                        ELSE market_cap_highest_at_ts
                    END,
                    market_cap_highest_at = CASE
                        WHEN ? > COALESCE(market_cap_highest, 0) THEN ?
                        WHEN market_cap_highest_at IS NULL OR market_cap_highest_at = '' THEN ?
                        ELSE market_cap_highest_at
                    END
                WHERE mint = ?
            """, (
                s['peak'],
                market_cap,
                s.get('peak_at'),
                s.get('peak_at'),
                market_cap,
                peak_iso,
                peak_iso,
                mint,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"Error updating peak market cap for {mint}: {e}")
    
    def _get_peak_market_cap(self, mint: str) -> Optional[tuple]:
        """Get peak market cap and timestamp for a token."""
        try:
            import sqlite3
            conn = db_connect(self.db_path, timeout=15)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT peak_market_cap, peak_market_cap_at FROM token_market_cap_peaks WHERE mint = ?",
                (mint,)
            )
            row = cursor.fetchone()
            conn.close()
            return row
        except Exception as e:
            logger.debug(f"Error fetching peak market cap for {mint}: {e}")
            return None

    def _sync_new_tokens(self) -> None:
        """
        Lightweight auto-sync for recently active validated pools.

        This keeps future tokens entering the worker pipeline even if the dashboard
        has not explicitly re-registered them yet, without re-adding the old
        "track the entire database" behavior.
        """
        try:
            conn = db_connect(self.db_path, timeout=15)
            cursor = conn.cursor()
            now = int(time.time())
            cursor.execute("""
                SELECT DISTINCT tpa.mint
                FROM token_pool_accounts tpa
                LEFT JOIN tracked_tokens tt ON tt.mint = tpa.mint
                WHERE tpa.is_active = 1
                  AND tpa.vault_validation_status IN ('validated', 'pending')
                  AND COALESCE(tpa.updated_at, tpa.created_at, 0) >= ?
                  AND (
                      tt.mint IS NULL
                      OR tt.is_active = 0
                  )
                ORDER BY COALESCE(tpa.updated_at, tpa.created_at, 0) DESC
                LIMIT 100
            """, (now - 7200,))
            missing_mints = [row[0] for row in cursor.fetchall()]
            conn.close()

            for mint in missing_mints:
                self.registry.register_token(mint, priority_level='MEDIUM')
        except Exception as e:
            logger.debug(f"Error syncing recent validated pools to tracker: {e}")

    # Tier intervals (seconds between refreshes)
    _TIER0_INTERVAL = 3    # new tokens (<120s) — fast lane supplement
    _TIER1_INTERVAL = 5    # homepage tokens
    _TIER2_INTERVAL = 10   # everything else
    _TIER2_CAP = 20        # max Tier-2 tasks enqueued per cycle
    _TIER2_QUEUE_THRESHOLD = 50  # skip Tier-2 entirely if queue is this deep

    def _get_tokens_for_refresh(self) -> List[Dict]:
        """
        Three-tier scheduling:
          Tier 0 — tokens detected <120s ago  → every 3s
          Tier 1 — homepage top-25 mints      → every 5s
          Tier 2 — all other tracked tokens   → every 20s, capped at 5/cycle
        """
        self._refresh_top_mints()

        now = int(time.time())
        tier0, tier1, tier2 = [], [], []

        try:
            all_tokens = self.registry.get_tracked_tokens(active_only=True)

            for token in all_tokens:
                mint = token.get('mint', '')
                last_update = token.get('last_price_update', 0)
                age = now - last_update

                # Determine tier
                created_raw = token.get('created_at') or 0
                try:
                    if isinstance(created_raw, (int, float)):
                        token_age = now - int(created_raw)
                    else:
                        import datetime as _dt
                        ts = _dt.datetime.fromisoformat(
                            str(created_raw).rstrip('Z').replace(' ', 'T')
                        ).replace(tzinfo=_dt.timezone.utc).timestamp()
                        token_age = now - int(ts)
                except Exception:
                    token_age = 9999

                is_new    = token_age < 120 and mint in self._top_mints
                is_top    = mint in self._top_mints
                never_fetched = last_update == 0

                priority = token.get('priority_level', 'LOW').lower()
                self.stats['activity_distribution'][priority] = \
                    self.stats['activity_distribution'].get(priority, 0) + 1

                # Skip Tier 2 for tokens with no update in 6h — they're dead/old
                last_update = token.get('last_price_update', 0)
                recently_active = last_update > 0 and (now - last_update) < 21600

                if is_new and age >= self._TIER0_INTERVAL:
                    tier0.append(token)
                elif (is_top or never_fetched) and age >= self._TIER1_INTERVAL:
                    tier1.append(token)
                elif not is_top and not never_fetched and recently_active and age >= self._TIER2_INTERVAL:
                    tier2.append(token)

            # Tier 2: skip entirely if queue is backed up, cap otherwise
            queue_depth = self.queue.queue.qsize()
            if queue_depth >= self._TIER2_QUEUE_THRESHOLD:
                tier2 = []
                logger.debug(f"[TIERS] t2 suppressed queue_depth={queue_depth}")
            else:
                tier2 = tier2[:self._TIER2_CAP]

            result = tier0 + tier1 + tier2
            logger.debug(f"[TIERS] t0={len(tier0)} t1={len(tier1)} t2={len(tier2)} q={queue_depth}")
            return result

        except Exception as e:
            logger.error(f"Error getting tokens for refresh: {e}")
            return []


    def _compute_activity_score(self, token: Dict) -> str:
        """
        Compute activity level for a token.

        Scores based on:
        - Market cap (40% weight): Current vs peak
        - Price (30% weight): Has current price fetched
        - Price movement (20% weight): 1h price change %
        - Age (10% weight): Token age

        Returns: 'high', 'medium', 'low', or 'dormant'
        """
        try:
            score = 0

            # Market cap score (40% weight, 0-40 points)
            # Higher score if close to peak (still active) vs far from peak (declining)
            current_mc = token.get('market_cap_current', 0) or 0
            peak_mc = token.get('market_cap_highest', 0) or 0
            if peak_mc > 0:
                ratio = current_mc / peak_mc
                if ratio > 0.8:
                    mc_score = 40
                elif ratio > 0.5:
                    mc_score = 28
                elif ratio > 0.25:
                    mc_score = 16
                else:
                    mc_score = 4
            else:
                mc_score = 4
            score += mc_score

            # Price score (30% weight, 0-30 points)
            # Indicate if token has recent price data (active trading)
            current_price = token.get('price_current', 0) or 0
            if current_price > 0:
                price_score = 30  # Has current price
            else:
                price_score = 5  # No current price
            score += price_score

            # Price movement score (20% weight, 0-20 points)
            price_movement_score = self._compute_price_movement_score(token['mint'])
            score += price_movement_score

            # Age score (10% weight, 0-10 points)
            created_at = token.get('created_at')
            if created_at:
                try:
                    from datetime import datetime
                    # created_at might be a timestamp (numeric) or ISO string
                    if isinstance(created_at, (int, float)):
                        created_time = datetime.fromtimestamp(created_at)
                    else:
                        created_time = datetime.fromisoformat(str(created_at))
                    age_seconds = (datetime.now() - created_time).total_seconds()

                    if age_seconds < 300:  # < 5 min
                        age_score = 10
                    elif age_seconds < 1800:  # < 30 min
                        age_score = 8
                    elif age_seconds < 3600:  # < 1 hour
                        age_score = 6
                    elif age_seconds < 86400:  # < 24 hours
                        age_score = 4
                    else:
                        age_score = 1
                except Exception:
                    age_score = 1
            else:
                age_score = 1
            score += age_score

            # Map score to activity level
            if score >= 75:
                return 'high'
            elif score >= 40:
                return 'medium'
            elif score >= 20:
                return 'low'
            else:
                return 'dormant'

        except Exception as e:
            logger.warning(f"Error computing activity for {token.get('mint')}: {e}")
            return 'medium'  # Safe default  # Safe default

    def _compute_price_movement_score(self, mint: str) -> int:
        """
        Compute price movement in last hour.

        Returns: 0-20 points
        """
        try:
            import sqlite3
            from datetime import datetime, timedelta

            conn = db_connect(self.db_path, timeout=15)
            cursor = conn.cursor()

            # Get prices from last 1 hour
            one_hour_ago = int((datetime.now() - timedelta(hours=1)).timestamp())
            cursor.execute("""
                SELECT price_usd FROM token_price_snapshots
                WHERE mint = ? AND captured_at > ?
                ORDER BY captured_at DESC
                LIMIT 2
            """, (mint, one_hour_ago))

            rows = cursor.fetchall()
            conn.close()

            if len(rows) < 2:
                return 5  # Not enough data, neutral score

            current_price = rows[0][0] or 0
            older_price = rows[-1][0] or 0

            if older_price == 0:
                return 5

            change_pct = abs((current_price - older_price) / older_price) * 100

            if change_pct > 50:
                return 20
            elif change_pct > 25:
                return 15
            elif change_pct > 10:
                return 10
            elif change_pct > 5:
                return 5
            else:
                return 2

        except Exception as e:
            logger.debug(f"Error computing price movement for {mint}: {e}")
            return 5

    def _get_refresh_interval_for_activity(self, activity: str) -> int:
        """
        Get refresh interval in seconds for activity level.

        Returns: seconds between refreshes
        """
        intervals = {
            'high': 10,      # 10s for very active tokens
            'medium': 30,    # 30s for moderately active
            'low': 90,       # 90s for less active
            'dormant': 180   # 3 min for dormant (conservative)
        }
        return intervals.get(activity, 30)

    def _batch_fetch_prices(self, mints: List[str]) -> None:
        """Fetch prices for a list of mints in batches and track peak market cap."""
        import sqlite3
        from datetime import datetime

        for i in range(0, len(mints), self.batch_size):
            batch = mints[i:i + self.batch_size]
            try:
                prices = self.price_service.get_token_prices_sync(batch, cache_type='hot')
                self.stats['tokens_prefetched'] += len(prices)
                self.stats['api_calls'] += 1

                # Update peak market cap for each token
                try:
                    conn = db_connect(self.db_path, timeout=15)
                    cursor = conn.cursor()

                    for mint, price in prices.items():
                        if price.source == 'cached':
                            self.stats['cache_hits'] += 1

                        # Use market cap from price service (already calculated by Dexscreener/Jupiter)
                        market_cap = price.market_cap if price.market_cap else 0
                        migration_sql, migration_params = self._build_migration_signal_sql(market_cap, int(time.time()))

                        # Update current price only — peaks owned by price_service.py
                        cursor.execute(
                            f"""UPDATE token_analysis
                               SET price_current = ?,
                                   market_cap_current = ?,
                                   {migration_sql}
                               WHERE mint = ?""",
                            (price.price_usd, market_cap, *migration_params, mint)
                        )

                    conn.commit()
                    conn.close()
                except Exception as db_err:
                    logger.warning(f"Error updating peak market cap: {db_err}")

                logger.debug(f"Prefetched {len(batch)} tokens: {list(prices.keys())[:3]}...")
            except Exception as e:
                logger.error(f"Error fetching batch {batch[:3]}...: {e}")
                self.stats['errors'] += 1

    def _fetch_single_price(self, mint: str) -> TokenPrice:
        """
        Fetch price for a single token.

        Called by the price fetch queue worker.

        Returns TokenPrice object or unavailable placeholder.
        """
        try:
            # Fetch from price service (which handles multi-source, caching, etc.)
            prices = self.price_service.get_token_prices_sync([mint], cache_type='hot')
            return prices.get(mint)
        except Exception as e:
            logger.error(f"Error fetching price for {mint}: {e}")
            # Record fetch failure for inactivity tracking
            self._inactivity_manager.record_fetch_failure(mint)
            # Return unavailable placeholder
            return TokenPrice(
                mint=mint,
                price_usd=0,
                price_sol=0,
                liquidity_usd=0,
                volume_24h=0,
                market_cap=0,
                source='unavailable',
                is_stale=True
            )

    def _on_price_fetched(self, mint: str, price: TokenPrice) -> None:
        """
        Callback when price is fetched from queue.

        Updates database with price and market cap data.
        """
        try:
            from datetime import datetime

            # Track stats
            self.stats['tokens_prefetched'] += 1
            if price.source == 'cached':
                self.stats['cache_hits'] += 1

            # Update peak market cap
            market_cap = price.market_cap if price.market_cap else 0

            if market_cap > 0 or price.price_usd > 0:
                conn = db_connect(self.db_path, timeout=15)
                cursor = conn.cursor()
                now = int(time.time())
                migration_sql, migration_params = self._build_migration_signal_sql(market_cap, now)

                # Update current price only — peaks owned by price_service.py
                if market_cap > 0:
                    cursor.execute(
                        f"""UPDATE token_analysis
                           SET price_current = ?,
                               market_cap_current = ?,
                               price_source = ?,
                               price_updated_at = ?,
                               {migration_sql}
                           WHERE mint = ?""",
                        (price.price_usd, market_cap, price.source, now, *migration_params, mint)
                    )
                else:
                    # No valid market cap, just update price
                    cursor.execute(
                        f"""UPDATE token_analysis
                           SET price_current = ?,
                               price_source = ?,
                               price_updated_at = ?,
                               {migration_sql}
                           WHERE mint = ?""",
                        (price.price_usd, price.source, now, *migration_params, mint)
                    )

                conn.commit()
                conn.close()

                # Update timestamp
                self.registry.update_price_timestamp(mint)

                # Store snapshot so peak tracking works for queue-fetched tokens
                # Guard: skip onchain prices with no liquidity (bad pool-reserve ratio)
                if market_cap > 1.0 and not (price.source == 'onchain' and (price.liquidity_usd or 0) < 10):
                    try:
                        self.price_service._store_snapshot(price)
                    except Exception as snap_err:
                        logger.debug(f"[ON_PRICE_FETCHED] snapshot store failed for {mint[:16]}: {snap_err}")

                # Record activity for inactivity tracking (reset inactivity timer)
                self._inactivity_manager.record_activity(mint)

                # 🚀 BROADCAST TO UI VIA SSE
                try:
                    from src.core.price_stream import get_price_stream
                    price_stream = get_price_stream()

                    # Only broadcast if there are subscribers
                    if price_stream.get_subscriber_count() > 0:
                        import asyncio

                        event = {
                            "type": "price_update",
                            "mint": mint,
                            "price_usd": price.price_usd,
                            "price_sol": price.price_sol,
                            "market_cap": market_cap,
                            "liquidity_usd": price.liquidity_usd,
                            "source": price.source,
                            "updated_at": int(time.time())
                        }
                        _log_first_price(mint, price.price_usd, market_cap or 0, price.source or "prefetch")

                        # Broadcast asynchronously (non-blocking)
                        try:
                            asyncio.create_task(price_stream.broadcast(event))
                        except RuntimeError:
                            # No event loop in current thread, create one
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            try:
                                loop.run_until_complete(price_stream.broadcast(event))
                                # Drain any tasks scheduled during broadcast before closing
                                pending = asyncio.all_tasks(loop)
                                if pending:
                                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                            finally:
                                loop.close()

                except Exception as e:
                    logger.debug(f"[PRICE_STREAM] Failed to broadcast price update: {e}")

        except Exception as e:
            logger.error(f"Error in price fetch callback for {mint}: {e}")
            self.stats['errors'] += 1

    def trigger_pool_refresh(self) -> None:
        """Refresh WebSocket subscriptions with newly registered pools.

        Called after vault discovery registers new pools. Uses incremental refresh
        if WebSocket is running, or full rebuild if not started yet.

        Debounced to prevent reconnect storms when multiple pools discovered quickly.
        """
        import time
        now = time.time()

        # Debounce: skip if refresh was triggered recently
        if now - self._last_pool_refresh < self._refresh_debounce_seconds:
            logger.debug(f"[PRICE_WORKER] ⏱️ Refresh debounced (last was {now - self._last_pool_refresh:.1f}s ago)")
            return

        print("[PRICE_WORKER] 🔔 trigger_pool_refresh() CALLED", flush=True)
        logger.info("[PRICE_WORKER] 🔔 trigger_pool_refresh() CALLED")
        self._last_pool_refresh = now

        try:
            from src.core.pool_price_engine import get_pool_fetcher

            fetcher = get_pool_fetcher(self.db_path)
            pools = fetcher.get_active_pools()

            if not pools:
                logger.warning("[PRICE_WORKER] No active pools found")
                return

            # If WebSocket already running, refresh incrementally (faster)
            if self._ws_client and self._ws_started:
                print(f"[PRICE_WORKER] 🔄 Refreshing WebSocket with {len(pools)} pools (incremental)", flush=True)
                logger.info(f"[PRICE_WORKER] 🔄 Refreshing WebSocket with {len(pools)} pools (incremental)")
                self._ws_client.refresh_pools(pools)
                # Sync pool map cache so WS fast-path doesn't miss new tokens
                self._pool_map_cache = {(p["mint"], p["base_account"]): p for p in pools}
            else:
                # Full rebuild: stop old, start new
                if self._ws_client:
                    print(f"[PRICE_WORKER] 🛑 Stopping old WebSocket client for full rebuild", flush=True)
                    logger.info(f"[PRICE_WORKER] 🛑 Stopping old WebSocket client for full rebuild")
                    self._ws_client.stop()
                    self._ws_started = False

                print(f"[PRICE_WORKER] 🚀 Starting fresh WebSocket with {len(pools)} pools", flush=True)
                logger.info(f"[PRICE_WORKER] 🚀 Starting fresh WebSocket with {len(pools)} pools")
                self._start_ws_client()

        except Exception as e:
            logger.error(f"[PRICE_WORKER] ❌ Error refreshing: {e}", exc_info=True)

    def has_pool_data(self, pool_address: str = None) -> bool:
        """
        Check if PoolStateStore has been hydrated with real reserve data.

        If pool_address provided, check that specific pool.
        If None, check if ANY pool data exists (bootstrap readiness indicator).
        """
        if pool_address:
            # Check specific pool
            reserves = self._pool_state.get_pools_for_mint(pool_address)
            if reserves:
                for base_account, base_raw, quote_raw in reserves:
                    if base_raw and quote_raw and base_raw > 0 and quote_raw > 0:
                        return True
            return False
        else:
            # Check if any pool data exists
            all_mints = self._pool_state.get_all_mints()
            if not all_mints:
                return False
            # Check at least one mint has hydrated reserves
            for mint in all_mints:
                reserves = self._pool_state.get_pools_for_mint(mint)
                if reserves:
                    for base_account, base_raw, quote_raw in reserves:
                        if base_raw and quote_raw and base_raw > 0 and quote_raw > 0:
                            return True
            return False

    def bootstrap_single_pool(self, mint: str, pool_meta: Dict) -> bool:
        """Fetch reserves for a single newly registered pool and seed PoolStateStore.

        Called immediately after a new migration pool is registered so that
        _recompute_prices_from_ws_state() can price it before the next WS event
        arrives (which may take 30-60s if the pool is quiet).

        Returns True if reserves were successfully seeded.
        """
        try:
            import asyncio as _aio
            from src.core.pool_price_engine import get_pool_fetcher

            fetcher = get_pool_fetcher(self.db_path)
            loop = _aio.new_event_loop()
            try:
                reserves_dict = loop.run_until_complete(fetcher.fetch_reserves([pool_meta]))
            finally:
                loop.close()

            if not reserves_dict:
                logger.info(f"[BOOTSTRAP_POOL] No reserves returned for {mint[:16]}...")
                return False

            seeded = 0
            for (r_mint, base_account), (base_raw, quote_raw) in reserves_dict.items():
                if r_mint != mint:
                    continue
                if base_raw and quote_raw and base_raw > 0 and quote_raw > 0:
                    self._pool_state.update_reserve(mint, base_account, "base", base_raw)
                    self._pool_state.update_reserve(mint, base_account, "quote", quote_raw)
                    seeded += 1
                    logger.info(
                        f"[BOOTSTRAP_POOL] ✅ {mint[:16]}... reserves seeded "
                        f"base={base_raw} quote={quote_raw}"
                    )
                    print(
                        f"[BOOTSTRAP_POOL] ✅ {mint[:16]}... reserves seeded "
                        f"base={base_raw} quote={quote_raw}",
                        flush=True
                    )

            return seeded > 0

        except Exception as e:
            logger.warning(f"[BOOTSTRAP_POOL] Failed for {mint[:16]}...: {e}")
            return False

    def get_stats(self) -> Dict:
        """Get worker statistics including circuit breaker and source metrics."""
        stats = self.stats.copy()

        # Add circuit breaker state from price service
        if hasattr(self.price_service, 'circuit_breaker'):
            stats['circuit_breaker'] = {
                k: {
                    'disabled': v['disabled'],
                    'cooldown_remaining_secs': max(0, 600 - (time.time() - v.get('disabled_at', 0)))
                }
                for k, v in self.price_service.circuit_breaker.items()
            }

        # Add source attempt metrics
        if hasattr(self.price_service, 'source_attempts'):
            stats['source_metrics'] = {
                source: {
                    'attempts_tracked': len(attempts),
                    'recent_success_rate': (
                        sum(1 for _, s in attempts if s) / len(attempts)
                        if attempts else 0.0
                    )
                }
                for source, attempts in self.price_service.source_attempts.items()
            }

        return {
            'worker': stats,
            'registry': self.registry.get_stats()
        }


# Global worker instance
_price_worker: Optional[BackgroundPriceWorker] = None


def get_price_worker(db_path: str = 'database/flex_complete_database.db') -> BackgroundPriceWorker:
    """Get or create singleton price worker."""
    global _price_worker
    if _price_worker is None:
        _price_worker = BackgroundPriceWorker(db_path)
    return _price_worker


def start_price_worker(db_path: str = 'database/flex_complete_database.db') -> BackgroundPriceWorker:
    """Start the background price worker."""
    worker = get_price_worker(db_path)
    worker.start()
    return worker
