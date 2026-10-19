"""
Multi-pool price aggregation.

When multiple pools exist for a token, compute liquidity-weighted price:

price = Σ(price_i × liquidity_i) / Σ(liquidity_i)
"""

import logging
from typing import List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TokenPrice:
    """Price for a token with metadata."""
    mint: str
    price_usd: float
    liquidity_usd: float
    volume_24h: Optional[float] = None
    market_cap: Optional[float] = None
    source: str = 'pool'
    pool_address: Optional[str] = None
    is_stale: bool = False


class PoolAggregator:
    """
    Aggregate prices from multiple pools.

    Computes liquidity-weighted average price.
    """

    @staticmethod
    def aggregate(prices: List[TokenPrice]) -> Optional[TokenPrice]:
        """
        Aggregate prices from multiple pools.

        Args:
            prices: List of prices from different pools for same token

        Returns:
            Aggregated price (liquidity-weighted mean)
        """
        if not prices:
            return None

        valid_prices = [p for p in prices if p and p.price_usd > 0]
        if not valid_prices:
            return None

        # Find best pool (highest liquidity)
        best = max(valid_prices, key=lambda p: p.liquidity_usd)

        # Compute liquidity-weighted average price
        total_liquidity = sum(p.liquidity_usd for p in valid_prices)
        if total_liquidity <= 0:
            return best

        weighted_price = sum(
            p.price_usd * p.liquidity_usd
            for p in valid_prices
        ) / total_liquidity

        # Compute total liquidity and market cap
        total_volume = sum(p.volume_24h or 0 for p in valid_prices)
        total_market_cap = sum(p.market_cap or 0 for p in valid_prices)

        # Return aggregated price with best pool metadata
        result = TokenPrice(
            mint=best.mint,
            price_usd=weighted_price,
            liquidity_usd=total_liquidity,
            volume_24h=total_volume if total_volume > 0 else None,
            market_cap=total_market_cap if total_market_cap > 0 else None,
            source=f"pool({len(valid_prices)})",
            pool_address=best.pool_address,
            is_stale=False
        )

        if len(valid_prices) > 1:
            logger.debug(
                f"Aggregated {len(valid_prices)} pools for {best.mint[:8]}: "
                f"${weighted_price:.2e} (liquidity: ${total_liquidity:.0f})"
            )

        return result

    @staticmethod
    def compute_weighted_price(prices: List[TokenPrice]) -> float:
        """Compute liquidity-weighted average price."""
        if not prices:
            return 0.0

        total_liquidity = sum(p.liquidity_usd for p in prices)
        if total_liquidity <= 0:
            return prices[0].price_usd

        return sum(p.price_usd * p.liquidity_usd for p in prices) / total_liquidity
