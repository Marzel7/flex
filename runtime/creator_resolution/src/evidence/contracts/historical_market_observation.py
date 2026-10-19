"""Pure EB0.3A supplemental historical-market evidence contract.

The contract accepts already bounded records only.  It has no database,
network, provider, ranking, creator-identity, or policy dependencies.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Iterable, Mapping, Optional, Tuple


CONTRACT_VERSION = "eb0.3a.v1"
AUTHORITY_CLASS = "SUPPLEMENTAL_NON_AUTHORITATIVE"
QUALITY_STATES = frozenset({"OBSERVED", "CONFLICTING", "DEGRADED"})
COMPLETENESS_STATES = frozenset({"COMPLETE_INTERVAL", "PARTIAL_INTERVAL"})
EARLIEST_OBSERVATION_SEMANTICS = frozenset(
    {"NOT_ASSERTED", "PROVIDER_WINDOW_EARLIEST", "PAGE_EARLIEST_NOT_HISTORY"}
)


class HistoricalMarketObservationError(ValueError):
    """Named fail-closed error for invalid EB0.3A evidence."""


@dataclass(frozen=True)
class HistoricalMarketObservation:
    mint: str
    authority_class: str
    provider: str
    provider_version: str
    endpoint_id: str
    endpoint_version: str
    interval: str
    interval_start_utc_ns: int
    interval_end_utc_ns: int
    observed_at_utc_ns: int
    open_value: str
    high_value: str
    low_value: str
    close_value: str
    volume_value: str
    market_cap_value: Optional[str]
    quote_unit: str
    quality_state: str
    completeness_state: str
    conflict_group_id: Optional[str]
    earliest_observation_semantics: str
    request_run_id: str
    physical_request_sequence: int
    request_cost_units: int
    source_record_digest: str
    provenance_digest: str
    observation_id: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise HistoricalMarketObservationError(f"EB0_3A_INVALID_{field.upper()}")
    return value.strip()


def _integer(record: Mapping[str, object], field: str, *, positive: bool = False) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < int(positive):
        raise HistoricalMarketObservationError(f"EB0_3A_INVALID_{field.upper()}")
    return value


def _decimal(record: Mapping[str, object], field: str, *, positive: bool) -> str:
    value = record.get(field)
    if not isinstance(value, str):
        raise HistoricalMarketObservationError(f"EB0_3A_{field.upper()}_MUST_BE_DECIMAL_STRING")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise HistoricalMarketObservationError(f"EB0_3A_INVALID_{field.upper()}") from exc
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise HistoricalMarketObservationError(f"EB0_3A_INVALID_{field.upper()}")
    normalized = format(number.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _optional_decimal(record: Mapping[str, object], field: str) -> Optional[str]:
    return None if record.get(field) is None else _decimal(record, field, positive=True)


def _project(record: Mapping[str, object]) -> HistoricalMarketObservation:
    mint = _text(record, "mint")
    authority = _text(record, "authority_class")
    provider = _text(record, "provider")
    provider_version = _text(record, "provider_version")
    endpoint_id = _text(record, "endpoint_id")
    endpoint_version = _text(record, "endpoint_version")
    interval = _text(record, "interval")
    start = _integer(record, "interval_start_utc_ns")
    end = _integer(record, "interval_end_utc_ns", positive=True)
    observed = _integer(record, "observed_at_utc_ns")
    quote = _text(record, "quote_unit")
    quality = _text(record, "quality_state")
    completeness = _text(record, "completeness_state")
    earliest = _text(record, "earliest_observation_semantics")
    run_id = _text(record, "request_run_id")
    sequence = _integer(record, "physical_request_sequence", positive=True)
    cost = _integer(record, "request_cost_units", positive=True)
    source_digest = _text(record, "source_record_digest")
    conflict = record.get("conflict_group_id")
    if conflict is not None and (not isinstance(conflict, str) or not conflict.strip()):
        raise HistoricalMarketObservationError("EB0_3A_INVALID_CONFLICT_GROUP_ID")
    conflict = conflict.strip() if isinstance(conflict, str) else None

    if authority != AUTHORITY_CLASS:
        raise HistoricalMarketObservationError("EB0_3A_AUTHORITY_PROMOTION_REJECTED")
    if end <= start or observed < end:
        raise HistoricalMarketObservationError("EB0_3A_INVALID_INTERVAL_TIMING")
    if quality not in QUALITY_STATES:
        raise HistoricalMarketObservationError("EB0_3A_UNKNOWN_QUALITY_STATE")
    if completeness not in COMPLETENESS_STATES:
        raise HistoricalMarketObservationError("EB0_3A_UNKNOWN_COMPLETENESS_STATE")
    if earliest not in EARLIEST_OBSERVATION_SEMANTICS:
        raise HistoricalMarketObservationError("EB0_3A_UNKNOWN_EARLIEST_SEMANTICS")
    if quality == "CONFLICTING" and conflict is None:
        raise HistoricalMarketObservationError("EB0_3A_CONFLICT_GROUP_REQUIRED")
    if quality != "CONFLICTING" and conflict is not None:
        raise HistoricalMarketObservationError("EB0_3A_UNUSED_CONFLICT_GROUP")

    open_value = _decimal(record, "open_value", positive=True)
    high_value = _decimal(record, "high_value", positive=True)
    low_value = _decimal(record, "low_value", positive=True)
    close_value = _decimal(record, "close_value", positive=True)
    volume_value = _decimal(record, "volume_value", positive=False)
    market_cap = _optional_decimal(record, "market_cap_value")
    o, h, l, c = map(Decimal, (open_value, high_value, low_value, close_value))
    if h < max(o, c) or l > min(o, c) or h < l:
        raise HistoricalMarketObservationError("EB0_3A_INVALID_OHLC_RANGE")

    provenance = {
        "contract_version": CONTRACT_VERSION,
        "mint": mint,
        "provider": provider,
        "provider_version": provider_version,
        "endpoint_id": endpoint_id,
        "endpoint_version": endpoint_version,
        "request_run_id": run_id,
        "physical_request_sequence": sequence,
        "request_cost_units": cost,
        "source_record_digest": source_digest,
    }
    provenance_digest = _digest(provenance)
    identity = {
        **provenance,
        "authority_class": authority,
        "interval": interval,
        "interval_start_utc_ns": start,
        "interval_end_utc_ns": end,
        "observed_at_utc_ns": observed,
        "open_value": open_value,
        "high_value": high_value,
        "low_value": low_value,
        "close_value": close_value,
        "volume_value": volume_value,
        "market_cap_value": market_cap,
        "quote_unit": quote,
        "quality_state": quality,
        "completeness_state": completeness,
        "conflict_group_id": conflict,
        "earliest_observation_semantics": earliest,
        "provenance_digest": provenance_digest,
    }
    return HistoricalMarketObservation(
        mint, authority, provider, provider_version, endpoint_id, endpoint_version,
        interval, start, end, observed, open_value, high_value, low_value,
        close_value, volume_value, market_cap, quote, quality, completeness,
        conflict, earliest, run_id, sequence, cost, source_digest,
        provenance_digest, _digest(identity),
    )


def project_historical_market_observations(
    records: Iterable[Mapping[str, object]],
) -> Tuple[HistoricalMarketObservation, ...]:
    """Project, exact-deduplicate, and deterministically order supplemental facts."""

    observations = {}
    for record in records:
        observation = _project(record)
        observations[observation.observation_id] = observation
    return tuple(observations[key] for key in sorted(observations))


def projection_digest(observations: Iterable[HistoricalMarketObservation]) -> str:
    payload = [asdict(item) for item in sorted(observations, key=lambda x: x.observation_id)]
    return _digest(payload)
