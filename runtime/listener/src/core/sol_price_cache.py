"""
SOL Price Cache with async-safe 20s TTL.

Reduces Jupiter API calls by ~95%.
Shared across all price calculations.
"""

import asyncio
import time
import logging
from typing import Optional, Callable
from datetime import datetime

logger = logging.getLogger(__name__)


class SolPriceCache:
    """
    Async-safe cache for SOL/USD price.
    TTL: 20 seconds
    """

    def __init__(self, ttl_seconds: int = 20):
        self.price: Optional[float] = None
        self.last_update: float = 0
        self.ttl = ttl_seconds
        self._lock = asyncio.Lock()
        self.fetch_count = 0
        self.cache_hits = 0
        self.cache_misses = 0

    async def get_price(self, fetcher_func: Callable) -> float:
        """
        Get SOL price with caching.

        Args:
            fetcher_func: async callable that returns SOL/USD price

        Returns:
            SOL price in USD
        """
        async with self._lock:
            now = time.time()
            age = now - self.last_update

            # Return cached if still fresh
            if self.price is not None and age < self.ttl:
                self.cache_hits += 1
                logger.debug(f"SOL price cache hit (age={age:.1f}s, price=${self.price:.2f})")
                return self.price

            self.cache_misses += 1
            logger.info(f"SOL price cache miss (age={age:.1f}s) - fetching fresh")

            # Fetch new price
            try:
                self.price = await fetcher_func()
                self.last_update = now
                self.fetch_count += 1
                logger.info(f"SOL price updated: ${self.price:.2f}")
                return self.price
            except Exception as e:
                logger.error(f"Failed to fetch SOL price: {e}")
                if self.price is None:
                    raise
                logger.warning(f"Using stale price: ${self.price:.2f} (age={age:.1f}s)")
                return self.price

    def get_price_sync(self) -> Optional[float]:
        """
        Get cached SOL price synchronously (no async, no fetch).
        Returns the cached price if available, None if cache is stale/empty.
        Safe to call from sync threads.
        """
        now = time.time()
        age = now - self.last_update
        if self.price is not None and age < self.ttl:
            logger.debug(f"SOL price (sync) cache hit: ${self.price:.2f} (age={age:.1f}s)")
            return self.price
        logger.debug(f"SOL price (sync) cache miss: stale or empty (age={age:.1f}s)")
        return None

    def get_stats(self) -> dict:
        """Get cache statistics."""
        now = time.time()
        age = now - self.last_update if self.last_update > 0 else 0

        return {
            'current_price': self.price,
            'age_seconds': age,
            'ttl_seconds': self.ttl,
            'total_fetches': self.fetch_count,
            'cache_hits': self.cache_hits,
            'cache_misses': self.cache_misses,
            'hit_rate': self.cache_hits / (self.cache_hits + self.cache_misses)
                        if (self.cache_hits + self.cache_misses) > 0 else 0,
            'last_update': datetime.fromtimestamp(self.last_update).isoformat()
                          if self.last_update > 0 else None
        }

    def invalidate(self):
        """Force cache invalidation (testing only)."""
        self.last_update = 0
        logger.info("SOL price cache invalidated")


# Global instance
_sol_price_cache: Optional[SolPriceCache] = None


def get_sol_price_cache() -> SolPriceCache:
    """Get or create global SOL price cache."""
    global _sol_price_cache
    if _sol_price_cache is None:
        _sol_price_cache = SolPriceCache(ttl_seconds=20)
        logger.info("Created global SOL price cache (20s TTL)")
    return _sol_price_cache
