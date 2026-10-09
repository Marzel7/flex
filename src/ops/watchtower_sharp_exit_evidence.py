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
FAILURE_DIAGNOSTIC_MAX_BYTES = 1024
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


class ExitEvidenceValidationError(ValueError):
    """Fail-closed validation error carrying no raw provider values."""

    def __init__(self, category: str, diagnostic: Mapping[str, Any]):
        self.category = category
        self.diagnostic = dict(diagnostic)
        super().__init__(category)


def _schema_fingerprint(payload: object) -> str:
    """Hash field names/types only; never preserve provider values or candles."""
    if not isinstance(payload, Mapping):
        shape: dict[str, Any] = {"payload_type": type(payload).__name__}
    else:
        data = payload.get("data")
        items = data.get("items") if isinstance(data, Mapping) else None
        first = items[0] if isinstance(items, list) and items else None
        shape = {"payload_type": "mapping", "top_keys": sorted(map(str, payload.keys()))[:20],
                 "data_type": type(data).__name__, "data_keys": sorted(map(str, data.keys()))[:20] if isinstance(data, Mapping) else [],
                 "items_type": type(items).__name__, "first_item_type": type(first).__name__,
                 "first_item_keys": sorted(map(str, first.keys()))[:20] if isinstance(first, Mapping) else []}
    return hashlib.sha256(_canonical(shape).encode()).hexdigest()


def _value_class(value: object) -> str:
    if value is None: return "NULL"
    if isinstance(value, bool): return "BOOLEAN"
    if isinstance(value, (int, float)): return "NUMBER" if math.isfinite(float(value)) else "NONFINITE_NUMBER"
    if isinstance(value, str): return "STRING"
    return type(value).__name__.upper()


def _failure(category: str, *, path: str, index: int | None, expected: str, actual: object, schema: str) -> ExitEvidenceValidationError:
    return ExitEvidenceValidationError(category, {"failure_category": category, "field_path": path, "candle_index": index,
        "expected": expected, "actual_class": _value_class(actual), "response_schema_fingerprint": schema})


def compact_failure_diagnostic(error: ExitEvidenceValidationError, *, request_id: str) -> dict[str, Any]:
    """Return the bounded durable-safe diagnosis for a future one-shot request."""
    if not request_id or not isinstance(error, ExitEvidenceValidationError):
        raise ValueError("INVALID_FAILURE_DIAGNOSTIC_INPUT")
    result = {"contract_version": CONTRACT_VERSION, "request_identity": request_id, **error.diagnostic}
    if len(_canonical(result).encode()) > FAILURE_DIAGNOSTIC_MAX_BYTES:
        raise ValueError("FAILURE_DIAGNOSTIC_OVERSIZE")
    return result


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


def _payload_items(outcome: ProviderTransportOutcome) -> tuple[tuple[Mapping[str, Any], ...], str]:
    """Validate the actual binding response shape without touching a transport."""
    if not isinstance(outcome, ProviderTransportOutcome):
        raise ValueError("INVALID_TRANSPORT_OUTCOME")
    schema = _schema_fingerprint(outcome.payload)
    if outcome.status_code != 200 or outcome.error_state is not None:
        raise ValueError("UNSUCCESSFUL_TRANSPORT_OUTCOME")
    if not isinstance(outcome.response_headers, Mapping) or not outcome.response_headers:
        raise _failure("UNSUPPORTED_RESPONSE_SHAPE", path="response_headers", index=None, expected="non-empty mapping", actual=outcome.response_headers, schema=schema)
    if not isinstance(outcome.payload, Mapping):
        raise _failure("UNSUPPORTED_RESPONSE_SHAPE", path="payload", index=None, expected="mapping", actual=outcome.payload, schema=schema)
    if outcome.payload.get("success") is not True:
        raise _failure("UNSUPPORTED_RESPONSE_SHAPE", path="payload.success", index=None, expected="boolean true", actual=outcome.payload.get("success"), schema=schema)
    data = outcome.payload.get("data")
    if not isinstance(data, Mapping) or not isinstance(data.get("items"), list):
        raise _failure("UNSUPPORTED_RESPONSE_SHAPE", path="payload.data.items", index=None, expected="list", actual=data.get("items") if isinstance(data, Mapping) else data, schema=schema)
    if any(not isinstance(item, Mapping) for item in data["items"]):
        index = next(index for index, item in enumerate(data["items"]) if not isinstance(item, Mapping))
        raise _failure("UNSUPPORTED_RESPONSE_SHAPE", path="payload.data.items", index=index, expected="mapping", actual=data["items"][index], schema=schema)
    return tuple(data["items"]), schema


