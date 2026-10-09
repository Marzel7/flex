"""Provider-free, lower-bound-only Watchtower post-entry MC minima.

This module deliberately does not acquire, persist, or project lifecycle facts.
It describes the lowest valid one-minute MCAP candle low *returned* for a fully
post-entry portion of a requested window.  Missing buckets remain evidence gaps;
they are never filled or treated as no-trade intervals.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


ONE_MINUTE = 60
WINDOW_SECONDS = (5 * 60, 15 * 60, 60 * 60)
CONTRACT_VERSION = "WATCHTOWER_PARTIAL_OBSERVED_MINIMUM_V1"


@dataclass(frozen=True)
class ObservedMinimum:
    """Compact lower-bound evidence for one fully post-entry time window."""

    window_seconds: int
    minimum_status: str
    coverage_status: str
    observed_minimum_mc_usd: float | None
    observed_minimum_timestamp: int | None
    observed_minimum_offset_seconds: int | None
    observed_drawdown_percent: float | None
    candle_interval_seconds: int
    expected_bucket_count: int
    valid_bucket_count: int
    missing_bucket_timestamps: tuple[int, ...]
    invalid_bucket_timestamps: tuple[int, ...]
    provenance: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_seconds": self.window_seconds,
            "minimum_status": self.minimum_status,
            "coverage_status": self.coverage_status,
            "observed_minimum_mc_usd": self.observed_minimum_mc_usd,
            "observed_minimum_timestamp": self.observed_minimum_timestamp,
            "observed_minimum_offset_seconds": self.observed_minimum_offset_seconds,
            "observed_drawdown_percent": self.observed_drawdown_percent,
            "candle_interval_seconds": self.candle_interval_seconds,
            "expected_bucket_count": self.expected_bucket_count,
            "valid_bucket_count": self.valid_bucket_count,
            "missing_bucket_timestamps": list(self.missing_bucket_timestamps),
            "invalid_bucket_timestamps": list(self.invalid_bucket_timestamps),
            "provenance": self.provenance,
        }


def _positive_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _first_wholly_post_entry_bucket(entry_timestamp: int) -> int:
    return ((entry_timestamp + ONE_MINUTE - 1) // ONE_MINUTE) * ONE_MINUTE


def _expected_buckets(entry_timestamp: int, window_seconds: int) -> tuple[int, ...]:
    """Return only complete one-minute buckets contained in the requested window."""
    start = _first_wholly_post_entry_bucket(entry_timestamp)
    end = entry_timestamp + window_seconds
    return tuple(bucket for bucket in range(start, end, ONE_MINUTE) if bucket + ONE_MINUTE <= end)


def _validated_lows(candles: Iterable[Mapping[str, Any]]) -> tuple[dict[int, float | None], tuple[int, ...]]:
    """Validate provider order/identity without silently repairing it."""
    values: dict[int, float | None] = {}
    previous: int | None = None
    for candle in candles:
        timestamp = candle.get("timestamp")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp % ONE_MINUTE:
            raise ValueError("INVALID_ONE_MINUTE_CANDLE_TIMESTAMP")
        if previous is not None and timestamp <= previous:
            raise ValueError("DUPLICATE_OR_OUT_OF_ORDER_CANDLES")
        previous = timestamp
        values[timestamp] = _positive_number(candle.get("low_mc_usd"))
    return values, tuple(values)


def observed_minima(
    *,
    entry_timestamp: int,
    entry_mc_usd: float,
    candles: Iterable[Mapping[str, Any]],
    provider_provenance: str,
    windows: tuple[int, ...] = WINDOW_SECONDS,
) -> dict[str, Any]:
    """Build compact observed-minimum evidence without changing any lifecycle fact.

    Candles must be provider-ordered, unique, one-minute bucket starts.  A
    bucket that begins before entry or ends after a requested window is excluded;
    therefore a candle cannot leak pre-entry or post-window movement into an
    observed minimum.  Sparse output remains explicitly partial.
    """
    if isinstance(entry_timestamp, bool) or not isinstance(entry_timestamp, int) or entry_timestamp <= 0:
        raise ValueError("INVALID_ENTRY_TIMESTAMP")
    entry = _positive_number(entry_mc_usd)
    if entry is None:
        raise ValueError("INVALID_ENTRY_MC")
    if not provider_provenance:
        raise ValueError("MISSING_PROVIDER_PROVENANCE")
    if not windows or any(window <= 0 or window % ONE_MINUTE for window in windows):
        raise ValueError("INVALID_WINDOWS")

    lows, _ = _validated_lows(candles)
    results: dict[str, dict[str, Any]] = {}
    for window in windows:
        expected = _expected_buckets(entry_timestamp, window)
        valid = {timestamp: lows[timestamp] for timestamp in expected if lows.get(timestamp) is not None}
        invalid = tuple(timestamp for timestamp in expected if timestamp in lows and lows[timestamp] is None)
        missing = tuple(timestamp for timestamp in expected if timestamp not in lows)
        coverage = "COMPLETE_OBSERVED_WINDOW"
        if missing or invalid:
            # A missing suffix may be response truncation, while an internal gap
            # is provider-missing coverage.  Both remain non-complete evidence.
            absent = tuple(timestamp for timestamp in expected if timestamp not in valid)
            coverage = "TRUNCATION_SUSPECTED" if absent and absent == expected[-len(absent):] else "PROVIDER_MISSING_COVERAGE"
        if not valid:
            result = ObservedMinimum(window, "NO_VALID_POST_ENTRY_CANDLE", coverage, None, None, None, None,
                                     ONE_MINUTE, len(expected), 0, missing, invalid, provider_provenance)
        else:
            timestamp, minimum = min(valid.items(), key=lambda item: (item[1], item[0]))
            status = "COMPLETE_OBSERVED_WINDOW" if coverage == "COMPLETE_OBSERVED_WINDOW" else "PARTIAL_OBSERVED_MINIMUM"
            result = ObservedMinimum(window, status, coverage, minimum, timestamp, timestamp - entry_timestamp,
                                     (minimum / entry - 1.0) * 100.0, ONE_MINUTE, len(expected), len(valid),
                                     missing, invalid, provider_provenance)
        results[str(window)] = result.as_dict()
    return {
        "contract_version": CONTRACT_VERSION,
        "entry_timestamp": entry_timestamp,
        "entry_mc_usd": entry,
        "results": results,
    }
