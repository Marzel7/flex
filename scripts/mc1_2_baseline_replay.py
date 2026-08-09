#!/usr/bin/env python3
"""MC1.2 Phase G — historical replay validation for the Live Ingestion
flow-health baseline/rate engine (src/ops/mission_control_capabilities.py).

Read-only. Walks forward through real historical birth timestamps,
computing a trailing baseline AT EACH POINT IN TIME (never looking
ahead) exactly the way the live system would, and classifies each
15-minute window the same way evaluate_rate_signal() does. Prints a
window-by-window trace plus a summary, so the algorithm's behavior on
real production data (not synthetic fixtures) can be inspected directly.

This script does not modify anything -- read-only queries against the
existing database, no writes, no ingestion interaction.

Usage:
    python -m scripts.mc1_2_baseline_replay [--hours N] [--lookback-days D]
"""
from __future__ import annotations

import argparse
import collections
import os
import sqlite3
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import src.ops.mission_control_capabilities as mc

DB_PATH = os.environ.get(
    "DB_PATH", os.path.join(_REPO_ROOT, "database", "flex_complete_database.db")
)


def _load_bucket_counts(event_type: str, bucket_sec: int) -> dict[int, int]:
    col, where = mc._BASELINE_EVENT_COLUMNS[event_type]
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    try:
        rows = conn.execute(
            f"SELECT CAST({col} AS INTEGER) / {bucket_sec} AS bucket, COUNT(*) AS n "
            f"FROM token_analysis WHERE {where} GROUP BY bucket"
        ).fetchall()
    finally:
        conn.close()
    return {int(b): int(n) for b, n in rows}


def _walk_forward_baseline(buckets: dict[int, int], w: int, lookback_windows: int, min_nonzero: int) -> float | None:
    lookback = [buckets.get(x, 0) for x in range(w - lookback_windows, w)]
    nonzero = sorted(v for v in lookback if v > 0)
    if len(nonzero) < min_nonzero:
        return None
    n = len(nonzero)
    median = nonzero[n // 2] if n % 2 == 1 else (nonzero[n // 2 - 1] + nonzero[n // 2]) / 2.0
    return median / (mc.BASELINE_BUCKET_MIN)


def _trend_direction(observed: float, prior_observed: float) -> str:
    """MC1.3 Phase B's exact classification, reproduced here for replay
    against historical windows (compute_live_ingestion_trend itself
    always compares against real 'now', so it can't be pointed at an
    arbitrary past window directly -- this mirrors its logic precisely
    so Phase G can validate historical transitions)."""
    if prior_observed <= 0 and observed <= 0:
        return "stable"
    if prior_observed <= 0:
        return "improving"
    delta_ratio = (observed - prior_observed) / prior_observed
    if delta_ratio > mc.TREND_DIRECTION_EPSILON:
        return "improving"
    if delta_ratio < -mc.TREND_DIRECTION_EPSILON:
        return "degrading"
    return "stable"


def replay(event_type: str, hours: int, lookback_days: float) -> dict:
    bucket_sec = mc.BASELINE_BUCKET_MIN * 60
    buckets = _load_bucket_counts(event_type, bucket_sec)
    if not buckets:
        print(f"No {event_type} data found.")
        return {}

    last_w = max(buckets)
    lookback_windows = int(lookback_days * 86400 / bucket_sec)
    replay_windows = int(hours * 60 / mc.BASELINE_BUCKET_MIN)

    counts = collections.Counter()
    trend_counts = collections.Counter()
    transitions = []
    trend_reversals = []
    prev_status = None
    prev_observed = None
    prev_trend = None

    print(f"\n=== Replay: {event_type} — last {hours}h, {lookback_days}d trailing baseline ===")
    print(f"{'window':>10} {'observed/min':>14} {'baseline/min':>14} {'status':>10} {'trend':>12}")

    for w in range(last_w - replay_windows, last_w + 1):
        baseline = _walk_forward_baseline(buckets, w, lookback_windows, mc.BASELINE_MIN_NONZERO_BUCKETS)
        observed = buckets.get(w, 0) / float(mc.BASELINE_BUCKET_MIN)
        if baseline and baseline > 0:
            ratio = observed / baseline
            if ratio < mc.RATE_CRITICAL_RATIO:
                status = "CRITICAL"
            elif ratio < mc.RATE_WARNING_RATIO:
                status = "WARNING"
            else:
                status = "HEALTHY"
        else:
            status = "NO_BASELINE"
        counts[status] += 1
        if status != prev_status:
            transitions.append((w, prev_status, status))
        prev_status = status

        # Phase G: trend direction, comparing this window's observed rate
        # against the IMMEDIATELY PRIOR window (mirrors compute_live_ingestion_trend's
        # current-vs-prior-5m comparison, generalized to this replay's
        # bucket size).
        trend = _trend_direction(observed, prev_observed) if prev_observed is not None else "insufficient_history"
        trend_counts[trend] += 1
        if prev_trend is not None and trend != prev_trend and trend != "stable" and prev_trend != "stable" and trend != prev_trend:
            # A "reversal" here means direction flipped between the two
            # genuinely opposite states (improving<->degrading), not a
            # transition through "stable" -- flagged separately below as
            # the interesting case Phase G's "no false reversals" gate
            # cares about.
            if {trend, prev_trend} == {"improving", "degrading"}:
                trend_reversals.append((w, prev_trend, trend))
        prev_trend = trend
        prev_observed = observed

        baseline_str = f"{baseline:.2f}" if baseline else "None"
        print(f"{w:>10} {observed:>14.2f} {baseline_str:>14} {status:>10} {trend:>12}")

    print(f"\nStatus summary: {dict(counts)}")
    print(f"Trend summary: {dict(trend_counts)}")
    print(f"Status transitions ({len(transitions)}):")
    for w, frm, to in transitions:
        print(f"  window {w}: {frm} -> {to}")
    print(f"Direct improving<->degrading trend reversals ({len(trend_reversals)}):")
    for w, frm, to in trend_reversals:
        print(f"  window {w}: {frm} -> {to}")

    return {
        "counts": dict(counts),
        "trend_counts": dict(trend_counts),
        "transitions": transitions,
        "trend_reversals": trend_reversals,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--lookback-days", type=float, default=mc.BASELINE_LOOKBACK_DAYS)
    args = ap.parse_args()

    replay("births", args.hours, args.lookback_days)
    replay("migrations", args.hours, args.lookback_days)


if __name__ == "__main__":
    main()
