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
    transitions = []
    prev_status = None

    print(f"\n=== Replay: {event_type} — last {hours}h, {lookback_days}d trailing baseline ===")
    print(f"{'window':>10} {'observed/min':>14} {'baseline/min':>14} {'status':>10}")

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
        baseline_str = f"{baseline:.2f}" if baseline else "None"
        print(f"{w:>10} {observed:>14.2f} {baseline_str:>14} {status:>10}")

    print(f"\nSummary: {dict(counts)}")
    print(f"Transitions ({len(transitions)}):")
    for w, frm, to in transitions:
        print(f"  window {w}: {frm} -> {to}")

    return {"counts": dict(counts), "transitions": transitions}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--lookback-days", type=float, default=mc.BASELINE_LOOKBACK_DAYS)
    args = ap.parse_args()

    replay("births", args.hours, args.lookback_days)
    replay("migrations", args.hours, args.lookback_days)


if __name__ == "__main__":
    main()
