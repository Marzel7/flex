"""
Enhanced Token Classification Logic - V2

Improved rug vs slow_rug differentiation using:
- Speed of collapse (key differentiator)
- Velocity decay and momentum
- Floor holding metrics
- Sustainable peak vs flash pump detection
"""

import logging
from typing import Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


# =========================================================================
# DATA CLASSES & ENUMS
# =========================================================================

class TokenOutcome(Enum):
    RUG = "rug"
    SLOW_RUG = "slow_rug"
    SUCCESS = "success"
    RUNNER = "runner"  # New: Early success indicators, not yet graduated
    NEUTRAL = "neutral"


@dataclass
class ClassificationResult:
    """Result of token outcome classification"""
    outcome: TokenOutcome
    outcome_score: float                    # 0-1 confidence
    reason: str                             # Explanation of classification
    peak_type: str                          # 'flash' | 'sustainable' | 'final'
    peak_hold_time_minutes: int            # How long peak was maintained
    peak_confidence: float                  # 0-1 confidence in peak quality


# =========================================================================
# CLASSIFICATION ENGINE
# =========================================================================

class ClassificationEngineV2:
    """Enhanced token outcome classification"""

    @staticmethod
    def classify(mint: str, metrics: dict) -> ClassificationResult:
        """
        Enhanced classification incorporating velocity, momentum, floor holding.
        More nuanced than V1 rules.

        Args:
            mint: Token mint
            metrics: Token metrics dict with keys:
                - peak_market_cap
                - final_market_cap
                - max_drawdown_pct
                - time_to_peak_minutes
                - time_from_peak_to_50pct_drawdown_min (key for rug vs slow_rug)
                - velocity_decay_rate
                - lifecycle_duration_minutes
                - volatility_index

        Returns:
            ClassificationResult with outcome, score, reason, and peak metadata
        """

        peak_mc = metrics.get('peak_market_cap', 0)
        final_mc = metrics.get('final_market_cap', 0)
        max_dd = metrics.get('max_drawdown_pct', 0)
        ttp = metrics.get('time_to_peak_minutes', 0)

        # Key metric for rug vs slow_rug differentiation
        time_to_50pct_dd = metrics.get('time_from_peak_to_50pct_drawdown_min', 999)

        velocity_decay = metrics.get('velocity_decay_rate', 0)
        sustained_floor = final_mc / peak_mc if peak_mc > 0 else 0
        lifecycle_duration = metrics.get('lifecycle_duration_minutes', 0)
        volatility = metrics.get('volatility_index', 0)

        reason_parts = []

        # ===== RUG CLASSIFICATION =====
        # Fast failure with characteristic signature
        if (peak_mc < 100_000 and
            ttp < 30 and
            max_dd > 80):

            # KEY DIFFERENTIATOR: Did it crash FAST?
            if time_to_50pct_dd < 5:  # Lost 50% in <5 min = FAST crash
                outcome = TokenOutcome.RUG
                confidence = 0.95
                peak_type = 'flash'
                peak_hold_time = 1
                peak_confidence = 0.95

                reason_parts.append(f'rug_signature:')
                reason_parts.append(f'peak_mc={peak_mc:.0f}<$100k')
                reason_parts.append(f'ttp={ttp}<30min')
                reason_parts.append(f'dd={max_dd:.1f}>80%')
                reason_parts.append(f'fast_crash={time_to_50pct_dd}<5min')

                return ClassificationResult(
                    outcome=outcome,
                    outcome_score=confidence,
                    reason=' && '.join(reason_parts),
                    peak_type=peak_type,
                    peak_hold_time_minutes=peak_hold_time,
                    peak_confidence=peak_confidence
                )

            else:
                # Slower decay = not a rug, check for slow_rug instead
                pass

        # ===== SLOW RUG CLASSIFICATION =====
        # Investor trap: looks good early, decays over hours
        if (peak_mc >= 50_000 and
            max_dd >= 80 and
            final_mc < 5_000):

            # Check it wasn't fast (that would be rug)
            if time_to_50pct_dd > 10:  # Took >10 min to lose 50% = GRADUAL
                outcome = TokenOutcome.SLOW_RUG
                confidence = 0.92
                peak_type = 'sustainable'
                peak_hold_time = int(time_to_50pct_dd)
                peak_confidence = 0.90

                reason_parts.append(f'slow_rug:')
                reason_parts.append(f'peak_mc={peak_mc:.0f}>$50k')
                reason_parts.append(f'dd={max_dd:.1f}>80%')
                reason_parts.append(f'final_mc={final_mc:.0f}<$5k')
                reason_parts.append(f'gradual_decline={time_to_50pct_dd}>10min')

                return ClassificationResult(
                    outcome=outcome,
                    outcome_score=confidence,
                    reason=' && '.join(reason_parts),
                    peak_type=peak_type,
                    peak_hold_time_minutes=peak_hold_time,
                    peak_confidence=peak_confidence
                )

        # ===== SUCCESS CLASSIFICATION =====
        # Sustained real value creation
        if (peak_mc >= 250_000 and
            final_mc >= 50_000 and
            max_dd <= 75):

            # Check 1: Did it sustain above 50% of peak?
            if sustained_floor >= 0.5:
                outcome = TokenOutcome.SUCCESS
                confidence = 0.90
                peak_type = 'final'
                peak_hold_time = int(lifecycle_duration * 0.5)  # Estimated
                peak_confidence = 0.95

                reason_parts.append(f'success_sustained:')
                reason_parts.append(f'peak_mc=${peak_mc:,.0f}>$250k')
                reason_parts.append(f'final_mc=${final_mc:,.0f}>$50k')
                reason_parts.append(f'dd={max_dd:.1f}<75%')
                reason_parts.append(f'retention={sustained_floor:.1%}>=50%')

                return ClassificationResult(
                    outcome=outcome,
                    outcome_score=confidence,
                    reason=' && '.join(reason_parts),
                    peak_type=peak_type,
                    peak_hold_time_minutes=peak_hold_time,
                    peak_confidence=peak_confidence
                )

            # Check 2: Even if below 50%, still high absolute value
            elif final_mc >= 100_000:
                outcome = TokenOutcome.SUCCESS
                confidence = 0.85
                peak_type = 'final'
                peak_hold_time = int(lifecycle_duration * 0.4)
                peak_confidence = 0.85

                reason_parts.append(f'success_partial:')
                reason_parts.append(f'peak_mc=${peak_mc:,.0f}>$250k')
                reason_parts.append(f'final_mc=${final_mc:,.0f}>$100k')
                reason_parts.append(f'retention={sustained_floor:.1%}')

                return ClassificationResult(
                    outcome=outcome,
                    outcome_score=confidence,
                    reason=' && '.join(reason_parts),
                    peak_type=peak_type,
                    peak_hold_time_minutes=peak_hold_time,
                    peak_confidence=peak_confidence
                )

        # ===== RUNNER CLASSIFICATION (NEW) =====
        # Early success indicators, not yet graduated
        if (peak_mc >= 250_000 and
            final_mc >= 100_000 and
            lifecycle_duration < 240):  # <4 hours

            # Still rising or stable, not classified yet
            outcome = TokenOutcome.RUNNER
            confidence = 0.80
            peak_type = 'developing'
            peak_hold_time = int(lifecycle_duration * 0.3)
            peak_confidence = 0.75

            reason_parts.append(f'runner:')
            reason_parts.append(f'peak_mc=${peak_mc:,.0f}>$250k')
            reason_parts.append(f'final_mc=${final_mc:,.0f}>$100k')
            reason_parts.append(f'lifecycle=<4h')
            reason_parts.append(f'still_active_not_classified')

            return ClassificationResult(
                outcome=outcome,
                outcome_score=confidence,
                reason=' && '.join(reason_parts),
                peak_type=peak_type,
                peak_hold_time_minutes=peak_hold_time,
                peak_confidence=peak_confidence
            )

        # ===== NEUTRAL (EVERYTHING ELSE) =====
        outcome = TokenOutcome.NEUTRAL
        confidence = 0.5
        peak_type = 'unclear'
        peak_hold_time = 0
        peak_confidence = 0.3

        reason_parts.append(f'neutral:')
        reason_parts.append(f'peak_mc=${peak_mc:,.0f}')
        reason_parts.append(f'final_mc=${final_mc:,.0f}')
        reason_parts.append(f'dd={max_dd:.1f}%')
        reason_parts.append(f'ttp={ttp}min')
        reason_parts.append(f'no_strong_pattern_match')

        return ClassificationResult(
            outcome=outcome,
            outcome_score=confidence,
            reason=' && '.join(reason_parts),
            peak_type=peak_type,
            peak_hold_time_minutes=peak_hold_time,
            peak_confidence=peak_confidence
        )

    @staticmethod
    def detect_recovery(mint: str, snapshots: list) -> dict:
        """
        Detect if token is recovering from early dip.
        Prevents false classification of recovering tokens.

        Args:
            mint: Token mint
            snapshots: List of recent snapshots (dicts with 'market_cap')

        Returns:
            dict with keys:
                - is_recovering: bool
                - low_point_mc: float (if recovering)
                - current_mc: float (if recovering)
                - recovery_ratio: float (if recovering)
        """

        if not snapshots or len(snapshots) < 10:
            return {'is_recovering': False}

        # Get last 20 snapshots
        recent = snapshots[:20]

        try:
            low_point = min(s['market_cap_usd'] for s in recent)
            current_mc = recent[0]['market_cap_usd']  # Latest

            recovery_ratio = current_mc / low_point if low_point > 0 else 0

            if recovery_ratio > 1.5:  # Up > 50% from low
                return {
                    'is_recovering': True,
                    'low_point_mc': low_point,
                    'current_mc': current_mc,
                    'recovery_ratio': recovery_ratio
                }

            return {'is_recovering': False}

        except (KeyError, ValueError):
            return {'is_recovering': False}

    @staticmethod
    def classify_peak_type(peak_hold_minutes: int, volatility: float) -> str:
        """
        Classify type of peak reached.

        Args:
            peak_hold_minutes: How long peak was maintained
            volatility: Price volatility metric

        Returns:
            'flash' | 'sustainable' | 'final'
        """

        if peak_hold_minutes < 2 and volatility > 0.7:
            return 'flash'
        elif peak_hold_minutes >= 5 and volatility < 0.4:
            return 'sustainable'
        else:
            return 'final'


