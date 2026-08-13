"""EB0.3E exact normalizer for the qualified GMGN v1 kline envelope.

Only frozen/fake response mappings are accepted here.  There is no HTTP client,
credential, URL, database, runtime service, ranking, or policy dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from typing import Mapping, Protocol

from .historical_market_observation_adapters import (
    MAX_CANDLE_ROWS,
    PROJECTION_SCHEMA_VERSION,
    AdapterResult,
    adapt_market_kline_projection,
    canonical_digest,
)


NORMALIZER_VERSION = "eb0.3e.v1"
MAX_RESPONSE_BYTES = 1_048_576
EXPECTED_REQUEST_COST_UNITS = 2
_ENVELOPE_FIELDS = frozenset({"list"})
_CANDLE_FIELDS = frozenset(
    {"time", "open", "close", "high", "low", "volume", "source", "amount"}
)


class GmgnMarketKlineNormalizerError(ValueError):
    """Named fail-closed EB0.3E normalization error."""


class FrozenGmgnEnvelopeTransport(Protocol):
    def load_envelope(self) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class RequestMetadata:
    platform_mint: str
    provider_version: str
    endpoint_version: str
    interval: str
    request_from_ms: int
    request_to_ms: int
    observed_at_ms: int
    request_run_id: str
    physical_request_sequence: int
    request_cost_units: int
    physical_requests_observed: int
    retry: bool
    failover: bool
    pagination: bool
    quality_state: str = "OBSERVED"
    conflict_group_id: str | None = None


@dataclass(frozen=True)
class NormalizedGmgnKline:
    projection: Mapping[str, object]
    adapter_result: AdapterResult
    raw_envelope_digest: str
    raw_envelope_bytes: int
    discarded_fields: tuple[str, ...]


def _fail(code: str) -> None:
    raise GmgnMarketKlineNormalizerError(f"EB0_3E_{code}")


def _text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        _fail(f"INVALID_{field.upper()}")
    return value.strip()


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(f"INVALID_{field.upper()}")
    return value


def _decimal_string(value: object, field: str, *, positive: bool) -> str:
    if not isinstance(value, str):
        _fail(f"{field.upper()}_MUST_BE_DECIMAL_STRING")
    try:
        number = Decimal(value)
    except InvalidOperation:
        _fail(f"INVALID_{field.upper()}")
    if not number.is_finite() or number < 0 or (positive and number == 0):
        _fail(f"INVALID_{field.upper()}")
    return value


def _validate_metadata(metadata: RequestMetadata) -> None:
    for field in ("platform_mint", "provider_version", "endpoint_version", "request_run_id"):
        _text(getattr(metadata, field), field)
    if metadata.interval != "1m":
        _fail("INTERVAL_NOT_1M")
    start = _integer(metadata.request_from_ms, "request_from_ms")
    end = _integer(metadata.request_to_ms, "request_to_ms", minimum=1)
    observed = _integer(metadata.observed_at_ms, "observed_at_ms", minimum=1)
    if end <= start or observed < end:
        _fail("INVALID_REQUEST_BOUNDS")
    _integer(metadata.physical_request_sequence, "physical_request_sequence", minimum=1)
    if metadata.request_cost_units != EXPECTED_REQUEST_COST_UNITS:
        _fail("REQUEST_COST_MISMATCH")
    if metadata.physical_requests_observed != 1:
        _fail("PHYSICAL_REQUEST_COUNT_MISMATCH")
    if metadata.retry or metadata.failover or metadata.pagination:
        _fail("REQUEST_SCOPE_EXPANSION")
    if metadata.quality_state not in {"OBSERVED", "CONFLICTING", "DEGRADED"}:
        _fail("INVALID_QUALITY_STATE")
    if metadata.quality_state == "CONFLICTING":
        _text(metadata.conflict_group_id, "conflict_group_id")
    elif metadata.conflict_group_id is not None:
        _fail("UNUSED_CONFLICT_GROUP")


def normalize_gmgn_market_kline(
    envelope: Mapping[str, object], metadata: RequestMetadata,
) -> NormalizedGmgnKline:
    """Normalize exactly one qualified v1 envelope and validate EB0.3C replay."""

    _validate_metadata(metadata)
    if not isinstance(envelope, Mapping) or frozenset(envelope) != _ENVELOPE_FIELDS:
        _fail("ENVELOPE_SCHEMA_DRIFT")
    raw = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    raw_bytes = len(raw.encode("utf-8"))
    if raw_bytes > MAX_RESPONSE_BYTES:
        _fail("RESPONSE_BYTE_CEILING_EXCEEDED")
    rows = envelope.get("list")
    if not isinstance(rows, list) or not rows:
        _fail("EMPTY_LIST")
    if len(rows) > MAX_CANDLE_ROWS:
        _fail("ROW_CEILING_EXCEEDED")

    candles: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, Mapping) or frozenset(row) != _CANDLE_FIELDS:
            _fail("CANDLE_SCHEMA_DRIFT")
        time_ms = _integer(row.get("time"), "time")
        _text(row.get("source"), "source", allow_empty=True)
        _decimal_string(row.get("amount"), "amount", positive=False)
        candle = {"time_ms": time_ms}
        for field in ("open", "high", "low", "close"):
            candle[field] = _decimal_string(row.get(field), field, positive=True)
        candle["volume"] = _decimal_string(row.get("volume"), "volume", positive=False)
        candles.append(candle)

    projection: dict[str, object] = {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "platform_mint": metadata.platform_mint,
        "provider": "gmgn",
        "provider_version": f"{metadata.provider_version}:{NORMALIZER_VERSION}",
        "endpoint_id": "market-kline",
        "endpoint_version": metadata.endpoint_version,
        "interval": metadata.interval,
        "request_from_ms": metadata.request_from_ms,
        "request_to_ms": metadata.request_to_ms,
        "observed_at_ms": metadata.observed_at_ms,
        "request_run_id": metadata.request_run_id,
        "physical_request_sequence": metadata.physical_request_sequence,
        "request_cost_units": metadata.request_cost_units,
        "response_digest": canonical_digest(candles),
        "quality_state": metadata.quality_state,
        "completeness_state": "PARTIAL_INTERVAL",
        "conflict_group_id": metadata.conflict_group_id,
        "earliest_observation_semantics": "PAGE_EARLIEST_NOT_HISTORY",
        "candles": candles,
    }
    result = adapt_market_kline_projection(projection)
    return NormalizedGmgnKline(
        projection=projection,
        adapter_result=result,
        raw_envelope_digest=canonical_digest(envelope),
        raw_envelope_bytes=raw_bytes,
        discarded_fields=("source", "amount"),
    )


def normalize_gmgn_market_kline_from_transport(
    transport: FrozenGmgnEnvelopeTransport, metadata: RequestMetadata,
) -> NormalizedGmgnKline:
    """Invoke a frozen/fake envelope transport exactly once."""

    return normalize_gmgn_market_kline(transport.load_envelope(), metadata)
