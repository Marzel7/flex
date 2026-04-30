"""
Token Behaviour Categorisation

Derived analytics layer that classifies tokens from historical price snapshots.
Reads from token_price_snapshots (read-only). Writes to token_behavior and
token_behavior_history tables.

Categories: immediate_rug | runner | faded_runner | choppy_runner | rug | slow_rug | insufficient_history | unknown
Unknown sub-states (replaces bare "unknown"):
  collecting   — not enough snapshots yet to say anything
  late_start   — tracking quality is possibly/likely late; peak may have been missed
  low_peak     — token never developed (max_return < LOW_PEAK_RETURN_MAX)
  unclassified — sufficient data, return > low_peak threshold, but pattern ambiguous

Peak MC gate:
  Tokens with peak_market_cap_usd >= LARGE_PEAK_MC_USD (500k) are NEVER labeled rug/immediate_rug/slow_rug.
  Post-peak behavior categories for large-peak tokens:
    runner        — held well after strong run
    faded_runner  — strong run then faded but not terminal
    rugged_later  — reached 500k+ peak then near-total collapse (was a real trade; not a rug label)
    choppy_runner — volatile but alive
    small_runner  — moderate upside (return >= SMALL_RUNNER_RETURN_MIN, peak 100k-500k)

This module is separate from the live monitoring pipeline (token_lifecycle.py,
lifecycle_classification_v2.py). It operates post-hoc on historical data only.
"""

import sqlite3
import logging
import statistics
import time
from dataclasses import dataclass
from typing import List, Tuple

logger = logging.getLogger(__name__)

# =========================================================================
# THRESHOLD CONSTANTS (all tunable — adjust for dataset characteristics)
# =========================================================================

# =========================================================================
# TIERED DATA QUALITY THRESHOLDS (for progressive classification)
# =========================================================================

# Early classification (noisy but informative)
EARLY_MIN_SNAPSHOTS = 8
EARLY_MIN_LIFETIME_SECS = 120      # 2 minutes

# Mid-quality classification (reasonable confidence)
MID_MIN_SNAPSHOTS = 30
MID_MIN_LIFETIME_SECS = 300        # 5 minutes

# Full classification (high confidence)
FULL_MIN_SNAPSHOTS = 100
FULL_MIN_LIFETIME_SECS = 600       # 10 minutes

# Default gates for system (allow early signals, but flag as low confidence)
MIN_SNAPSHOTS = EARLY_MIN_SNAPSHOTS
MIN_LIFETIME_SECS = EARLY_MIN_LIFETIME_SECS

# immediate_rug: very early peak, then major collapse (pump-and-dump)
IMMEDIATE_RUG_TIME_TO_PEAK_MAX = 300      # Peak must occur within this many seconds
IMMEDIATE_RUG_DRAWDOWN_MIN = 0.85         # Fraction of peak lost
IMMEDIATE_RUG_RECOVERY_MAX = 0.25         # Latest price as fraction of peak

# rug: pumps, then collapses almost fully
RUG_MAX_RETURN_MIN = 2.0                  # Multiple of initial price
RUG_DRAWDOWN_MIN = 0.90                   # Fraction of peak lost

# slow_rug: weak upside, gradual bleed lower
SLOW_RUG_MAX_RETURN_MAX = 2.0             # Must NOT return 2x or more
SLOW_RUG_SLOPE_MAX = 0.0                  # Slope must be negative
SLOW_RUG_DRAWDOWN_MIN = 0.70              # Fraction of peak lost

# runner: large sustained appreciation without major collapse
RUNNER_MAX_RETURN_MIN = 5.0               # Multiple of initial price
RUNNER_DRAWDOWN_MAX = 0.50                # Fraction of peak lost
RUNNER_RECOVERY_MIN = 0.50                # Latest as fraction of peak

# faded_runner: strong upside, then material decline but not terminal
FADED_RUNNER_MAX_RETURN_MIN = 3.0         # Multiple of initial price
FADED_RUNNER_DRAWDOWN_MIN = 0.50          # Fraction of peak lost (lower bound)
FADED_RUNNER_DRAWDOWN_MAX = 0.85          # Fraction of peak lost (upper bound)
FADED_RUNNER_RECOVERY_MIN = 0.15          # Latest as fraction of peak (lower bound)
FADED_RUNNER_RECOVERY_MAX = 0.50          # Latest as fraction of peak (upper bound)

# choppy_runner: large upside with big retracements, still alive
CHOPPY_RUNNER_MAX_RETURN_MIN = 3.0        # Multiple of initial price
CHOPPY_RUNNER_RECOVERY_MIN = 0.35         # Latest as fraction of peak

# slope_early window: first N seconds of life
SLOPE_EARLY_WINDOW_SECS = 300             # 5 minutes

# unknown sub-state thresholds
# Tokens below this multiple never meaningfully moved — "low_peak"
LOW_PEAK_RETURN_MAX = 1.5

# =========================================================================
# G-CLASS SYSTEM  (peak_market_cap_usd only — orthogonal to outcome)
# =========================================================================
G1_MC = 5_000_000   # Elite      (>=5M)
G2_MC = 2_000_000   # Strong     (2–5M)
G3_MC = 500_000     # High Opp   (500k–2M)
G4_MC = 300_000     # Pre-Runner (300–500k)
G5_MC = 150_000     # Mid Tier   (150–300k)
G6_MC = 75_000      # Weak       (75–150k)
# G7 = everything below G6 (<75k)

