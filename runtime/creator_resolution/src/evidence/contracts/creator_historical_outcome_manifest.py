"""Immutable EB0.2D manifests for frozen creator outcome projections."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Iterable, Mapping, Tuple

from .creator_historical_outcome import (
    CONTRACT_VERSION,
    CreatorHistoricalOutcomeFact,
    project_creator_historical_outcomes,
    projection_digest,
)
from .creator_historical_outcome_adapters import ADAPTER_VERSION


MANIFEST_SCHEMA_VERSION = "eb0.2d.v1"


class CreatorHistoricalOutcomeManifestError(ValueError):
    """Named fail-closed error for invalid EB0.2D manifests or facts."""


@dataclass(frozen=True)
class CreatorHistoricalOutcomeManifest:
    schema_version: str
    contract_version: str
    adapter_version: str
    input_digest: str
    projection_digest: str
    fact_count: int
    eligible_denominator_count: int
    unknown_count: int
    not_observed_count: int
    outcome_kind_counts: Mapping[str, int]
    outcome_state_counts: Mapping[str, int]
    quality_counts: Mapping[str, int]
    completeness_counts: Mapping[str, int]
    conflicting_fact_count: int
    facts: Tuple[CreatorHistoricalOutcomeFact, ...]
    manifest_digest: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["facts"] = [asdict(item) for item in self.facts]
        return payload


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: object) -> str:
    return sha256(_canonical_json(value)).hexdigest()


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _reproject(fact: CreatorHistoricalOutcomeFact) -> CreatorHistoricalOutcomeFact:
    record = {
        "creator": fact.creator,
        "mint": fact.mint,
        "cohort_event_time_utc_ns": fact.cohort_event_time_utc_ns,
        "outcome_kind": fact.outcome_kind,
        "horizon_utc_ns": fact.horizon_utc_ns,
        "observed_through_utc_ns": fact.observed_through_utc_ns,
        "outcome_state": fact.outcome_state,
        "outcome_event_time_utc_ns": fact.outcome_event_time_utc_ns,
        "threshold_value": fact.threshold_value,
        "source": fact.source,
        "source_version": fact.source_version,
        "quality_state": fact.quality_state,
        "completeness_state": fact.completeness_state,
        "source_record_digest": fact.source_record_digest,
    }
    projected = project_creator_historical_outcomes([record])[0]
    if projected != fact:
        raise CreatorHistoricalOutcomeManifestError("EB0_2D_NONCANONICAL_FACT")
    return projected


def build_creator_historical_outcome_manifest(
    facts: Iterable[CreatorHistoricalOutcomeFact],
) -> CreatorHistoricalOutcomeManifest:
    material = [_reproject(item) for item in facts]
    if not material:
        raise CreatorHistoricalOutcomeManifestError("EB0_2D_EMPTY_INPUT")
    encoded = [_canonical_json(asdict(item)) for item in material]
    if len(set(encoded)) != len(encoded):
        raise CreatorHistoricalOutcomeManifestError("EB0_2D_DUPLICATE_INPUT")
    ordered = tuple(sorted(material, key=lambda item: item.fact_id))
    body = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "input_digest": _digest([asdict(item) for item in ordered]),
        "projection_digest": projection_digest(ordered),
        "fact_count": len(ordered),
        "eligible_denominator_count": sum(item.denominator_eligible for item in ordered),
        "unknown_count": sum(item.outcome_state == "UNKNOWN" for item in ordered),
        "not_observed_count": sum(
            item.completeness_state == "NOT_OBSERVED" for item in ordered
        ),
        "outcome_kind_counts": _counts(item.outcome_kind for item in ordered),
        "outcome_state_counts": _counts(item.outcome_state for item in ordered),
        "quality_counts": _counts(item.quality_state for item in ordered),
        "completeness_counts": _counts(item.completeness_state for item in ordered),
        "conflicting_fact_count": sum(item.quality_state == "CONFLICTING" for item in ordered),
        "facts": [asdict(item) for item in ordered],
    }
    return CreatorHistoricalOutcomeManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        contract_version=CONTRACT_VERSION,
        adapter_version=ADAPTER_VERSION,
        input_digest=body["input_digest"],
        projection_digest=body["projection_digest"],
        fact_count=body["fact_count"],
        eligible_denominator_count=body["eligible_denominator_count"],
        unknown_count=body["unknown_count"],
        not_observed_count=body["not_observed_count"],
        outcome_kind_counts=body["outcome_kind_counts"],
        outcome_state_counts=body["outcome_state_counts"],
        quality_counts=body["quality_counts"],
        completeness_counts=body["completeness_counts"],
        conflicting_fact_count=body["conflicting_fact_count"],
        facts=ordered,
        manifest_digest=_digest(body),
    )


def verify_creator_historical_outcome_manifest(
    manifest: CreatorHistoricalOutcomeManifest,
    facts: Iterable[CreatorHistoricalOutcomeFact],
) -> bool:
    if manifest.schema_version != MANIFEST_SCHEMA_VERSION:
        raise CreatorHistoricalOutcomeManifestError("EB0_2D_SCHEMA_VERSION_MISMATCH")
    if manifest.contract_version != CONTRACT_VERSION:
        raise CreatorHistoricalOutcomeManifestError("EB0_2D_CONTRACT_VERSION_MISMATCH")
    if manifest.adapter_version != ADAPTER_VERSION:
        raise CreatorHistoricalOutcomeManifestError("EB0_2D_ADAPTER_VERSION_MISMATCH")
    rebuilt = build_creator_historical_outcome_manifest(facts)
    if rebuilt != manifest:
        raise CreatorHistoricalOutcomeManifestError("EB0_2D_REPLAY_MISMATCH")
    return True
