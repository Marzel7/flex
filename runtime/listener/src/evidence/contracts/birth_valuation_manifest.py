"""Immutable EB0.1D manifests for frozen canonical birth/valuation projections."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Iterable, Mapping, Tuple

from .birth_valuation import (
    BirthValuationObservation,
    project_birth_valuation,
    projection_digest,
)
from .birth_valuation_adapters import ADAPTER_VERSION


MANIFEST_SCHEMA_VERSION = "eb0.1d.v1"
CONTRACT_VERSION = "eb0.1a.v1"
_CANONICAL_INPUT_FIELDS = frozenset(
    {
        "mint",
        "event_kind",
        "event_time_utc_ns",
        "source",
        "source_version",
        "observed_at_utc_ns",
        "price_or_market_cap_value",
        "valuation_semantics",
        "quality_state",
        "completeness_state",
        "source_record_digest",
    }
)


class BirthValuationManifestError(ValueError):
    """Named fail-closed error for noncanonical manifests or inputs."""


@dataclass(frozen=True)
class BirthValuationManifest:
    schema_version: str
    contract_version: str
    adapter_version: str
    input_digest: str
    projection_digest: str
    observation_count: int
    event_counts: Mapping[str, int]
    quality_counts: Mapping[str, int]
    completeness_counts: Mapping[str, int]
    conflicting_observation_count: int
    missing_valuation_count: int
    observations: Tuple[BirthValuationObservation, ...]
    manifest_digest: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["observations"] = [asdict(item) for item in self.observations]
        return payload


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: object) -> str:
    return sha256(_canonical_json(value)).hexdigest()


def _sorted_counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _canonicalise_input(record: Mapping[str, object]) -> tuple[dict[str, object], BirthValuationObservation]:
    fields = frozenset(record)
    if fields != _CANONICAL_INPUT_FIELDS:
        missing = sorted(_CANONICAL_INPUT_FIELDS - fields)
        extra = sorted(fields - _CANONICAL_INPUT_FIELDS)
        raise BirthValuationManifestError(
            f"EB0_1D_NONCANONICAL_FIELDS missing={missing} extra={extra}"
        )
    observation = project_birth_valuation([record])[0]
    canonical = {
        "mint": observation.mint,
        "event_kind": observation.event_kind,
        "event_time_utc_ns": observation.event_time_utc_ns,
        "source": observation.source,
        "source_version": observation.source_version,
        "observed_at_utc_ns": observation.observed_at_utc_ns,
        "price_or_market_cap_value": observation.price_or_market_cap_value,
        "valuation_semantics": observation.valuation_semantics,
        "quality_state": observation.quality_state,
        "completeness_state": observation.completeness_state,
        "source_record_digest": observation.source_record_digest,
    }
    if dict(record) != canonical:
        raise BirthValuationManifestError("EB0_1D_INPUT_NOT_CANONICALLY_NORMALISED")
    return canonical, observation


def build_birth_valuation_manifest(
    records: Iterable[Mapping[str, object]],
) -> BirthValuationManifest:
    """Build a deterministic manifest from canonical adapter output only."""

    canonical_inputs: list[dict[str, object]] = []
    for record in records:
        canonical, _ = _canonicalise_input(record)
        canonical_inputs.append(canonical)
    if not canonical_inputs:
        raise BirthValuationManifestError("EB0_1D_EMPTY_INPUT")

    encoded = [_canonical_json(item) for item in canonical_inputs]
    if len(set(encoded)) != len(encoded):
        raise BirthValuationManifestError("EB0_1D_DUPLICATE_INPUT")
    canonical_inputs.sort(key=_canonical_json)
    observations = project_birth_valuation(canonical_inputs)
    if len(observations) != len(canonical_inputs):
        raise BirthValuationManifestError("EB0_1D_PROJECTION_IDENTITY_COLLISION")

    body = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "input_digest": _digest(canonical_inputs),
        "projection_digest": projection_digest(observations),
        "observation_count": len(observations),
        "event_counts": _sorted_counts(item.event_kind for item in observations),
        "quality_counts": _sorted_counts(item.quality_state for item in observations),
        "completeness_counts": _sorted_counts(item.completeness_state for item in observations),
        "conflicting_observation_count": sum(
            item.quality_state == "CONFLICTING" for item in observations
        ),
        "missing_valuation_count": sum(
            item.price_or_market_cap_value is None for item in observations
        ),
        "observations": [asdict(item) for item in observations],
    }
    return BirthValuationManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        contract_version=CONTRACT_VERSION,
        adapter_version=ADAPTER_VERSION,
        input_digest=body["input_digest"],
        projection_digest=body["projection_digest"],
        observation_count=body["observation_count"],
        event_counts=body["event_counts"],
        quality_counts=body["quality_counts"],
        completeness_counts=body["completeness_counts"],
        conflicting_observation_count=body["conflicting_observation_count"],
        missing_valuation_count=body["missing_valuation_count"],
        observations=observations,
        manifest_digest=_digest(body),
    )


def verify_birth_valuation_manifest(
    manifest: BirthValuationManifest,
    records: Iterable[Mapping[str, object]],
) -> bool:
    """Rebuild and require exact equality, including schema bindings and digests."""

    if manifest.schema_version != MANIFEST_SCHEMA_VERSION:
        raise BirthValuationManifestError("EB0_1D_SCHEMA_VERSION_MISMATCH")
    if manifest.contract_version != CONTRACT_VERSION:
        raise BirthValuationManifestError("EB0_1D_CONTRACT_VERSION_MISMATCH")
    if manifest.adapter_version != ADAPTER_VERSION:
        raise BirthValuationManifestError("EB0_1D_ADAPTER_VERSION_MISMATCH")
    rebuilt = build_birth_valuation_manifest(records)
    if manifest != rebuilt:
        raise BirthValuationManifestError("EB0_1D_REPLAY_MISMATCH")
    return True
