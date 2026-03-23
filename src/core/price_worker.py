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
import logging
import time
import threading
import asyncio
from typing import List, Dict, Optional
from src.core.price_service import get_price_service, TokenPrice
from src.core.price_fetch_queue import get_price_queue, FetchTask, start_price_queue_worker

logger = logging.getLogger(__name__)


class PriceWorkerRegistry:
    """Manages the tracked tokens registry."""

    def __init__(self, db_path: str = 'database/flex_complete_database.db'):
        self.db_path = db_path
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
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
                INSERT OR REPLACE INTO tracked_tokens
                (mint, symbol, pair_address, priority_level, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
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
                 interval: int = 10, batch_size: int = 20):
        """
        Initialize worker.

        Args:
            db_path: Path to database
            interval: Refresh interval in seconds (default 10)
            batch_size: Legacy parameter (no longer used; queue handles batching)
        """
        self.db_path = db_path
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

        self._pool_state = get_pool_state()  # Use singleton shared with listener
        self._ws_client: Optional[PoolWebSocketClient] = None
        self._ws_manager = get_websocket_manager_sharded(self._pool_state, db_path)
        self._ws_started = False
        self._last_fallback_poll = 0

        # Debounce WebSocket refresh to prevent reconnect storms
        self._last_pool_refresh = 0
        self._refresh_debounce_seconds = 5

        # Use new SOL price cache (20s TTL) instead of manual caching
        self._sol_price_cache = get_sol_price_cache()

        # Market cap calculator with supply caching
        self._market_cap_calc = get_market_cap_calculator(db_path)

        # Priority queue: top tokens for WebSocket, others use Dexscreener fallback
        self._top_mints = set()  # Top 20-25 most recent tokens (from UI)
        self._top_mints_updated = 0  # Last time we refreshed this list

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

    def start(self) -> None:
        """Start the background worker."""
        if self.running:
            logger.warning("Worker already running")
            return

        # Start the price fetch queue worker
        start_price_queue_worker(self._fetch_single_price)

        self.running = True
        
        # Initialize PoolStateStore BEFORE starting the worker thread
        # This must happen before _run_loop starts cycling
        logger.info("[PRICE_WORKER] Initializing PoolStateStore...")
        try:
            from src.core.pool_price_engine import get_pool_fetcher
            fetcher = get_pool_fetcher(self.db_path)
            pools = fetcher.get_active_pools()
            if pools:
                for pool in pools:
                    mint = pool.get("mint")
                    base_account = pool.get("base_account")
                    if mint and base_account:
                        # Initialize with zero reserves - WebSocket will populate real values
                        self._pool_state.update_reserve(mint, base_account, "base", 0)
                        self._pool_state.update_reserve(mint, base_account, "quote", 0)
                all_mints = self._pool_state.get_all_mints()
                logger.info(f"[PRICE_INIT] ✅ Populated {len(all_mints)} mints in PoolStateStore")
                print(f"[PRICE_INIT] ✅ Populated {len(all_mints)} mints in PoolStateStore", flush=True)
        except Exception as e:
            logger.error(f"[PRICE_INIT] Failed: {e}", exc_info=True)
            print(f"[PRICE_INIT] ERROR: {e}", flush=True)
        
        # NOW start the worker thread (pool state is already initialized)
        logger.info("[PRICE_WORKER] Creating thread")
        print("[PRICE_WORKER] Creating thread", flush=True)
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

        logger.info(f"Background price worker started (interval={self.interval}s, using request queue)")

    def _initialize_pool_state_sync(self) -> None:
        """Initialize PoolStateStore - populate it immediately so WebSocket updates can apply."""
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

                # Initialize PoolStateStore with fetched (or zero) reserves
                print(f"[PRICE_INIT] Initializing {len(pools)} pools in PoolStateStore...", flush=True)
                populated = 0
                for i, pool in enumerate(pools):
                    mint = pool.get("mint")
                    base_account = pool.get("base_account")
                    quote_account = pool.get("quote_account")
                    if mint and base_account and quote_account:
                        # Try to get fetched reserves, fall back to 0
                        (base_raw, quote_raw) = reserves_dict.get((mint, base_account), (0, 0))
                        self._pool_state.update_reserve(mint, base_account, "base", base_raw)
                        self._pool_state.update_reserve(mint, base_account, "quote", quote_raw)
                        if base_raw > 0 or quote_raw > 0:
                            print(f"[PRICE_INIT] Pool {mint[:12]}...: base={base_raw}, quote={quote_raw}", flush=True)
                        populated += 1
                    if (i + 1) % 20 == 0:
                        print(f"[PRICE_INIT] Initialized {i + 1}/{len(pools)} pools...", flush=True)
                
                all_mints = self._pool_state.get_all_mints()
                print(f"[PRICE_INIT] ✅ Done! {len(all_mints)} mints ready for WebSocket", flush=True)
                logger.info(f"[PRICE_INIT] ✅ Initialized {len(all_mints)} mints")
                
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
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Get top 25 most recent tokens
            cursor.execute("""
                SELECT mint FROM token_analysis
                ORDER BY created_at DESC
                LIMIT 25
            """)
            rows = cursor.fetchall()
            self._top_mints = {row['mint'] for row in rows}
            self._top_mints_updated = now
            self.stats['top_mints_count'] = len(self._top_mints)
            conn.close()
        except Exception as e:
            logger.debug(f"Error refreshing top mints: {e}")

    def _start_ws_client(self) -> None:
        """Start WebSocket client for pool subscriptions."""
        try:
            from src.core.pool_price_engine import get_pool_fetcher

            fetcher = get_pool_fetcher(self.db_path)
            pools = fetcher.get_active_pools()

            if not pools:
                logger.info("[PRICE_WORKER] No pools to subscribe to")
                return

            logger.info(f"[PRICE_WORKER] Creating WebSocket client for {len(pools)} pools")
            self._ws_client = __import__('src.core.pool_price_engine', fromlist=['PoolWebSocketClient']).PoolWebSocketClient(self._pool_state, self.db_path)

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
        print("[PRICE_DEBUG] refresh_cycle START", flush=True)
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

            conn = sqlite3.connect(self.db_path)
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

        # Check for stale WS (no events >2 minutes) — force more frequent fallback poll
        ws_is_stale = False
        if self._ws_client and not in_critical_window:
            time_since_last_event = now - self._ws_client._last_event_received
            if time_since_last_event > self._ws_client.WS_STALE_THRESHOLD:
                ws_is_stale = True
                self._ws_client.stats["is_stale"] = True
                if now - self._last_fallback_poll >= 30:
                    logger.warning(f"WS stale for {time_since_last_event:.0f}s — triggering fallback poll")
        elif in_critical_window:
            logger.debug(f"Suppressing stale WS fallback poll during critical discovery window")

        # Fallback poll: every 60s normally, every 30s if WS is stale
        poll_interval = 30 if ws_is_stale else 60
        if now - self._last_fallback_poll >= poll_interval:
            try:
                asyncio.run(self._fetch_pool_prices_async())
            except Exception as e:
                logger.error(f"Pool fallback poll error: {e}")
            self._last_fallback_poll = now

        # Check for pools marked as stale by PoolStateStore (no updates >5 min)
        stale_mints = self._pool_state.mark_stale_pools(now)

        # Primary: compute prices from WebSocket-maintained reserve state
        self._recompute_prices_from_ws_state()

        # Sync WS stats to worker stats
        if self._ws_client:
            self.stats['ws_stats'] = dict(self._ws_client.stats)

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

        # Batch-fetch all reserves: now keyed by (mint, base_account)
        reserves = await fetcher.fetch_reserves(pools)
        
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
                    self._update_peak_market_cap(mint, aggregated.market_cap, now)
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
                conn = sqlite3.connect(self.db_path, timeout=5)
                cursor = conn.cursor()
                for mint, token_price in new_cache.items():
                    cursor.execute("""
                        UPDATE token_analysis
                        SET price_current = ?,
                            market_cap_current = ?,
                            price_source = ?,
                            price_updated_at = ?
                        WHERE mint = ?
                    """, (token_price.price_usd, token_price.market_cap, token_price.source, now, mint))
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
                print("[PRICE_DEBUG] No active pools found", flush=True)
                return

            # Key by (mint, base_account) to support multiple pools per token
            pool_map = {(p["mint"], p["base_account"]): p for p in pools}
            print(f"[PRICE_DEBUG] Built pool_map with {len(pool_map)} pool entries", flush=True)

            # Get all distinct mints
            mints = self._pool_state.get_all_mints()
            print(f"[PRICE_DEBUG] Mints in PoolStateStore: {len(mints)}", flush=True)

            # Get SOL price from cache (20s TTL, reduces API calls by ~95%)
            async def fetch_sol():
                return await PoolPriceCalculator.fetch_sol_price_usd()

            try:
                sol_price_usd = asyncio.run(self._sol_price_cache.get_price(fetch_sol))
            except Exception as e:
                logger.error(f"Failed to get SOL price: {e}")
                return

            if not sol_price_usd or sol_price_usd <= 0:
                print(f"[PRICE_DEBUG] Invalid SOL price: {sol_price_usd}", flush=True)
                return

            print(f"[PRICE_DEBUG] SOL price valid: ${sol_price_usd:.2f}", flush=True)

            # Fetch token supplies for all mints (cached by MarketCapCalculator)
            # NOTE: Skipping supply fetch to avoid slowdown — use pool token_supply instead
            print(f"[PRICE_DEBUG] Skipping supply fetch (using pool defaults)", flush=True)
            supply_cache = {}

            new_cache: Dict[str, TokenPrice] = {}
            now = int(time.time())

            print(f"[PRICE_DEBUG] Starting mint loop for {len(mints)} mints", flush=True)
            processed = 0
            for mint in mints:
                # Get all pools for this mint
                pool_reserves = self._pool_state.get_pools_for_mint(mint)
                processed += 1
                if processed % 10 == 1:
                    print(f"[PRICE_DEBUG] Processing mint {processed}/{len(mints)}: {mint[:16]}... reserves={len(pool_reserves) if pool_reserves else 0}", flush=True)
                if not pool_reserves:
                    continue

                print(f"[PRICE_DEBUG] {mint[:16]}... ✓ reserves present: {len(pool_reserves)} pools", flush=True)

                last_price = self.price_service.pool_price_cache.get(mint)
                last_price_usd = last_price.price_usd if last_price else None

                # Use cached supply or fallback to pool's token_supply
                supply = supply_cache.get(mint, 0)

                # Compute price for each pool
                candidate_prices = []
                for base_account, base_raw, quote_raw in pool_reserves:
                    pool = pool_map.get((mint, base_account))
                    if not pool:
                        print(f"[PRICE_DEBUG] {mint[:16]}... ✗ pool metadata MISSING for base_account={base_account[:16]}... (looked in {len(pool_map)} pool entries)", flush=True)
                        continue

                    print(f"[PRICE_DEBUG] {mint[:16]}... ✓ pool metadata loaded: decimals={pool.get('base_decimals')}/{pool.get('quote_decimals')}, quote={pool.get('quote_token')[:16]}", flush=True)

                    # Use fetched supply, fallback to pool value, then default
                    total_supply = supply or pool.get("token_supply", 0)

                    print(f"[PRICE_DEBUG] {mint[:16]}... Computing price: base_raw={base_raw}, quote_raw={quote_raw}, total_supply={total_supply}", flush=True)
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
                        logger.info(f"[PRICE_DEBUG] {mint[:16]}... ✓ price computed: ${token_price.price_usd}")
                        candidate_prices.append(token_price)
                    else:
                        logger.debug(f"[PRICE_DEBUG] {mint[:16]}... ✗ price calculation returned None")

                # Aggregate prices from all pools for this mint
                aggregated = PoolAggregator.aggregate(candidate_prices)
                if aggregated:
                    logger.info(f"[PRICE_DEBUG] {mint[:16]}... ✓ aggregated price: ${aggregated.price_usd}")
                    # Track peak market cap
                    if aggregated.market_cap > 0:
                        self._update_peak_market_cap(mint, aggregated.market_cap, now)
                        # Store peak info in token price for API response
                        peak_info = self._get_peak_market_cap(mint)
                        if peak_info:
                            aggregated.peak_market_cap = peak_info[0]
                            aggregated.peak_market_cap_at = peak_info[1]

                    new_cache[mint] = aggregated
                else:
                    logger.debug(f"[PRICE_DEBUG] {mint[:16]}... ✗ no candidate prices to aggregate")

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
            if new_cache:
                try:
                    conn = sqlite3.connect(self.db_path, timeout=5)
                    cursor = conn.cursor()
                    for mint, token_price in new_cache.items():
                        cursor.execute("""
                            UPDATE token_analysis
                            SET price_current = ?,
                                market_cap_current = ?,
                                price_source = ?,
                                price_updated_at = ?
                            WHERE mint = ?
                        """, (token_price.price_usd, token_price.market_cap, token_price.source, now, mint))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.debug(f"Failed to update token_analysis with pool prices: {e}")

        except Exception as e:
            logger.error(f"Error recomputing prices from WS state: {e}")
    
    def _update_peak_market_cap(self, mint: str, market_cap: float, timestamp: int) -> None:
        """Update peak market cap for a token if new value is higher."""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()
            
            # Check current peak
            cursor.execute(
                "SELECT peak_market_cap FROM token_market_cap_peaks WHERE mint = ?",
                (mint,)
            )
            row = cursor.fetchone()
            
            # Update if new peak or first time
            if not row or market_cap > row[0]:
                cursor.execute("""
                    INSERT INTO token_market_cap_peaks (mint, peak_market_cap, peak_market_cap_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(mint) DO UPDATE SET
                    peak_market_cap = ?,
                    peak_market_cap_at = ?
                """, (mint, market_cap, timestamp, market_cap, timestamp))
                conn.commit()
            
            conn.close()
        except Exception as e:
            logger.debug(f"Error updating peak market cap for {mint}: {e}")
    
    def _get_peak_market_cap(self, mint: str) -> Optional[tuple]:
        """Get peak market cap and timestamp for a token."""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path, timeout=10)
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
        Disabled: No longer auto-sync all tokens.

        Only tokens explicitly registered via /api/price/batch/register
        will be tracked. This prevents wasteful fetching of 2000+ tokens
        when we only display 25.

        Previously this method would sync all tokens from token_analysis,
        causing the system to fetch prices for every token in the database.
        Now only the 25 visible tokens are registered by the dashboard.
        """
        pass  # No-op: relying on explicit registration only

    def _get_tokens_for_refresh(self) -> List[Dict]:
        """
        Get tokens for refresh based on priority_level and UI visibility.

        Priority queue strategy:
        - Top 20-25 tokens (UI page): WebSocket + Dexscreener sources
        - Other tokens: Dexscreener fallback only (lighter weight)

        Each tier has a fixed refresh interval.
        """
        # Refresh top mints list (updates every 30s)
        self._refresh_top_mints()

        tokens_to_fetch = []
        now = int(time.time())

        try:
            # Get all active tokens
            all_tokens = self.registry.get_tracked_tokens(active_only=True)

            # Separate top-priority from regular tokens
            top_priority_tokens = []
            regular_tokens = []

            for token in all_tokens:
                mint = token.get('mint', '')
                is_top = mint in self._top_mints

                # Use priority_level directly
                priority = token.get('priority_level', 'LOW').upper()
                # For top mints, use shorter interval; for others, use longer
                if is_top:
                    interval = self._get_refresh_interval_for_activity('HIGH')  # 10s
                else:
                    interval = self._get_refresh_interval_for_activity('LOW')  # 200s

                # Check if this token is due for refresh
                last_update = token.get('last_price_update', 0)
                time_since_update = now - last_update

                if time_since_update >= interval:
                    if is_top:
                        top_priority_tokens.append(token)
                    else:
                        regular_tokens.append(token)

                # Track priority distribution
                self.stats['activity_distribution'][priority.lower()] = \
                    self.stats['activity_distribution'].get(priority.lower(), 0) + 1

            # Prioritize top tokens in the fetch queue
            tokens_to_fetch = top_priority_tokens + regular_tokens

            # Limit batch size to prevent overload
            return tokens_to_fetch[:20]

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

            conn = sqlite3.connect(self.db_path, timeout=5)
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
                    conn = sqlite3.connect(self.db_path, timeout=5)
                    cursor = conn.cursor()

                    for mint, price in prices.items():
                        if price.source == 'cached':
                            self.stats['cache_hits'] += 1

                        # Use market cap from price service (already calculated by Dexscreener/Jupiter)
                        market_cap = price.market_cap if price.market_cap else 0

                        # Get current peak and creation time
                        cursor.execute(
                            "SELECT market_cap_highest, market_cap_highest_at, created_at FROM token_analysis WHERE mint = ?",
                            (mint,)
                        )
                        row = cursor.fetchone()
                        peak_mc = row[0] if row and row[0] else None
                        peak_mc_at = row[1] if row and row[1] else None
                        created_at = row[2] if row and row[2] else None

                        now = datetime.now().isoformat(sep=' ')

                        if market_cap > 0:
                            # If this is first price fetch (no peak set yet), set it now
                            if peak_mc is None and peak_mc_at is None:
                                cursor.execute(
                                    """UPDATE token_analysis
                                       SET price_current = ?, market_cap_current = ?,
                                           market_cap_highest = ?, market_cap_highest_at = ?
                                       WHERE mint = ?""",
                                    (price.price_usd, market_cap, market_cap, now, mint)
                                )
                            # Update if this is a higher market cap than previous peak
                            elif market_cap > peak_mc:
                                cursor.execute(
                                    """UPDATE token_analysis
                                       SET price_current = ?, market_cap_current = ?,
                                           market_cap_highest = ?, market_cap_highest_at = ?
                                       WHERE mint = ?""",
                                    (price.price_usd, market_cap, market_cap, now, mint)
                                )
                            else:
                                # Just update current price, keep existing peak
                                cursor.execute(
                                    """UPDATE token_analysis
                                       SET price_current = ?, market_cap_current = ?
                                       WHERE mint = ?""",
                                    (price.price_usd, market_cap, mint)
                                )
                        else:
                            # No price, but still update current market cap if we have it
                            cursor.execute(
                                """UPDATE token_analysis
                                   SET price_current = ?, market_cap_current = ?
                                   WHERE mint = ?""",
                                (price.price_usd, market_cap, mint)
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
                conn = sqlite3.connect(self.db_path, timeout=5)
                cursor = conn.cursor()

                # Get current peak
                cursor.execute(
                    "SELECT market_cap_highest, market_cap_highest_at FROM token_analysis WHERE mint = ?",
                    (mint,)
                )
                row = cursor.fetchone()
                peak_mc = row[0] if row and row[0] else None
                peak_mc_at = row[1] if row and row[1] else None

                now = datetime.now().isoformat(sep=' ')

                if market_cap > 0:
                    if peak_mc is None:
                        # First fetch: set peak
                        cursor.execute(
                            """UPDATE token_analysis
                               SET price_current = ?, market_cap_current = ?,
                                   market_cap_highest = ?, market_cap_highest_at = ?
                               WHERE mint = ?""",
                            (price.price_usd, market_cap, market_cap, now, mint)
                        )
                    elif market_cap > peak_mc:
                        # New peak
                        cursor.execute(
                            """UPDATE token_analysis
                               SET price_current = ?, market_cap_current = ?,
                                   market_cap_highest = ?, market_cap_highest_at = ?
                               WHERE mint = ?""",
                            (price.price_usd, market_cap, market_cap, now, mint)
                        )
                    else:
                        # Just update current
                        cursor.execute(
                            """UPDATE token_analysis
                               SET price_current = ?, market_cap_current = ?
                               WHERE mint = ?""",
                            (price.price_usd, market_cap, mint)
                        )
                else:
                    # No valid market cap, just update price
                    cursor.execute(
                        """UPDATE token_analysis
                           SET price_current = ?
                           WHERE mint = ?""",
                        (price.price_usd, mint)
                    )

                conn.commit()
                conn.close()

                # Update timestamp
                self.registry.update_price_timestamp(mint)

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
