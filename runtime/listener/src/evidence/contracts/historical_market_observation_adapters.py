"""EB0.3C bounded adapter for frozen market-kline response projections.

This module deliberately knows no GMGN client, URL, credential, database, or
runtime service.  A caller may inject a fake projection transport; the adapter
invokes it once and accepts only the exact credential-free schema below.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping, Protocol, Tuple

from .historical_market_observation import (
    HistoricalMarketObservation,
    project_historical_market_observations,
)


ADAPTER_VERSION = "eb0.3c.v1"
PROJECTION_SCHEMA_VERSION = "eb0.3c.market-kline-projection.v1"
INTERVAL = "1m"
INTERVAL_MS = 60_000
MAX_CANDLE_ROWS = 1_000

_TOP_LEVEL_FIELDS = frozenset({
    "schema_version", "platform_mint", "provider", "provider_version",
    "endpoint_id", "endpoint_version", "interval", "request_from_ms",
    "request_to_ms", "observed_at_ms", "request_run_id",
    "physical_request_sequence", "request_cost_units", "response_digest",
    "quality_state", "completeness_state", "conflict_group_id",
    "earliest_observation_semantics", "candles",
})
_CANDLE_FIELDS = frozenset({"time_ms", "open", "high", "low", "close", "volume"})
_FORBIDDEN_FIELD_FRAGMENTS = (
    "api_key", "apikey", "authorization", "bearer", "credential", "secret",
    "token", "cookie", "password", "market_cap", "liquidity", "rank",
    "score", "creator", "operator", "profit", "cashflow", "policy", "cursor",
)


class HistoricalMarketObservationAdapterError(ValueError):
    """Named fail-closed error for invalid EB0.3C inputs."""


class FrozenProjectionTransport(Protocol):
    """Dependency boundary used only for injected frozen/fake transports."""

    def load_projection(self) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class AdapterResult:
    observations: Tuple[HistoricalMarketObservation, ...]
    response_digest: str
    request_run_id: str
    physical_request_sequence: int
    request_cost_units: int


def canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(payload.encode("utf-8")).hexdigest()


def _fail(code: str) -> None:
    raise HistoricalMarketObservationAdapterError(f"EB0_3C_{code}")


def _text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        _fail(f"INVALID_{field.upper()}")
    return value.strip()


def _integer(record: Mapping[str, object], field: str, *, minimum: int = 0) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(f"INVALID_{field.upper()}")
    return value


def _reject_forbidden_fields(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(fragment in normalized for fragment in _FORBIDDEN_FIELD_FRAGMENTS):
                _fail("FORBIDDEN_FIELD")
            _reject_forbidden_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden_fields(child)


def _validate_projection(projection: Mapping[str, object]) -> list[Mapping[str, object]]:
    if not isinstance(projection, Mapping):
        _fail("INVALID_PROJECTION")
    if frozenset(projection) != _TOP_LEVEL_FIELDS:
        _fail("SCHEMA_DRIFT")
    _reject_forbidden_fields(projection)
    if projection.get("schema_version") != PROJECTION_SCHEMA_VERSION:
        _fail("UNKNOWN_SCHEMA_VERSION")
    if projection.get("interval") != INTERVAL:
        _fail("INTERVAL_NOT_1M")
    request_from = _integer(projection, "request_from_ms")
    request_to = _integer(projection, "request_to_ms", minimum=1)
    observed_at = _integer(projection, "observed_at_ms", minimum=1)
    if request_to <= request_from or observed_at < request_to:
        _fail("INVALID_REQUEST_BOUNDS")
    _integer(projection, "physical_request_sequence", minimum=1)
    _integer(projection, "request_cost_units", minimum=1)
    if projection.get("earliest_observation_semantics") != "PAGE_EARLIEST_NOT_HISTORY":
        _fail("EARLIEST_SEMANTICS_PROMOTION")
    if projection.get("completeness_state") != "PARTIAL_INTERVAL":
        _fail("COMPLETENESS_PROMOTION")
    quality = projection.get("quality_state")
    if quality not in {"OBSERVED", "CONFLICTING", "DEGRADED"}:
        _fail("INVALID_QUALITY_STATE")
    conflict = projection.get("conflict_group_id")
    if quality == "CONFLICTING":
        if not isinstance(conflict, str) or not conflict.strip():
            _fail("CONFLICT_GROUP_REQUIRED")
    elif conflict is not None:
        _fail("UNUSED_CONFLICT_GROUP")
    candles = projection.get("candles")
    if not isinstance(candles, list) or not candles:
        _fail("EMPTY_CANDLES")
    if len(candles) > MAX_CANDLE_ROWS:
        _fail("ROW_CEILING_EXCEEDED")
    for candle in candles:
        if not isinstance(candle, Mapping) or frozenset(candle) != _CANDLE_FIELDS:
            _fail("CANDLE_SCHEMA_DRIFT")
        time_ms = _integer(candle, "time_ms")
        if not request_from <= time_ms < request_to:
            _fail("CANDLE_OUTSIDE_REQUEST_BOUNDS")
        for field in ("open", "high", "low", "close", "volume"):
            if not isinstance(candle.get(field), str):
                _fail(f"{field.upper()}_MUST_BE_DECIMAL_STRING")
    times = [int(candle["time_ms"]) for candle in candles]
    if times != sorted(times) or len(times) != len(set(times)):
        _fail("CANDLE_ORDER_OR_DUPLICATE")
    if _text(projection, "response_digest") != canonical_digest(candles):
        _fail("RESPONSE_DIGEST_MISMATCH")
    return candles


def adapt_market_kline_projection(projection: Mapping[str, object]) -> AdapterResult:
    """Map one exact bounded frozen projection into EB0.3A observations."""

    candles = _validate_projection(projection)
    mint = _text(projection, "platform_mint")
    provider = _text(projection, "provider")
    provider_version = _text(projection, "provider_version")
    endpoint_id = _text(projection, "endpoint_id")
    endpoint_version = _text(projection, "endpoint_version")
    run_id = _text(projection, "request_run_id")
    sequence = int(projection["physical_request_sequence"])
    cost = int(projection["request_cost_units"])
    response_digest = str(projection["response_digest"])
    observed_at_ns = int(projection["observed_at_ms"]) * 1_000_000
    source_digest = canonical_digest({
        "adapter_version": ADAPTER_VERSION,
        "projection_schema_version": PROJECTION_SCHEMA_VERSION,
        "response_digest": response_digest,
        "request_from_ms": projection["request_from_ms"],
        "request_to_ms": projection["request_to_ms"],
    })
    records = []
    for candle in candles:
        start_ns = int(candle["time_ms"]) * 1_000_000
        records.append({
            "mint": mint,
            "authority_class": "SUPPLEMENTAL_NON_AUTHORITATIVE",
            "provider": provider,
            "provider_version": f"{provider_version}:{ADAPTER_VERSION}",
            "endpoint_id": endpoint_id,
            "endpoint_version": endpoint_version,
            "interval": INTERVAL,
            "interval_start_utc_ns": start_ns,
            "interval_end_utc_ns": start_ns + INTERVAL_MS * 1_000_000,
            "observed_at_utc_ns": observed_at_ns,
            "open_value": candle["open"],
            "high_value": candle["high"],
            "low_value": candle["low"],
            "close_value": candle["close"],
            "volume_value": candle["volume"],
            "market_cap_value": None,
            "quote_unit": "USD",
            "quality_state": projection["quality_state"],
            "completeness_state": "PARTIAL_INTERVAL",
            "conflict_group_id": projection["conflict_group_id"],
            "earliest_observation_semantics": "PAGE_EARLIEST_NOT_HISTORY",
            "request_run_id": run_id,
            "physical_request_sequence": sequence,
            "request_cost_units": cost,
            "source_record_digest": source_digest,
        })
    observations = project_historical_market_observations(records)
    return AdapterResult(observations, response_digest, run_id, sequence, cost)


def adapt_market_kline_from_transport(transport: FrozenProjectionTransport) -> AdapterResult:
    """Invoke an injected frozen/fake transport exactly once and adapt its page."""

    return adapt_market_kline_projection(transport.load_projection())
