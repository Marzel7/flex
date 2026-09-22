"""Shared Watchtower price-fact reduction: historical replay and Monitor use one rule."""
from __future__ import annotations


CONTRACT_VERSION = "WATCHTOWER_PRICE_FACT_CONTRACT_V1"
ENTRY_METHOD = "FIRST_FULL_POST_MIGRATION_SECOND_MC"
LIFECYCLE_RESOLUTION = "15m"


def reduce_watchtower_price_facts(entry_mc, entry_timestamp: int, candles: list[dict], *, current_close=None) -> dict:
    """Peak/crossing math only; callers own evidence qualification and persistence."""
    ordered = sorted(candles, key=lambda row: int(row["timestamp"]))
    peak = {"value": entry_mc, "timestamp": int(entry_timestamp), "evidence": "ENTRY_REFERENCE"}
    for candle in ordered:
        if candle["high"] > peak["value"]:
            peak = {"value": candle["high"], "timestamp": int(candle["timestamp"]), "evidence": "15M_OHLC_HIGH"}
    crossings = {multiple: next((int(row["timestamp"]) for row in ordered if row["high"] >= entry_mc * multiple), None) for multiple in (2, 5, 10)}
    close = current_close if current_close is not None else (ordered[-1]["close"] if ordered else entry_mc)
    return {"peak": peak, "crossings": crossings, "current_close": close,
            "peak_multiple": peak["value"] / entry_mc,
            "drawdown_percent": (peak["value"] - close) * 100 / peak["value"] if peak["value"] else 0}
