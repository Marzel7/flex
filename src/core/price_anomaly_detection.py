"""
FLEX Price Anomaly Detection

Detects abnormal price changes that may indicate:
- Rug pulls
- Pool manipulation
- Temporary market anomalies
- Liquidity issues

Detection methods:
1. Percentage change threshold (e.g., >50% change)
2. Liquidity vs. volume ratio
3. Historical deviation analysis
4. Volatility spikes
"""

import sqlite3
import logging
import time
import math
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass
from statistics import mean, stdev

logger = logging.getLogger(__name__)


@dataclass
class AnomalyDetectionResult:
    """Result of anomaly detection."""
    mint: str
    is_anomaly: bool
    anomaly_type: Optional[str]  # 'rug_pull', 'liquidity_issue', 'volatility_spike', 'manipulated_pool'
    anomaly_score: float  # 0-100
    confidence: float  # 0-100, how confident in the detection
    reasons: List[str]
    previous_price: float
    current_price: float
    price_change_percent: float
    current_liquidity: float
    volume_to_liquidity_ratio: float


class PriceAnomalyDetector:
    """Detects anomalous price changes."""

    def __init__(self, db_path: str = 'database/flex_complete_database.db'):
        self.db_path = db_path
        # Thresholds
        self.price_change_threshold = 0.50  # 50% change triggers investigation
        self.liquidity_threshold = 1000  # Minimum $1k liquidity
        self.volume_to_liquidity_threshold = 5.0  # Volume should be <5x liquidity
        self.volatility_threshold = 0.75  # 75% volatility over 24h
        self.lookback_hours = 24

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def detect_anomaly(self, mint: str, current_price: float,
                      current_liquidity: float, current_volume: float,
                      previous_price: Optional[float] = None) -> AnomalyDetectionResult:
        """
        Detect anomalies in token price.

        Args:
            mint: Token mint
            current_price: Current USD price
            current_liquidity: Current liquidity in USD
            current_volume: 24h trading volume
            previous_price: Previous price (for comparison)

        Returns: AnomalyDetectionResult with detection details
        """
        reasons = []
        anomaly_score = 0
        anomaly_type = None

        # Get previous price from snapshots if not provided
        if previous_price is None:
            previous_price = self._get_previous_price(mint)

        price_change_percent = 0
        if previous_price and previous_price > 0:
            price_change_percent = (current_price - previous_price) / previous_price

        # Check 1: Price change threshold
        price_change_anomaly = self._check_price_change(
            mint, current_price, previous_price, price_change_percent
        )
        if price_change_anomaly[0]:
            anomaly_score += price_change_anomaly[1]
            reasons.extend(price_change_anomaly[2])
            anomaly_type = 'price_spike'

        # Check 2: Liquidity issues
        liquidity_anomaly = self._check_liquidity(mint, current_liquidity, current_volume)
        if liquidity_anomaly[0]:
            anomaly_score += liquidity_anomaly[1]
            reasons.extend(liquidity_anomaly[2])
            anomaly_type = 'liquidity_issue'

        # Check 3: Volume to liquidity ratio
        vol_liq_anomaly = self._check_volume_liquidity_ratio(
            mint, current_volume, current_liquidity
        )
        if vol_liq_anomaly[0]:
            anomaly_score += vol_liq_anomaly[1]
            reasons.extend(vol_liq_anomaly[2])
            anomaly_type = 'manipulated_pool'

        # Check 4: Historical volatility
        volatility_anomaly = self._check_volatility(mint)
        if volatility_anomaly[0]:
            anomaly_score += volatility_anomaly[1]
            reasons.extend(volatility_anomaly[2])
            anomaly_type = 'volatility_spike'

        # Normalize score to 0-100
        anomaly_score = min(100, anomaly_score)

        # Determine if anomaly
        is_anomaly = anomaly_score >= 50

        # Calculate confidence
        confidence = self._calculate_confidence(reasons, current_liquidity, current_volume)

        volume_to_liquidity = (current_volume / current_liquidity) if current_liquidity > 0 else 0

        return AnomalyDetectionResult(
            mint=mint,
            is_anomaly=is_anomaly,
            anomaly_type=anomaly_type,
            anomaly_score=anomaly_score,
            confidence=confidence,
            reasons=reasons if reasons else ['No anomalies detected'],
            previous_price=previous_price or current_price,
            current_price=current_price,
            price_change_percent=price_change_percent,
            current_liquidity=current_liquidity,
            volume_to_liquidity_ratio=volume_to_liquidity
        )

    def _check_price_change(self, mint: str, current_price: float,
                           previous_price: Optional[float],
                           price_change_percent: float) -> Tuple[bool, float, List[str]]:
        """
        Check for extreme price changes.

        Returns: (is_anomaly, score, reasons)
        """
        reasons = []
        score = 0

        if not previous_price or previous_price == 0:
            return False, 0, []

        abs_change = abs(price_change_percent)

        # 50%+ change is significant
        if abs_change > self.price_change_threshold:
            score += 40
            direction = "increased" if price_change_percent > 0 else "decreased"
            reasons.append(f"Price {direction} {abs_change*100:.1f}%")

        # 100%+ change is very anomalous
        if abs_change > 1.0:
            score += 30
            reasons.append(f"Extreme price movement: {abs_change*100:.1f}%")

        # Rug pull pattern: price drops 90%+
        if price_change_percent < -0.9:
            score += 50
            reasons.append("Severe price drop (>90%) - possible rug pull")

        return score > 0, score, reasons

    def _check_liquidity(self, mint: str, current_liquidity: float,
                        current_volume: float) -> Tuple[bool, float, List[str]]:
        """
        Check for liquidity issues.

        Returns: (is_anomaly, score, reasons)
        """
        reasons = []
        score = 0

        if current_liquidity < self.liquidity_threshold:
            score += 35
            reasons.append(f"Very low liquidity: ${current_liquidity:,.0f}")

        if current_liquidity < 100:
            score += 50
            reasons.append("Critically low liquidity (<$100)")

        # Check liquidity trend
        prev_liquidity = self._get_previous_liquidity(mint)
        if prev_liquidity and prev_liquidity > 0:
            liquidity_change = (current_liquidity - prev_liquidity) / prev_liquidity
            if liquidity_change < -0.5:  # 50% liquidity drop
                score += 30
                reasons.append(f"Liquidity dropped {abs(liquidity_change)*100:.1f}%")

        return score > 0, score, reasons

    def _check_volume_liquidity_ratio(self, mint: str, current_volume: float,
                                     current_liquidity: float) -> Tuple[bool, float, List[str]]:
        """
        Check for suspicious volume-to-liquidity ratios.

        High ratio (volume >> liquidity) suggests small pool with large trades.

        Returns: (is_anomaly, score, reasons)
        """
        reasons = []
        score = 0

        if current_liquidity == 0:
            return False, 0, []

        ratio = current_volume / current_liquidity

        # Ratio > 5 is suspicious (trading 5x the liquidity)
        if ratio > self.volume_to_liquidity_threshold:
            score += 30
            reasons.append(f"High volume-to-liquidity ratio: {ratio:.1f}x")

        # Ratio > 20 is very suspicious
        if ratio > 20:
            score += 40
            reasons.append(f"Extreme volume-to-liquidity ratio: {ratio:.1f}x (possible manipulation)")

        return score > 0, score, reasons

    def _check_volatility(self, mint: str) -> Tuple[bool, float, List[str]]:
        """
        Check for volatility spikes in 24h history.

        Returns: (is_anomaly, score, reasons)
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cutoff_time = int(time.time()) - (self.lookback_hours * 3600)

            cursor.execute("""
                SELECT price_usd FROM token_price_snapshots
                WHERE mint = ? AND captured_at > ?
                ORDER BY captured_at ASC
            """, (mint, cutoff_time))

            prices = [row['price_usd'] for row in cursor.fetchall()]
            conn.close()

            if len(prices) < 5:
                return False, 0, []

            # Calculate coefficient of variation
            avg_price = mean(prices)
            if avg_price == 0:
                return False, 0, []

            std_dev = stdev(prices) if len(prices) > 1 else 0
            coefficient_of_variation = (std_dev / avg_price) * 100

            reasons = []
            score = 0

            if coefficient_of_variation > self.volatility_threshold * 100:
                score += 25
                reasons.append(f"High volatility: {coefficient_of_variation:.1f}% CV")

            if coefficient_of_variation > 150:
                score += 30
                reasons.append(f"Extreme volatility: {coefficient_of_variation:.1f}% CV")

            return score > 0, score, reasons

        except Exception as e:
            logger.debug(f"Error checking volatility for {mint}: {e}")
            return False, 0, []

    def _get_previous_price(self, mint: str) -> Optional[float]:
        """Get most recent previous price from snapshots."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            # Get price from 1 hour ago
            cutoff_time = int(time.time()) - 3600

            cursor.execute("""
                SELECT price_usd FROM token_price_snapshots
                WHERE mint = ? AND captured_at < ?
                ORDER BY captured_at DESC
                LIMIT 1
            """, (mint, cutoff_time))

            row = cursor.fetchone()
            conn.close()

            return row['price_usd'] if row else None
        except Exception as e:
            logger.debug(f"Error getting previous price for {mint}: {e}")
            return None

    def _get_previous_liquidity(self, mint: str) -> Optional[float]:
        """Get most recent previous liquidity from snapshots."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cutoff_time = int(time.time()) - 3600

            cursor.execute("""
                SELECT liquidity_usd FROM token_price_snapshots
                WHERE mint = ? AND captured_at < ?
                ORDER BY captured_at DESC
                LIMIT 1
            """, (mint, cutoff_time))

            row = cursor.fetchone()
            conn.close()

            return row['liquidity_usd'] if row else None
        except Exception as e:
            logger.debug(f"Error getting previous liquidity for {mint}: {e}")
            return None

    def _calculate_confidence(self, reasons: List[str], liquidity: float,
                             volume: float) -> float:
        """
        Calculate confidence in anomaly detection.

        Returns: 0-100
        """
        # More reasons = higher confidence
        confidence = min(100, len(reasons) * 20)

        # Good liquidity and volume = higher confidence
        if liquidity > 10000 and volume > 50000:
            confidence = min(100, confidence + 20)

        # Poor data = lower confidence
        if liquidity < 100:
            confidence = max(0, confidence - 30)

        return confidence


# Singleton instance
_anomaly_detector: Optional[PriceAnomalyDetector] = None


def get_anomaly_detector(db_path: str = 'database/flex_complete_database.db') -> PriceAnomalyDetector:
    """Get or create singleton detector."""
    global _anomaly_detector
    if _anomaly_detector is None:
        _anomaly_detector = PriceAnomalyDetector(db_path)
    return _anomaly_detector
