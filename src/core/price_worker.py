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
            batch_size: Tokens to fetch per API call (default 20)
        """
        self.db_path = db_path
        self.interval = interval
        self.batch_size = batch_size
        self.price_service = get_price_service(db_path)
        self.registry = PriceWorkerRegistry(db_path)
        self.running = False
        self.thread = None
        self.stats = {
            'cycles': 0,
            'tokens_prefetched': 0,
            'api_calls': 0,
            'cache_hits': 0,
            'errors': 0,
            'last_run': None,
            'last_error': None
        }

    def start(self) -> None:
        """Start the background worker."""
        if self.running:
            logger.warning("Worker already running")
            return

        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info(f"Background price worker started (interval={self.interval}s, batch={self.batch_size})")

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
        """One complete refresh cycle with adaptive scheduling."""
        cycle_start = time.time()
        self.stats['cycles'] += 1

        # Get tokens to refresh based on adaptive scheduling
        tokens_to_fetch = self._get_tokens_for_refresh()

        if not tokens_to_fetch:
            logger.debug("No tracked tokens to refresh")
            return

        # Batch fetch prices
        mints = [t['mint'] for t in tokens_to_fetch]
        self._batch_fetch_prices(mints)

        # Update timestamps
        for mint in mints:
            self.registry.update_price_timestamp(mint)

        duration = time.time() - cycle_start
        self.stats['last_run'] = duration
        logger.debug(
            f"Prefetch cycle {self.stats['cycles']}: "
            f"{len(mints)} tokens, {duration:.2f}s, "
            f"{self.stats['api_calls']} API calls"
        )

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
        """Fetch prices for a list of mints in batches."""
        for i in range(0, len(mints), self.batch_size):
            batch = mints[i:i + self.batch_size]
            try:
                prices = self.price_service.get_token_prices_sync(batch, cache_type='hot')
                self.stats['tokens_prefetched'] += len(prices)
                self.stats['api_calls'] += 1

                # Count cache hits
                for mint, price in prices.items():
                    if price.source == 'cached':
                        self.stats['cache_hits'] += 1

                logger.debug(f"Prefetched {len(batch)} tokens: {list(prices.keys())[:3]}...")
            except Exception as e:
                logger.error(f"Error fetching batch {batch[:3]}...: {e}")
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
