"""Accepted DEV014 opening policy, retained for DEV004 fixture qualification.

Source: 71b6e338cfb54057ec4984d41bfbe02477c90dba. Pure evidence selection; no acquisition or storage.
"""
from __future__ import annotations
from typing import Any, Mapping

REQUESTED_ENTRY_SECOND_OFFSET = 1

PLUS1_ENTRY_METHOD = "FIRST_FULL_POST_MIGRATION_SECOND_MC"

MIGRATION_SECOND_FALLBACK_METHOD = "MIGRATION_SECOND_MC_FALLBACK"

BOUNDED_POST_MIGRATION_METHOD = "BOUNDED_POST_MIGRATION_MC_FALLBACK"

BOUNDED_POST_MIGRATION_EXACTNESS = "POST_MIGRATION_OFFSET_2S_OBSERVED_MC"

MAX_ENTRY_OFFSET_SECONDS = 2

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
