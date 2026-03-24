"""
Token Behaviour Categorisation

Derived analytics layer that classifies tokens from historical price snapshots.
Reads from token_price_snapshots (read-only). Writes to token_behavior and
token_behavior_history tables.

Categories: immediate_rug | rug | slow_rug | runner | choppy_runner | unknown

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

# Minimum data quality gates — below these, always unknown
MIN_SNAPSHOTS = 8           # Fewer snapshots → insufficient data
MIN_LIFETIME_SECS = 180     # Shorter lifetime → insufficient data

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

# choppy_runner: large upside with big retracements, still alive
CHOPPY_RUNNER_MAX_RETURN_MIN = 3.0        # Multiple of initial price
CHOPPY_RUNNER_RECOVERY_MIN = 0.35         # Latest as fraction of peak

# slope_early window: first N seconds of life
SLOPE_EARLY_WINDOW_SECS = 300             # 5 minutes


# =========================================================================
# DATA STRUCTURES
# =========================================================================

@dataclass
class TokenBehaviorFeatures:
    """
    Derived features for a token from its price history.
    All fields are concrete (no Optional); defensive defaults used in compute_features.
    """
    mint: str
    initial_price_usd: float
    peak_price_usd: float
    latest_price_usd: float
    max_return_multiple: float              # peak / initial
    drawdown_from_peak: float               # (peak - latest) / peak
    recovery_ratio: float                   # latest / peak
    time_to_peak_secs: int                  # seconds from first to peak
    lifetime_secs: int                      # seconds from first to last
    snapshot_count: int                     # number of snapshots
    volatility: float                       # std dev of percentage changes
    slope_early: float                      # linear slope, first 5 minutes
    slope_total: float                      # linear slope, full lifetime


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
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS token_behavior (
                mint                TEXT PRIMARY KEY,
                category            TEXT NOT NULL
                                        CHECK(category IN (
                                            'immediate_rug','rug','slow_rug',
                                            'runner','choppy_runner','unknown'
                                        )),
                confidence          REAL NOT NULL DEFAULT 0.0,
                initial_price_usd   REAL,
                peak_price_usd      REAL,
                latest_price_usd    REAL,
                max_return_multiple REAL,
                drawdown_from_peak  REAL,
                recovery_ratio      REAL,
                time_to_peak_secs   INTEGER,
                lifetime_secs       INTEGER,
                snapshot_count      INTEGER,
                volatility          REAL,
                slope_early         REAL,
                slope_total         REAL,
                classified_at       INTEGER NOT NULL,
                created_at          INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tb_category
                ON token_behavior(category);

            CREATE INDEX IF NOT EXISTS idx_tb_classified_at
                ON token_behavior(classified_at DESC);

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
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT snapshot_id, price_usd, captured_at
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
    """
    prices = [row['price_usd'] for row in snapshots]
    times = [row['captured_at'] for row in snapshots]

    n = len(prices)
    t_first, t_last = times[0], times[-1]
    lifetime_secs = t_last - t_first

    initial_price = prices[0]
    latest_price = prices[-1]
    peak_price = max(prices)
    peak_idx = prices.index(peak_price)
    t_peak = times[peak_idx]
    time_to_peak = t_peak - t_first

    # max_return_multiple: guard against zero initial
    if initial_price > 0:
        max_return_multiple = peak_price / initial_price
    else:
        max_return_multiple = 0.0

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

    return TokenBehaviorFeatures(
        mint=mint,
        initial_price_usd=initial_price,
        peak_price_usd=peak_price,
        latest_price_usd=latest_price,
        max_return_multiple=max_return_multiple,
        drawdown_from_peak=drawdown_from_peak,
        recovery_ratio=recovery_ratio,
        time_to_peak_secs=time_to_peak,
        lifetime_secs=lifetime_secs,
        snapshot_count=n,
        volatility=volatility,
        slope_early=slope_early,
        slope_total=slope_total,
    )


