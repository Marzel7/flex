"""MC1.1/MC1.2 -- Mission Control capability-based severity layer.

Implements the frozen MC1.0 design (docs/design/mc1_0_capability_severity_model.md):
a stateless derived layer that reads the 7 existing /api/health/full
subsystem blocks and re-groups them into capability-scoped status,
evidence, and incidents. Almost the entire module performs NO
measurement of its own -- every input is a field already present in the
subsystem dicts computed by api_health_full() in src/core/main.py.

The one deliberate exception, added in MC1.2 (docs/design/mc1_2_live_ingestion_flow_health.md):
the historical rate baseline engine (get_expected_rate_per_min /
_compute_historical_baseline_per_min below) issues its own small,
indexed, read-only queries against the existing token_analysis table
(the same table/columns main.py's ingestion block already reads
last_birth_age_secs/last_migration_age_secs from) to compute a real
expected-events-per-minute baseline. This is READ-ONLY and does not
touch, modify, or add to the ingestion pipeline, PumpPortal, PumpSwap,
listeners, or any worker -- it is Mission Control observing the same
data those systems already write, exactly as every other signal in this
module does. Results are cached in-memory with a short TTL (see
_BASELINE_CACHE) so this doesn't add a query to every single dashboard
poll, consistent with the module's stateless design (no new database
tables, no new workers).

Stateless per MC1.0 Amendment 3 / MC1.1's explicit instruction: no new
database tables, no new workers. Incident first_detected_at is computed
by walking backward through data the subsystem blocks already expose
(birth/migration timestamps) where possible; for composite capabilities
without a single monotonic timestamp, an in-memory (per-process) dict is
used, with the accepted limitation that it resets on process restart --
this is the explicitly smaller-scope alternative to persistence that
MC1.0 Section 11 named as the default when only live status (not
historical analytics) is required. The baseline cache follows the same
in-memory, resets-on-restart convention.
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DB_PATH = os.environ.get(
    "DB_PATH", os.path.join(_REPO_ROOT, "database", "flex_complete_database.db")
)

CAPABILITY_NAMES = (
    "live_ingestion",
    "creator_funding",
    "operational_intelligence",
    "watchtower",
    "infrastructure",
    "price_tracking",
)

# MC1.0 Section 3 -- degradation propagation: a downstream capability's
# displayed severity is floored (not overridden) by its upstream
# capability's severity. Order matters: each entry's upstream is the
# capability immediately above it in the hierarchy.
_UPSTREAM_OF = {
    "creator_funding": "live_ingestion",
    "operational_intelligence": "creator_funding",
    "watchtower": "operational_intelligence",
}

_SEVERITY_RANK = {"HEALTHY": 0, "UNKNOWN": 1, "WARNING": 2, "CRITICAL": 3}


def _rank(status: str) -> int:
    return _SEVERITY_RANK.get(status, 1)


def _max_status(*statuses: str) -> str:
    best = "HEALTHY"
    for s in statuses:
        if _rank(s) > _rank(best):
            best = s
    return best


# ── Phase D: Rate Engine ────────────────────────────────────────────────────
# MC1.0 Section 5: rate is the PRIMARY signal, elapsed-silence is the
# FALLBACK. This engine implements the MECHANISM only -- it does not ship
# a hardcoded production rate. get_expected_rate() is the interface a
# future implementation-time step wires to a real historical-baseline
# query; until that exists, it returns None (insufficient history),
# which correctly routes evaluation to the fallback path below.

RATE_WINDOW_MIN = int(os.environ.get("MC_RATE_WINDOW_MIN", "15"))
RATE_CRITICAL_RATIO = float(os.environ.get("MC_RATE_CRITICAL_RATIO", "0.1"))
RATE_WARNING_RATIO = float(os.environ.get("MC_RATE_WARNING_RATIO", "0.5"))

# Fallback-only constants (MC1.0 Section 6): never the primary trigger.
BIRTH_SILENCE_FALLBACK_SEC = int(os.environ.get("MC_BIRTH_SILENCE_FALLBACK_SEC", "5400"))
MIGRATION_SILENCE_FALLBACK_SEC = int(os.environ.get("MC_MIGRATION_SILENCE_FALLBACK_SEC", "5400"))

# ── MC1.2A: Migration Elapsed-Time Policy ───────────────────────────────────
#
# MC1.2 evaluated migrations with the SAME rate-ratio logic as births
# (evaluate_rate_signal, above). In production this over-escalated: births
# occur near-continuously so a rate-ratio collapse reliably means a real
# problem, but migrations are naturally bursty (a token migrates once, at
# an unpredictable point after birth) -- a normal dormant period between
# bursts produced the same CRITICAL classification as a genuine outage.
#
# This does not change how births are evaluated (Phase A: no change to
# birth logic) -- it adds a SEPARATE, elapsed-time-since-last-migration
# policy used only for migrations, calibrated from observed production
# behaviour rather than a rate ratio. Configurable via environment
# variables, per the charter's explicit "must remain configurable, do not
# hard-code production policy" instruction -- the numbers below are the
# charter's own suggested starting values, not a hardcoded final policy.
MIGRATION_DORMANT_MAX_MIN = float(os.environ.get("MC_MIGRATION_DORMANT_MAX_MIN", "20"))
MIGRATION_WARNING_MAX_MIN = float(os.environ.get("MC_MIGRATION_WARNING_MAX_MIN", "30"))
MIGRATION_CONCERN_MAX_MIN = float(os.environ.get("MC_MIGRATION_CONCERN_MAX_MIN", "40"))
# Beyond MIGRATION_CONCERN_MAX_MIN is CRITICAL.


def evaluate_migration_elapsed_policy(silence_secs: Optional[int]) -> Dict[str, Any]:
    """MC1.2A Phase B: migration health classified by elapsed time since
    the last migration, using configurable minute bands, instead of the
    rate-ratio logic births use. This directly encodes migrations' bursty
    operational profile: 0-20min is normal/dormant (not evidence of a
    problem), 20-30min is worth watching (WARNING), 30-40min is a real
    concern trending toward failure (still WARNING-ranked -- MC1.0's
    severity vocabulary has no fifth level between WARNING and CRITICAL,
    so 'Concern' is a PRESENTATION label the dashboard applies to this
    band, not a new backend status value), and only beyond 40min does this
    become CRITICAL. silence_secs=None (no migration ever recorded) is
    treated as UNKNOWN, matching every other signal's convention for
    absent data -- never silently CRITICAL or HEALTHY."""
    if silence_secs is None:
        return {
            "status": "UNKNOWN",
            "band": "unknown",
            "elapsed_min": None,
            "detail": "no migration recorded yet",
        }

    elapsed_min = silence_secs / 60.0
    if elapsed_min <= MIGRATION_DORMANT_MAX_MIN:
        status, band = "HEALTHY", "dormant"
    elif elapsed_min <= MIGRATION_WARNING_MAX_MIN:
        status, band = "WARNING", "warning"
    elif elapsed_min <= MIGRATION_CONCERN_MAX_MIN:
        status, band = "WARNING", "concern"
    else:
        status, band = "CRITICAL", "critical"

    return {
        "status": status,
        "band": band,
        "elapsed_min": round(elapsed_min, 1),
        "detail": f"{elapsed_min:.1f}min since last migration (band={band})",
    }


def compute_observed_rate_per_min(event_count_in_window: int, window_min: int = RATE_WINDOW_MIN) -> float:
    """Observed events/min over the rolling window. Pure function -- the
    caller is responsible for counting events (no new DB query is added
    by this module; main.py's existing ingestion block already has the
    raw timestamps needed, or a future implementation step adds one
    cheap indexed COUNT query at the call site, not here)."""
    if window_min <= 0:
        return 0.0
    return event_count_in_window / float(window_min)


def get_expected_rate_per_min(event_type: str) -> Optional[float]:
    """MC1.0 Section 5 / MC1.2 Phase A: expected rate is a computed,
    self-updating historical baseline, never a fixed number shipped in
    code. Delegates to the real baseline engine below. Returns None only
    when insufficient history exists to compute one (e.g. a brand-new
    deployment) -- callers MUST treat None as "fall back to silence-based
    detection" per Section 5's evaluation order, never as zero."""
    return _get_cached_baseline(event_type)


