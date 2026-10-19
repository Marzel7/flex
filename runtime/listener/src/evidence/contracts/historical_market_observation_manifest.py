"""EB0.3F immutable manifests for frozen supplemental market observations."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import re
from typing import Mapping, Tuple

from .gmgn_market_kline_normalizer import (
    NORMALIZER_VERSION,
    RequestMetadata,
    normalize_gmgn_market_kline,
)
from .historical_market_observation import (
    CONTRACT_VERSION,
    HistoricalMarketObservation,
    projection_digest,
)
from .historical_market_observation_adapters import ADAPTER_VERSION, canonical_digest


MANIFEST_SCHEMA_VERSION = "eb0.3f.v1"
EXPECTED_SOURCE_FILES = frozenset({"raw_envelope.json", "response_projection.json"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class HistoricalMarketObservationManifestError(ValueError):
    """Named fail-closed EB0.3F manifest error."""


@dataclass(frozen=True)
class HistoricalMarketObservationManifest:
    schema_version: str
    contract_version: str
    adapter_version: str
    normalizer_version: str
    platform_mint: str
    interval: str
    request_from_ms: int
    request_to_ms: int
    request_run_id: str
    physical_request_sequence: int
    request_cost_units: int
    raw_envelope_digest: str
    response_projection_digest: str
    observation_projection_digest: str
    source_file_hashes: Mapping[str, str]
    row_count: int
    quality_counts: Mapping[str, int]
    completeness_counts: Mapping[str, int]
    conflict_count: int
    market_cap_observed_count: int
    earliest_semantics_counts: Mapping[str, int]
    observations: Tuple[HistoricalMarketObservation, ...]
    manifest_digest: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["observations"] = [asdict(item) for item in self.observations]
        return payload


def _fail(code: str) -> None:
    raise HistoricalMarketObservationManifestError(f"EB0_3F_{code}")


def _counts(values) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _validate_file_hashes(source_file_hashes: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(source_file_hashes, Mapping):
        _fail("INVALID_SOURCE_FILE_HASHES")
    if frozenset(source_file_hashes) != EXPECTED_SOURCE_FILES:
        _fail("SOURCE_FILE_SET_MISMATCH")
    normalized = {}
    for name, digest in source_file_hashes.items():
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            _fail("INVALID_SOURCE_FILE_HASH")
        normalized[name] = digest
    return dict(sorted(normalized.items()))


def build_historical_market_observation_manifest(
    *,
    envelope: Mapping[str, object],
    metadata: RequestMetadata,
    source_file_hashes: Mapping[str, str],
) -> HistoricalMarketObservationManifest:
    """Normalize frozen evidence and bind its exact deterministic replay."""

    file_hashes = _validate_file_hashes(source_file_hashes)
    normalized = normalize_gmgn_market_kline(envelope, metadata)
    observations = normalized.adapter_result.observations
    if not observations:
        _fail("EMPTY_OBSERVATIONS")
    if len({item.observation_id for item in observations}) != len(observations):
        _fail("DUPLICATE_OBSERVATIONS")
    if any(item.market_cap_value is not None for item in observations):
        _fail("MARKET_CAP_INFERENCE_REJECTED")
    if any(item.authority_class != "SUPPLEMENTAL_NON_AUTHORITATIVE" for item in observations):
        _fail("AUTHORITY_PROMOTION_REJECTED")
    if any(item.earliest_observation_semantics != "PAGE_EARLIEST_NOT_HISTORY" for item in observations):
        _fail("EARLIEST_SEMANTICS_PROMOTION_REJECTED")

    projection = normalized.projection
    projection_hash = canonical_digest(projection)
    body = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "platform_mint": metadata.platform_mint,
        "interval": metadata.interval,
        "request_from_ms": metadata.request_from_ms,
        "request_to_ms": metadata.request_to_ms,
        "request_run_id": metadata.request_run_id,
        "physical_request_sequence": metadata.physical_request_sequence,
        "request_cost_units": metadata.request_cost_units,
        "raw_envelope_digest": normalized.raw_envelope_digest,
        "response_projection_digest": projection_hash,
        "observation_projection_digest": projection_digest(observations),
        "source_file_hashes": file_hashes,
        "row_count": len(observations),
        "quality_counts": _counts(item.quality_state for item in observations),
        "completeness_counts": _counts(item.completeness_state for item in observations),
        "conflict_count": sum(item.quality_state == "CONFLICTING" for item in observations),
        "market_cap_observed_count": sum(item.market_cap_value is not None for item in observations),
        "earliest_semantics_counts": _counts(
            item.earliest_observation_semantics for item in observations
        ),
        "observations": [asdict(item) for item in observations],
    }
    return HistoricalMarketObservationManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        contract_version=CONTRACT_VERSION,
        adapter_version=ADAPTER_VERSION,
        normalizer_version=NORMALIZER_VERSION,
        platform_mint=metadata.platform_mint,
        interval=metadata.interval,
        request_from_ms=metadata.request_from_ms,
        request_to_ms=metadata.request_to_ms,
        request_run_id=metadata.request_run_id,
        physical_request_sequence=metadata.physical_request_sequence,
        request_cost_units=metadata.request_cost_units,
        raw_envelope_digest=normalized.raw_envelope_digest,
        response_projection_digest=projection_hash,
        observation_projection_digest=body["observation_projection_digest"],
        source_file_hashes=file_hashes,
        row_count=len(observations),
        quality_counts=body["quality_counts"],
        completeness_counts=body["completeness_counts"],
        conflict_count=body["conflict_count"],
        market_cap_observed_count=body["market_cap_observed_count"],
        earliest_semantics_counts=body["earliest_semantics_counts"],
        observations=observations,
        manifest_digest=canonical_digest(body),
    )


def verify_historical_market_observation_manifest(
    manifest: HistoricalMarketObservationManifest,
    *,
    envelope: Mapping[str, object],
    metadata: RequestMetadata,
    source_file_hashes: Mapping[str, str],
) -> bool:
    if manifest.schema_version != MANIFEST_SCHEMA_VERSION:
        _fail("SCHEMA_VERSION_MISMATCH")
    if manifest.contract_version != CONTRACT_VERSION:
        _fail("CONTRACT_VERSION_MISMATCH")
    if manifest.adapter_version != ADAPTER_VERSION:
        _fail("ADAPTER_VERSION_MISMATCH")
    if manifest.normalizer_version != NORMALIZER_VERSION:
        _fail("NORMALIZER_VERSION_MISMATCH")
    rebuilt = build_historical_market_observation_manifest(
        envelope=envelope, metadata=metadata, source_file_hashes=source_file_hashes
    )
    if rebuilt != manifest:
        _fail("REPLAY_MISMATCH")
    return True
