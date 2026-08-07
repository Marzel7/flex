"""Generic, non-authoritative Operation Contract v1 execution runtime."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from ..contracts import canonical_json_bytes
from .formalization import (
    BehaviourModuleInput, BehaviourObservation, CandidateState,
    ContractLifecycle, ContractRegistryModel, DetectorInput, DetectorResult,
    LifecycleRecommendation, TopologyRevision, Window, _satisfies,
)
from .registry import RuntimeRegistries
from .storage import OperationRuntimeStore


@dataclass(frozen=True)
class EvidenceAvailability:
    evidence_id: str
    fact_family: str
    fact_schema_version: str


@dataclass(frozen=True)
class PrimitiveAvailability:
    primitive_id: str
    primitive_type: str
    primitive_version: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationRequest:
    contract_id: str
    contract_version: Optional[str]
    subjects: tuple[str, ...]
    observation_window: Window
    evidence: tuple[EvidenceAvailability, ...]
    primitives: tuple[PrimitiveAvailability, ...]
    evidence_watermark: str
    primitive_watermark: str
    current_candidate_state: Optional[CandidateState]
    generated_at: int


@dataclass(frozen=True)
class EvaluationResult:
    contract_id: str
    contract_version: str
    behaviours: tuple[BehaviourObservation, ...]
    topology: TopologyRevision
    detector_input: DetectorInput
    detector_result: DetectorResult
    lifecycle_recommendation: Optional[LifecycleRecommendation]
    persistence: Mapping[str, int]


class OperationRuntime:
    """Executes registered code selected only by a validated contract."""

    def __init__(self, *, contracts: ContractRegistryModel,
                 registries: RuntimeRegistries, store: OperationRuntimeStore) -> None:
        self.contracts = contracts
        self.registries = registries
        self.store = store

    def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        contract = self._select_contract(request)
        version = str(contract["contract_version"])
        evidence_refs = tuple(sorted({item.evidence_id for item in request.evidence}))
        primitive_refs = tuple(sorted({item.primitive_id for item in request.primitives}))
        self._validate_requirements(contract, request)
        input_digest = hashlib.sha256(canonical_json_bytes({
            "contract": contract["contract_digest"],
            "subjects": sorted(set(request.subjects)),
            "window": {"start": request.observation_window.start,
                       "end": request.observation_window.end},
            "evidence_watermark": request.evidence_watermark,
            "primitive_watermark": request.primitive_watermark,
            "evidence_refs": evidence_refs,
            "primitive_refs": primitive_refs,
        })).hexdigest()

        behaviours = []
        for declaration in contract["behaviour_modules"]:
            implementation = self.registries.behaviours.resolve(
                declaration["module_id"], declaration["module_version"]
            )
            allowed_types = set(declaration["required_primitive_types"])
            selected = tuple(sorted(item.primitive_id for item in request.primitives
                                    if item.primitive_type in allowed_types))
            observation = implementation.evaluate(BehaviourModuleInput(
                contract_id=request.contract_id, contract_version=version,
                module_id=declaration["module_id"],
                module_version=declaration["module_version"],
                subjects=tuple(sorted(set(request.subjects))),
                observation_window=request.observation_window,
                primitive_refs=selected,
                parameters=declaration["parameters"],
            ))
            self._validate_behaviour(observation, declaration, request.contract_id, version,
                                     request.subjects, evidence_refs, primitive_refs)
            behaviours.append(observation)

        topology_declaration = contract["topology_contract"]
        topology_impl = self.registries.topologies.resolve(topology_declaration["topology_version"])
        topology = topology_impl.generate(
            contract=contract, subjects=request.subjects,
            primitive_refs=primitive_refs, evidence_refs=evidence_refs,
        )
        self._validate_topology(topology, contract, request, evidence_refs, primitive_refs)

        detector_declaration = contract["detector"]
        detector_input = DetectorInput.create(
            contract_id=request.contract_id, contract_version=version,
            detector_version=detector_declaration["detector_version"],
            subjects=request.subjects, evidence_watermark=request.evidence_watermark,
            primitive_watermark=request.primitive_watermark,
            observation_window=request.observation_window,
            evidence_refs=evidence_refs, primitive_refs=primitive_refs,
            behaviour_observation_refs=tuple(item.observation_id for item in behaviours),
            topology_revision_ref=topology.revision_id, input_digest=input_digest,
        )
        detector = self.registries.detectors.resolve(
            detector_declaration["detector_id"], detector_declaration["detector_version"]
        )
        detector_result = detector.evaluate(detector_input)
        self._validate_detector_result(detector_result, detector_input, contract)
        lifecycle = self._lifecycle_recommendation(
            detector_result, request.current_candidate_state, request.generated_at
        )
        outputs: list[Any] = [*behaviours, topology, detector_input, detector_result]
        if lifecycle is not None:
            outputs.append(lifecycle)
        persistence = self.store.append_outputs(outputs)
        return EvaluationResult(
            contract_id=request.contract_id, contract_version=version,
            behaviours=tuple(behaviours), topology=topology,
            detector_input=detector_input, detector_result=detector_result,
            lifecycle_recommendation=lifecycle, persistence=persistence,
        )

    def presentation(self, contract_id: str, version: Optional[str] = None) -> Mapping[str, Any]:
        contract = self._contract(contract_id, version)
        declaration = contract["presentation_schema"]
        self.registries.presentations.resolve(declaration["schema_version"])
        return dict(declaration)

    def governance_policy(self, contract_id: str, version: Optional[str] = None) -> Mapping[str, Any]:
        return dict(self._contract(contract_id, version)["governance_policy"])

    def _contract(self, contract_id: str, version: Optional[str]) -> Mapping[str, Any]:
        contract = self.contracts.active(contract_id) if version is None else self.contracts.get(contract_id, version)
        if contract is None:
            raise LookupError(f"no ACTIVE contract: {contract_id}")
        return contract

    def _select_contract(self, request: EvaluationRequest) -> Mapping[str, Any]:
        contract = self._contract(request.contract_id, request.contract_version)
        state = self.contracts.state(request.contract_id, str(contract["contract_version"]))
        if state not in {ContractLifecycle.SHADOW, ContractLifecycle.ACTIVE}:
            raise ValueError(f"contract is not executable in state {state.value}")
        return contract

    @staticmethod
    def _validate_requirements(contract: Mapping[str, Any], request: EvaluationRequest) -> None:
        evidence = {(item.fact_family, item.fact_schema_version) for item in request.evidence}
        primitives = {(item.primitive_type, item.primitive_version) for item in request.primitives}
        for declaration, available, key in (
            (contract["evidence_requirements"], evidence, "fact_family"),
            (contract["primitive_requirements"], primitives, "primitive_type"),
        ):
            for item in declaration:
                if item["required"] and not any(
                    name == item[key] and _satisfies(version, item["version_constraint"])
                    for name, version in available
                ):
                    raise ValueError(f"required runtime input unavailable: {item[key]}")

    @staticmethod
    def _validate_behaviour(value: BehaviourObservation, declaration: Mapping[str, Any],
                            contract_id: str, version: str, subjects: Sequence[str],
                            evidence_refs: Sequence[str], primitive_refs: Sequence[str]) -> None:
        if (value.contract_id, value.contract_version, value.module_id, value.module_version) != (
            contract_id, version, declaration["module_id"], declaration["module_version"]
        ):
            raise ValueError("Behaviour Observation producer identity mismatch")
        if value.subjects != tuple(sorted(set(subjects))) or dict(value.parameters) != dict(declaration["parameters"]):
            raise ValueError("Behaviour Observation input identity mismatch")
        if not set(value.evidence_refs) <= set(evidence_refs) or not set(value.primitive_refs) <= set(primitive_refs):
            raise ValueError("Behaviour Observation references undeclared runtime inputs")

    @staticmethod
    def _validate_topology(value: TopologyRevision, contract: Mapping[str, Any],
                           request: EvaluationRequest, evidence_refs: Sequence[str],
                           primitive_refs: Sequence[str]) -> None:
        declaration = contract["topology_contract"]
        if (value.contract_id, value.contract_version, value.topology_version) != (
            request.contract_id, contract["contract_version"], declaration["topology_version"]
        ):
            raise ValueError("Topology Revision producer identity mismatch")
        roles = set(declaration["local_roles"])
        rules = {(item["source_role"], item["destination_role"], item["primitive_type"])
                 for item in declaration["edge_rules"]}
        node_roles = {item.entity_ref: item.local_role for item in value.nodes}
        if not set(node_roles.values()) <= roles:
            raise ValueError("Topology Revision contains undeclared local role")
        if value.subjects != tuple(sorted(set(request.subjects))):
            raise ValueError("Topology Revision subjects do not match evaluation")
        for node in value.nodes:
            if not set(node.evidence_refs) <= set(evidence_refs) or not set(node.primitive_refs) <= set(primitive_refs):
                raise ValueError("Topology Revision references undeclared runtime inputs")
        for edge in value.edges:
            signature = (node_roles.get(edge.source), node_roles.get(edge.destination), edge.primitive_type)
            if signature not in rules:
                raise ValueError("Topology Revision edge is not permitted by contract")
            if not set(edge.evidence_refs) <= set(evidence_refs) or not set(edge.primitive_refs) <= set(primitive_refs):
                raise ValueError("Topology Revision references undeclared runtime inputs")

    @staticmethod
    def _validate_detector_result(value: DetectorResult, detector_input: DetectorInput,
                                  contract: Mapping[str, Any]) -> None:
        if (value.contract_id, value.contract_version, value.detector_version,
                value.input_digest) != (
            detector_input.contract_id, detector_input.contract_version,
            detector_input.detector_version, detector_input.input_digest,
        ):
            raise ValueError("Detector Result identity does not match Detector Input")
        if value.subjects != detector_input.subjects:
            raise ValueError("Detector Result subjects do not match Detector Input")
        evidence_refs = set(detector_input.evidence_refs)
        if not set(value.supporting_evidence_ids) <= evidence_refs or not set(value.contradictory_evidence_ids) <= evidence_refs:
            raise ValueError("Detector Result references undeclared Evidence")
        allowed = set(contract["governance_policy"]["allowed_recommendations"])
        if value.governance_recommendation is not None and value.governance_recommendation not in allowed:
            raise ValueError("Detector Result governance recommendation is not allowed by contract")

    @staticmethod
    def _lifecycle_recommendation(result: DetectorResult, current: Optional[CandidateState],
                                  generated_at: int) -> Optional[LifecycleRecommendation]:
        proposal = result.candidate_lifecycle_recommendation
        if proposal is None:
            return None
        if current is None:
            raise ValueError("candidate state is required for a lifecycle recommendation")
        target = CandidateState(**dict(proposal["recommended_state"]))
        return LifecycleRecommendation.create(
            contract_id=result.contract_id, contract_version=result.contract_version,
            subjects=result.subjects, current_state=current, recommended_state=target,
            reason=str(proposal["reason"]), detector_result_ref=result.result_id,
            input_digest=result.input_digest, generated_at=generated_at,
        )