def evaluate_rate_signal(
    *,
    event_type: str,
    observed_count_in_window: int,
    silence_secs: Optional[int],
    fallback_silence_sec: int,
    window_min: int = RATE_WINDOW_MIN,
) -> Dict[str, Any]:
    """MC1.0 Section 5's evaluation order: rate primary, silence fallback.
    Returns a dict with the signal's abnormal/status classification plus
    the raw values needed for the evidence detail string (Section 8A)."""
    observed_rate = compute_observed_rate_per_min(observed_count_in_window, window_min)
    expected_rate = get_expected_rate_per_min(event_type)

    if expected_rate is not None and expected_rate > 0:
        ratio = observed_rate / expected_rate
        if ratio < RATE_CRITICAL_RATIO:
            status = "CRITICAL"
        elif ratio < RATE_WARNING_RATIO:
            status = "WARNING"
        else:
            status = "HEALTHY"
        return {
            "status": status,
            "mode": "rate",
            "observed_rate_per_min": round(observed_rate, 4),
            "expected_rate_per_min": round(expected_rate, 4),
            "detail": (
                f"observed {observed_rate:.2f}/min vs expected "
                f"{expected_rate:.2f}/min baseline (primary signal)"
            ),
        }

    # Fallback: no baseline available yet.
    if silence_secs is not None and silence_secs > fallback_silence_sec:
        status = "CRITICAL"
    else:
        status = "HEALTHY"
    return {
        "status": status,
        "mode": "silence_fallback",
        "silence_secs": silence_secs,
        "fallback_threshold_secs": fallback_silence_sec,
        "detail": (
            f"no baseline available; silence={silence_secs}s "
            f"(fallback threshold {fallback_silence_sec}s)"
            if silence_secs is not None
            else "no baseline available; silence unknown"
        ),
    }


# ── MC1.2 Phase A: Historical Baseline Engine ───────────────────────────────
#
# Investigation (see docs/design/mc1_2_live_ingestion_flow_health.md) found
# the naive approach -- median rate over ALL windows in a lookback period,
# including zero-event windows -- is unsafe on this platform's real data:
# a full-history scan showed a single contiguous 594-HOUR (24.75-day) gap
# with zero recorded births, and even a recent 7-day window had 101/557
# (18%) completely empty 15-minute windows. Averaging/medianing across
# those zero windows would drag the "expected" rate toward zero and
# silently defeat the whole point of a baseline (a collapsed-to-near-zero
# baseline can never detect a collapse). A 1-day lookback was also
# measured to be unstable (2.27/min) vs. 7-day and 14-day, which converge
# closely (19.33/min vs 19.53/min) -- a very short window can be
# dominated by whatever is happening right now, which is exactly the
# instability a baseline exists to avoid.
#
# Chosen approach: median rate over only the NONZERO windows within the
# lookback period. This is a purely statistical exclusion of low/zero-
# activity windows -- it requires no persisted incident history (staying
# consistent with this module's stateless design) and is inherently
# robust to a minority of the lookback period being an outage (real or,
# in this dev environment's case, the process simply not running),
# satisfying Phase A's "exclude known outage windows" requirement without
# needing a ground-truth list of exactly which windows were outages.

BASELINE_LOOKBACK_DAYS = float(os.environ.get("MC_BASELINE_LOOKBACK_DAYS", "7"))
BASELINE_BUCKET_MIN = int(os.environ.get("MC_BASELINE_BUCKET_MIN", "15"))
BASELINE_MIN_NONZERO_BUCKETS = int(os.environ.get("MC_BASELINE_MIN_NONZERO_BUCKETS", "8"))
BASELINE_CACHE_TTL_SEC = int(os.environ.get("MC_BASELINE_CACHE_TTL_SEC", "300"))

