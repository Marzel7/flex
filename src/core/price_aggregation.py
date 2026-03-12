"""
FLEX Price Aggregation Layer

Fetches prices from multiple sources and combines them using consensus methods.

Sources:
1. Dexscreener (primary DEX prices)
2. Jupiter (quote-based fallback pricing)
3. DEX pool data (direct pool pricing)

Consensus method:
- Median price from available sources
- Weighted average (optional)
- Outlier detection and removal

Benefits:
- Prevents single-source manipulation
- Increases price reliability
- Detects suspicious prices
"""

import logging
import asyncio
import aiohttp
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from src.core.price_service import TokenPrice, DexscreenerClient, JupiterClient

logger = logging.getLogger(__name__)


@dataclass
class PriceSource:
    """Individual price source result."""
    source: str  # 'dexscreener', 'jupiter', 'raydium', 'orca'
    price_usd: float
    liquidity_usd: float
    volume_24h: float
    market_cap: float
    confidence: float  # 0-100, source-specific reliability


@dataclass
class AggregatedPrice:
    """Consensus price from multiple sources."""
    mint: str
    price_usd: float  # Median/consensus price
    price_sol: float
    liquidity_usd: float  # Median/average liquidity
    volume_24h: float
    market_cap: float
    source: str  # 'aggregated'
    sources_used: List[str]  # Which sources contributed
    source_count: int
    pair_address: Optional[str]
    timestamp: int
    is_stale: bool
    aggregation_method: str  # 'median', 'weighted', 'average'


class DEXPoolClient:
    """Fetches prices from DEX pools directly."""

    BASE_URL = "https://api.raydium.io/v2"  # Example Raydium API

    @staticmethod
    async def get_price(mint: str) -> Optional[PriceSource]:
        """
        Fetch price from DEX pool data.

        Uses Raydium or Orca pool information for price discovery.
        """
        try:
            # Would query DEX pool APIs for direct pool pricing
            # For now, returns None (not yet implemented)
            logger.debug(f"DEX pool pricing not yet implemented for {mint}")
            return None
        except Exception as e:
            logger.debug(f"DEX pool error for {mint}: {e}")
            return None