@dataclass(frozen=True)
class _Candle:
    start: int
    open_mc_usd: float
    high_mc_usd: float
    low_mc_usd: float
    close_mc_usd: float


def _field(item: Mapping[str, Any], aliases: tuple[str, ...], *, index: int, schema: str, constraint: str) -> object:
    for name in aliases:
        if name in item:
            return item[name]
    raise _failure("MISSING_REQUIRED_FIELD", path="item." + aliases[0], index=index, expected=constraint, actual=None, schema=schema)


def _candles(items: tuple[Mapping[str, Any], ...], *, start: int, end: int, schema: str) -> tuple[_Candle, ...]:
    if len(items) == 0:
        raise _failure("UNSUPPORTED_RESPONSE_SHAPE", path="payload.data.items", index=None, expected="non-empty list", actual=items, schema=schema)
    rows: list[_Candle] = []
    previous: int | None = None
    for index, item in enumerate(items):
        raw_timestamp = _field(item, ("unixTime", "unix_time", "timestamp"), index=index, schema=schema, constraint="integer epoch seconds")
        timestamp = _timestamp(raw_timestamp)
        if timestamp is None or timestamp % INTERVAL_SECONDS:
            raise _failure("INVALID_TIMESTAMP" if timestamp is not None else "INVALID_FIELD_TYPE", path="item.unixTime", index=index, expected="30-second integer epoch", actual=raw_timestamp, schema=schema)
        raw_values = (_field(item, ("o", "open"), index=index, schema=schema, constraint="positive number"),
                      _field(item, ("h", "high"), index=index, schema=schema, constraint="positive number"),
                      _field(item, ("l", "low"), index=index, schema=schema, constraint="positive number"),
                      _field(item, ("c", "close"), index=index, schema=schema, constraint="positive number"))
        values = tuple(_positive(value) for value in raw_values)
        if any(value is None for value in values):
            bad = next(position for position, value in enumerate(values) if value is None)
            raise _failure("INVALID_OHLC_VALUE" if _value_class(raw_values[bad]) in {"NUMBER", "NONFINITE_NUMBER"} else "INVALID_FIELD_TYPE", path=("item.open", "item.high", "item.low", "item.close")[bad], index=index, expected="positive finite number", actual=raw_values[bad], schema=schema)
        if timestamp < start or timestamp >= end:
            raise _failure("OUT_OF_RANGE_TIMESTAMP", path="item.unixTime", index=index, expected=f"[{start},{end})", actual=raw_timestamp, schema=schema)
        if previous is not None and timestamp <= previous:
            category = "DUPLICATE_TIMESTAMP" if timestamp == previous else "OUT_OF_ORDER_TIMESTAMP"
            raise _failure(category, path="item.unixTime", index=index, expected="strictly ascending unique timestamp", actual=raw_timestamp, schema=schema)
        previous = timestamp
        open_mc, high_mc, low_mc, close_mc = values
        if low_mc > min(open_mc, high_mc, close_mc) or high_mc < max(open_mc, low_mc, close_mc):
            raise _failure("OHLC_INCONSISTENCY", path="item.ohlc", index=index, expected="low<=open/high/close<=high", actual="OHLC_RELATIONSHIP", schema=schema)
        rows.append(_Candle(timestamp, open_mc, high_mc, low_mc, close_mc))
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
    items, schema = _payload_items(outcome)
    candles = _candles(items, start=window_start, end=window_end, schema=schema)
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
