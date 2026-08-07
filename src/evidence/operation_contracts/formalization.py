"""Machine-enforced EP3.0A Operation Runtime Contract v1 semantics."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Sequence
from types import MappingProxyType

import jsonschema

from ..contracts import canonical_json_bytes


SCHEMA_PATH = Path(__file__).with_name("operation_contract_v1.schema.json")
OUTPUT_SCHEMA_PATH = Path(__file__).with_name("runtime_output_v1.schema.json")


class ContractLifecycle(str, Enum):
    DRAFT = "DRAFT"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    DISABLED = "DISABLED"


class Maturity(str, Enum):
    UNKNOWN = "UNKNOWN"
    OBSERVED = "OBSERVED"
    BEHAVIOURAL_CLUSTER = "BEHAVIOURAL_CLUSTER"
    INVESTIGATE = "INVESTIGATE"
    DISMISSED = "DISMISSED"


class GovernanceIdentity(str, Enum):
    UNCONFIRMED = "UNCONFIRMED"
    CONFIRMED_OPERATION = "CONFIRMED_OPERATION"
    CANONICAL = "CANONICAL"
    REVIEW = "REVIEW"


class Activity(str, Enum):
    ACTIVE = "ACTIVE"
    DORMANT = "DORMANT"
    REACTIVATED = "REACTIVATED"
    HISTORICAL = "HISTORICAL"


CONTRACT_TRANSITIONS = {
    ContractLifecycle.DRAFT: {ContractLifecycle.SHADOW, ContractLifecycle.DISABLED},
    ContractLifecycle.SHADOW: {ContractLifecycle.ACTIVE, ContractLifecycle.DEPRECATED, ContractLifecycle.DISABLED},
    ContractLifecycle.ACTIVE: {ContractLifecycle.DEPRECATED, ContractLifecycle.DISABLED},
    ContractLifecycle.DEPRECATED: {ContractLifecycle.DISABLED},
    ContractLifecycle.DISABLED: set(),
}

MATURITY_TRANSITIONS = {
    Maturity.UNKNOWN: {Maturity.OBSERVED},
    Maturity.OBSERVED: {Maturity.BEHAVIOURAL_CLUSTER, Maturity.DISMISSED},
    Maturity.BEHAVIOURAL_CLUSTER: {Maturity.INVESTIGATE, Maturity.DISMISSED},
    Maturity.INVESTIGATE: {Maturity.DISMISSED},
    Maturity.DISMISSED: {Maturity.OBSERVED},
}
GOVERNANCE_TRANSITIONS = {
    GovernanceIdentity.UNCONFIRMED: {GovernanceIdentity.REVIEW, GovernanceIdentity.CONFIRMED_OPERATION},
    GovernanceIdentity.REVIEW: {GovernanceIdentity.UNCONFIRMED, GovernanceIdentity.CONFIRMED_OPERATION},
    GovernanceIdentity.CONFIRMED_OPERATION: {GovernanceIdentity.CANONICAL, GovernanceIdentity.REVIEW},
    GovernanceIdentity.CANONICAL: {GovernanceIdentity.REVIEW},
}
ACTIVITY_TRANSITIONS = {
    Activity.ACTIVE: {Activity.DORMANT, Activity.HISTORICAL},
    Activity.DORMANT: {Activity.REACTIVATED, Activity.HISTORICAL},
    Activity.REACTIVATED: {Activity.ACTIVE, Activity.DORMANT, Activity.HISTORICAL},
    Activity.HISTORICAL: set(),
}


def _schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_contract_bytes(contract: Mapping[str, Any]) -> bytes:
    value = dict(contract)
    value.pop("contract_digest", None)
    return canonical_json_bytes(value)


def contract_digest(contract: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_contract_bytes(contract)).hexdigest()


def _semver(value: str) -> tuple[int, int, int, str]:
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?", value)
    if not match:
        raise ValueError(f"invalid semantic version: {value}")
    return int(match[1]), int(match[2]), int(match[3]), match[4] or ""


def _satisfies(version: str, constraint: str) -> bool:
    prefix = next((item for item in (">=", "^", "~", "=") if constraint.startswith(item)), "=")
    target = constraint[len(prefix):] if constraint.startswith(prefix) else constraint
    if "." not in version or "." not in target:
        if prefix != "=":
            raise ValueError("range constraints require semantic x.y.z versions")
        return version == target
    current_value, target_value = _semver(version), _semver(target)
    current, wanted = current_value[:3], target_value[:3]
    if prefix == "=": return current == wanted
    if prefix == ">=": return current >= wanted
    if prefix == "^": return current >= wanted and current[0] == wanted[0]
    return current >= wanted and current[:2] == wanted[:2]


def validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(contract))
    jsonschema.Draft202012Validator(_schema(SCHEMA_PATH)).validate(value)
    if value.get("contract_digest") and value["contract_digest"] != contract_digest(value):
        raise ValueError("contract_digest does not match canonical contract content")
    evidence = [item["fact_family"] for item in value["evidence_requirements"]]
    primitives = [item["primitive_type"] for item in value["primitive_requirements"]]
    modules = [(item["module_id"], item["module_version"]) for item in value["behaviour_modules"]]
    if len(evidence) != len(set(evidence)): raise ValueError("duplicate Evidence requirement")
    if len(primitives) != len(set(primitives)): raise ValueError("duplicate Primitive requirement")
    if len(modules) != len(set(modules)): raise ValueError("duplicate Behaviour module version")
    primitive_set = set(primitives)
    for module in value["behaviour_modules"]:
        missing = set(module["required_primitive_types"]) - primitive_set
        if missing: raise ValueError(f"Behaviour module has undeclared Primitive dependencies: {sorted(missing)}")
    topology = value["topology_contract"]
    roles = set(topology["local_roles"])
    for edge in topology["edge_rules"]:
        if edge["source_role"] not in roles or edge["destination_role"] not in roles:
            raise ValueError("topology edge references undeclared operation-local role")
        if edge["primitive_type"] not in primitive_set:
            raise ValueError("topology edge references undeclared Primitive dependency")
    value["contract_digest"] = contract_digest(value)
    return value


def validate_runtime_output(value: Mapping[str, Any]) -> None:
    jsonschema.Draft202012Validator(_schema(OUTPUT_SCHEMA_PATH)).validate(dict(value))


def _digest(kind: str, value: Mapping[str, Any]) -> str:
    body = dict(value)
    for key in ("observation_id", "revision_id", "input_id", "result_id", "recommendation_id", "generated_at"):
        body.pop(key, None)
    return hashlib.sha256(canonical_json_bytes([kind, body])).hexdigest()


@dataclass(frozen=True)
class Window:
    start: Optional[int]
    end: Optional[int]


@dataclass(frozen=True)
class CandidateState:
    maturity: str
    governance_identity: str
    activity: str


@dataclass(frozen=True)
class BehaviourModuleInput:
    contract_id: str
    contract_version: str
    module_id: str
    module_version: str
    subjects: tuple[str, ...]
    observation_window: Window
    primitive_refs: tuple[str, ...]
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class BehaviourObservation:
    output_type: str
    observation_id: str
    contract_id: str
    contract_version: str
    module_id: str
    module_version: str
    subjects: tuple[str, ...]
    parameters: Mapping[str, Any]
    observation_window: Window
    measured_values: Mapping[str, Any]
    evidence_refs: tuple[str, ...]
    primitive_refs: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    quality_state: str
    input_digest: str
    generated_at: int

    @classmethod
    def create(cls, **values: Any) -> "BehaviourObservation":
        body = {"output_type": "BehaviourObservation", **values}
        body["subjects"] = tuple(sorted(set(body["subjects"])))
        body["evidence_refs"] = tuple(sorted(set(body["evidence_refs"])))
        body["primitive_refs"] = tuple(sorted(set(body["primitive_refs"])))
        body["missing_inputs"] = tuple(sorted(set(body["missing_inputs"])))
        body["parameters"] = _freeze(body["parameters"])
        body["measured_values"] = _freeze(body["measured_values"])
        identity_body = _plain(body)
        body["observation_id"] = _digest("BehaviourObservation", identity_body)
        result = cls(**body)
        validate_runtime_output(result.to_dict())
        return result

    def to_dict(self) -> dict[str, Any]: return _plain(self)


@dataclass(frozen=True)
class TopologyNode:
    entity_ref: str
    local_role: str
    contract_id: str
    contract_version: str
    evidence_refs: tuple[str, ...]
    primitive_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(sorted(set(self.evidence_refs))))
        object.__setattr__(self, "primitive_refs", tuple(sorted(set(self.primitive_refs))))


@dataclass(frozen=True)
class TopologyEdge:
    source: str
    destination: str
    primitive_type: str
    cardinality: str
    temporal_constraint: Optional[Mapping[str, Any]]
    required: bool
    evidence_refs: tuple[str, ...]
    primitive_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "temporal_constraint", _freeze(self.temporal_constraint))
        object.__setattr__(self, "evidence_refs", tuple(sorted(set(self.evidence_refs))))
        object.__setattr__(self, "primitive_refs", tuple(sorted(set(self.primitive_refs))))


@dataclass(frozen=True)
class TopologyRevision:
    output_type: str
    revision_id: str
    contract_id: str
    contract_version: str
    topology_version: str
    subjects: tuple[str, ...]
    nodes: tuple[TopologyNode, ...]
    edges: tuple[TopologyEdge, ...]
    input_digest: str
    generated_at: int

    @classmethod
    def create(cls, **values: Any) -> "TopologyRevision":
        body = {"output_type": "TopologyRevision", **values}
        body["subjects"] = tuple(sorted(set(body["subjects"])))
        body["nodes"] = tuple(sorted(body["nodes"], key=lambda item: (item.entity_ref, item.local_role)))
        body["edges"] = tuple(sorted(body["edges"], key=lambda item: (item.source, item.destination, item.primitive_type)))
        body["revision_id"] = _digest("TopologyRevision", _plain(body))
        result = cls(**body); validate_runtime_output(result.to_dict()); return result

    def to_dict(self) -> dict[str, Any]: return _plain(self)


@dataclass(frozen=True)
class DetectorInput:
    output_type: str
    input_id: str
    contract_id: str
    contract_version: str
    detector_version: str
    subjects: tuple[str, ...]
    evidence_watermark: str
    primitive_watermark: str
    observation_window: Window
    evidence_refs: tuple[str, ...]
    primitive_refs: tuple[str, ...]
    behaviour_observation_refs: tuple[str, ...]
    topology_revision_ref: Optional[str]
    input_digest: str

    @classmethod
    def create(cls, **values: Any) -> "DetectorInput":
        body = {"output_type": "DetectorInput", **values}
        for key in ("subjects", "evidence_refs", "primitive_refs", "behaviour_observation_refs"):
            body[key] = tuple(sorted(set(body[key])))
        body["input_id"] = _digest("DetectorInput", _plain(body))
        result = cls(**body); validate_runtime_output(result.to_dict()); return result

    def to_dict(self) -> dict[str, Any]: return _plain(self)


@dataclass(frozen=True)
class DetectorResult:
    output_type: str
    result_id: str
    contract_id: str
    contract_version: str
    detector_version: str
    subjects: tuple[str, ...]
    observation_window: Window
    identity_evidence: Mapping[str, Any]
    topology_evidence: Mapping[str, Any]
    behaviour_evidence: Mapping[str, Any]
    operational_contact: Mapping[str, Any]
    infrastructure_overlap: Mapping[str, Any]
    funding_overlap: Mapping[str, Any]
    temporal_overlap: Mapping[str, Any]
    supporting_evidence_ids: tuple[str, ...]
    contradictory_evidence_ids: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    confidence_output: Optional[Mapping[str, Any]]
    candidate_lifecycle_recommendation: Optional[Mapping[str, Any]]
    governance_recommendation: Optional[str]
    input_watermark: Mapping[str, str]
    input_digest: str
    generated_at: int

    @classmethod
    def create(cls, **values: Any) -> "DetectorResult":
        body = {"output_type": "DetectorResult", **values}
        for key in ("subjects", "supporting_evidence_ids", "contradictory_evidence_ids", "missing_inputs"):
            body[key] = tuple(sorted(set(body[key])))
        for key in ("identity_evidence", "topology_evidence", "behaviour_evidence",
                    "operational_contact", "infrastructure_overlap", "funding_overlap",
                    "temporal_overlap", "confidence_output",
                    "candidate_lifecycle_recommendation", "input_watermark"):
            body[key] = _freeze(body[key])
        body["result_id"] = _digest("DetectorResult", _plain(body))
        result = cls(**body); validate_runtime_output(result.to_dict()); return result

    def to_dict(self) -> dict[str, Any]: return _plain(self)


@dataclass(frozen=True)
class LifecycleRecommendation:
    output_type: str
    recommendation_id: str
    contract_id: str
    contract_version: str
    subjects: tuple[str, ...]
    current_state: CandidateState
    recommended_state: CandidateState
    reason: str
    detector_result_ref: str
    input_digest: str
    generated_at: int
    automatic_execution: bool = False

    @classmethod
    def create(cls, **values: Any) -> "LifecycleRecommendation":
        body = {"output_type": "LifecycleRecommendation", "automatic_execution": False, **values}
        body["subjects"] = tuple(sorted(set(body["subjects"])))
        validate_candidate_transition(body["current_state"], body["recommended_state"])
        body["recommendation_id"] = _digest("LifecycleRecommendation", _plain(body))
        result = cls(**body); validate_runtime_output(result.to_dict()); return result

    def to_dict(self) -> dict[str, Any]: return _plain(self)


class BehaviourModuleProtocol(Protocol):
    module_id: str
    module_version: str
    def evaluate(self, value: BehaviourModuleInput) -> BehaviourObservation: ...


class TopologyModuleProtocol(Protocol):
    topology_version: str
    def generate(self, *, contract: Mapping[str, Any], subjects: Sequence[str],
                 primitive_refs: Sequence[str], evidence_refs: Sequence[str]) -> TopologyRevision: ...


class DetectorProtocol(Protocol):
    detector_id: str
    detector_version: str
    def evaluate(self, value: DetectorInput) -> DetectorResult: ...


def validate_candidate_transition(current: CandidateState, target: CandidateState) -> None:
    changes = 0
    for source, destination, graph, enum_type in (
        (current.maturity, target.maturity, MATURITY_TRANSITIONS, Maturity),
        (current.governance_identity, target.governance_identity, GOVERNANCE_TRANSITIONS, GovernanceIdentity),
        (current.activity, target.activity, ACTIVITY_TRANSITIONS, Activity),
    ):
        if source != destination:
            changes += 1
            if enum_type(destination) not in graph[enum_type(source)]:
                raise ValueError(f"invalid candidate lifecycle transition: {source} -> {destination}")
    if changes != 1:
        raise ValueError("a lifecycle recommendation must change exactly one dimension")


@dataclass(frozen=True)
class _RegistryRecord:
    contract: Mapping[str, Any]
    digest: str
    state: ContractLifecycle
    published: bool


class ContractRegistryModel:
    """Reference semantics for EP3.0 registry implementation tests."""

    def __init__(self, *, evidence_versions: Mapping[str, Sequence[str]],
                 primitive_versions: Mapping[str, Sequence[str]],
                 behaviour_versions: Mapping[str, Sequence[str]],
                 detector_versions: Mapping[str, Sequence[str]],
                 presentation_versions: Sequence[str]) -> None:
        self.evidence_versions = evidence_versions
        self.primitive_versions = primitive_versions
        self.behaviour_versions = behaviour_versions
        self.detector_versions = detector_versions
        self.presentation_versions = tuple(presentation_versions)
        self._records: dict[tuple[str, str], _RegistryRecord] = {}

    def register(self, contract: Mapping[str, Any]) -> str:
        value = validate_contract(contract)
        key = value["contract_id"], value["contract_version"]
        existing = self._records.get(key)
        if existing:
            if existing.digest != value["contract_digest"]:
                raise ValueError("contract ID/version collision with different content")
            return existing.digest
        self._check_dependencies(value)
        state = ContractLifecycle(value["lifecycle_status"])
        frozen = _freeze(value)
        self._records[key] = _RegistryRecord(frozen, value["contract_digest"], state,
                                             state is not ContractLifecycle.DRAFT)
        return value["contract_digest"]

    def _check_dependencies(self, contract: Mapping[str, Any]) -> None:
        groups = (
            (contract["evidence_requirements"], "fact_family", self.evidence_versions),
            (contract["primitive_requirements"], "primitive_type", self.primitive_versions),
        )
        for requirements, key, available in groups:
            for item in requirements:
                versions = available.get(item[key], ())
                if item["required"] and not any(_satisfies(version, item["version_constraint"]) for version in versions):
                    raise ValueError(f"missing or incompatible dependency: {item[key]}")
        for item in contract["behaviour_modules"]:
            versions = self.behaviour_versions.get(item["module_id"], ())
            if item["module_version"] not in versions:
                raise ValueError(f"missing Behaviour module: {item['module_id']}@{item['module_version']}")
        detector = contract["detector"]
        if detector["detector_version"] not in self.detector_versions.get(detector["detector_id"], ()):
            raise ValueError("missing Detector dependency")
        if contract["presentation_schema"]["schema_version"] not in self.presentation_versions:
            raise ValueError("missing Presentation schema dependency")

    def transition(self, contract_id: str, version: str, target: ContractLifecycle) -> None:
        key = contract_id, version
        record = self._records[key]
        if target not in CONTRACT_TRANSITIONS[record.state]:
            raise ValueError(f"invalid contract transition: {record.state.value} -> {target.value}")
        if target is ContractLifecycle.ACTIVE:
            active = [item for item in self._records.values()
                      if item.contract["contract_id"] == contract_id and item.state is ContractLifecycle.ACTIVE]
            if active: raise ValueError("only one ACTIVE version is permitted per Operation")
            self._check_dependencies(record.contract)
        self._records[key] = replace(record, state=target, published=True)

    def rollback(self, contract_id: str, target_version: str) -> None:
        target_key = contract_id, target_version
        target = self._records[target_key]
        if target.state not in {ContractLifecycle.DEPRECATED, ContractLifecycle.SHADOW}:
            raise ValueError("rollback target must be a published SHADOW or DEPRECATED version")
        active_keys = [key for key, item in self._records.items()
                       if item.contract["contract_id"] == contract_id and item.state is ContractLifecycle.ACTIVE]
        if len(active_keys) != 1: raise ValueError("rollback requires exactly one current ACTIVE version")
        self._check_dependencies(target.contract)
        current_key = active_keys[0]
        self._records[current_key] = replace(self._records[current_key], state=ContractLifecycle.DEPRECATED)
        self._records[target_key] = replace(target, state=ContractLifecycle.ACTIVE, published=True)

    def active(self, contract_id: str) -> Optional[Mapping[str, Any]]:
        items = [record.contract for record in self._records.values()
                 if record.contract["contract_id"] == contract_id and record.state is ContractLifecycle.ACTIVE]
        return items[0] if items else None

    def versions(self, contract_id: str) -> tuple[str, ...]:
        return tuple(sorted((version for key_id, version in self._records if key_id == contract_id), key=_semver))


def _plain(value: Any) -> Any:
    if isinstance(value, Enum): return value.value
    if is_dataclass(value): return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping): return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)): return [_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if value is None: return None
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value