# Outcome thresholds (drawdown / recovery from peak)
OUTCOME_HELD_DRAWDOWN_MAX    = 0.40   # <= 40% down from peak → "Held"
OUTCOME_FADED_DRAWDOWN_MIN   = 0.40   # > 40% down
OUTCOME_FADED_RECOVERY_MIN   = 0.15   # still has >=15% of peak → "Faded"
OUTCOME_CRASHED_DRAWDOWN_MIN = 0.80   # > 80% down with < 15% recovery → "Crashed Later"
OUTCOME_IMMED_FAIL_TTP_MAX   = 300    # peak within 5m → "Immediate Fail"
OUTCOME_IMMED_FAIL_DD_MIN    = 0.85   # and > 85% drawdown

# =========================================================================
# PEAK MARKET CAP GATE
# Tokens that reached these MC levels had real trading opportunity and must
# never receive a rug/immediate_rug/slow_rug label.
# =========================================================================

# >= this value: never a rug — classify post-peak behavior instead
LARGE_PEAK_MC_USD = 500_000

# 100k-500k: meaningful token, prefer small_runner over rug
MEDIUM_PEAK_MC_USD = 100_000

# small_runner: token had return above noise threshold but not runner-grade
SMALL_RUNNER_RETURN_MIN = 1.5

# rugged_later: large peak but near-complete collapse (was tradeable, still collapsed)
RUGGED_LATER_DRAWDOWN_MIN = 0.85   # 85%+ of peak lost
RUGGED_LATER_RECOVERY_MAX = 0.15   # latest price < 15% of peak


# =========================================================================
# DATA STRUCTURES
# =========================================================================

def compute_token_class(peak_market_cap_usd: float) -> str:
    """
    Assign G-class from peak market cap alone.
    Returns 'G1'..'G7' or 'G?' when peak MC is unavailable (0).
    """
    if peak_market_cap_usd <= 0:
        return "G?"
    if peak_market_cap_usd >= G1_MC:
        return "G1"
    if peak_market_cap_usd >= G2_MC:
        return "G2"
    if peak_market_cap_usd >= G3_MC:
        return "G3"
    if peak_market_cap_usd >= G4_MC:
        return "G4"
    if peak_market_cap_usd >= G5_MC:
        return "G5"
    if peak_market_cap_usd >= G6_MC:
        return "G6"
    return "G7"


def compute_outcome(
    drawdown_from_peak: float,
    recovery_ratio: float,
    time_to_peak_secs: int,
    snapshot_count: int,
    is_active: bool = True,
) -> str:
    """
    Derive outcome from post-peak behaviour.

    Outcomes (priority order):
      Immediate Fail  — peaked within 5m AND collapsed 85%+
      Live            — token is still actively tracked
      Held            — drawdown <= 40% from peak
      Choppy          — volatile but survived (high drawdown, decent recovery)
      Faded           — 40-80% drawdown, still has some value
      Crashed Later   — 80%+ drawdown, near-zero recovery
    """
    # Not enough data
    if snapshot_count < EARLY_MIN_SNAPSHOTS:
        return "Collecting"

    # Immediate pump-and-dump (regardless of active state)
    if (time_to_peak_secs <= OUTCOME_IMMED_FAIL_TTP_MAX
            and drawdown_from_peak >= OUTCOME_IMMED_FAIL_DD_MIN):
        return "Immediate Fail"

    # Still being tracked — outcome is not final
    if is_active:
        return "Live"

    if drawdown_from_peak <= OUTCOME_HELD_DRAWDOWN_MAX:
        return "Held"

    if drawdown_from_peak >= OUTCOME_CRASHED_DRAWDOWN_MIN and recovery_ratio < OUTCOME_FADED_RECOVERY_MIN:
        return "Crashed Later"

    if drawdown_from_peak >= OUTCOME_FADED_DRAWDOWN_MIN and recovery_ratio >= OUTCOME_FADED_RECOVERY_MIN:
        # Significant drawdown but still has value — check if choppy (high volatility indicator)
        # We use recovery_ratio as proxy: if it bounced back above 35%, consider choppy
        if recovery_ratio >= CHOPPY_RUNNER_RECOVERY_MIN:
            return "Choppy"
        return "Faded"

    return "Faded"


@dataclass
class TokenBehaviorFeatures:
    """
    Derived features for a token from its price history.
    All fields are concrete (no Optional); defensive defaults used in compute_features.

    Dual initial price handling:
    - initial_price_observed_usd: first snapshot price (objective, may be late)
    - initial_price_robust_usd: median of first 5 snapshots (noise-resistant)
    - max_return_multiple: calculated from robust initial (used for classification)
    - max_return_multiple_observed: calculated from observed initial (for UI transparency)
    """
    mint: str
    initial_price_observed_usd: float       # first snapshot
    initial_price_robust_usd: float         # median of first 5 (or first if <5)
    peak_price_usd: float
    latest_price_usd: float
    max_return_multiple: float              # peak / robust_initial (for classification)
    max_return_multiple_observed: float     # peak / observed_initial (for UI)
    drawdown_from_peak: float               # (peak - latest) / peak
    recovery_ratio: float                   # latest / peak
    time_to_peak_secs: int                  # seconds from first to peak
    lifetime_secs: int                      # seconds from first to last
    snapshot_count: int                     # number of snapshots
    volatility: float                       # std dev of percentage changes
    slope_early: float                      # linear slope, first 5 minutes
    slope_total: float                      # linear slope, full lifetime
    tracking_quality: str                   # "good" | "possibly_late" | "likely_late"
    peak_market_cap_usd: float = 0.0        # peak MC from market_cap column (0 if unavailable)
    peak_grade: str = "G?"                  # G-class at peak MC
    peak_grade_reached_at: int = 0          # unix ts when peak grade was first reached
    peak_grade_held_secs: int = 0           # seconds token held its peak grade before dropping


