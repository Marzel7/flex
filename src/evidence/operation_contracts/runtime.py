"""Generic, non-authoritative Operation Contract v1 execution runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from ..contracts import EvidenceRecord
from ..primitives.contracts import PrimitiveObservation
from .formalization import (
    BehaviourModuleInput, BehaviourObservation, CandidateState,
    ContractLifecycle, ContractRegistryModel, DetectorInput, DetectorResult,
    LifecycleRecommendation, TopologyModuleInput, TopologyRevision, Window, _satisfies,
)
from .input_windows import (
    EvidenceInputWindow, PrimitiveInputWindow, RuntimeEvaluationSnapshot,
)
from .registry import RuntimeRegistries
from .storage import OperationRuntimeStore


@dataclass(frozen=True)
class EvaluationRequest:
    contract_id: str
    contract_version: Optional[str]
    subjects: tuple[str, ...]
    observation_window: Window
    evidence: tuple[EvidenceRecord, ...]
    primitives: tuple[PrimitiveObservation, ...]
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
        snapshot = self.materialize_snapshot(request, contract=contract)
        return self.evaluate_snapshot(snapshot, current_candidate_state=request.current_candidate_state)

    def materialize_snapshot(self, request: EvaluationRequest,
                             *, contract: Mapping[str, Any] | None = None) -> RuntimeEvaluationSnapshot:
        """Orchestration owns resolution; returned snapshot needs no data-store reads."""
        contract = contract or self._select_contract(request)
        subjects = set(request.subjects)
        evidence_requirements = contract["evidence_requirements"]
        primitive_requirements = contract["primitive_requirements"]
        evidence = tuple(item for item in request.evidence if any(
            item.fact_family == requirement["fact_family"]
            and _satisfies(item.fact_schema_version, requirement["version_constraint"])
            for requirement in evidence_requirements
        ) and self._time_contains(item.observed_at, request.observation_window))
        primitives = tuple(item for item in request.primitives if any(
            item.primitive_type == requirement["primitive_type"]
            and _satisfies(item.primitive_version, requirement["version_constraint"])
            for requirement in primitive_requirements
        ) and self._window_overlaps(item.observation_window, request.observation_window)
            and (not subjects or bool(subjects.intersection(item.subjects))))
        evidence_window = EvidenceInputWindow.create(
            subjects=request.subjects, start=request.observation_window.start,
            end=request.observation_window.end, watermark=request.evidence_watermark,
            observations=evidence,
        )
        primitive_window = PrimitiveInputWindow.create(
            subjects=request.subjects, start=request.observation_window.start,
            end=request.observation_window.end, watermark=request.primitive_watermark,
            observations=primitives,
        )
        snapshot = RuntimeEvaluationSnapshot.create(
            contract=contract, subjects=request.subjects,
            observation_start=request.observation_window.start,
            observation_end=request.observation_window.end,
            evidence_window=evidence_window, primitive_window=primitive_window,
            generated_at=request.generated_at,
        )
        self._validate_requirements(contract, snapshot)
        return snapshot

    def evaluate_snapshot(self, snapshot: RuntimeEvaluationSnapshot, *,
                          current_candidate_state: Optional[CandidateState]) -> EvaluationResult:
        contract = self.contracts.get(snapshot.contract_id, snapshot.contract_version)
        if contract["contract_digest"] != snapshot.contract_digest:
            raise ValueError("snapshot Contract digest mismatch")
        state = self.contracts.state(snapshot.contract_id, snapshot.contract_version)
        if state not in {ContractLifecycle.SHADOW, ContractLifecycle.ACTIVE}:
            raise ValueError(f"contract is not executable in state {state.value}")
        self._validate_requirements(contract, snapshot)
        version = str(contract["contract_version"])
        evidence_refs = snapshot.evidence_window.refs
        primitive_refs = snapshot.primitive_window.refs
        observation_window = Window(snapshot.observation_start, snapshot.observation_end)

        behaviours = []
        for declaration in contract["behaviour_modules"]:
            implementation = self.registries.behaviours.resolve(
                declaration["module_id"], declaration["module_version"]
            )
            allowed_types = set(declaration["required_primitive_types"])
            selected_window = snapshot.primitive_window.select(tuple(allowed_types))
            observation = implementation.evaluate(BehaviourModuleInput(
                contract_id=snapshot.contract_id, contract_version=version,
                module_id=declaration["module_id"],
                module_version=declaration["module_version"],
                subjects=snapshot.subjects,
                observation_window=observation_window,
                evidence_window=snapshot.evidence_window,
                primitive_window=selected_window,
                primitive_refs=selected_window.refs,
                parameters=declaration["parameters"],
                snapshot_digest=snapshot.input_digest,
                generated_at=snapshot.generated_at,
            ))
            self._validate_behaviour(observation, declaration, snapshot.contract_id, version,
                                     snapshot.subjects, evidence_refs, primitive_refs)
            behaviours.append(observation)

        topology_declaration = contract["topology_contract"]
        topology_impl = self.registries.topologies.resolve(topology_declaration["topology_version"])
        topology = topology_impl.generate(TopologyModuleInput(
            contract=contract, subjects=snapshot.subjects,
            observation_window=observation_window,
            evidence_window=snapshot.evidence_window,
            primitive_window=snapshot.primitive_window,
            behaviour_observations=tuple(behaviours),
            snapshot_digest=snapshot.input_digest, generated_at=snapshot.generated_at,
        ))
        self._validate_topology(topology, contract, snapshot, evidence_refs, primitive_refs,
                                tuple(item.observation_id for item in behaviours))

        detector_declaration = contract["detector"]
        detector_input = DetectorInput.create(
            contract_id=snapshot.contract_id, contract_version=version,
            detector_version=detector_declaration["detector_version"],
            subjects=snapshot.subjects, evidence_watermark=snapshot.evidence_window.watermark,
            primitive_watermark=snapshot.primitive_window.watermark,
            observation_window=observation_window,
            evidence_refs=evidence_refs, primitive_refs=primitive_refs,
            behaviour_observation_refs=tuple(item.observation_id for item in behaviours),
            topology_revision_ref=topology.revision_id,
            evidence_window=snapshot.evidence_window,
            primitive_window=snapshot.primitive_window,
            behaviour_observations=tuple(behaviours), topology_revision=topology,
            snapshot_digest=snapshot.input_digest, generated_at=snapshot.generated_at,
            input_digest=snapshot.input_digest,
        )
        detector = self.registries.detectors.resolve(
            detector_declaration["detector_id"], detector_declaration["detector_version"]
        )
        detector_result = detector.evaluate(detector_input)
        self._validate_detector_result(detector_result, detector_input, contract)
        lifecycle = self._lifecycle_recommendation(
            detector_result, current_candidate_state, snapshot.generated_at
        )
        outputs: list[Any] = [*behaviours, topology, detector_input, detector_result]
        if lifecycle is not None:
            outputs.append(lifecycle)
        persistence = self.store.append_outputs(outputs)
        return EvaluationResult(
            contract_id=snapshot.contract_id, contract_version=version,
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
    def _validate_requirements(contract: Mapping[str, Any], snapshot: RuntimeEvaluationSnapshot) -> None:
        evidence = {(item.fact_family, item.fact_schema_version)
                    for item in snapshot.evidence_window.observations}
        primitives = {(item.primitive_type, item.primitive_version)
                      for item in snapshot.primitive_window.observations}
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
                           snapshot: RuntimeEvaluationSnapshot, evidence_refs: Sequence[str],
                           primitive_refs: Sequence[str], behaviour_refs: Sequence[str]) -> None:
        declaration = contract["topology_contract"]
        if (value.contract_id, value.contract_version, value.topology_version) != (
            snapshot.contract_id, contract["contract_version"], declaration["topology_version"]
        ):
            raise ValueError("Topology Revision producer identity mismatch")
        roles = set(declaration["local_roles"])
        rules = {(item["source_role"], item["destination_role"], item["primitive_type"])
                 for item in declaration["edge_rules"]}
        node_roles = {item.entity_ref: item.local_role for item in value.nodes}
        if not set(node_roles.values()) <= roles:
            raise ValueError("Topology Revision contains undeclared local role")
        if value.subjects != snapshot.subjects:
            raise ValueError("Topology Revision subjects do not match evaluation")
        if value.behaviour_observation_refs != tuple(sorted(set(behaviour_refs))):
            raise ValueError("Topology Revision Behaviour provenance mismatch")
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
        if value.primitive_refs != detector_input.primitive_refs:
            raise ValueError("Detector Result Primitive provenance mismatch")
        if value.behaviour_observation_refs != detector_input.behaviour_observation_refs:
            raise ValueError("Detector Result Behaviour provenance mismatch")
        if value.topology_revision_ref != detector_input.topology_revision_ref:
            raise ValueError("Detector Result Topology provenance mismatch")

    @staticmethod
    def _time_contains(timestamp: int, window: Window) -> bool:
        return not ((window.start is not None and timestamp < window.start)
                    or (window.end is not None and timestamp > window.end))

    @staticmethod
    def _window_overlaps(candidate: Any, window: Window) -> bool:
        return not ((window.start is not None and candidate.end is not None and candidate.end < window.start)
                    or (window.end is not None and candidate.start is not None and candidate.start > window.end))

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
