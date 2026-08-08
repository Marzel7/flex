"""Immutable, bounded EP3.0B runtime observation input contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence

from ..contracts import EvidenceProvenance, EvidenceRecord, canonical_json_bytes
from ..primitives.contracts import ObservationWindow, PrimitiveObservation


def freeze(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool, Enum)):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(freeze(item) for item in value)
    if is_dataclass(value):
        return value
    raise TypeError(f"runtime snapshot cannot freeze {type(value).__name__}")


def plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


def immutable_evidence(record: EvidenceRecord) -> EvidenceRecord:
    provenance = EvidenceProvenance(
        endpoint_method=record.provenance.endpoint_method,
        request_parameters_digest=record.provenance.request_parameters_digest,
        upstream_dependency=record.provenance.upstream_dependency,
        acquisition_path=record.provenance.acquisition_path,
        cache_source=record.provenance.cache_source,
        dependency_group=record.provenance.dependency_group,
        parent_evidence_ids=tuple(record.provenance.parent_evidence_ids),
    )
    return replace(record, payload=freeze(record.payload), provenance=provenance)


def immutable_primitive(record: PrimitiveObservation) -> PrimitiveObservation:
    return replace(
        record,
        evidence_ids=tuple(record.evidence_ids), subjects=tuple(record.subjects),
        parameters=freeze(record.parameters), output_payload=freeze(record.output_payload),
        observation_window=ObservationWindow(record.observation_window.start,
                                             record.observation_window.end),
        missing_inputs=tuple(record.missing_inputs),
    )


def _unique(records: Sequence[Any], key: str, label: str) -> tuple[Any, ...]:
    by_id: dict[str, Any] = {}
    payloads: dict[str, bytes] = {}
    for item in records:
        item_id = str(getattr(item, key))
        payload = canonical_json_bytes(plain(item))
        if item_id in payloads and payloads[item_id] != payload:
            raise ValueError(f"{label} identity collision: {item_id}")
        payloads[item_id] = payload
        by_id[item_id] = item
    return tuple(by_id[item_id] for item_id in sorted(by_id))


@dataclass(frozen=True)
class EvidenceInputWindow:
    subjects: tuple[str, ...]
    start: Optional[int]
    end: Optional[int]
    watermark: str
    observations: tuple[EvidenceRecord, ...]
    digest: str

    @classmethod
    def create(cls, *, subjects: Sequence[str], start: Optional[int], end: Optional[int],
               watermark: str, observations: Sequence[EvidenceRecord],
               maximum: int = 10_000) -> "EvidenceInputWindow":
        if len(observations) > maximum:
            raise ValueError(f"Evidence input window exceeds bound of {maximum}")
        ordered = _unique(tuple(immutable_evidence(item) for item in observations),
                          "evidence_id", "Evidence")
        body = {"subjects": sorted(set(subjects)), "start": start, "end": end,
                "watermark": watermark, "observations": plain(ordered)}
        digest = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
        return cls(tuple(body["subjects"]), start, end, watermark, ordered, digest)

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(item.evidence_id for item in self.observations)

    def to_dict(self) -> dict[str, Any]:
        return plain(self)


@dataclass(frozen=True)
class PrimitiveInputWindow:
    subjects: tuple[str, ...]
    start: Optional[int]
    end: Optional[int]
    watermark: str
    observations: tuple[PrimitiveObservation, ...]
    digest: str

    @classmethod
    def create(cls, *, subjects: Sequence[str], start: Optional[int], end: Optional[int],
               watermark: str, observations: Sequence[PrimitiveObservation],
               maximum: int = 10_000) -> "PrimitiveInputWindow":
        if len(observations) > maximum:
            raise ValueError(f"Primitive input window exceeds bound of {maximum}")
        ordered = _unique(tuple(immutable_primitive(item) for item in observations),
                          "primitive_id", "Primitive")
        body = {"subjects": sorted(set(subjects)), "start": start, "end": end,
                "watermark": watermark, "observations": plain(ordered)}
        digest = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
        return cls(tuple(body["subjects"]), start, end, watermark, ordered, digest)

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(item.primitive_id for item in self.observations)

    def select(self, primitive_types: Sequence[str]) -> "PrimitiveInputWindow":
        allowed = set(primitive_types)
        return PrimitiveInputWindow.create(
            subjects=self.subjects, start=self.start, end=self.end, watermark=self.watermark,
            observations=tuple(item for item in self.observations
                               if item.primitive_type in allowed),
            maximum=len(self.observations) or 1,
        )

    def to_dict(self) -> dict[str, Any]:
        return plain(self)


@dataclass(frozen=True)
class RuntimeEvaluationSnapshot:
    contract_id: str
    contract_version: str
    contract_digest: str
    module_versions: tuple[tuple[str, str], ...]
    topology_version: str
    detector_version: str
    subjects: tuple[str, ...]
    observation_start: Optional[int]
    observation_end: Optional[int]
    generated_at: int
    evidence_window: EvidenceInputWindow
    primitive_window: PrimitiveInputWindow
    input_digest: str

    @classmethod
    def create(cls, *, contract: Mapping[str, Any], subjects: Sequence[str],
               observation_start: Optional[int], observation_end: Optional[int],
               evidence_window: EvidenceInputWindow,
               primitive_window: PrimitiveInputWindow,
               generated_at: int) -> "RuntimeEvaluationSnapshot":
        modules = tuple(sorted((item["module_id"], item["module_version"])
                               for item in contract["behaviour_modules"]))
        body = {
            "contract_id": contract["contract_id"],
            "contract_version": contract["contract_version"],
            "contract_digest": contract["contract_digest"],
            "module_versions": modules,
            "topology_version": contract["topology_contract"]["topology_version"],
            "detector_version": contract["detector"]["detector_version"],
            "subjects": sorted(set(subjects)),
            "observation_start": observation_start,
            "observation_end": observation_end,
            "generated_at": int(generated_at),
            "evidence_window_digest": evidence_window.digest,
            "primitive_window_digest": primitive_window.digest,
        }
        digest = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
        return cls(
            contract_id=body["contract_id"], contract_version=body["contract_version"],
            contract_digest=body["contract_digest"], module_versions=modules,
            topology_version=body["topology_version"], detector_version=body["detector_version"],
            subjects=tuple(body["subjects"]), observation_start=observation_start,
            observation_end=observation_end, generated_at=int(generated_at), evidence_window=evidence_window,
            primitive_window=primitive_window, input_digest=digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return plain(self)