class PriceAggregator:
    """Aggregates prices from multiple sources."""

    def __init__(self, db_path: str = 'database/flex_complete_database.db'):
        self.db_path = db_path
        self.min_sources = 1  # Minimum sources required for aggregation
        self.outlier_threshold = 0.25  # 25% deviation triggers outlier

    async def fetch_from_all_sources(self, mint: str) -> Dict[str, Optional[PriceSource]]:
        """
        Fetch price from all available sources in parallel.

        Returns: {
            'dexscreener': PriceSource(...) | None,
            'jupiter': PriceSource(...) | None,
            'dex_pool': PriceSource(...) | None
        }
        """
        try:
            # Parallel fetch from all sources
            dex_price, jup_price, pool_price = await asyncio.gather(
                self._fetch_dexscreener(mint),
                self._fetch_jupiter(mint),
                self._fetch_dex_pool(mint)
            )

            return {
                'dexscreener': dex_price,
                'jupiter': jup_price,
                'dex_pool': pool_price
            }
        except Exception as e:
            logger.error(f"Error fetching from all sources for {mint}: {e}")
            return {
                'dexscreener': None,
                'jupiter': None,
                'dex_pool': None
            }

    async def _fetch_dexscreener(self, mint: str) -> Optional[PriceSource]:
        """Fetch from Dexscreener and normalize."""
        try:
            price = await DexscreenerClient.get_price(mint)
            if not price:
                return None

            return PriceSource(
                source='dexscreener',
                price_usd=price.price_usd,
                liquidity_usd=price.liquidity_usd,
                volume_24h=price.volume_24h,
                market_cap=price.market_cap,
                confidence=100  # Primary source
            )
        except Exception as e:
            logger.debug(f"Dexscreener fetch error for {mint}: {e}")
            return None

    async def _fetch_jupiter(self, mint: str) -> Optional[PriceSource]:
        """Fetch from Jupiter and normalize."""
        try:
            price = await JupiterClient.get_price(mint)
            if not price:
                return None

            return PriceSource(
                source='jupiter',
                price_usd=price.price_usd,
                liquidity_usd=0,  # Jupiter doesn't provide this
                volume_24h=0,
                market_cap=0,
                confidence=85  # Secondary source
            )
        except Exception as e:
            logger.debug(f"Jupiter fetch error for {mint}: {e}")
            return None

    async def _fetch_dex_pool(self, mint: str) -> Optional[PriceSource]:
        """Fetch from DEX pools and normalize."""
        try:
            price = await DEXPoolClient.get_price(mint)
            return price
        except Exception as e:
            logger.debug(f"DEX pool fetch error for {mint}: {e}")
            return None

    def _detect_outliers(self, sources: List[PriceSource]) -> List[PriceSource]:
        """
        Remove outlier prices that deviate significantly from median.

        Returns: List of non-outlier sources
        """
        if len(sources) < 2:
            return sources

        prices = [s.price_usd for s in sources]
        median = sorted(prices)[len(prices) // 2]

        filtered = []
        for source in sources:
            deviation = abs(source.price_usd - median) / median if median > 0 else 0
            if deviation <= self.outlier_threshold:
                filtered.append(source)
            else:
                logger.warning(
                    f"Outlier detected from {source.source}: "
                    f"${source.price_usd:.8f} (median: ${median:.8f}, "
                    f"deviation: {deviation*100:.1f}%)"
                )

        return filtered if filtered else sources

    def _compute_median_price(self, sources: List[PriceSource]) -> AggregatedPrice:
        """Compute consensus price using median method."""
        if not sources:
            raise ValueError("No price sources available")

        prices = sorted([s.price_usd for s in sources])
        median_price = prices[len(prices) // 2]

        # Liquidity: average of available values
        liquidity_values = [s.liquidity_usd for s in sources if s.liquidity_usd > 0]
        median_liquidity = (sum(liquidity_values) / len(liquidity_values)) if liquidity_values else 0

        # Volume: average of available values
        volume_values = [s.volume_24h for s in sources if s.volume_24h > 0]
        median_volume = (sum(volume_values) / len(volume_values)) if volume_values else 0

        # Market cap: average of available values
        cap_values = [s.market_cap for s in sources if s.market_cap > 0]
        median_cap = (sum(cap_values) / len(cap_values)) if cap_values else 0

        # Estimate SOL price
        sol_price = 180  # Assume 1 SOL = $180
        price_sol = median_price / sol_price if sol_price > 0 else 0

        return AggregatedPrice(
            mint='',  # Set by caller
            price_usd=median_price,
            price_sol=price_sol,
            liquidity_usd=median_liquidity,
            volume_24h=median_volume,
            market_cap=median_cap,
            source='aggregated',
            sources_used=[s.source for s in sources],
            source_count=len(sources),
            pair_address=None,  # Multi-source, no single pair
            timestamp=int(__import__('time').time()),
            is_stale=False,
            aggregation_method='median'
        )

    def _compute_weighted_price(self, sources: List[PriceSource]) -> AggregatedPrice:
        """Compute consensus price using weighted average method."""
        if not sources:
            raise ValueError("No price sources available")

        total_weight = sum(s.confidence for s in sources)
        if total_weight == 0:
            return self._compute_median_price(sources)

        # Weighted price
        weighted_price = sum(s.price_usd * s.confidence for s in sources) / total_weight

        # Weighted liquidity
        liquidity_values = [(s.liquidity_usd, s.confidence) for s in sources if s.liquidity_usd > 0]
        weighted_liquidity = (
            sum(liq * conf for liq, conf in liquidity_values) /
            sum(conf for _, conf in liquidity_values)
        ) if liquidity_values else 0

        # Weighted volume
        volume_values = [(s.volume_24h, s.confidence) for s in sources if s.volume_24h > 0]
        weighted_volume = (
            sum(vol * conf for vol, conf in volume_values) /
            sum(conf for _, conf in volume_values)
        ) if volume_values else 0

        # Weighted market cap
        cap_values = [(s.market_cap, s.confidence) for s in sources if s.market_cap > 0]
        weighted_cap = (
            sum(cap * conf for cap, conf in cap_values) /
            sum(conf for _, conf in cap_values)
        ) if cap_values else 0

        # Estimate SOL price
        sol_price = 180
        price_sol = weighted_price / sol_price if sol_price > 0 else 0

        return AggregatedPrice(
            mint='',  # Set by caller
            price_usd=weighted_price,
            price_sol=price_sol,
            liquidity_usd=weighted_liquidity,
            volume_24h=weighted_volume,
            market_cap=weighted_cap,
            source='aggregated',
            sources_used=[s.source for s in sources],
            source_count=len(sources),
            pair_address=None,
            timestamp=int(__import__('time').time()),
            is_stale=False,
            aggregation_method='weighted'
        )

    async def aggregate_price(self, mint: str, method: str = 'median') -> Optional[AggregatedPrice]:
        """
        Aggregate prices from all sources.

        Args:
            mint: Token mint address
            method: 'median' or 'weighted'

        Returns: AggregatedPrice with consensus price
        """
        try:
            # Fetch from all sources
            results = await self.fetch_from_all_sources(mint)

            # Filter to successful fetches
            sources = [p for p in results.values() if p is not None]

            if len(sources) < self.min_sources:
                logger.warning(f"Insufficient sources for {mint} (got {len(sources)})")
                return None

            # Detect and remove outliers
            filtered_sources = self._detect_outliers(sources)

            # Compute consensus
            if method == 'weighted':
                agg_price = self._compute_weighted_price(filtered_sources)
            else:
                agg_price = self._compute_median_price(filtered_sources)

            agg_price.mint = mint

            logger.debug(
                f"Aggregated price for {mint}: ${agg_price.price_usd:.8f} "
                f"({method} of {agg_price.source_count} sources: "
                f"{', '.join(agg_price.sources_used)})"
            )

            return agg_price

        except Exception as e:
            logger.error(f"Error aggregating price for {mint}: {e}")
            return None

    def aggregate_price_sync(self, mint: str, method: str = 'median') -> Optional[AggregatedPrice]:
        """Synchronous wrapper for aggregate_price."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.aggregate_price(mint, method))
        finally:
            loop.close()


# Singleton instance
_price_aggregator: Optional[PriceAggregator] = None


def get_price_aggregator(db_path: str = 'database/flex_complete_database.db') -> PriceAggregator:
    """Get or create singleton aggregator."""
    global _price_aggregator
    if _price_aggregator is None:
        _price_aggregator = PriceAggregator(db_path)
    return _price_aggregator
