"""Gap-aware, observed-only pre-peak market-cap metrics.

This module consumes normalized OHLCV observations supplied by the existing
Birdeye path.  It neither requests data nor persists raw candles.  Values are
explicitly *observed*: a sparse response is useful for bounded analysis but
never promoted to complete provider history.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


OBSERVED_SPARSE = "OBSERVED_SPARSE"
OBSERVED_CONTIGUOUS = "OBSERVED_CONTIGUOUS"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def _timestamp(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _normalized(candles: Iterable[Mapping[str, Any]], start: int, end: int) -> list[dict[str, float | int]]:
    """Keep only positive OHLC observations inside the requested window.

    A candle's high and low share its interval timestamp.  Callers must not
    infer an order between them; the drawdown reducer below only compares a
    low with highs from strictly earlier intervals.
    """
    normalized: list[dict[str, float | int]] = []
    for candle in candles:
        timestamp = _timestamp(candle.get("timestamp", candle.get("unixTime")))
        opening = _number(candle.get("open", candle.get("o")))
        high = _number(candle.get("high", candle.get("h")))
        low = _number(candle.get("low", candle.get("l")))
        close = _number(candle.get("close", candle.get("c")))
        if timestamp is None or not start <= timestamp < end or None in (opening, high, low, close):
            continue
        if low > high:
            continue
        normalized.append({"timestamp": timestamp, "open": opening, "high": high, "low": low, "close": close})
    # Duplicate timestamps cannot establish additional chronological evidence.
    deduped = {int(candle["timestamp"]): candle for candle in normalized}
    return [deduped[timestamp] for timestamp in sorted(deduped)]


def _coverage(candles: list[dict[str, float | int]], start: int, end: int, interval_seconds: int) -> dict[str, Any]:
    expected = range(start, end, interval_seconds)
    observed_timestamps = {int(candle["timestamp"]) for candle in candles}
    missing = [timestamp for timestamp in expected if timestamp not in observed_timestamps]
    runs: list[int] = []
    current = 0
    for timestamp in expected:
        if timestamp in observed_timestamps:
            if current:
                runs.append(current)
                current = 0
        else:
            current += 1
    if current:
        runs.append(current)
    missing_count = len(missing)
    qualification = (
        OBSERVED_CONTIGUOUS if len(candles) >= 2 and missing_count == 0
        else OBSERVED_SPARSE if len(candles) >= 2
        else INSUFFICIENT_EVIDENCE
    )
    return {
        "qualification_status": qualification,
        "requested_window_start": start,
        "requested_window_end": end,
        "candle_interval_seconds": interval_seconds,
        "qualifying_candle_count": len(candles),
        "first_returned_candle_timestamp": int(candles[0]["timestamp"]) if candles else None,
        "last_returned_candle_timestamp": int(candles[-1]["timestamp"]) if candles else None,
        "missing_interval_count": missing_count,
        "missing_interval_duration_seconds": missing_count * interval_seconds,
        "largest_unobserved_gap_seconds": max(runs, default=0) * interval_seconds,
        "coverage_is_complete": missing_count == 0,
    }


def calculate_prepeak_observed_metrics(
    candles: Iterable[Mapping[str, Any]], *, opening_market_cap: float | int | None,
    opening_timestamp: int | None, observed_peak_market_cap: float | int | None,
    observed_peak_timestamp: int | None, requested_window_start: int,
    requested_window_end: int, candle_interval_seconds: int = 60,
    provenance: str = "BIRDEYE_OHLCV_MCAP",
) -> dict[str, Any]:
    """Calculate conservative pre-peak metrics from ordered, possibly sparse candles.

    A drawdown low is compared only to a high from an earlier interval. Recovery
    requires the close of a later interval to meet that preceding high, avoiding
    an intraminute high/low ordering claim.
    """
    if requested_window_end <= requested_window_start or candle_interval_seconds <= 0:
        raise ValueError("requested window and candle interval must be positive")
    opening = _number(opening_market_cap)
    opening_at = _timestamp(opening_timestamp)
    peak = _number(observed_peak_market_cap)
    peak_at = _timestamp(observed_peak_timestamp)
    normalized = _normalized(candles, requested_window_start, requested_window_end)
    coverage = _coverage(normalized, requested_window_start, requested_window_end, candle_interval_seconds)
    result: dict[str, Any] = {
        "evidence_source": provenance,
        "observation_coverage": coverage,
        "lowest_observed_market_cap": None,
        "lowest_observed_timestamp": None,
        "below_opening_market_cap": None,
        "maximum_observed_prepeak_drawdown_percent": None,
        "drawdown_preceding_high_market_cap": None,
        "drawdown_preceding_high_timestamp": None,
        "drawdown_subsequent_low_market_cap": None,
        "drawdown_subsequent_low_timestamp": None,
        "recovery_time_seconds": None,
        "opening_to_observed_peak_multiple": peak / opening if opening and peak and opening_at is not None and peak_at is not None and opening_at <= peak_at else None,
    }
    if coverage["qualification_status"] == INSUFFICIENT_EVIDENCE:
        return result

    lowest = min(normalized, key=lambda candle: (float(candle["low"]), int(candle["timestamp"])))
    result["lowest_observed_market_cap"] = lowest["low"]
    result["lowest_observed_timestamp"] = lowest["timestamp"]
    result["below_opening_market_cap"] = bool(opening is not None and any(float(candle["low"]) < opening for candle in normalized))

    # The external opening is a point observation that can seed a later loss;
    # each candle high is deliberately admitted only *after* its own low.
    preceding_high: dict[str, float | int] | None = (
        {"value": opening, "timestamp": opening_at, "source": "QUALIFIED_OPENING_BASELINE"}
        if opening is not None and opening_at is not None and opening_at <= int(normalized[0]["timestamp"])
        else None
    )
    maximum: dict[str, float | int] | None = None
    for candle in normalized:
        if preceding_high is not None and float(candle["low"]) < float(preceding_high["value"]):
            drawdown = (float(preceding_high["value"]) - float(candle["low"])) * 100.0 / float(preceding_high["value"])
            if maximum is None or drawdown > float(maximum["drawdown"]):
                maximum = {"drawdown": drawdown, "high": preceding_high["value"], "high_timestamp": preceding_high["timestamp"], "low": candle["low"], "low_timestamp": candle["timestamp"]}
        if preceding_high is None or float(candle["high"]) > float(preceding_high["value"]):
            preceding_high = {"value": candle["high"], "timestamp": candle["timestamp"], "source": "CANDLE_HIGH"}

    if maximum is None:
        return result
    result.update({
        "maximum_observed_prepeak_drawdown_percent": maximum["drawdown"],
        "drawdown_preceding_high_market_cap": maximum["high"],
        "drawdown_preceding_high_timestamp": maximum["high_timestamp"],
        "drawdown_subsequent_low_market_cap": maximum["low"],
        "drawdown_subsequent_low_timestamp": maximum["low_timestamp"],
    })
    low_at = int(maximum["low_timestamp"])
    high = float(maximum["high"])
    recovery = next((int(candle["timestamp"]) for candle in normalized if int(candle["timestamp"]) > low_at and float(candle["close"]) >= high), None)
    result["recovery_time_seconds"] = recovery - low_at if recovery is not None else None
    return result