def classify_token(features: TokenBehaviorFeatures) -> Tuple[str, float]:
    """
    Classify a token into a behaviour category based on features.

    Returns:
        (category: str, confidence: float)

    Confidence is a float in [0, 1] (higher = more certain).
    Priority order: immediate_rug > runner > choppy_runner > rug > slow_rug > unknown

    Gates:
    - snapshot_count < MIN_SNAPSHOTS → unknown
    - lifetime_secs < MIN_LIFETIME_SECS → unknown
    """
    f = features

    # Gate: insufficient data
    if f.snapshot_count < MIN_SNAPSHOTS or f.lifetime_secs < MIN_LIFETIME_SECS:
        return ("unknown", 0.0)

    # 1. immediate_rug (highest priority)
    if (f.time_to_peak_secs <= IMMEDIATE_RUG_TIME_TO_PEAK_MAX
            and f.drawdown_from_peak >= IMMEDIATE_RUG_DRAWDOWN_MIN
            and f.recovery_ratio <= IMMEDIATE_RUG_RECOVERY_MAX):
        conf = _immediate_rug_confidence(f)
        return ("immediate_rug", conf)

    # 2. runner
    if (f.max_return_multiple >= RUNNER_MAX_RETURN_MIN
            and f.drawdown_from_peak <= RUNNER_DRAWDOWN_MAX
            and f.recovery_ratio >= RUNNER_RECOVERY_MIN):
        conf = _runner_confidence(f)
        return ("runner", conf)

    # 3. choppy_runner
    if (f.max_return_multiple >= CHOPPY_RUNNER_MAX_RETURN_MIN
            and f.recovery_ratio >= CHOPPY_RUNNER_RECOVERY_MIN):
        conf = _choppy_runner_confidence(f)
        return ("choppy_runner", conf)

    # 4. rug
    if (f.max_return_multiple >= RUG_MAX_RETURN_MIN
            and f.drawdown_from_peak >= RUG_DRAWDOWN_MIN):
        conf = _rug_confidence(f)
        return ("rug", conf)

    # 5. slow_rug
    if (f.max_return_multiple < SLOW_RUG_MAX_RETURN_MAX
            and f.slope_total < SLOW_RUG_SLOPE_MAX
            and f.drawdown_from_peak >= SLOW_RUG_DRAWDOWN_MIN):
        conf = _slow_rug_confidence(f)
        return ("slow_rug", conf)

    # 6. unknown (conflicting or ambiguous signals)
    return ("unknown", 0.0)


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
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        f = features

        # Upsert primary record
        cursor.execute("""
            INSERT INTO token_behavior (
                mint, category, confidence,
                initial_price_usd, peak_price_usd, latest_price_usd,
                max_return_multiple, drawdown_from_peak, recovery_ratio,
                time_to_peak_secs, lifetime_secs, snapshot_count,
                volatility, slope_early, slope_total,
                classified_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mint) DO UPDATE SET
                category            = excluded.category,
                confidence          = excluded.confidence,
                initial_price_usd   = excluded.initial_price_usd,
                peak_price_usd      = excluded.peak_price_usd,
                latest_price_usd    = excluded.latest_price_usd,
                max_return_multiple = excluded.max_return_multiple,
                drawdown_from_peak  = excluded.drawdown_from_peak,
                recovery_ratio      = excluded.recovery_ratio,
                time_to_peak_secs   = excluded.time_to_peak_secs,
                lifetime_secs       = excluded.lifetime_secs,
                snapshot_count      = excluded.snapshot_count,
                volatility          = excluded.volatility,
                slope_early         = excluded.slope_early,
                slope_total         = excluded.slope_total,
                classified_at       = excluded.classified_at
        """, (
            f.mint, category, confidence,
            f.initial_price_usd, f.peak_price_usd, f.latest_price_usd,
            f.max_return_multiple, f.drawdown_from_peak, f.recovery_ratio,
            f.time_to_peak_secs, f.lifetime_secs, f.snapshot_count,
            f.volatility, f.slope_early, f.slope_total,
            now, now
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
        return ("unknown", 0.0)

    features = compute_features(mint, snapshots)
    category, confidence = classify_token(features)
    if not skip_upsert:
        upsert_behavior(features, category, confidence, db_path)
    return (category, confidence)
