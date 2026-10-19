"""
FLEX Price Confidence Scoring

Computes a confidence score for each token price based on:
- Liquidity quality (35%)
- Trading volume (25%)
- Source reliability (20%)
- Price stability (20%)

Output: HIGH, MEDIUM, LOW confidence bands
"""

import logging
import sqlite3
import time
from typing import Dict, Tuple, Optional
from dataclasses import dataclass
from src.core.price_service import TokenPrice

logger = logging.getLogger(__name__)


@dataclass
class PriceConfidence:
    """Price confidence assessment."""
    confidence_band: str  # HIGH, MEDIUM, LOW
    confidence_score: float  # 0-100
    liquidity_score: float
    volume_score: float
    source_score: float
    stability_score: float
    reasons: list  # List of factors affecting confidence


class PriceConfidenceScorer:
    """Computes confidence scores for token prices."""

    def __init__(self, db_path: str = 'database/flex_complete_database.db'):
        self.db_path = db_path
        self.min_liquidity_usd = 1000  # Minimum liquidity for HIGH confidence
        self.min_volume_24h = 5000  # Minimum 24h volume for HIGH confidence
        self.stability_lookback_hours = 24

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def compute_confidence(self, price: TokenPrice) -> PriceConfidence:
        """
        Compute confidence score for a price.

        Args:
            price: TokenPrice object

        Returns:
            PriceConfidence with band (HIGH/MEDIUM/LOW) and component scores
        """
        if price.source == 'unavailable':
            return PriceConfidence(
                confidence_band='LOW',
                confidence_score=0,
                liquidity_score=0,
                volume_score=0,
                source_score=0,
                stability_score=0,
                reasons=['No price data available']
            )

        # Compute component scores
        liquidity_score = self._liquidity_score(price.liquidity_usd)
        volume_score = self._volume_score(price.volume_24h)
        source_score = self._source_score(price.source)
        stability_score = self._stability_score(price.mint)

        # Composite score with weights
        confidence_score = (
            liquidity_score * 0.35 +
            volume_score * 0.25 +
            source_score * 0.20 +
            stability_score * 0.20
        )

        # Determine band
        if confidence_score >= 75:
            band = 'HIGH'
        elif confidence_score >= 50:
            band = 'MEDIUM'
        else:
            band = 'LOW'

        # Collect reasons
        reasons = []
        if liquidity_score < 40:
            reasons.append(f"Low liquidity: ${price.liquidity_usd:,.0f}")
        if volume_score < 40:
            reasons.append(f"Low volume: ${price.volume_24h:,.0f}/24h")
        if price.is_stale:
            reasons.append("Price data is stale")
        if source_score < 80:
            reasons.append(f"Lower reliability source: {price.source}")

        return PriceConfidence(
            confidence_band=band,
            confidence_score=confidence_score,
            liquidity_score=liquidity_score,
            volume_score=volume_score,
            source_score=source_score,
            stability_score=stability_score,
            reasons=reasons if reasons else ['Good liquidity and volume']
        )

    def _liquidity_score(self, liquidity_usd: float) -> float:
        """
        Score based on available liquidity.

        Returns: 0-100
        """
        if liquidity_usd >= 100000:  # $100k+
            return 100
        if liquidity_usd >= 50000:  # $50k+
            return 90
        if liquidity_usd >= self.min_liquidity_usd:  # $1k+
            return 70
        if liquidity_usd >= 100:  # $100+
            return 40
        return 0

    def _volume_score(self, volume_24h: float) -> float:
        """
        Score based on 24h trading volume.

        Returns: 0-100
        """
        if volume_24h >= 1000000:  # $1M+
            return 100
        if volume_24h >= 100000:  # $100k+
            return 85
        if volume_24h >= self.min_volume_24h:  # $5k+
            return 70
        if volume_24h >= 1000:  # $1k+
            return 45
        return 0

    def _source_score(self, source: str) -> float:
        """
        Score based on source reliability.

        Returns: 0-100
        """
        source_reliability = {
            'dexscreener': 100,  # Primary, most reliable
            'jupiter': 85,       # Secondary, quote-based
            'cached': 60,        # Potentially stale
            'unavailable': 0
        }
        return source_reliability.get(source, 50)

    def _stability_score(self, mint: str) -> float:
        """Return neutral confidence without a retired dense history store."""
        return 50

    def batch_confidence(self, prices: Dict[str, TokenPrice]) -> Dict[str, PriceConfidence]:
        """Compute confidence scores for multiple prices."""
        return {mint: self.compute_confidence(price) for mint, price in prices.items()}


# Singleton instance
_confidence_scorer: Optional[PriceConfidenceScorer] = None


def get_confidence_scorer(db_path: str = 'database/flex_complete_database.db') -> PriceConfidenceScorer:
    """Get or create singleton scorer."""
    global _confidence_scorer
    if _confidence_scorer is None:
        _confidence_scorer = PriceConfidenceScorer(db_path)
    return _confidence_scorer