# =========================================================================
# EXAMPLE USAGE
# =========================================================================

if __name__ == "__main__":
    # Example: Classify a rug token
    rug_metrics = {
        'peak_market_cap': 50_000,
        'final_market_cap': 1_000,
        'max_drawdown_pct': 95,
        'time_to_peak_minutes': 15,
        'time_from_peak_to_50pct_drawdown_min': 3,  # Fast collapse
        'velocity_decay_rate': 0.9,
        'lifecycle_duration_minutes': 120,
        'volatility_index': 0.85,
    }

    result = ClassificationEngineV2.classify("test_rug", rug_metrics)
    print(f"Rug token: {result.outcome.value} (confidence: {result.outcome_score:.2f})")
    print(f"  Reason: {result.reason}")
    print(f"  Peak type: {result.peak_type}")

    # Example: Classify a success token
    success_metrics = {
        'peak_market_cap': 500_000,
        'final_market_cap': 300_000,
        'max_drawdown_pct': 40,
        'time_to_peak_minutes': 45,
        'time_from_peak_to_50pct_drawdown_min': 180,  # Gradual
        'velocity_decay_rate': 0.3,
        'lifecycle_duration_minutes': 300,
        'volatility_index': 0.25,
    }

    result = ClassificationEngineV2.classify("test_success", success_metrics)
    print(f"\nSuccess token: {result.outcome.value} (confidence: {result.outcome_score:.2f})")
    print(f"  Reason: {result.reason}")
    print(f"  Peak type: {result.peak_type}")

    print("\n[CLASSIFICATION_V2] Module loaded successfully")