_BASELINE_EVENT_COLUMNS = {
    "births": ("analyzed_at", "source_platform='pumpfun' AND analyzed_at IS NOT NULL"),
    "migrations": ("migrated_at", "migrated_at IS NOT NULL"),
}

_BASELINE_CACHE: Dict[str, Any] = {}  # event_type -> (computed_at_mono, value_or_None)


def _compute_historical_baseline_per_min(event_type: str) -> Optional[float]:
    """Real DB-backed baseline (Phase A). Read-only, indexed queries
    against token_analysis -- same table/columns main.py's ingestion
    block already reads last_birth_age_secs/last_migration_age_secs
    from. Returns None if there isn't enough recent nonzero history to
    trust a baseline (BASELINE_MIN_NONZERO_BUCKETS), per Section 5's
    contract that None means "fall back to silence-based detection"."""
    col = _BASELINE_EVENT_COLUMNS.get(event_type)
    if not col:
        return None
    timestamp_col, where_clause = col

    conn = None
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=3)
        now = int(time.time())
        lookback_start = now - int(BASELINE_LOOKBACK_DAYS * 86400)
        bucket_sec = BASELINE_BUCKET_MIN * 60

        # Bucket timestamps directly in SQL (CAST/divide/floor via integer
        # division) so only bucket->count pairs cross into Python, not raw
        # rows -- a 7-day lookback at pumpfun's real volume is still
        # ~100K+ rows, and there's no reason to pull them individually
        # when SQLite can aggregate them in one indexed pass.
        rows = conn.execute(
            f"""
            SELECT CAST({timestamp_col} AS INTEGER) / {bucket_sec} AS bucket, COUNT(*) AS n
            FROM token_analysis
            WHERE {where_clause} AND {timestamp_col} >= ?
            GROUP BY bucket
            """,
            (lookback_start,),
        ).fetchall()
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    nonzero_counts = [r[1] for r in rows if r[1] and r[1] > 0]
    if len(nonzero_counts) < BASELINE_MIN_NONZERO_BUCKETS:
        return None

    nonzero_counts.sort()
    n = len(nonzero_counts)
    median_per_bucket = (
        nonzero_counts[n // 2]
        if n % 2 == 1
        else (nonzero_counts[n // 2 - 1] + nonzero_counts[n // 2]) / 2.0
    )
    return median_per_bucket / float(BASELINE_BUCKET_MIN)


def _get_cached_baseline(event_type: str) -> Optional[float]:
    """Short in-memory TTL cache (default 5min) so the baseline query
    doesn't run on every single /api/health/full poll -- consistent with
    this module's stateless design (in-memory only, no new table),
    matching the same convention already used for incident
    first_detected_at tracking (_incident_start_cache, below)."""
    cached = _BASELINE_CACHE.get(event_type)
    now_mono = time.monotonic()
    if cached is not None and (now_mono - cached[0]) < BASELINE_CACHE_TTL_SEC:
        return cached[1]
    value = _compute_historical_baseline_per_min(event_type)
    _BASELINE_CACHE[event_type] = (now_mono, value)
    return value


def reset_baseline_cache() -> None:
    """Test/debug hook -- forces the next get_expected_rate_per_min()
    call to recompute rather than reuse a cached value."""
    _BASELINE_CACHE.clear()


# ── MC1.3 Phase A/B: Live Ingestion Trend (real historical data) ───────────
#
# Live Ingestion is the one capability with a genuine, queryable event
# history (token_analysis.analyzed_at/migrated_at) -- exactly the same
# data MC1.2's baseline engine already reads. Its trend is therefore
# computed the same way: real historical windows, bucketed server-side,
# walked forward, NO new persistence, NO in-memory buffer needed (unlike
# every other capability -- see the Phase C section below for why they
# differ).

TREND_WINDOWS_MIN = {
    "5m": 5,
    "15m": 15,
    "60m": 60,
    "24h": 24 * 60,
}
TREND_DIRECTION_EPSILON = float(os.environ.get("MC_TREND_DIRECTION_EPSILON", "0.05"))  # 5% change = noise floor


def _rate_over_window(event_type: str, window_min: int, ago_min: int = 0) -> float:
    """Observed rate/min over a window ending `ago_min` minutes before
    now (ago_min=0 means "ending now"). Read-only indexed COUNT query,
    same table/columns as count_recent_events -- this is that same
    concept generalized to an arbitrary historical window instead of
    only 'now'."""
    col = _BASELINE_EVENT_COLUMNS.get(event_type)
    if not col:
        return 0.0
    timestamp_col, where_clause = col
    conn = None
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=3)
        now = int(time.time())
        window_end = now - ago_min * 60
        window_start = window_end - window_min * 60
        row = conn.execute(
            f"SELECT COUNT(*) FROM token_analysis WHERE {where_clause} AND {timestamp_col} >= ? AND {timestamp_col} < ?",
            (window_start, window_end),
        ).fetchone()
        count = int(row[0]) if row else 0
        return count / float(window_min) if window_min > 0 else 0.0
    except Exception:
        return 0.0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def compute_live_ingestion_trend(event_type: str) -> Dict[str, Any]:
    """MC1.3 Phase B: rate + direction across the fixed trend windows
    (5m/15m/60m/24h), plus a simple two-point comparison (current 5m
    window vs. the PRIOR 5m window) to classify direction. This is
    explicitly a MEASURED comparison of two real historical windows, not
    a prediction or extrapolation -- 'still falling' / 'recovering' means
    the rate in the most recent window is measurably lower/higher than
    the window immediately before it, nothing more."""
    rates = {
        label: _rate_over_window(event_type, minutes)
        for label, minutes in TREND_WINDOWS_MIN.items()
    }
    current_5m = rates["5m"]
    prior_5m = _rate_over_window(event_type, 5, ago_min=5)

    if prior_5m <= 0 and current_5m <= 0:
        direction = "stable"
    elif prior_5m <= 0:
        direction = "improving"
    else:
        delta_ratio = (current_5m - prior_5m) / prior_5m
        if delta_ratio > TREND_DIRECTION_EPSILON:
            direction = "improving"
        elif delta_ratio < -TREND_DIRECTION_EPSILON:
            direction = "degrading"
        else:
            direction = "stable"

    expected = get_expected_rate_per_min(event_type)
    pct_of_baseline = (current_5m / expected * 100.0) if expected and expected > 0 else None

    return {
        "direction": direction,
        "rates_per_min": rates,
        "current_vs_prior_5m": {"current": round(current_5m, 4), "prior": round(prior_5m, 4)},
        "pct_of_baseline": round(pct_of_baseline, 1) if pct_of_baseline is not None else None,
        "expected_per_min": expected,
    }


# ── MC1.3 Phase C: Capability Trend (in-memory rolling sample buffer) ──────
#
# Unlike Live Ingestion, the other 5 capabilities have no queryable event
# history anywhere -- wt_worker_heartbeat, funding_queue_pending,
# database.p99_wait_ms etc. are all single CURRENT-VALUE rows with no
# history table. Computing a real trend for them without adding new
# persistence (out of scope, "no new backend systems") requires sampling
# forward from now: an in-memory, per-process rolling buffer of
# (timestamp, status_rank) pairs, appended once per compute_capabilities()
# call. This means trend for these 5 capabilities is only available once
# enough SAMPLES (not wall-clock time alone, since poll cadence varies)
# have accumulated -- it warms up after the process has been running and
# polled a few times, and resets on restart, exactly like the incident-
# start cache above. This is an explicit, honest limitation: it does NOT
# invent history that doesn't exist, and reports "insufficient history"
# rather than approximating a trend from too little data.

TREND_BUFFER_MAX_SAMPLES = int(os.environ.get("MC_TREND_BUFFER_MAX_SAMPLES", "2000"))
TREND_BUFFER_MIN_SAMPLES = int(os.environ.get("MC_TREND_BUFFER_MIN_SAMPLES", "3"))

_capability_sample_history: Dict[str, List[tuple]] = {}  # name -> [(mono_ts, wall_ts, status_rank), ...]


def _record_capability_samples(capabilities: Dict[str, Dict[str, Any]]) -> None:
    mono_now = time.monotonic()
    wall_now = time.time()
    for name, cap in capabilities.items():
        buf = _capability_sample_history.setdefault(name, [])
        buf.append((mono_now, wall_now, _rank(cap.get("status", "HEALTHY"))))
        if len(buf) > TREND_BUFFER_MAX_SAMPLES:
            del buf[: len(buf) - TREND_BUFFER_MAX_SAMPLES]


def reset_trend_buffers() -> None:
    """Test/debug hook."""
    _capability_sample_history.clear()


def compute_capability_trend(name: str) -> Dict[str, Any]:
    """MC1.3 Phase C: direction/duration from the in-memory sample
    buffer. Compares the CURRENT sample's status rank against the oldest
    sample within each trend window still present in the buffer --
    'improving'/'degrading' means severity rank measurably decreased/
    increased across that span of ACTUAL recorded samples, never a
    forecast. Returns insufficient-history state (not a guessed trend)
    when too few samples exist or the buffer doesn't yet span the
    window."""
    buf = _capability_sample_history.get(name) or []
    if len(buf) < TREND_BUFFER_MIN_SAMPLES:
        return {
            "direction": "insufficient_history",
            "samples_collected": len(buf),
            "samples_required": TREND_BUFFER_MIN_SAMPLES,
        }

    mono_now, wall_now, current_rank = buf[-1]
    oldest_mono, oldest_wall, oldest_rank = buf[0]
    span_sec = mono_now - oldest_mono

    windows = {}
    for label, minutes in TREND_WINDOWS_MIN.items():
        window_sec = minutes * 60
        # Find the sample closest to (but not after) window_sec ago.
        target_mono = mono_now - window_sec
        candidate = None
        for sample in buf:
            if sample[0] <= target_mono:
                candidate = sample
            else:
                break
        if candidate is None:
            windows[label] = {"available": False}
            continue
        rank_then = candidate[2]
        if current_rank < rank_then:
            direction = "improving"
        elif current_rank > rank_then:
            direction = "degrading"
        else:
            direction = "stable"
        windows[label] = {"available": True, "direction": direction, "rank_then": rank_then, "rank_now": current_rank}

    # Overall direction: prefer the shortest window that actually has
    # data (most immediate signal), matching Phase C's "no prediction,
    # only measured direction" -- recent measured movement, not the
    # longest span available.
    # MC1.3 fix (found in testing): if NO window has data yet (buffer
    # hasn't spanned even the shortest 5m window), the direction must be
    # reported as insufficient_history, not silently defaulted to
    # "stable" -- "stable" is a measured claim (rank unchanged across a
    # real span of time) and must never be produced from zero actual
    # comparison points.
    overall_direction = "insufficient_history"
    for label in ("5m", "15m", "60m", "24h"):
        w = windows.get(label, {})
        if w.get("available"):
            overall_direction = w["direction"]
            break

    # "Duration in current direction/status": walk backward from the
    # most recent sample while the rank stays the same as `current_rank`,
    # so "how long has it been CRITICAL" is a measured span of real
    # samples, not an estimate.
    for sample in reversed(buf):
        if sample[2] == current_rank:
            duration_in_direction_sec = mono_now - sample[0]
        else:
            break

    return {
        "direction": overall_direction,
        "windows": windows,
        "samples_collected": len(buf),
        "buffer_span_secs": round(span_sec, 1),
        "duration_in_current_status_secs": round(duration_in_direction_sec, 1),
    }


def count_recent_events(event_type: str, window_min: int = RATE_WINDOW_MIN) -> int:
    """MC1.2 Phase B: observed event count in the rolling window -- the
    numerator evaluate_rate_signal needs to compute observed_rate_per_min.
    Read-only, indexed COUNT query (idx_ta_analyzed_at / idx_ta_migrated_at
    already exist), same table/columns/filters as the historical baseline
    and as main.py's own last_birth_age_secs/last_migration_age_secs
    computation -- not a new measurement concept, just a windowed count
    instead of a single most-recent timestamp. Returns 0 (not None) on
    any failure -- a failed count must never be silently treated as a
    healthy nonzero rate; combined with evaluate_rate_signal's ratio
    check, an erroneous 0 here is conservative (reads as a full outage,
    which is safe to surface and easy to notice/correct) rather than
    silently hiding a real problem."""
    col = _BASELINE_EVENT_COLUMNS.get(event_type)
    if not col:
        return 0
    timestamp_col, where_clause = col
    conn = None
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=3)
        cutoff = int(time.time()) - window_min * 60
        row = conn.execute(
            f"SELECT COUNT(*) FROM token_analysis WHERE {where_clause} AND {timestamp_col} >= ?",
            (cutoff,),
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ── Phase A/B: Capability + Evidence Engine ─────────────────────────────────

def _signal(name: str, abnormal: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "abnormal": bool(abnormal), "detail": detail}


def _evidence(signals: List[Dict[str, Any]]) -> Dict[str, int]:
    return {"abnormal": sum(1 for s in signals if s["abnormal"]), "total": len(signals)}


def _capability_result(status: str, signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "status": status,
        "degraded_by": None,  # filled in by _apply_propagation
        "evidence": _evidence(signals),
        "signals": signals,
    }


def _compute_live_ingestion(subsystems: Dict[str, Any]) -> Dict[str, Any]:
    ingestion = subsystems.get("ingestion") or {}
    now = int(time.time())

    birth_age = ingestion.get("last_birth_age_secs")
    mig_age = ingestion.get("last_migration_age_secs")
    birth_queue = ingestion.get("birth_queue_pending") or 0
    mig_queue = ingestion.get("migration_queue_pending") or 0
    pp_status = ingestion.get("pumpportal") or "UNKNOWN"
    ps_status = ingestion.get("pumpswap") or "UNKNOWN"
    listener_log_age = ingestion.get("listener_log_age_secs")

    # MC1.2 Phase B/C: observed event flow is now the PRIMARY signal.
    # count_recent_events() + get_expected_rate_per_min() together drive
    # evaluate_rate_signal()'s primary rate-based evaluation (falls back
    # to birth_age/mig_age silence only if the historical baseline is
    # unavailable, e.g. insufficient recent history) -- this is the exact
    # mechanism MC1.0 Section 5 always specified; MC1.2 completes it by
    # wiring in a real count and a real baseline instead of the
    # placeholder 0/None that made every prior evaluation fall through to
    # the silence-only path.
    birth_count = count_recent_events("births")
    birth_rate = evaluate_rate_signal(
        event_type="births",
        observed_count_in_window=birth_count,
        silence_secs=birth_age,
        fallback_silence_sec=BIRTH_SILENCE_FALLBACK_SEC,
    )
    # MC1.2A Phase B: migrations use the elapsed-time band policy, NOT the
    # rate-ratio logic births use (Phase A: birth logic is unchanged,
    # above). Migrations are naturally bursty -- a rate-ratio comparison
    # against a historical baseline treated a normal dormant period the
    # same as a genuine collapse. This is a calibration change only: same
    # underlying data (last_migration_age_secs, already read as mig_age),
    # different, purpose-built classification for that data.
    migration_rate = evaluate_migration_elapsed_policy(mig_age)

    # MC1.2 Phase C/D: connection status is SUPPORTING EVIDENCE only --
    # it contributes its own signal entries (visible diagnostic context)
    # but must never override or lower the severity the rate signals
    # already established. STALE/UNKNOWN are this frozen subsystem's
    # actual closest equivalents to the charter's "DISCONNECTED" example
    # (the ingestion block, which MC1.2 does not modify, only ever
    # produces UNKNOWN/RETRYING/CONNECTED/STALE) -- both are treated as
    # the more severe connection-signal case, RETRYING as the lesser one,
    # matching Case 2's "not connected -> CRITICAL-level signal" intent
    # without inventing a new status value in the frozen ingestion code.
    pp_disconnected = pp_status in ("STALE", "UNKNOWN")
    ps_disconnected = ps_status in ("STALE", "UNKNOWN")

    signals = [
        _signal("birth_rate_collapse", birth_rate["status"] != "HEALTHY", birth_rate["detail"]),
        # MC1.2A: abnormal only when migration_rate's status is itself
        # WARNING/CRITICAL under the calibrated elapsed-time policy --
        # HEALTHY (the "dormant" band) is NOT abnormal, so a normal
        # migration lull no longer contributes an abnormal evidence signal
        # or opens an incident (Phase E: "a normal migration lull must
        # never create a CRITICAL incident").
        _signal("migration_health", migration_rate["status"] not in ("HEALTHY", "UNKNOWN"), migration_rate["detail"]),
        _signal("pumpportal_connection", pp_status == "RETRYING" or pp_disconnected, pp_status),
        _signal("pumpswap_connection", ps_status == "RETRYING" or ps_disconnected, ps_status),
        _signal(
            "listener_log_freshness",
            listener_log_age is not None and listener_log_age > 300,
            f"listener_log_age_secs={listener_log_age}" if listener_log_age is not None else "unknown",
        ),
        _signal(
            "queue_backlog",
            (birth_queue > 5) or (mig_queue > 5),
            f"birth_queue_pending={birth_queue} migration_queue_pending={mig_queue}",
        ),
    ]

    # MC1.2 Phase C, calibrated by MC1.2A Phase E: birth flow is the
    # PRIMARY driver of Live Ingestion severity. Migration health is
    # supporting context -- it is included in the same _max_status() call
    # (so it CAN still independently raise severity, per Phase E: "unless
    # it independently exceeds its calibrated thresholds"), but because
    # migration_rate's status now comes from the calibrated elapsed-time
    # policy above (HEALTHY through the whole normal 0-20min dormant
    # band), a normal migration lull contributes HEALTHY here and can
    # never floor/raise the overall status -- exactly MC1.2A's "a normal
    # migration lull must never create a CRITICAL incident" requirement.
    # Only a migration silence that genuinely exceeds MIGRATION_CONCERN_MAX_MIN
    # (calibrated, configurable) still raises this to CRITICAL, same as
    # any other independently-abnormal signal. Connection state and
    # listener freshness remain supporting evidence exactly as MC1.2 left
    # them -- _max_status can only raise, never lower, so a CONNECTED
    # socket can never mask a collapsed rate.
    status = _max_status(
        birth_rate["status"],
        migration_rate["status"],
        "CRITICAL" if listener_log_age is not None and listener_log_age > 600 else "HEALTHY",
        "CRITICAL" if pp_disconnected or ps_disconnected else ("WARNING" if pp_status == "RETRYING" or ps_status == "RETRYING" else "HEALTHY"),
        "WARNING" if (birth_queue > 5 or mig_queue > 5) else "HEALTHY",
    )
    result = _capability_result(status, signals)
    # MC1.2 Phase E: expose the raw observed/expected rate numbers
    # directly (not just the signal detail string) so the dashboard card
    # can promote them to headline operator metrics without re-deriving
    # or re-querying anything -- same values evaluate_rate_signal already
    # computed, just also surfaced at the capability level.
    result["flow_metrics"] = {
        "births": {
            "observed_per_min": birth_rate.get("observed_rate_per_min"),
            "expected_per_min": birth_rate.get("expected_rate_per_min"),
            "mode": birth_rate.get("mode"),
            "last_birth_age_secs": birth_age,
        },
        "migrations": {
            # MC1.2A: migrations no longer carry a rate-ratio mode/observed-
            # vs-expected pair -- they're evaluated on elapsed-time bands
            # (see evaluate_migration_elapsed_policy). "mode" is fixed at
            # "elapsed_policy" so the dashboard can tell this apart from
            # births' "rate"/"silence_fallback" modes and render migration
            # messaging (Phase C/D) instead of a baseline comparison.
            "mode": "elapsed_policy",
            "status": migration_rate.get("status"),
            "band": migration_rate.get("band"),
            "elapsed_min": migration_rate.get("elapsed_min"),
            "last_migration_age_secs": mig_age,
        },
    }
    # MC1.3 Phase B: real-data trend (5m/15m/60m/24h rates + measured
    # direction), computed the same way as flow_metrics above -- no new
    # persistence, genuine historical event data.
    result["trend"] = {
        "births": compute_live_ingestion_trend("births"),
        "migrations": compute_live_ingestion_trend("migrations"),
    }
    return result


def _compute_creator_funding(subsystems: Dict[str, Any]) -> Dict[str, Any]:
    intelligence = subsystems.get("intelligence") or {}

    worker_status = intelligence.get("funding_worker_status") or "UNKNOWN"
    hb_age = intelligence.get("funding_worker_heartbeat_age_secs")
    oldest_age = intelligence.get("funding_queue_oldest_pending_age_secs")
    pending = intelligence.get("funding_queue_pending") or 0

    signals = [
        _signal(
            "worker_status",
            worker_status not in ("RUNNING",),
            worker_status,
        ),
        _signal(
            "heartbeat_freshness",
            hb_age is not None and hb_age > 120,
            f"{hb_age}s (threshold 120s)" if hb_age is not None else "unknown",
        ),
        _signal(
            "oldest_eligible_age",
            oldest_age is not None and oldest_age > 3600,
            f"{oldest_age}s (threshold 3600s)" if oldest_age is not None else "unknown",
        ),
        _signal(
            "queue_backlog_growth",
            False,  # MC1.0 Section 4: trend detection deferred -- no new
            # measurement added by this module; current pending count
            # alone is not evidence of GROWTH (X78.15 established large
            # pending counts are expected/normal). Left as an always-
            # normal placeholder signal so the evidence denominator (4)
            # matches MC1.0's Section 10 worked example without asserting
            # an abnormal condition this module cannot actually measure.
            "normal" if pending is not None else "unknown",
        ),
    ]

    status = "HEALTHY"
    if worker_status not in ("RUNNING",) and pending > 0:
        status = "CRITICAL" if worker_status == "STOPPED" else "WARNING"
    elif oldest_age is not None and oldest_age > 3600:
        status = "WARNING"

    return _capability_result(status, signals)


def _compute_operational_intelligence(subsystems: Dict[str, Any]) -> Dict[str, Any]:
    intelligence = subsystems.get("intelligence") or {}

    wp_age = intelligence.get("watch_pipeline_age_secs")
    wp_interval = intelligence.get("watch_pipeline_interval_secs") or 300
    cp_age = intelligence.get("crq_worker_age_secs")
    crq_failed = intelligence.get("creator_queue_failed") or 0
    missing = intelligence.get("missing_creators_1h") or 0

    signals = [
        _signal(
            "watch_pipeline_freshness",
            wp_age is not None and wp_age > wp_interval * 2,
            f"{wp_age}s (interval {wp_interval}s)" if wp_age is not None else "unknown",
        ),
        _signal(
            "creator_resolution_freshness",
            cp_age is not None and cp_age > 120,
            f"{cp_age}s (threshold 120s)" if cp_age is not None else "unknown",
        ),
        _signal(
            "resolution_failures",
            crq_failed > 5,
            f"{crq_failed} failed (threshold 5)",
        ),
        _signal(
            "missing_creator_attribution",
            missing > 0,
            f"{missing} migrated tokens missing creator in last 1h",
        ),
    ]

    status = "HEALTHY"
    if crq_failed > 5:
        status = "WARNING"
    elif wp_age is not None and wp_age > wp_interval * 2:
        status = "WARNING"
    elif cp_age is not None and cp_age > 120:
        status = "WARNING"

    return _capability_result(status, signals)


def _compute_watchtower(subsystems: Dict[str, Any]) -> Dict[str, Any]:
    cascade_infra = subsystems.get("cascade_infrastructure") or {}
    cascade_activity = subsystems.get("cascade_activity") or {}

    infra_status = cascade_infra.get("status") or "UNKNOWN"
    hb_age = cascade_infra.get("heartbeat_age_secs")
    subs_total = cascade_infra.get("subs_total") or 0

    signals = [
        _signal("cascade_connection", infra_status == "OFFLINE", infra_status),
        _signal(
            "cascade_heartbeat_freshness",
            hb_age is not None and hb_age > 120,
            f"{hb_age}s (threshold 120s)" if hb_age is not None else "unknown",
        ),
        _signal("subscriptions_active", subs_total == 0, f"subs_total={subs_total}"),
    ]

    status = "HEALTHY"
    if infra_status == "OFFLINE":
        status = "CRITICAL"
    elif infra_status == "DEGRADED" or subs_total == 0:
        status = "WARNING"

    return _capability_result(status, signals)


def _compute_infrastructure(subsystems: Dict[str, Any]) -> Dict[str, Any]:
    database = subsystems.get("database") or {}
    api_health = subsystems.get("api") or {}

    db_status = database.get("status") or "UNKNOWN"
    p99 = database.get("p99_wait_ms") or 0
    q_depth = database.get("serializer_queue_depth") or 0
    gunicorn_alive = api_health.get("gunicorn_alive", True)
    errors_5m = api_health.get("errors_5m") or 0

    signals = [
        _signal("database_pressure", db_status in ("AT_RISK", "CRITICAL", "PRESSURE"), db_status),
        _signal("write_lane_p99", p99 > 5000, f"p99={p99}ms"),
        _signal("api_process_alive", not gunicorn_alive, "gunicorn_alive" if gunicorn_alive else "gunicorn not alive"),
    ]

    status = "HEALTHY"
    if db_status == "CRITICAL" or not gunicorn_alive:
        status = "CRITICAL"
    elif db_status in ("AT_RISK", "PRESSURE") or errors_5m > 5 or q_depth > 10:
        status = "WARNING"

    return _capability_result(status, signals)


def _compute_price_tracking(subsystems: Dict[str, Any]) -> Dict[str, Any]:
    price_worker = subsystems.get("price_worker") or {}

    pw_status = price_worker.get("status") or "UNKNOWN"
    peak_age = price_worker.get("last_peak_update_age_secs")
    snap_age = price_worker.get("last_snapshot_write_age_secs")
    snap_expected = price_worker.get("snapshot_expected")

    signals = [
        _signal("worker_alive", pw_status == "DOWN", pw_status),
        _signal(
            "peak_update_freshness",
            peak_age is not None and peak_age > 120,
            f"{peak_age}s (threshold 120s)" if peak_age is not None else "unknown",
        ),
        _signal(
            "snapshot_freshness",
            bool(snap_expected) and snap_age is not None and snap_age > 120,
            f"{snap_age}s (threshold 120s)" if snap_age is not None else "unknown",
        ),
    ]

    # PEAK-ONLY is an intentionally-degraded, non-alarming state (MC1.0
    # Section 3) -- it must not classify as WARNING/CRITICAL on its own.
    status = "HEALTHY"
    if pw_status == "DOWN":
        status = "CRITICAL"
    elif pw_status in ("STALE", "DEGRADED"):
        status = "WARNING"

    return _capability_result(status, signals)


_COMPUTE_FN = {
    "live_ingestion": _compute_live_ingestion,
    "creator_funding": _compute_creator_funding,
    "operational_intelligence": _compute_operational_intelligence,
    "watchtower": _compute_watchtower,
    "infrastructure": _compute_infrastructure,
    "price_tracking": _compute_price_tracking,
}


def _apply_propagation(capabilities: Dict[str, Dict[str, Any]]) -> None:
    """MC1.0 Section 3: loss of an upstream capability floors (does not
    override) a downstream capability's displayed severity. Mutates in
    place. Order-dependent -- must be applied top-down through the
    hierarchy (live_ingestion -> creator_funding -> operational_intelligence
    -> watchtower), which _UPSTREAM_OF's structure guarantees via a
    single linear walk since each capability has at most one upstream."""
    for name in ("creator_funding", "operational_intelligence", "watchtower"):
        upstream_name = _UPSTREAM_OF.get(name)
        if not upstream_name:
            continue
        upstream = capabilities.get(upstream_name)
        own = capabilities.get(name)
        if not upstream or not own:
            continue
        if _rank(upstream["status"]) >= _rank("WARNING"):
            # degraded_by is set whenever the upstream is itself
            # abnormal, REGARDLESS of whether it changes the downstream's
            # own status -- a capability that is independently CRITICAL
            # for its own reasons is STILL also being degraded by an
            # upstream problem if one exists, and the playbook (MC1.0
            # Section 13 step 4) explicitly expects operators to see that
            # annotation to know both are in play. Only the STATUS floor
            # (not the annotation) is conditional on the upstream being
            # more severe than the capability's own independent status.
            own["degraded_by"] = upstream_name
            # Floor to WARNING, never silently escalate past the
            # capability's own independently-computed status if that
            # status is already higher (own CRITICAL stays CRITICAL, not
            # downgraded to WARNING) -- MC1.0 Section 3's explicit
            # "never suppressed" requirement.
            own["status"] = _max_status(own["status"], "WARNING")


def compute_capabilities(subsystems: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Phase A entry point. Deterministic given `subsystems` and the
    real-data sources it reads (token_analysis for Live Ingestion's own
    trend, computed inside _compute_live_ingestion) -- identical inputs
    always produce identical capability status/evidence/signals.

    MC1.3 note: this function now has ONE deliberate, documented side
    effect -- it appends one sample to the in-memory rolling trend buffer
    (_record_capability_samples) for the 5 capabilities that have no
    other source of historical data (see Phase C's module-level comment
    for why). This does not affect status/evidence/signals determinism;
    it only feeds compute_capability_trend()'s buffer, called separately
    by the caller (main.py) after this function returns. Live Ingestion's
    own `trend` key is populated INSIDE _compute_live_ingestion from real
    historical data, not from this buffer -- the generic buffer-based
    trend is only attached to the other 5 capabilities, below."""
    capabilities = {name: _COMPUTE_FN[name](subsystems) for name in CAPABILITY_NAMES}
    _apply_propagation(capabilities)
    _record_capability_samples(capabilities)
    for name, cap in capabilities.items():
        if "trend" not in cap:  # live_ingestion already set its own real-data trend
            cap["trend"] = compute_capability_trend(name)
    return capabilities


# ── Phase C: Incident Engine (stateless) ────────────────────────────────────

# In-memory, per-process incident-start tracking -- the explicitly
# smaller-scope alternative to persistence named in MC1.0 Section 11 for
# composite capabilities without a single monotonic timestamp to derive
# first_detected_at from statelessly. Resets on process restart (accepted
# limitation, documented in MC1.0 Section 11 and MC1.1's instruction to
# implement the stateless option only).
_incident_start_cache: Dict[str, float] = {}


def _first_detected_at(capability_name: str, is_active: bool) -> Optional[float]:
    if not is_active:
        _incident_start_cache.pop(capability_name, None)
        return None
    if capability_name not in _incident_start_cache:
        _incident_start_cache[capability_name] = time.time()
    return _incident_start_cache[capability_name]


_IMPACT_LABELS = {
    "birth_rate_collapse": "Birth rate collapsed",
    "migration_health": "Migration activity below expected profile",
    "pumpportal_connection": "PumpPortal unavailable",
    "pumpswap_connection": "PumpSwap unavailable",
    "listener_log_freshness": "Listener unhealthy",
    "queue_backlog": "Queue backlog growing",
    "worker_status": "Worker not running",
    "heartbeat_freshness": "Worker heartbeat stale",
    "oldest_eligible_age": "Oldest eligible work stalled",
    "cascade_connection": "Cascade infrastructure offline",
    "cascade_heartbeat_freshness": "Cascade heartbeat stale",
    "subscriptions_active": "No active subscriptions",
    "database_pressure": "Database under pressure",
    "write_lane_p99": "Database write latency elevated",
    "api_process_alive": "API process not responding",
    "worker_alive": "Price worker down",
    "peak_update_freshness": "Peak price updates stale",
    "snapshot_freshness": "Price snapshots stale",
    "queue_backlog_growth": "Funding queue backlog growing",
    "watch_pipeline_freshness": "Watch pipeline stale",
    "creator_resolution_freshness": "Creator resolution worker stale",
    "resolution_failures": "Creator resolution failures elevated",
    "missing_creator_attribution": "Missing creator attribution",
}

_TITLE_LABELS = {
    "live_ingestion": "Live ingestion unavailable",
    "creator_funding": "Creator funding degraded",
    "operational_intelligence": "Operational intelligence degraded",
    "watchtower": "WATCHTOWER degraded",
    "infrastructure": "Infrastructure degraded",
    "price_tracking": "Price tracking degraded",
}


def compute_incidents(capabilities: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Phase C entry point. MC1.0 Section 7: one capability -> one
    incident, recomputed on every poll (membership is not mutable state
    -- only first_detected_at is tracked, per Section 11's stateless
    option). Multiple abnormal signals within one capability's evidence
    list become that ONE incident's impact/contributing_signals lists,
    never separate incidents (Section 7.4). Different capabilities never
    merge into one incident (Section 7.5) even if concurrent.

    A capability whose only abnormal status comes from upstream
    propagation (degraded_by is set AND it has zero of its own abnormal
    evidence signals) does NOT open its own incident -- MC1.0 Section 13
    (operator playbook) is explicit that this case is "a propagated
    annotation, not a second incident." Only capabilities with their own
    independent abnormal evidence open an incident card; propagation is
    still fully visible via each capability tile's `degraded_by` field
    (Phase G's capability grid), just not duplicated as a second incident."""
    now = int(time.time())
    incidents: List[Dict[str, Any]] = []

    for name in CAPABILITY_NAMES:
        cap = capabilities.get(name) or {}
        status = cap.get("status", "HEALTHY")
        signals = cap.get("signals") or []
        abnormal_signals = [s for s in signals if s.get("abnormal")]

        is_active = _rank(status) >= _rank("WARNING") and (
            cap.get("degraded_by") is None or len(abnormal_signals) > 0
        )

        started_at = _first_detected_at(name, is_active)
        if not is_active or started_at is None:
            continue

        impact = [_IMPACT_LABELS.get(s["name"], s["name"]) for s in abnormal_signals]
        contributing = [f'{_IMPACT_LABELS.get(s["name"], s["name"])}: {s.get("detail", "")}' for s in abnormal_signals]

        first_detected_at = int(started_at)
        incidents.append({
            "id": f"{name}:{first_detected_at}",
            "capability": name,
            "severity": status,
            "title": _TITLE_LABELS.get(name, name),
            "impact": impact,
            "contributing_signals": contributing,
            "first_detected_at": first_detected_at,
            "current_duration_secs": max(0, now - first_detected_at),
            "recovered_at": None,
        })

    return incidents


# ── Phase E: Platform Status ─────────────────────────────────────────────────

def compute_platform_status(capabilities: Dict[str, Dict[str, Any]]) -> str:
    """MC1.0 Section 8: platform-level status is the maximum severity
    across all capabilities. Replaces the prior subsystem-level if/elif
    chain as the SOURCE for the existing top-level `status` field --
    the field's name/shape in the API response is unchanged (Phase F),
    only its derivation."""
    return _max_status(*(cap.get("status", "HEALTHY") for cap in capabilities.values()))
