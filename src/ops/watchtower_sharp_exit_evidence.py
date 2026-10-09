"""Pure compact evidence contract for bounded 30-second Watchtower exit research.

This module consumes the established ``ProviderTransportOutcome`` produced by
``BirdeyeProductionBinding``.  It deliberately owns neither credential,
transport, retry, queue, database, nor lifecycle behavior.  Its output is a
derived event record only: raw payloads and full candle histories are never
returned or persisted.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

from src.ops.token_data_provider_bindings import ProviderTransportOutcome


CONTRACT_VERSION = "WATCHTOWER_SHARP_EXIT_30S_EVIDENCE_V1"
INTERVAL_SECONDS = 30
WINDOW_BEFORE_SECONDS = 300
WINDOW_AFTER_SECONDS = 4500
RAPID_COLLAPSE_PERCENT = 85.0
_HEADER_ALLOWLIST = frozenset({"x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "x-birdeye-cu", "x-compute-units"})


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _timestamp(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if str(number) == str(value) or isinstance(value, int) else None


def request_identity(*, mint: str, entry_timestamp: int, candidate_start: int) -> str:
    """Return the deterministic identity for the exact bounded 30s request."""
    if not mint or entry_timestamp <= 0 or candidate_start <= 0 or candidate_start % 900:
        raise ValueError("INVALID_30S_REQUEST_IDENTITY")
    return hashlib.sha256(_canonical({
        "contract_version": CONTRACT_VERSION,
        "mint": mint,
        "entry_timestamp": entry_timestamp,
        "candidate_start": candidate_start,
        "endpoint": "/defi/v3/ohlcv",
        "chart_type": "mcap",
        "currency": "usd",
        "type": "30s",
        "mode": "range",
        "padding": False,
        "time_from": candidate_start - WINDOW_BEFORE_SECONDS,
        "time_to": candidate_start + WINDOW_AFTER_SECONDS,
    }).encode()).hexdigest()


def _payload_items(outcome: ProviderTransportOutcome) -> tuple[Mapping[str, Any], ...]:
    """Validate the actual binding response shape without touching a transport."""
    if not isinstance(outcome, ProviderTransportOutcome):
        raise ValueError("INVALID_TRANSPORT_OUTCOME")
    if outcome.status_code != 200 or outcome.error_state is not None:
        raise ValueError("UNSUCCESSFUL_TRANSPORT_OUTCOME")
    if not isinstance(outcome.response_headers, Mapping) or not outcome.response_headers:
        raise ValueError("MISSING_RESPONSE_HEADERS")
    if not isinstance(outcome.payload, Mapping):
        raise ValueError("MISSING_PROVIDER_PAYLOAD")
    if outcome.payload.get("success") is not True:
        raise ValueError("INVALID_PROVIDER_SUCCESS_STATE")
    data = outcome.payload.get("data")
    if not isinstance(data, Mapping) or not isinstance(data.get("items"), list):
        raise ValueError("MISSING_PROVIDER_CANDLE_ITEMS")
    if any(not isinstance(item, Mapping) for item in data["items"]):
        raise ValueError("NON_MAPPING_PROVIDER_CANDLE_ITEM")
    return tuple(data["items"])


@dataclass(frozen=True)
class _Candle:
    start: int
    open_mc_usd: float
    high_mc_usd: float
    low_mc_usd: float
    close_mc_usd: float


def _candles(items: tuple[Mapping[str, Any], ...], *, start: int, end: int) -> tuple[_Candle, ...]:
    if len(items) == 0:
        raise ValueError("EMPTY_PROVIDER_CANDLE_LIST")
    rows: list[_Candle] = []
    previous: int | None = None
    for item in items:
        timestamp = _timestamp(item.get("unixTime"))
        values = (_positive(item.get("o")), _positive(item.get("h")), _positive(item.get("l")), _positive(item.get("c")))
        if timestamp is None or timestamp % INTERVAL_SECONDS or any(value is None for value in values):
            raise ValueError("INVALID_30S_MCAP_CANDLE")
        if timestamp < start or timestamp >= end:
            raise ValueError("OUT_OF_RANGE_30S_CANDLE")
        if previous is not None and timestamp <= previous:
            raise ValueError("DUPLICATE_OR_OUT_OF_ORDER_30S_CANDLES")
        previous = timestamp
        open_mc, high_mc, low_mc, close_mc = values
        if low_mc > min(open_mc, high_mc, close_mc) or high_mc < max(open_mc, low_mc, close_mc):
            raise ValueError("INVALID_30S_OHLC_RELATIONSHIP")
        rows.append(_Candle(timestamp, open_mc, high_mc, low_mc, close_mc))
    if len(rows) != len(items):
        raise ValueError("NON_MAPPING_PROVIDER_CANDLE_ITEM")
    return tuple(rows)


def _missing_ranges(expected: tuple[int, ...], present: set[int]) -> tuple[tuple[int, int], ...]:
    missing = [timestamp for timestamp in expected if timestamp not in present]
    if not missing:
        return ()
    ranges: list[tuple[int, int]] = []
    first = last = missing[0]
    for timestamp in missing[1:]:
        if timestamp == last + INTERVAL_SECONDS:
            last = timestamp
        else:
            ranges.append((first, last))
            first = last = timestamp
    ranges.append((first, last))
    return tuple(ranges)


def normalize_30s_exit_evidence(
    *,
    outcome: ProviderTransportOutcome,
    mint: str,
    entry_timestamp: int,
    entry_mc_usd: float,
    candidate_start: int,
    request_id: str,
    provider_provenance: str,
    observed_peak_timestamp: int | None = None,
) -> dict[str, Any]:
    """Normalize one bounded response into compact, gap-explicit exit evidence.

    No raw provider object or full candle list appears in the result.  A response
    is accepted only when every returned candle is ordered, unique, aligned,
    in-range, finite, positive, and internally consistent.
    """
    if not mint or not request_id or not provider_provenance or entry_timestamp <= 0 or candidate_start <= 0:
        raise ValueError("MISSING_EXIT_EVIDENCE_IDENTITY")
    entry_mc = _positive(entry_mc_usd)
    if entry_mc is None or candidate_start % 900:
        raise ValueError("INVALID_EXIT_EVIDENCE_INPUT")
    if observed_peak_timestamp is not None and observed_peak_timestamp <= 0:
        raise ValueError("INVALID_OBSERVED_PEAK_TIMESTAMP")
    window_start = candidate_start - WINDOW_BEFORE_SECONDS
    window_end = candidate_start + WINDOW_AFTER_SECONDS
    candles = _candles(_payload_items(outcome), start=window_start, end=window_end)
    expected = tuple(range(window_start, window_end, INTERVAL_SECONDS))
    missing = tuple(timestamp for timestamp in expected if timestamp not in {candle.start for candle in candles})
    missing_ranges = _missing_ranges(expected, {candle.start for candle in candles})
    candidate = tuple(candle for candle in candles if candidate_start <= candle.start < candidate_start + 900)
    red = tuple(candle for candle in candidate if candle.close_mc_usd < candle.open_mc_usd)
    largest = max(red, key=lambda candle: (candle.open_mc_usd - candle.close_mc_usd, -candle.start), default=None)
    metadata = {str(key).lower(): str(value) for key, value in outcome.response_headers.items() if str(key).lower() in _HEADER_ALLOWLIST}
    coverage = "COMPLETE_30S_COVERAGE" if not missing else "PARTIAL_30S_COVERAGE"
    event: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "mint": mint,
        "request_identity": request_id,
        "provider_provenance": provider_provenance,
        "entry_timestamp": entry_timestamp,
        "entry_mc_usd": entry_mc,
        "candidate_start": candidate_start,
        "candidate_end": candidate_start + 900,
        "window_start": window_start,
        "window_end": window_end,
        "candle_interval_seconds": INTERVAL_SECONDS,
        "returned_candle_count": len(candles),
        "expected_bucket_count": len(expected),
        "missing_bucket_timestamps": list(missing),
        "missing_bucket_ranges": [list(value) for value in missing_ranges],
        "coverage_classification": coverage,
        "provider_response_metadata": metadata,
    }
    if largest is None:
        event.update({"exit_classification": "INSUFFICIENT_EVIDENCE" if not candidate else "NO_QUALIFYING_COLLAPSE", "largest_observed_red_candle": None, "post_exit_recovery": {"status": "INSUFFICIENT_EVIDENCE"}})
    else:
        collapse_pct = (1.0 - largest.close_mc_usd / largest.open_mc_usd) * 100.0
        post = tuple(candle for candle in candles if candle.start > largest.start)
        if coverage != "COMPLETE_30S_COVERAGE":
            classification = "PARTIAL_EXIT_EVIDENCE"
        elif collapse_pct >= RAPID_COLLAPSE_PERCENT:
            classification = "HIGH_RESOLUTION_EXIT_CONFIRMED"
        elif len(red) > 1:
            classification = "MULTI_CANDLE_DECLINE"
        else:
            classification = "RAPID_EXIT_CANDIDATE"
        event.update({
            "exit_classification": classification,
            "largest_observed_red_candle": {
                "start": largest.start,
                "end": largest.start + INTERVAL_SECONDS,
                "open_mc_usd": largest.open_mc_usd,
                "high_mc_usd": largest.high_mc_usd,
                "low_mc_usd": largest.low_mc_usd,
                "close_mc_usd": largest.close_mc_usd,
                "absolute_collapse_usd": largest.open_mc_usd - largest.close_mc_usd,
                "collapse_percent": collapse_pct,
                "entry_to_exit_start_seconds": largest.start - entry_timestamp,
                "entry_to_exit_end_seconds": largest.start + INTERVAL_SECONDS - entry_timestamp,
                "observed_peak_to_exit_start_seconds": largest.start - observed_peak_timestamp if observed_peak_timestamp else None,
            },
            "post_exit_recovery": {
                "status": "OBSERVED_PARTIAL" if coverage != "COMPLETE_30S_COVERAGE" else "OBSERVED_COMPLETE_WINDOW",
                "observed_candle_count": len(post),
                "observed_min_mc_usd": min((candle.low_mc_usd for candle in post), default=None),
                "observed_max_mc_usd": max((candle.high_mc_usd for candle in post), default=None),
            },
        })
    evidence_without_identity = dict(event)
    event["evidence_identity"] = hashlib.sha256(_canonical(evidence_without_identity).encode()).hexdigest()
    return event
