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
            'queue_stats': {},
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
        """One complete refresh cycle with activity-based scheduling."""
        cycle_start = time.time()
        self.stats['cycles'] += 1

        # Reset activity distribution for this cycle
        self.stats['activity_distribution'] = {
            'high': 0,
            'medium': 0,
            'low': 0,
            'dormant': 0
        }

        # First, sync new tokens from token_analysis to tracked_tokens
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
        Get tokens for refresh based on computed activity scores.

        Replaces static HIGH/MEDIUM/LOW scheduling with dynamic activity scoring.
        Activity computed from volume, market cap, price movement, and age.
        """
        tokens_to_fetch = []
        now = int(time.time())

        try:
            # Get all active tokens
            all_tokens = self.registry.get_tracked_tokens(active_only=True)

            for token in all_tokens:
                # Compute activity level
                activity = self._compute_activity_score(token)
                interval = self._get_refresh_interval_for_activity(activity)

                # Check if this token is due for refresh
                last_update = token.get('last_price_update', 0)
                time_since_update = now - last_update

                if time_since_update >= interval:
                    tokens_to_fetch.append(token)

                # Track activity distribution
                self.stats['activity_distribution'][activity] += 1

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
