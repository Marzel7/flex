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


def summarize_observed_lifecycle(*, creation_timestamp: int, candles: list[dict], interval_seconds: int,
                                 requested_start: int, requested_end: int) -> dict:
    """Compact observed-only lifecycle projection over already validated OHLC.

    The existing reducer owns peak selection.  This adds the missing strict
    post-peak ordering, recovery, and coverage labels without creating a new
    price source or retaining candles.
    """
    if interval_seconds <= 0 or requested_start >= requested_end:
        raise ValueError("INVALID_LIFECYCLE_COVERAGE_CONTEXT")
    ordered = sorted(candles, key=lambda row: int(row["timestamp"]))
    if not ordered:
        return {"coverage_confidence": "NO_VALID_CANDLES"}
    peak = max(ordered, key=lambda row: (float(row["high"]), -int(row["timestamp"])))
    subsequent = [row for row in ordered if int(row["timestamp"]) > int(peak["timestamp"])]
    trough = min(subsequent, key=lambda row: (float(row["low"]), int(row["timestamp"]))) if subsequent else None
    recovery_rows = [row for row in ordered if trough is not None and int(row["timestamp"]) > int(trough["timestamp"])]
    recovery = max(recovery_rows, key=lambda row: (float(row["high"]), -int(row["timestamp"]))) if recovery_rows else None
    gaps = [int(right["timestamp"]) - int(left["timestamp"]) for left, right in zip(ordered, ordered[1:])
            if int(right["timestamp"]) - int(left["timestamp"]) > interval_seconds]
    coverage = "CONTIGUOUS_REQUESTED_WINDOW" if (int(ordered[0]["timestamp"]) <= requested_start
        and int(ordered[-1]["timestamp"]) + interval_seconds >= requested_end and not gaps) else "PARTIAL_OBSERVED_WINDOW"
    result = {
        "coverage_confidence": coverage,
        "first_observed_timestamp": int(ordered[0]["timestamp"]),
        "last_observed_timestamp": int(ordered[-1]["timestamp"]),
        "last_observed_mc_usd": float(ordered[-1]["close"]),
        "observed_maximum_mc_usd": float(peak["high"]),
        "observed_maximum_timestamp": int(peak["timestamp"]),
        "creation_to_observed_maximum_seconds": max(0, int(peak["timestamp"]) - int(creation_timestamp)),
        "internal_gap_seconds": max(gaps, default=0),
    }
    if trough is None:
        return {**result, "peak_to_subsequent_trough": "UNAVAILABLE_NO_LATER_CANDLE", "recovery_peak": "UNAVAILABLE_NO_LATER_TROUGH"}
    decline = (float(peak["high"]) - float(trough["low"])) * 100 / float(peak["high"])
    result["peak_to_subsequent_trough"] = {"observed_minimum_mc_usd": float(trough["low"]), "observed_minimum_timestamp": int(trough["timestamp"]), "drawdown_percent": decline}
    result["recovery_peak"] = ("UNAVAILABLE_NO_LATER_CANDLE" if recovery is None else {
        "observed_maximum_mc_usd": float(recovery["high"]), "observed_maximum_timestamp": int(recovery["timestamp"]),
    })
    return result