# =========================================================================
# PRIVATE HELPERS — LINEAR REGRESSION & CONFIDENCE
# =========================================================================

def _linear_slope(pts: List[Tuple[float, float]]) -> float:
    """
    Compute OLS slope of (x, y) pairs.
    Uses formula: slope = (n*Σxy - Σx*Σy) / (n*Σx² - (Σx)²)
    Returns 0.0 if < 2 points or x values identical.
    Pure Python — no numpy dependency.
    """
    n = len(pts)
    if n < 2:
        return 0.0
    sx = sum(x for x, _ in pts)
    sy = sum(y for _, y in pts)
    sxy = sum(x * y for x, y in pts)
    sx2 = sum(x * x for x, _ in pts)
    denom = n * sx2 - sx * sx
    if denom == 0:
        return 0.0
    return (n * sxy - sx * sy) / denom


def _immediate_rug_confidence(f: TokenBehaviorFeatures) -> float:
    """Confidence for immediate_rug: more extreme collapse = higher."""
    drawdown_excess = min(
        (f.drawdown_from_peak - IMMEDIATE_RUG_DRAWDOWN_MIN) / (1.0 - IMMEDIATE_RUG_DRAWDOWN_MIN),
        1.0
    )
    recovery_penalty = min(
        (IMMEDIATE_RUG_RECOVERY_MAX - f.recovery_ratio) / IMMEDIATE_RUG_RECOVERY_MAX,
        1.0
    )
    speed_bonus = max(
        (IMMEDIATE_RUG_TIME_TO_PEAK_MAX - f.time_to_peak_secs) / IMMEDIATE_RUG_TIME_TO_PEAK_MAX,
        0.0
    )
    return round((drawdown_excess + recovery_penalty + speed_bonus) / 3.0, 4)


def _rug_confidence(f: TokenBehaviorFeatures) -> float:
    """Confidence for rug: deeper drawdown + higher multiple = higher."""
    drawdown_excess = min(
        (f.drawdown_from_peak - RUG_DRAWDOWN_MIN) / (1.0 - RUG_DRAWDOWN_MIN),
        1.0
    )
    multiple_excess = min((f.max_return_multiple - RUG_MAX_RETURN_MIN) / 10.0, 1.0)
    return round((drawdown_excess + multiple_excess) / 2.0, 4)


def _slow_rug_confidence(f: TokenBehaviorFeatures) -> float:
    """Confidence for slow_rug: steeper negative slope = higher."""
    drawdown_excess = min(
        (f.drawdown_from_peak - SLOW_RUG_DRAWDOWN_MIN) / (1.0 - SLOW_RUG_DRAWDOWN_MIN),
        1.0
    )
    # Slope is in price-per-second, scaled by 1e6 to bring into comparable range
    slope_signal = min(abs(f.slope_total) * 1e6, 1.0)
    return round((drawdown_excess + slope_signal) / 2.0, 4)


def _runner_confidence(f: TokenBehaviorFeatures) -> float:
    """Confidence for runner: higher multiple + better recovery = higher."""
    multiple_excess = min((f.max_return_multiple - RUNNER_MAX_RETURN_MIN) / 15.0, 1.0)
    recovery_quality = min(
        (f.recovery_ratio - RUNNER_RECOVERY_MIN) / (1.0 - RUNNER_RECOVERY_MIN),
        1.0
    )
    return round((multiple_excess + recovery_quality) / 2.0, 4)


def _faded_runner_confidence(f: TokenBehaviorFeatures) -> float:
    """
    Confidence for faded_runner: had strong upside, then material decline.

    Higher confidence when:
    - Multiple is well above 3.0x
    - Drawdown is clearly in the 50-85% range (not too shallow, not too deep)
    - Recovery is in the 15-50% range (not completely dead, not still strong)
    """
    # Multiple quality: how much above the 3.0x threshold
    multiple_excess = min((f.max_return_multiple - FADED_RUNNER_MAX_RETURN_MIN) / 7.0, 1.0)

    # Drawdown quality: how clearly centered in the 50-85% range
    drawdown_range = FADED_RUNNER_DRAWDOWN_MAX - FADED_RUNNER_DRAWDOWN_MIN
    drawdown_mid = (FADED_RUNNER_DRAWDOWN_MIN + FADED_RUNNER_DRAWDOWN_MAX) / 2.0
    drawdown_quality = max(0.0, 1.0 - abs(f.drawdown_from_peak - drawdown_mid) / (drawdown_range / 2.0))

    # Recovery quality: how clearly centered in the 15-50% range
    recovery_range = FADED_RUNNER_RECOVERY_MAX - FADED_RUNNER_RECOVERY_MIN
    recovery_mid = (FADED_RUNNER_RECOVERY_MIN + FADED_RUNNER_RECOVERY_MAX) / 2.0
    recovery_quality = max(0.0, 1.0 - abs(f.recovery_ratio - recovery_mid) / (recovery_range / 2.0))

    # Blend: multiple matters most for faded runners (to distinguish from choppy)
    confidence = round(multiple_excess * 0.4 + drawdown_quality * 0.3 + recovery_quality * 0.3, 4)
    return min(confidence, 0.85)  # Cap at 0.85 since faded runners are inherently uncertain


