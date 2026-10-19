"""Pure EB0.2A creator historical outcome evidence projection.

No database, service, network, provider, ranking, or attribution dependencies belong
here. Inputs must already be bounded, qualified, and frozen by the caller.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Iterable, Mapping, Optional, Tuple


OUTCOME_KINDS = frozenset({"MIGRATION_BY_HORIZON", "MARKET_CAP_AT_LEAST_BY_HORIZON"})
CONTRACT_VERSION = "eb0.2a.v1"
OUTCOME_STATES = frozenset({"OBSERVED_TRUE", "OBSERVED_FALSE", "UNKNOWN"})
QUALITY_STATES = frozenset({"VERIFIED", "OBSERVED", "CONFLICTING", "UNKNOWN"})
COMPLETENESS_STATES = frozenset({"COMPLETE", "PARTIAL", "NOT_OBSERVED"})


class CreatorHistoricalOutcomeContractError(ValueError):
    """Named fail-closed error for invalid EB0.2A evidence."""


@dataclass(frozen=True)
class CreatorHistoricalOutcomeFact:
    creator: str
    mint: str
    cohort_event_time_utc_ns: int
    outcome_kind: str
    horizon_utc_ns: int
    horizon_end_utc_ns: int
    observed_through_utc_ns: int
    outcome_state: str
    outcome_event_time_utc_ns: Optional[int]
    threshold_value: Optional[str]
    source: str
    source_version: str
    quality_state: str
    completeness_state: str
    denominator_eligible: bool
    source_record_digest: str
    provenance_digest: str
    fact_id: str


def _digest(value: Mapping[str, object]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(payload.encode("utf-8")).hexdigest()


def _text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CreatorHistoricalOutcomeContractError(f"EB0_2A_INVALID_{field.upper()}")
    return value.strip()


def _time(record: Mapping[str, object], field: str, *, optional: bool = False) -> Optional[int]:
    value = record.get(field)
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CreatorHistoricalOutcomeContractError(f"EB0_2A_INVALID_{field.upper()}")
    return value


def _decimal(value: object) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CreatorHistoricalOutcomeContractError("EB0_2A_THRESHOLD_MUST_BE_DECIMAL_STRING")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise CreatorHistoricalOutcomeContractError("EB0_2A_INVALID_THRESHOLD") from exc
    if not number.is_finite() or number <= 0:
        raise CreatorHistoricalOutcomeContractError("EB0_2A_THRESHOLD_MUST_BE_POSITIVE")
    normalised = format(number.normalize(), "f")
    return normalised.rstrip("0").rstrip(".") if "." in normalised else normalised


def _project_one(record: Mapping[str, object]) -> CreatorHistoricalOutcomeFact:
    creator = _text(record, "creator")
    mint = _text(record, "mint")
    cohort_time = _time(record, "cohort_event_time_utc_ns")
    horizon = _time(record, "horizon_utc_ns")
    observed_through = _time(record, "observed_through_utc_ns")
    outcome_kind = _text(record, "outcome_kind")
    outcome_state = _text(record, "outcome_state")
    event_time = _time(record, "outcome_event_time_utc_ns", optional=True)
    threshold = _decimal(record.get("threshold_value"))
    source = _text(record, "source")
    source_version = _text(record, "source_version")
    quality = _text(record, "quality_state")
    completeness = _text(record, "completeness_state")
    source_record_digest = _text(record, "source_record_digest")

    if horizon == 0:
        raise CreatorHistoricalOutcomeContractError("EB0_2A_HORIZON_MUST_BE_POSITIVE")
    horizon_end = cohort_time + horizon
    if outcome_kind not in OUTCOME_KINDS:
        raise CreatorHistoricalOutcomeContractError("EB0_2A_UNKNOWN_OUTCOME_KIND")
    if outcome_state not in OUTCOME_STATES:
        raise CreatorHistoricalOutcomeContractError("EB0_2A_UNKNOWN_OUTCOME_STATE")
    if quality not in QUALITY_STATES:
        raise CreatorHistoricalOutcomeContractError("EB0_2A_UNKNOWN_QUALITY_STATE")
    if completeness not in COMPLETENESS_STATES:
        raise CreatorHistoricalOutcomeContractError("EB0_2A_UNKNOWN_COMPLETENESS_STATE")
    if observed_through < cohort_time:
        raise CreatorHistoricalOutcomeContractError("EB0_2A_OBSERVATION_PRECEDES_COHORT")

    if outcome_kind == "MIGRATION_BY_HORIZON" and threshold is not None:
        raise CreatorHistoricalOutcomeContractError("EB0_2A_UNUSED_THRESHOLD")
    if outcome_kind == "MARKET_CAP_AT_LEAST_BY_HORIZON" and threshold is None:
        raise CreatorHistoricalOutcomeContractError("EB0_2A_MISSING_THRESHOLD")

    if outcome_state == "OBSERVED_TRUE":
        if event_time is None or not cohort_time <= event_time <= horizon_end:
            raise CreatorHistoricalOutcomeContractError("EB0_2A_TRUE_OUTCOME_NOT_PROVEN_IN_HORIZON")
        if event_time > observed_through:
            raise CreatorHistoricalOutcomeContractError("EB0_2A_FUTURE_LEAKAGE")
    elif event_time is not None:
        raise CreatorHistoricalOutcomeContractError("EB0_2A_UNEXPECTED_OUTCOME_EVENT_TIME")

    horizon_complete = observed_through >= horizon_end
    if completeness == "COMPLETE" and not horizon_complete:
        raise CreatorHistoricalOutcomeContractError("EB0_2A_FALSE_COMPLETENESS_CLAIM")
    if completeness != "COMPLETE" and outcome_state == "OBSERVED_FALSE":
        raise CreatorHistoricalOutcomeContractError("EB0_2A_CENSORED_NEGATIVE_FORBIDDEN")
    if completeness == "NOT_OBSERVED" and outcome_state != "UNKNOWN":
        raise CreatorHistoricalOutcomeContractError("EB0_2A_NOT_OBSERVED_MUST_BE_UNKNOWN")
    if outcome_state == "UNKNOWN" and completeness == "COMPLETE":
        raise CreatorHistoricalOutcomeContractError("EB0_2A_COMPLETE_OUTCOME_CANNOT_BE_UNKNOWN")

    denominator_eligible = completeness == "COMPLETE"
    provenance = {
        "creator": creator,
        "mint": mint,
        "cohort_event_time_utc_ns": cohort_time,
        "source": source,
        "source_version": source_version,
        "source_record_digest": source_record_digest,
    }
    provenance_digest = _digest(provenance)
    identity = {
        **provenance,
        "outcome_kind": outcome_kind,
        "horizon_utc_ns": horizon,
        "horizon_end_utc_ns": horizon_end,
        "observed_through_utc_ns": observed_through,
        "outcome_state": outcome_state,
        "outcome_event_time_utc_ns": event_time,
        "threshold_value": threshold,
        "quality_state": quality,
        "completeness_state": completeness,
        "denominator_eligible": denominator_eligible,
        "provenance_digest": provenance_digest,
    }
    return CreatorHistoricalOutcomeFact(
        creator=creator,
        mint=mint,
        cohort_event_time_utc_ns=cohort_time,
        outcome_kind=outcome_kind,
        horizon_utc_ns=horizon,
        horizon_end_utc_ns=horizon_end,
        observed_through_utc_ns=observed_through,
        outcome_state=outcome_state,
        outcome_event_time_utc_ns=event_time,
        threshold_value=threshold,
        source=source,
        source_version=source_version,
        quality_state=quality,
        completeness_state=completeness,
        denominator_eligible=denominator_eligible,
        source_record_digest=source_record_digest,
        provenance_digest=provenance_digest,
        fact_id=_digest(identity),
    )


def project_creator_historical_outcomes(
    records: Iterable[Mapping[str, object]],
) -> Tuple[CreatorHistoricalOutcomeFact, ...]:
    """Project, exact-deduplicate, and deterministically order frozen facts."""

    by_identity = {}
    for record in records:
        fact = _project_one(record)
        by_identity[fact.fact_id] = fact
    return tuple(by_identity[key] for key in sorted(by_identity))


def projection_digest(facts: Iterable[CreatorHistoricalOutcomeFact]) -> str:
    payload = [asdict(item) for item in sorted(facts, key=lambda item: item.fact_id)]
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
