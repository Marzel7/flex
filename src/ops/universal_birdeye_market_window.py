"""Bounded Birdeye market-window adapter for the universal price monitor.

The adapter owns no scheduling, terminal policy, or UI behaviour.  It turns a
single qualified V3 OHLCV response into compact candle facts and refuses an
incomplete, duplicate, unordered, or gapped interval.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.ops.token_data_provider_bindings import ProviderTransportOutcome, build_birdeye_ohlcv_request
from src.ops import universal_provider_ledger as ledger


@dataclass(frozen=True)
class MarketWindow:
    request: dict[str, Any]
    coverage_start: int
    coverage_end: int
    observations: list[dict[str, Any]]


def _seconds(interval: str) -> int:
    values = {"1s": 1, "1m": 60, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
    if interval not in values:
        raise ValueError("UNQUALIFIED_INTERVAL")
    return values[interval]


def normalize_market_window(payload: dict[str, Any], *, request: dict[str, Any]) -> MarketWindow:
    """Return only compact OHLC/MC facts, with strict contiguous coverage."""
    params = request["request_parameters"]
    interval = str(params["type"])
    step = _seconds(interval)
    requested_start, requested_end = int(params["time_from"]), int(params["time_to"])
    raw = ((payload.get("data") or {}).get("items") or [])
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in raw:
        try:
            timestamp = int(item["unix_time"])
            close, high = float(item["c"]), float(item["h"])
        except (KeyError, TypeError, ValueError):
            continue
        if timestamp in seen or close <= 0 or high <= 0:
            raise ValueError("INVALID_OR_DUPLICATE_CANDLE")
        seen.add(timestamp)
        rows.append({"timestamp": timestamp, "mc_usd": close, "high_mc_usd": high, "resolution": interval})
    if rows != sorted(rows, key=lambda x: x["timestamp"]):
        raise ValueError("OUT_OF_ORDER_CANDLES")
    if not rows:
        raise ValueError("EMPTY_MARKET_WINDOW")
    # Candle timestamps designate bucket starts.  The final bucket must cover
    # the requested right boundary and every retained bucket must be adjacent.
    if rows[0]["timestamp"] > requested_start or rows[-1]["timestamp"] + step < requested_end:
        raise ValueError("BOUNDARY_COVERAGE_INCOMPLETE")
    if any(b["timestamp"] - a["timestamp"] != step for a, b in zip(rows, rows[1:])):
        raise ValueError("INTERNAL_COVERAGE_GAP")
    return MarketWindow(request, rows[0]["timestamp"], rows[-1]["timestamp"] + step, rows)


def acquire_market_window(*, mint: str, interval: str, time_from: int, time_to: int,
                          transport: Callable[[dict[str, Any]], ProviderTransportOutcome]) -> MarketWindow:
    """Perform precisely one provider attempt; transport is deliberately injected."""
    request = build_birdeye_ohlcv_request(address=mint, interval=interval, time_from=time_from, time_to=time_to)
    outcome = transport(request)
    if outcome.status_code != 200 or not isinstance(outcome.payload, dict):
        raise RuntimeError(outcome.error_state or f"HTTP_{outcome.status_code}")
    return normalize_market_window(outcome.payload, request=request)


def acquire_ledgered_market_window(conn: Any, *, work_id: str, operation_id: str, mint: str,
                                   interval: str, time_from: int, time_to: int,
                                   transport: Callable[[dict[str, Any]], ProviderTransportOutcome],
                                   caller: dict[str, Any]) -> MarketWindow:
    """One durable attempt, with the provider call deliberately outside a DB transaction."""
    request = build_birdeye_ohlcv_request(address=mint, interval=interval, time_from=time_from, time_to=time_to)
    ledger.ensure(conn)
    call_id = ledger.plan(conn, work_id=work_id, operation_id=operation_id, mint=mint,
                          provider_class="Birdeye", provider_method="/defi/v3/ohlcv",
                          request_kind="MARKET_WINDOW", request_identity_hash=ledger.ident(request), attempt_number=1,
                          caller=caller)
    ledger.transition(conn, call_id, "BUDGET_ADMITTED", budget_admission_id="UNIVERSAL_BIRDEYE_GLOBAL_CAP_V1")
    ledger.transition(conn, call_id, "PROVIDER_CALL_STARTED")
    try:
        outcome = transport(request)  # never inside a transaction
        if outcome.status_code != 200 or not isinstance(outcome.payload, dict):
            raise RuntimeError(outcome.error_state or f"HTTP_{outcome.status_code}")
        window = normalize_market_window(outcome.payload, request=request)
    except Exception:
        # A started request has uncertain remote side effects until an explicit
        # recovery decision; no automatic retry is permitted.
        ledger.recover(conn)
        raise
    compact = {"coverage_start": window.coverage_start, "coverage_end": window.coverage_end,
               "observation_count": len(window.observations), "request_identity": ledger.ident(request)}
    ledger.store_result(conn, call_id, compact)
    ledger.transition(conn, call_id, "RESULT_COMMITTED")
    return window
