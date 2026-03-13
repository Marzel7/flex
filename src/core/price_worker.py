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
        self.stats = {
            'cycles': 0,
            'tokens_prefetched': 0,
            'api_calls': 0,
            'cache_hits': 0,
            'errors': 0,
            'last_run': None,
            'last_error': None,
            'queue_stats': {}
        }

    def start(self) -> None:
        """Start the background worker."""
        if self.running:
            logger.warning("Worker already running")
            return

        # Start the price fetch queue worker
        start_price_queue_worker(self._fetch_single_price)

        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info(f"Background price worker started (interval={self.interval}s, using request queue)")

    def stop(self) -> None:
        """Stop the background worker."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Background price worker stopped")

    def _run_loop(self) -> None:
        """Main worker loop."""
        while self.running:
            try:
                self._refresh_cycle()
                time.sleep(self.interval)
            except Exception as e:
                logger.error(f"Worker cycle error: {e}", exc_info=True)
                self.stats['last_error'] = str(e)
                self.stats['errors'] += 1
                time.sleep(self.interval)

    def _refresh_cycle(self) -> None:
        """One complete refresh cycle with request queue."""
        cycle_start = time.time()
        self.stats['cycles'] += 1

        # First, sync new tokens from token_analysis to tracked_tokens
        self._sync_new_tokens()

        # Get tokens to refresh
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

        duration = time.time() - cycle_start
        self.stats['last_run'] = duration
        self.stats['queue_stats'] = self.queue.get_stats()

        logger.debug(
            f"Prefetch cycle {self.stats['cycles']}: "
            f"enqueued {len(tasks)} tokens, cycle time {duration:.2f}s, "
            f"queue depth {self.queue.get_stats()['queue_depth']}"
        )

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
        Get tokens for refresh based on adaptive scheduling.

        Schedule:
        - HIGH: every 5-10 seconds (every cycle)
        - MEDIUM: every 30 seconds (every 3 cycles)
        - LOW: every 2-5 minutes (every 20+ cycles)
        """
        tokens_to_fetch = []

        # HIGH priority: every cycle (10s)
        high_priority = self.registry.get_tracked_tokens('HIGH')
        tokens_to_fetch.extend(high_priority)

        # MEDIUM priority: every 3 cycles (30s)
        if self.stats['cycles'] % 3 == 0:
            medium_priority = self.registry.get_tracked_tokens('MEDIUM')
            # Take half of medium tokens for load balancing
            tokens_to_fetch.extend(medium_priority[:len(medium_priority)//2])

        # LOW priority: every 20 cycles (200s / ~3 minutes)
        if self.stats['cycles'] % 20 == 0:
            low_priority = self.registry.get_tracked_tokens('LOW')
            # Take quarter of low tokens for load balancing
            tokens_to_fetch.extend(low_priority[:len(low_priority)//4])

        return tokens_to_fetch

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

    def get_stats(self) -> Dict:
        """Get worker statistics."""
        return {
            'worker': self.stats.copy(),
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
