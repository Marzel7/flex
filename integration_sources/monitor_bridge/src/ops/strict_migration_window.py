"""Bounded generic request family for the Watchtower migration opening.

This module deliberately owns only the request identity and the narrow
``migration + 1`` contract.  It delegates the actual Birdeye request,
normalization, and provider-error mapping to MonitorBirdeyeTransport; keeping
those behaviours in their existing authority prevents a second entry path.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Mapping


STRICT_MIGRATION_WINDOW_1S = "STRICT_MIGRATION_WINDOW_1S"
REQUESTED_ENTRY_SECOND_OFFSET = 1
PLUS1_ENTRY_METHOD = "FIRST_FULL_POST_MIGRATION_SECOND_MC"
MIGRATION_SECOND_FALLBACK_METHOD = "MIGRATION_SECOND_MC_FALLBACK"
BOUNDED_POST_MIGRATION_METHOD = "BOUNDED_POST_MIGRATION_MC_FALLBACK"
BOUNDED_POST_MIGRATION_EXACTNESS = "POST_MIGRATION_OFFSET_2S_OBSERVED_MC"
MAX_ENTRY_OFFSET_SECONDS = 2
STRICT_MIGRATION_WINDOW_BOUNDED = True
FAILURE_DIAGNOSTIC_MAX_BYTES = 1024


def normalize_opening_ohlcv(payload: Mapping[str, Any]) -> list[dict[str, float | int]]:
    """Shared fail-closed OHLCV normalizer for forward and recovery openings."""
    items = ((payload.get("data") or {}).get("items") or []
    )
    if not isinstance(items, list):
        raise ValueError("MALFORMED_OHLC_CONTAINER")
    candles = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("MALFORMED_OHLC_CANDLE")
        try:
            candle = {
                "timestamp": int(item.get("unixTime", item.get("unix_time", item.get("timestamp")))),
                "open": float(item.get("o", item.get("open"))),
                "high": float(item.get("h", item.get("high"))),
                "low": float(item.get("l", item.get("low"))),
                "close": float(item.get("c", item.get("close"))),
            }
        except (TypeError, ValueError):
            raise ValueError("MALFORMED_OHLC_CANDLE")
        if candle["timestamp"] <= 0 or candle["low"] < 0 or candle["high"] < candle["low"] or candle["high"] < candle["open"] or candle["high"] < candle["close"] or candle["low"] > candle["open"] or candle["low"] > candle["close"]:
            raise ValueError("INVALID_OHLC_INVARIANT")
        candles.append(candle)
    return sorted(candles, key=lambda row: int(row["timestamp"]))


def request_fingerprint(request: Mapping[str, Any]) -> dict[str, Any]:
    """A stable structural record; it deliberately excludes mint, timestamps and credentials."""
    structural = {
        "endpoint_family": "BIRDEYE_V3_OHLCV",
        "chain": "solana",
        "interval": "1s",
        "time_window_seconds": int(request["time_to"]) - int(request["time_from"]),
        "parameter_names": ["address", "chain", "currency", "time_from", "time_to", "type"],
        "options": {"currency": "usd", "padding": False, "range": "mcap"},
    }
    return {**structural, "fingerprint": _identity(structural)}


def _safe_code(value: Any) -> str:
    return re.sub(r"[^A-Z0-9_.-]", "_", str(value or "" ).upper())[:80] or "NONE"


def failure_diagnostic(*, request: Mapping[str, Any], provider_diagnostic: Mapping[str, Any] | None,
                       credential_alias: str, job_identity: str, attempt_timestamp: int | None = None) -> dict[str, Any]:
    """Project only bounded, non-secret strict-opening failure metadata for durable queues."""
    source = dict(provider_diagnostic or {})
    record = {
        "version": 1,
        "provider": "BIRDEYE",
        "credential_alias": credential_alias,
        "request_family": STRICT_MIGRATION_WINDOW_1S,
        "http_status": int(source.get("http_status") or 0),
        "provider_error_code": _safe_code(source.get("provider_error_code")),
        "failure_category": _safe_code(source.get("failure_category")),
        "failure_stage": _safe_code(source.get("failure_stage")),
        "attempt_timestamp": int(time.time() if attempt_timestamp is None else attempt_timestamp),
        "job_identity": str(job_identity)[:128],
        "request_fingerprint": request_fingerprint(request),
        "normalized_item_count": int(source.get("normalized_item_count") or 0),
        "normalized_timestamps": [int(value) for value in source.get("normalized_timestamps", [])[:2]],
        "migration_timestamp_present": bool(source.get("migration_timestamp_present")),
        "target_timestamp_present": bool(source.get("target_timestamp_present")),
    }
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > FAILURE_DIAGNOSTIC_MAX_BYTES:
        raise ValueError("STRICT_OPENING_FAILURE_DIAGNOSTIC_OVERSIZE")
    return record


def compare_request_context(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, bool]:
    keys = ("endpoint_family", "chain", "interval", "time_window_seconds", "parameter_names", "options")
    return {"identical": all(left.get(key) == right.get(key) for key in keys),
            "endpoint_difference": left.get("endpoint_family") != right.get("endpoint_family"),
            "interval_difference": left.get("interval") != right.get("interval"),
            "time_window_difference": left.get("time_window_seconds") != right.get("time_window_seconds"),
            "parameter_difference": left.get("parameter_names") != right.get("parameter_names") or left.get("options") != right.get("options")}


def _identity(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def plan(*, mint: str, migration_timestamp: int, time_to_offset: int = 2) -> dict[str, Any]:
    """Return a bounded acquisition window; selection remains t+1 then t only."""
    if not isinstance(mint, str) or not mint:
        raise ValueError("STRICT_MIGRATION_MINT_REQUIRED")
    if not isinstance(migration_timestamp, int) or migration_timestamp <= 0:
        raise ValueError("STRICT_MIGRATION_TIMESTAMP_REQUIRED")
    if time_to_offset not in {2, 4}:
        raise ValueError("STRICT_MIGRATION_WINDOW_OFFSET_REQUIRED")
    request = {
        "request_family": STRICT_MIGRATION_WINDOW_1S,
        "mint": mint,
        "migration_timestamp": migration_timestamp,
        "requested_entry_second": migration_timestamp + REQUESTED_ENTRY_SECOND_OFFSET,
        "time_from": migration_timestamp,
        "time_to": migration_timestamp + time_to_offset,
    }
    return {**request, "request_id": _identity(request)}


def dispatch(*, queue: Any, transport: Any, mint: str, migration_timestamp: int, time_to_offset: int = 2) -> dict[str, Any]:
    """Use the existing Monitor transport after the existing DEV-012 gate.

    ``acquire_watchtower_entry`` remains the one implementation of request
    construction, 1-second parsing, strict selection and 429 mapping.
    """
    request = plan(mint=mint, migration_timestamp=migration_timestamp, time_to_offset=time_to_offset)
    queue.admit_provider_dispatch(mint, "OPENING")
    entry, manifest = transport.acquire_watchtower_entry(
        {"mint": mint},
        {"migration_timestamp": migration_timestamp,
         "time_from": request["time_from"], "time_to": request["time_to"]},
    )
    return {"request": request, "entry": entry, "manifest": manifest}


def reduce_policy(*, migration_timestamp: int, entry: Mapping[str, Any]) -> dict[str, Any]:
    """Consume only evidence already accepted by the shared strict normalizer."""
    timestamp = entry.get("timestamp")
    plus_one = migration_timestamp + REQUESTED_ENTRY_SECOND_OFFSET
    fallback = migration_timestamp
    plus_two = migration_timestamp + MAX_ENTRY_OFFSET_SECONDS
    if timestamp not in {plus_one, fallback, plus_two}:
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "STRICT_MIGRATION_TARGET_SECOND_REQUIRED"}
    if timestamp == fallback and not entry.get("plus_one_absent"):
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "STRICT_MIGRATION_TARGET_SECOND_REQUIRED"}
    if timestamp == plus_two and (not entry.get("plus_one_absent") or not entry.get("migration_absent")):
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "STRICT_MIGRATION_TARGET_SECOND_REQUIRED"}
    try:
        mc = float(entry.get("mc"))
    except (TypeError, ValueError):
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "STRICT_MIGRATION_ENTRY_MC_REQUIRED"}
    if mc <= 0:
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "STRICT_MIGRATION_ENTRY_MC_REQUIRED"}
    method = PLUS1_ENTRY_METHOD if timestamp == plus_one else MIGRATION_SECOND_FALLBACK_METHOD if timestamp == fallback else BOUNDED_POST_MIGRATION_METHOD
    exactness = method if timestamp != plus_two else BOUNDED_POST_MIGRATION_EXACTNESS
    result = {"state": "QUALIFIED", "entry_method": method,
              "entry_timestamp": int(timestamp), "entry_mc_usd": mc, "entry_exactness": exactness}
    if timestamp == plus_two:
        result["entry_offset_seconds"] = MAX_ENTRY_OFFSET_SECONDS
    return result


def reduce_retained_offsets(*, migration_timestamp: int, offsets: Mapping[str, Any],
                            duplicate_offsets: tuple[int, ...] | list[int] = ()) -> dict[str, Any]:
    """Apply the one strict reducer to compact, already-retained candle evidence.

    This is deliberately an adapter, not an acquisition path: callers provide
    normalized offset-to-market-cap evidence and no provider, database, queue,
    or lifecycle object is accepted.  Ambiguous early offsets fail closed and
    later (``+3``/``+4``) offsets are never candidates.
    """
    try:
        values = {int(key): value for key, value in dict(offsets).items()}
        duplicates = {int(value) for value in duplicate_offsets}
    except (TypeError, ValueError):
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "STRICT_MIGRATION_RETAINED_OFFSETS_INVALID"}
    if any(offset not in {0, 1, 2, 3, 4} for offset in values) or any(offset not in {0, 1, 2, 3, 4} for offset in duplicates):
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "STRICT_MIGRATION_RETAINED_OFFSETS_INVALID"}
    if 1 in duplicates or 0 in duplicates or 2 in duplicates:
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "STRICT_MIGRATION_AMBIGUOUS_EARLY_OFFSET"}
    if 1 in values:
        entry = {"timestamp": migration_timestamp + 1, "mc": values[1]}
    elif 0 in values:
        entry = {"timestamp": migration_timestamp, "mc": values[0], "plus_one_absent": True}
    elif 2 in values:
        entry = {"timestamp": migration_timestamp + 2, "mc": values[2],
                 "plus_one_absent": True, "migration_absent": True}
    else:
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "STRICT_MIGRATION_TARGET_SECOND_REQUIRED"}
    return reduce_policy(migration_timestamp=migration_timestamp, entry=entry)
