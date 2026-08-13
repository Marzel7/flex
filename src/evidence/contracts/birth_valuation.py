"""Pure EB0.1A birth and valuation evidence projection.

This module deliberately has no database, service, network, or provider imports.
It converts already-bounded evidence records into immutable, deterministic facts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Iterable, Mapping, Optional, Tuple


EVENT_KINDS = frozenset(
    {"CHAIN_BIRTH", "PLATFORM_FIRST_SEEN", "MIGRATION", "MARKET_FIRST_OBSERVED"}
)
VALUATION_SEMANTICS = frozenset(
    {"UNKNOWN", "PRICE_AT_EVENT", "MARKET_CAP_AT_EVENT", "BIRTH_MARKET_CAP"}
)
QUALITY_STATES = frozenset({"VERIFIED", "OBSERVED", "CONFLICTING", "UNKNOWN"})
COMPLETENESS_STATES = frozenset({"COMPLETE", "PARTIAL", "NOT_OBSERVED"})


class BirthValuationContractError(ValueError):
    """Named fail-closed error for invalid EB0.1A evidence."""


@dataclass(frozen=True)
class BirthValuationObservation:
    mint: str
    event_kind: str
    event_time_utc_ns: int
    source: str
    source_version: str
    observed_at_utc_ns: int
    price_or_market_cap_value: Optional[str]
    valuation_semantics: str
    quality_state: str
    completeness_state: str
    source_record_digest: str
    provenance_digest: str
    observation_id: str


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: Mapping[str, object]) -> str:
    return sha256(_canonical_json(value)).hexdigest()


def _required_text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise BirthValuationContractError(f"EB0_1A_INVALID_{field.upper()}")
    return value.strip()


def _required_time(record: Mapping[str, object], field: str) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BirthValuationContractError(f"EB0_1A_INVALID_{field.upper()}")
    return value


def _normalise_value(value: object) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BirthValuationContractError("EB0_1A_VALUE_MUST_BE_DECIMAL_STRING")
    try:
        decimal = Decimal(value)
    except InvalidOperation as exc:
        raise BirthValuationContractError("EB0_1A_INVALID_VALUATION") from exc
    if not decimal.is_finite() or decimal <= 0:
        raise BirthValuationContractError("EB0_1A_VALUATION_MUST_BE_POSITIVE")
    normalised = format(decimal.normalize(), "f")
    return normalised.rstrip("0").rstrip(".") if "." in normalised else normalised


def _project_one(record: Mapping[str, object]) -> BirthValuationObservation:
    mint = _required_text(record, "mint")
    event_kind = _required_text(record, "event_kind")
    event_time = _required_time(record, "event_time_utc_ns")
    source = _required_text(record, "source")
    source_version = _required_text(record, "source_version")
    observed_at = _required_time(record, "observed_at_utc_ns")
    semantics = _required_text(record, "valuation_semantics")
    quality = _required_text(record, "quality_state")
    completeness = _required_text(record, "completeness_state")
    source_record_digest = _required_text(record, "source_record_digest")
    value = _normalise_value(record.get("price_or_market_cap_value"))

    if event_kind not in EVENT_KINDS:
        raise BirthValuationContractError("EB0_1A_UNKNOWN_EVENT_KIND")
    if semantics not in VALUATION_SEMANTICS:
        raise BirthValuationContractError("EB0_1A_UNKNOWN_VALUATION_SEMANTICS")
    if quality not in QUALITY_STATES:
        raise BirthValuationContractError("EB0_1A_UNKNOWN_QUALITY_STATE")
    if completeness not in COMPLETENESS_STATES:
        raise BirthValuationContractError("EB0_1A_UNKNOWN_COMPLETENESS_STATE")

    if value is None:
        if semantics != "UNKNOWN" or completeness != "NOT_OBSERVED":
            raise BirthValuationContractError("EB0_1A_MISSING_VALUATION_NOT_EXPLICIT")
    elif semantics == "UNKNOWN" or completeness == "NOT_OBSERVED":
        raise BirthValuationContractError("EB0_1A_OBSERVED_VALUATION_CONTRADICTION")

    birth_equivalence_proven = record.get("birth_equivalence_proven", False)
    if not isinstance(birth_equivalence_proven, bool):
        raise BirthValuationContractError("EB0_1A_INVALID_BIRTH_EQUIVALENCE_PROOF")
    if semantics == "BIRTH_MARKET_CAP":
        if (
            value is None
            or event_kind not in {"CHAIN_BIRTH", "MARKET_FIRST_OBSERVED"}
            or event_time != observed_at
            or quality != "VERIFIED"
            or completeness != "COMPLETE"
            or not birth_equivalence_proven
        ):
            raise BirthValuationContractError("EB0_1A_BIRTH_VALUATION_NOT_PROVEN")
    elif birth_equivalence_proven:
        raise BirthValuationContractError("EB0_1A_UNUSED_BIRTH_EQUIVALENCE_PROOF")

    provenance_fields = {
        "mint": mint,
        "event_kind": event_kind,
        "event_time_utc_ns": event_time,
        "source": source,
        "source_version": source_version,
        "observed_at_utc_ns": observed_at,
        "source_record_digest": source_record_digest,
    }
    provenance_digest = _digest(provenance_fields)
    identity_fields = {
        **provenance_fields,
        "price_or_market_cap_value": value,
        "valuation_semantics": semantics,
        "quality_state": quality,
        "completeness_state": completeness,
        "birth_equivalence_proven": birth_equivalence_proven,
        "provenance_digest": provenance_digest,
    }
    observation_id = _digest(identity_fields)
    return BirthValuationObservation(
        mint=mint,
        event_kind=event_kind,
        event_time_utc_ns=event_time,
        source=source,
        source_version=source_version,
        observed_at_utc_ns=observed_at,
        price_or_market_cap_value=value,
        valuation_semantics=semantics,
        quality_state=quality,
        completeness_state=completeness,
        source_record_digest=source_record_digest,
        provenance_digest=provenance_digest,
        observation_id=observation_id,
    )


def project_birth_valuation(
    records: Iterable[Mapping[str, object]],
) -> Tuple[BirthValuationObservation, ...]:
    """Project, exact-deduplicate, and deterministically order evidence facts."""

    by_identity = {}
    for record in records:
        observation = _project_one(record)
        by_identity[observation.observation_id] = observation
    return tuple(by_identity[key] for key in sorted(by_identity))


def projection_digest(observations: Iterable[BirthValuationObservation]) -> str:
    """Return an order-independent digest for a projected observation set."""

    payload = [asdict(item) for item in sorted(observations, key=lambda x: x.observation_id)]
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