def _choppy_runner_confidence(f: TokenBehaviorFeatures) -> float:
    """Confidence for choppy_runner: higher multiple + recovery = higher."""
    multiple_excess = min((f.max_return_multiple - CHOPPY_RUNNER_MAX_RETURN_MIN) / 7.0, 1.0)
    recovery_quality = min(
        (f.recovery_ratio - CHOPPY_RUNNER_RECOVERY_MIN) / (1.0 - CHOPPY_RUNNER_RECOVERY_MIN),
        1.0
    )
    return round((multiple_excess + recovery_quality) / 2.0, 4)


# =========================================================================
# PUBLIC API
# =========================================================================

def create_schema(db_path: str) -> None:
    """
    Create token_behavior and token_behavior_history tables + indexes.
    Idempotent — uses IF NOT EXISTS throughout.
    """
    conn = sqlite3.connect(db_path, timeout=15)
    try:
        cursor = conn.cursor()
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS token_behavior (
                mint                TEXT PRIMARY KEY,
                category            TEXT NOT NULL
                                        CHECK(category IN (
                                            'immediate_rug','runner','faded_runner',
                                            'choppy_runner','rug','slow_rug',
                                            'insufficient_history','unknown',
                                            'collecting','late_start','low_peak','unclassified',
                                            'rugged_later','small_runner'
                                        )),
                confidence          REAL NOT NULL DEFAULT 0.0,
                initial_price_observed_usd REAL,
                initial_price_robust_usd REAL,
                peak_price_usd      REAL,
                latest_price_usd    REAL,
                max_return_multiple REAL,
                max_return_multiple_observed REAL,
                drawdown_from_peak  REAL,
                recovery_ratio      REAL,
                time_to_peak_secs   INTEGER,
                lifetime_secs       INTEGER,
                snapshot_count      INTEGER,
                volatility          REAL,
                slope_early         REAL,
                slope_total         REAL,
                tracking_quality    TEXT DEFAULT 'good',
                classified_at       INTEGER NOT NULL,
                created_at          INTEGER NOT NULL,
                token_class         TEXT,
                outcome             TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_tb_category
                ON token_behavior(category);

            CREATE INDEX IF NOT EXISTS idx_tb_classified_at
                ON token_behavior(classified_at DESC);

            CREATE INDEX IF NOT EXISTS idx_tb_token_class
                ON token_behavior(token_class);

            CREATE TABLE IF NOT EXISTS token_behavior_history (
                history_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                mint                TEXT NOT NULL,
                category            TEXT NOT NULL,
                confidence          REAL NOT NULL,
                max_return_multiple REAL,
                drawdown_from_peak  REAL,
                recovery_ratio      REAL,
                time_to_peak_secs   INTEGER,
                lifetime_secs       INTEGER,
                snapshot_count      INTEGER,
                classified_at       INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tbh_mint
                ON token_behavior_history(mint, classified_at DESC);

            CREATE INDEX IF NOT EXISTS idx_tbh_category
                ON token_behavior_history(category);

            CREATE TABLE IF NOT EXISTS token_outcomes (
                mint                    TEXT PRIMARY KEY,
                first_seen_at           INTEGER,
                last_seen_at            INTEGER,
                tracking_ended_at       INTEGER,
                drop_reason             TEXT,
                peak_market_cap_usd     REAL,
                peak_market_cap_at      INTEGER,
                time_to_peak_secs       INTEGER,
                latest_market_cap_usd   REAL,
                latest_price_usd        REAL,
                lifetime_secs           INTEGER,
                snapshot_count_final    INTEGER,
                max_return_multiple     REAL,
                drawdown_from_peak      REAL,
                behaviour_category      TEXT,
                confidence              REAL,
                tracking_quality        TEXT,
                rating_1_to_10          INTEGER,
                rating_reason           TEXT,
                finalized_at            INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_to_finalized_at
                ON token_outcomes(finalized_at DESC);

            CREATE INDEX IF NOT EXISTS idx_to_category
                ON token_outcomes(behaviour_category);

            CREATE INDEX IF NOT EXISTS idx_to_rating
                ON token_outcomes(rating_1_to_10);
        """)
        conn.commit()
        logger.info("[TOKEN_BEHAVIOR] Schema initialised")
    finally:
        conn.close()


def load_snapshots(mint: str, db_path: str) -> List:
    """
    Load all price snapshots for a mint from token_price_snapshots.
    Filters out zero/null prices. Returns ordered ASC by captured_at.
    Returns empty list if mint not found or has no valid snapshots.
    """
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT snapshot_id, price_usd, market_cap, captured_at
            FROM token_price_snapshots
            WHERE mint = ? AND price_usd > 0
            ORDER BY captured_at ASC
        """, (mint,))
        return cursor.fetchall()
    finally:
        conn.close()


def compute_features(mint: str, snapshots: List) -> TokenBehaviorFeatures:
    """
    Derive all features from an ordered list of price snapshot rows.
    Each row expected to have: price_usd, captured_at.

    Caller should check snapshot_count >= MIN_SNAPSHOTS and
    lifetime_secs >= MIN_LIFETIME_SECS before relying on the result.

    Handles edge cases: zero initial price, zero peak price, single snapshot, etc.

    Dual initial price tracking:
    - observed_initial: first snapshot (objective but may be late)
    - robust_initial: median of first 5 (noise-resistant, better for classification)
    """
    prices = [row['price_usd'] for row in snapshots]
    times = [row['captured_at'] for row in snapshots]

    n = len(prices)
    t_first, t_last = times[0], times[-1]
    lifetime_secs = t_last - t_first

    # Dual initial price handling
    observed_initial_price = prices[0]
    robust_initial_price = statistics.median(prices[:5]) if n >= 5 else observed_initial_price

    latest_price = prices[-1]
    peak_price = max(prices)
    peak_idx = prices.index(peak_price)
    t_peak = times[peak_idx]
    time_to_peak = t_peak - t_first

    # Peak market cap: max of market_cap column at snapshot rows (cap unrealistic values)
    market_caps = [row['market_cap'] for row in snapshots
                   if row['market_cap'] and 0 < row['market_cap'] < 1e8]
    peak_market_cap_usd = max(market_caps) if market_caps else 0.0

    # Peak grade hold duration: when did the token first reach its peak G-class,
    # and how long did it hold that grade before dropping to a lower one?
    peak_grade = compute_token_class(peak_market_cap_usd)
    peak_grade_reached_at = 0
    peak_grade_held_secs = 0
    if peak_grade not in ("G?",) and market_caps:
        # Walk snapshots in time order to find first moment peak grade was achieved
        for row in snapshots:
            mc = row['market_cap']
            if mc and 0 < mc < 1e8 and compute_token_class(mc) == peak_grade:
                peak_grade_reached_at = int(row['captured_at'])
                break
        # Walk forward from peak_grade_reached_at to find when grade first dropped
        if peak_grade_reached_at:
            for row in snapshots:
                if row['captured_at'] < peak_grade_reached_at:
                    continue
                mc = row['market_cap']
                if mc and 0 < mc < 1e8 and compute_token_class(mc) != peak_grade:
                    peak_grade_held_secs = int(row['captured_at']) - peak_grade_reached_at
                    break
            # If grade never dropped, held from peak_grade_reached_at to last snapshot
            if peak_grade_held_secs == 0 and peak_grade_reached_at:
                peak_grade_held_secs = int(times[-1]) - peak_grade_reached_at

    # max_return_multiple: use robust initial for classification
    if robust_initial_price > 0:
        max_return_multiple = peak_price / robust_initial_price
    else:
        max_return_multiple = 0.0

    # max_return_multiple_observed: use observed initial for UI transparency
    if observed_initial_price > 0:
        max_return_multiple_observed = peak_price / observed_initial_price
    else:
        max_return_multiple_observed = 0.0

    # drawdown and recovery: guard against zero peak
    if peak_price > 0:
        drawdown_from_peak = (peak_price - latest_price) / peak_price
        recovery_ratio = latest_price / peak_price
    else:
        drawdown_from_peak = 0.0
        recovery_ratio = 0.0

    # volatility: std dev of consecutive percentage changes
    if n >= 2:
        pct_changes = []
        for i in range(1, n):
            prev = prices[i - 1]
            if prev > 0:
                pct_changes.append((prices[i] - prev) / prev)
        volatility = statistics.pstdev(pct_changes) if pct_changes else 0.0
    else:
        volatility = 0.0

    # slope_early: linear slope over first SLOPE_EARLY_WINDOW_SECS
    early_cutoff = t_first + SLOPE_EARLY_WINDOW_SECS
    early_pts = [(t - t_first, p) for t, p in zip(times, prices)
                 if t <= early_cutoff]
    slope_early = _linear_slope(early_pts)

    # slope_total: linear slope over entire lifetime
    all_pts = [(t - t_first, p) for t, p in zip(times, prices)]
    slope_total = _linear_slope(all_pts)

    # Tracking quality heuristic
    if time_to_peak < 60:
        tracking_quality = "likely_late"
    elif n >= 5:
        # Check if early prices are already near peak
        early_prices = prices[:5]
        early_max = max(early_prices)
        early_min = min(early_prices)
        if early_min > 0 and (early_max / early_min) > 2.0:
            # Big spread in early data suggests we caught the run
            tracking_quality = "good"
        elif early_max / peak_price > 0.9:
            # Early max is already 90%+ of peak, likely late entry
            tracking_quality = "possibly_late"
        else:
            tracking_quality = "good"
    else:
        tracking_quality = "good"

    return TokenBehaviorFeatures(
        mint=mint,
        initial_price_observed_usd=observed_initial_price,
        initial_price_robust_usd=robust_initial_price,
        peak_price_usd=peak_price,
        latest_price_usd=latest_price,
        max_return_multiple=max_return_multiple,
        max_return_multiple_observed=max_return_multiple_observed,
        drawdown_from_peak=drawdown_from_peak,
        recovery_ratio=recovery_ratio,
        time_to_peak_secs=time_to_peak,
        lifetime_secs=lifetime_secs,
        snapshot_count=n,
        volatility=volatility,
        slope_early=slope_early,
        slope_total=slope_total,
        tracking_quality=tracking_quality,
        peak_market_cap_usd=peak_market_cap_usd,
        peak_grade=peak_grade,
        peak_grade_reached_at=peak_grade_reached_at,
        peak_grade_held_secs=peak_grade_held_secs,
    )


def classify_token(features: TokenBehaviorFeatures) -> Tuple[str, float]:
    """
    Classify a token into a behaviour category based on features.

    Returns:
        (category: str, confidence: float)

    Confidence tiers:
    - 0.0-0.3: low (early signal, noisy data)
    - 0.3-0.7: medium (reasonable confidence)
    - 0.7-1.0: high (strong signal, mature data)

    Priority order:
      large-peak gate (>=500k MC) → runner > faded_runner > choppy_runner > rugged_later
      medium-peak gate (100k-500k MC) → runner > faded_runner > choppy_runner > small_runner
      standard path → immediate_rug > runner > faded_runner > choppy_runner > rug > slow_rug
    """
    f = features

    # Tracking quality multiplier:
    # - good: full trust
    # - possibly_late: reduce confidence moderately
    # - likely_late: reduce confidence more aggressively
    tracking_quality_multiplier = {
        "good": 1.0,
        "possibly_late": 0.85,
        "likely_late": 0.65,
    }.get(f.tracking_quality, 1.0)

    # ── Peak MC gate ──────────────────────────────────────────────────────────
    # Tokens that reached a meaningful market cap had real trading opportunity.
    # They must never receive a rug/immediate_rug/slow_rug label regardless of
    # what happened after the peak.
    peak_mc = f.peak_market_cap_usd  # 0.0 if unavailable — gate won't trigger

    if peak_mc >= LARGE_PEAK_MC_USD:
        # Token reached 500k+: classify purely by post-peak behavior, never rug
        # Gate: insufficient data
        if f.snapshot_count < EARLY_MIN_SNAPSHOTS:
            return ("insufficient_history", 0.0)

        confidence_penalty = (
            0.5 if f.snapshot_count < MID_MIN_SNAPSHOTS else
            0.8 if f.snapshot_count < FULL_MIN_SNAPSHOTS else 1.0
        )
        if f.lifetime_secs < MID_MIN_LIFETIME_SECS:
            confidence_penalty *= 0.6
        elif f.lifetime_secs < FULL_MIN_LIFETIME_SECS:
            confidence_penalty *= 0.85

        tqm = tracking_quality_multiplier

        # Runner: held well
        if (f.max_return_multiple >= RUNNER_MAX_RETURN_MIN
                and f.drawdown_from_peak <= RUNNER_DRAWDOWN_MAX
                and f.recovery_ratio >= RUNNER_RECOVERY_MIN):
            return ("runner", _runner_confidence(f) * confidence_penalty * tqm)

        # Faded runner: strong run, then material decline
        if (f.max_return_multiple >= FADED_RUNNER_MAX_RETURN_MIN
                and f.drawdown_from_peak >= FADED_RUNNER_DRAWDOWN_MIN
                and f.drawdown_from_peak <= FADED_RUNNER_DRAWDOWN_MAX
                and f.recovery_ratio >= FADED_RUNNER_RECOVERY_MIN
                and f.recovery_ratio <= FADED_RUNNER_RECOVERY_MAX):
            return ("faded_runner", _faded_runner_confidence(f) * confidence_penalty * tqm)

        # Choppy runner: still alive despite big retracements
        if (f.max_return_multiple >= CHOPPY_RUNNER_MAX_RETURN_MIN
                and f.recovery_ratio >= CHOPPY_RUNNER_RECOVERY_MIN):
            return ("choppy_runner", _choppy_runner_confidence(f) * confidence_penalty * tqm)

        # Rugged later: had a real peak, then near-total collapse — still NOT labeled "rug"
        if (f.drawdown_from_peak >= RUGGED_LATER_DRAWDOWN_MIN
                and f.recovery_ratio <= RUGGED_LATER_RECOVERY_MAX):
            return ("rugged_later", 0.7 * confidence_penalty * tqm)

        # Fallback for large-peak tokens that don't fit a clean post-peak pattern
        return ("faded_runner", 0.3 * confidence_penalty * tqm)

    if MEDIUM_PEAK_MC_USD <= peak_mc < LARGE_PEAK_MC_USD:
        # Token reached 100k-500k: meaningful but not large. Prefer small_runner over rug.
        if f.snapshot_count < EARLY_MIN_SNAPSHOTS:
            return ("insufficient_history", 0.0)

        confidence_penalty = (
            0.5 if f.snapshot_count < MID_MIN_SNAPSHOTS else
            0.8 if f.snapshot_count < FULL_MIN_SNAPSHOTS else 1.0
        )
        if f.lifetime_secs < MID_MIN_LIFETIME_SECS:
            confidence_penalty *= 0.6
        elif f.lifetime_secs < FULL_MIN_LIFETIME_SECS:
            confidence_penalty *= 0.85

        tqm = tracking_quality_multiplier

        # Standard strong-signal categories take priority
        if (f.max_return_multiple >= RUNNER_MAX_RETURN_MIN
                and f.drawdown_from_peak <= RUNNER_DRAWDOWN_MAX
                and f.recovery_ratio >= RUNNER_RECOVERY_MIN):
            return ("runner", _runner_confidence(f) * confidence_penalty * tqm)

        if (f.max_return_multiple >= FADED_RUNNER_MAX_RETURN_MIN
                and f.drawdown_from_peak >= FADED_RUNNER_DRAWDOWN_MIN
                and f.drawdown_from_peak <= FADED_RUNNER_DRAWDOWN_MAX
                and f.recovery_ratio >= FADED_RUNNER_RECOVERY_MIN
                and f.recovery_ratio <= FADED_RUNNER_RECOVERY_MAX):
            return ("faded_runner", _faded_runner_confidence(f) * confidence_penalty * tqm)

        if (f.max_return_multiple >= CHOPPY_RUNNER_MAX_RETURN_MIN
                and f.recovery_ratio >= CHOPPY_RUNNER_RECOVERY_MIN):
            return ("choppy_runner", _choppy_runner_confidence(f) * confidence_penalty * tqm)

        # Had meaningful MC — prefer small_runner over any rug label
        if f.max_return_multiple >= SMALL_RUNNER_RETURN_MIN:
            return ("small_runner", 0.5 * confidence_penalty * tqm)

        # Had MC but return was flat — could still fall through to standard path below
        # (will hit slow_rug or sub-states, which is acceptable for 100-500k tokens
        #  with <1.5x return — they didn't "run" even with that MC)

    # ── Standard path (peak_mc < 100k OR peak_mc unknown) ────────────────────

    # 1. immediate_rug (highest priority) — check first, before lifetime gate
    # Immediate rugs can happen within minutes, no lifetime requirement
    if (f.snapshot_count >= EARLY_MIN_SNAPSHOTS
            and f.time_to_peak_secs <= IMMEDIATE_RUG_TIME_TO_PEAK_MAX
            and f.drawdown_from_peak >= IMMEDIATE_RUG_DRAWDOWN_MIN
            and f.recovery_ratio <= IMMEDIATE_RUG_RECOVERY_MAX):
        conf = _immediate_rug_confidence(f) * tracking_quality_multiplier
        return ("immediate_rug", conf)

    # Gate: insufficient data
    if f.snapshot_count < EARLY_MIN_SNAPSHOTS:
        return ("insufficient_history", 0.0)

    # If barely enough snapshots, reduce confidence
    if f.snapshot_count < MID_MIN_SNAPSHOTS:
        confidence_penalty = 0.5
    elif f.snapshot_count < FULL_MIN_SNAPSHOTS:
        confidence_penalty = 0.8
    else:
        confidence_penalty = 1.0

    # Also penalize if lifetime is short (noisy data)
    if f.lifetime_secs < MID_MIN_LIFETIME_SECS:
        confidence_penalty *= 0.6
    elif f.lifetime_secs < FULL_MIN_LIFETIME_SECS:
        confidence_penalty *= 0.85

    # 2. runner
    if (f.max_return_multiple >= RUNNER_MAX_RETURN_MIN
            and f.drawdown_from_peak <= RUNNER_DRAWDOWN_MAX
            and f.recovery_ratio >= RUNNER_RECOVERY_MIN):
        conf = _runner_confidence(f) * confidence_penalty * tracking_quality_multiplier
        return ("runner", conf)

    # 3. faded_runner (strong upside, then material decline)
    if (f.max_return_multiple >= FADED_RUNNER_MAX_RETURN_MIN
            and f.drawdown_from_peak >= FADED_RUNNER_DRAWDOWN_MIN
            and f.drawdown_from_peak <= FADED_RUNNER_DRAWDOWN_MAX
            and f.recovery_ratio >= FADED_RUNNER_RECOVERY_MIN
            and f.recovery_ratio <= FADED_RUNNER_RECOVERY_MAX):
        conf = _faded_runner_confidence(f) * confidence_penalty * tracking_quality_multiplier
        return ("faded_runner", conf)

    # 4. choppy_runner
    if (f.max_return_multiple >= CHOPPY_RUNNER_MAX_RETURN_MIN
            and f.recovery_ratio >= CHOPPY_RUNNER_RECOVERY_MIN):
        conf = _choppy_runner_confidence(f) * confidence_penalty * tracking_quality_multiplier
        return ("choppy_runner", conf)

    # 5. rug
    if (f.max_return_multiple >= RUG_MAX_RETURN_MIN
            and f.drawdown_from_peak >= RUG_DRAWDOWN_MIN):
        conf = _rug_confidence(f) * confidence_penalty * tracking_quality_multiplier
        return ("rug", conf)

    # 6. slow_rug
    if (f.max_return_multiple < SLOW_RUG_MAX_RETURN_MAX
            and f.slope_total < SLOW_RUG_SLOPE_MAX
            and f.drawdown_from_peak >= SLOW_RUG_DRAWDOWN_MIN):
        conf = _slow_rug_confidence(f) * confidence_penalty * tracking_quality_multiplier
        return ("slow_rug", conf)

    # 7. Refined unknown sub-states — never return bare "unknown" for mature tokens
    #
    # Priority:
    #  collecting  — still gathering data (few snapshots)
    #  late_start  — tracking started after peak; classification unreliable
    #  low_peak    — token never developed; max_return below significance threshold
    #  unclassified — enough data, some movement, but no pattern matched

    if f.snapshot_count < MID_MIN_SNAPSHOTS:
        return ("collecting", 0.0)

    if f.tracking_quality in ("possibly_late", "likely_late"):
        return ("late_start", 0.0)

    if f.max_return_multiple < LOW_PEAK_RETURN_MAX:
        return ("low_peak", 0.0)

    return ("unclassified", 0.0)


def upsert_behavior(
    features: TokenBehaviorFeatures,
    category: str,
    confidence: float,
    db_path: str,
) -> bool:
    """
    Write (or overwrite) the behaviour record for a mint.
    Also appends a row to token_behavior_history.

    Uses SQLite ON CONFLICT DO UPDATE to preserve created_at timestamp
    from the first classification while updating all other fields.

    Returns:
        True on success, False on error (logged).
    """
    now = int(time.time())
    conn = sqlite3.connect(db_path, timeout=15)
    try:
        cursor = conn.cursor()
        f = features

        token_class = compute_token_class(f.peak_market_cap_usd)
        # For upsert, active=True since we're processing live tokens.
        # Finalized tokens get outcome re-computed in snapshot_retention_manager.
        outcome = compute_outcome(
            f.drawdown_from_peak, f.recovery_ratio,
            f.time_to_peak_secs, f.snapshot_count, is_active=True,
        )

        # Upsert primary record
        cursor.execute("""
            INSERT INTO token_behavior (
                mint, category, confidence,
                initial_price_observed_usd, initial_price_robust_usd,
                peak_price_usd, latest_price_usd,
                max_return_multiple, max_return_multiple_observed,
                drawdown_from_peak, recovery_ratio,
                time_to_peak_secs, lifetime_secs, snapshot_count,
                volatility, slope_early, slope_total,
                tracking_quality,
                classified_at, created_at,
                token_class, outcome,
                peak_grade, peak_grade_reached_at, peak_grade_held_secs
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mint) DO UPDATE SET
                category                      = excluded.category,
                confidence                    = excluded.confidence,
                initial_price_observed_usd    = excluded.initial_price_observed_usd,
                initial_price_robust_usd      = excluded.initial_price_robust_usd,
                peak_price_usd                = excluded.peak_price_usd,
                latest_price_usd              = excluded.latest_price_usd,
                max_return_multiple           = excluded.max_return_multiple,
                max_return_multiple_observed  = excluded.max_return_multiple_observed,
                drawdown_from_peak            = excluded.drawdown_from_peak,
                recovery_ratio                = excluded.recovery_ratio,
                time_to_peak_secs            = excluded.time_to_peak_secs,
                lifetime_secs                 = excluded.lifetime_secs,
                snapshot_count                = excluded.snapshot_count,
                volatility                    = excluded.volatility,
                slope_early                   = excluded.slope_early,
                slope_total                   = excluded.slope_total,
                tracking_quality              = excluded.tracking_quality,
                classified_at                 = excluded.classified_at,
                token_class                   = excluded.token_class,
                outcome                       = excluded.outcome,
                peak_grade                    = excluded.peak_grade,
                peak_grade_reached_at         = excluded.peak_grade_reached_at,
                peak_grade_held_secs          = excluded.peak_grade_held_secs
        """, (
            f.mint, category, confidence,
            f.initial_price_observed_usd, f.initial_price_robust_usd,
            f.peak_price_usd, f.latest_price_usd,
            f.max_return_multiple, f.max_return_multiple_observed,
            f.drawdown_from_peak, f.recovery_ratio,
            f.time_to_peak_secs, f.lifetime_secs, f.snapshot_count,
            f.volatility, f.slope_early, f.slope_total,
            f.tracking_quality,
            now, now,
            token_class, outcome,
            f.peak_grade, f.peak_grade_reached_at, f.peak_grade_held_secs,
        ))

        # Append history record (append-only)
        cursor.execute("""
            INSERT INTO token_behavior_history (
                mint, category, confidence,
                max_return_multiple, drawdown_from_peak, recovery_ratio,
                time_to_peak_secs, lifetime_secs, snapshot_count,
                classified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f.mint, category, confidence,
            f.max_return_multiple, f.drawdown_from_peak, f.recovery_ratio,
            f.time_to_peak_secs, f.lifetime_secs, f.snapshot_count,
            now
        ))

        conn.commit()
        return True
    except Exception as e:
        logger.error(f"[TOKEN_BEHAVIOR] upsert failed for {f.mint[:16]}: {e}")
        return False
    finally:
        conn.close()


def classify_mint(mint: str, db_path: str, skip_upsert: bool = False) -> Tuple[str, float]:
    """
    Convenience function: load snapshots, compute features, classify, and upsert.

    Args:
        mint: Token mint address
        db_path: Path to database
        skip_upsert: If True, compute classification but don't write to database (for dry-run)

    Returns:
        (category, confidence) tuple. Writes to database unless skip_upsert=True.
    """
    snapshots = load_snapshots(mint, db_path)
    if not snapshots:
        return ("collecting", 0.0)

    features = compute_features(mint, snapshots)
    category, confidence = classify_token(features)
    if not skip_upsert:
        upsert_behavior(features, category, confidence, db_path)
    return (category, confidence)
