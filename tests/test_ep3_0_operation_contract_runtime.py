from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from src.evidence.operation_contracts import (
    BehaviourObservation, CandidateState, ContractLifecycle, ContractRegistryModel,
    DetectorResult, TopologyEdge, TopologyNode, TopologyRevision, Window,
)
from src.evidence.operation_contracts.loader import OperationContractLoader
from src.evidence.operation_contracts.registry import RuntimeRegistries
from src.evidence.operation_contracts.runtime import (
    EvaluationRequest, EvidenceAvailability, OperationRuntime, PrimitiveAvailability,
)
from src.evidence.operation_contracts.storage import OperationRuntimeStore


A = "a" * 64
B = "b" * 64
C = "c" * 64


def _contract(version="1.0.0"):
    return {
        "schema_version": "1", "contract_id": "fixture.operation",
        "contract_version": version, "lifecycle_status": "DRAFT",
        "evidence_requirements": [{"fact_family": "TransactionFact", "version_constraint": "1", "required": True}],
        "primitive_requirements": [{"primitive_type": "SYSTEM_TRANSFER", "version_constraint": "1", "required": True}],
        "behaviour_modules": [{"module_id": "fixture.behaviour", "module_version": "1.0.0",
                               "required_primitive_types": ["SYSTEM_TRANSFER"], "parameters": {}}],
        "topology_contract": {"topology_version": "1.0.0", "local_roles": ["SOURCE", "DESTINATION"],
                              "edge_rules": [{"rule_id": "transfer", "source_role": "SOURCE",
                                              "destination_role": "DESTINATION", "primitive_type": "SYSTEM_TRANSFER",
                                              "cardinality": "ONE_TO_ONE", "temporal_constraint": None,
                                              "required": True}], "parameters": {}},
        "detector": {"detector_id": "fixture.detector", "detector_version": "1.0.0", "parameters": {}},
        "identity_contract": {"mode": "DECLARATIVE", "configuration": {}, "runtime_execution": "LOAD_VALIDATE_ONLY"},
        "confidence_model": {"model_type": "DISABLED", "configuration": {}, "runtime_execution": "LOAD_VALIDATE_ONLY"},
        "governance_policy": {"allowed_recommendations": ["OBSERVE_ONLY"], "automatic_execution": False,
                              "runtime_execution": "LOAD_VALIDATE_ONLY"},
        "monitoring_policy": {"evaluation_trigger": "MANUAL", "minimum_evidence_state": "COMPLETE",
                              "reevaluation_conditions": [], "staleness_window_seconds": None,
                              "dormancy_window_seconds": None, "runtime_execution": "LOAD_VALIDATE_ONLY"},
        "presentation_schema": {"schema_version": "1.0.0", "role_labels": {}, "topology_labels": {},
                                "evidence_class_labels": {}, "section_order": ["summary"],
                                "allowed_actions": ["open"], "display_contract_version": True},
    }


class Behaviour:
    module_id = "fixture.behaviour"
    module_version = "1.0.0"

    def evaluate(self, value):
        return BehaviourObservation.create(
            contract_id=value.contract_id, contract_version=value.contract_version,
            module_id=self.module_id, module_version=self.module_version,
            subjects=value.subjects, parameters=value.parameters,
            observation_window=value.observation_window, measured_values={"observed": 1},
            evidence_refs=(A,), primitive_refs=value.primitive_refs, missing_inputs=(),
            quality_state="PROVEN", input_digest=C, generated_at=10,
        )


class Topology:
    topology_version = "1.0.0"

    def generate(self, *, contract, subjects, primitive_refs, evidence_refs):
        nodes = (
            TopologyNode("source", "SOURCE", contract["contract_id"], contract["contract_version"], (A,), (B,)),
            TopologyNode("destination", "DESTINATION", contract["contract_id"], contract["contract_version"], (A,), (B,)),
        )
        edge = TopologyEdge("source", "destination", "SYSTEM_TRANSFER", "ONE_TO_ONE", None, True, (A,), (B,))
        return TopologyRevision.create(
            contract_id=contract["contract_id"], contract_version=contract["contract_version"],
            topology_version=self.topology_version, subjects=subjects, nodes=nodes, edges=(edge,),
            input_digest=C, generated_at=10,
        )


class Detector:
    detector_id = "fixture.detector"
    detector_version = "1.0.0"

    def __init__(self):
        self.recommendation = "OBSERVE_ONLY"

    def evaluate(self, value):
        return DetectorResult.create(
            contract_id=value.contract_id, contract_version=value.contract_version,
            detector_version=self.detector_version, subjects=value.subjects,
            observation_window=value.observation_window, identity_evidence={}, topology_evidence={},
            behaviour_evidence={"observed": True}, operational_contact={}, infrastructure_overlap={},
            funding_overlap={}, temporal_overlap={}, supporting_evidence_ids=(A,),
            contradictory_evidence_ids=(), missing_inputs=(), confidence_output=None,
            candidate_lifecycle_recommendation={"recommended_state": {
                "maturity": "OBSERVED", "governance_identity": "UNCONFIRMED", "activity": "ACTIVE"
            }, "reason": "observed"}, governance_recommendation=self.recommendation,
            input_watermark={"evidence": value.evidence_watermark, "primitive": value.primitive_watermark},
            input_digest=value.input_digest, generated_at=10,
        )


@pytest.fixture
def runtime(tmp_path):
    registries = RuntimeRegistries()
    behaviour, detector, topology = Behaviour(), Detector(), Topology()
    registries.behaviours.register(behaviour)
    registries.detectors.register(detector)
    registries.topologies.register(topology)
    registries.presentations.register("1.0.0", {"type": "object"})
    contracts = ContractRegistryModel(
        evidence_versions={"TransactionFact": ("1",)}, primitive_versions={"SYSTEM_TRANSFER": ("1",)},
        behaviour_versions={"fixture.behaviour": ("1.0.0",)}, detector_versions={"fixture.detector": ("1.0.0",)},
        presentation_versions=("1.0.0",),
    )
    loader = OperationContractLoader(contracts)
    loaded = loader.load_bytes(json.dumps(_contract()).encode())
    contracts.transition("fixture.operation", "1.0.0", ContractLifecycle.SHADOW)
    store = OperationRuntimeStore(tmp_path / "operation-runtime.db")
    store.open()
    store.append_contract(loaded, registered_at=1)
    yield OperationRuntime(contracts=contracts, registries=registries, store=store), contracts, store
    store.close()


def _request(version="1.0.0"):
    return EvaluationRequest(
        contract_id="fixture.operation", contract_version=version, subjects=("subject",),
        observation_window=Window(1, 2),
        evidence=(EvidenceAvailability(A, "TransactionFact", "1"),),
        primitives=(PrimitiveAvailability(B, "SYSTEM_TRANSFER", "1", (A,)),),
        evidence_watermark=A, primitive_watermark=B,
        current_candidate_state=CandidateState("UNKNOWN", "UNCONFIRMED", "ACTIVE"), generated_at=10,
    )


def test_loader_registry_and_declarative_policy_loading(runtime):
    engine, contracts, _ = runtime
    assert contracts.state("fixture.operation", "1.0.0") is ContractLifecycle.SHADOW
    assert engine.presentation("fixture.operation", "1.0.0")["section_order"] == ("summary",)
    assert engine.governance_policy("fixture.operation", "1.0.0")["automatic_execution"] is False


def test_full_generic_evaluation_persists_each_output(runtime):
    engine, _, store = runtime
    result = engine.evaluate(_request())
    assert result.detector_result.governance_recommendation == "OBSERVE_ONLY"
    assert result.lifecycle_recommendation.automatic_execution is False
    assert result.persistence == {"inserted": 5, "duplicates": 0}
    assert store.count("behaviour_observations") == 1
    assert store.count("topology_revisions") == 1
    assert store.count("detector_inputs") == 1
    assert store.count("detector_results") == 1
    assert store.count("lifecycle_recommendations") == 1


def test_replay_is_deterministic_and_idempotent(runtime):
    engine, _, _ = runtime
    first = engine.evaluate(_request())
    second = engine.evaluate(_request())
    assert first.detector_result.result_id == second.detector_result.result_id
    assert second.persistence == {"inserted": 0, "duplicates": 5}


def test_required_inputs_and_versions_fail_closed(runtime):
    engine, _, _ = runtime
    request = _request()
    with pytest.raises(ValueError, match="required runtime input"):
        engine.evaluate(EvaluationRequest(**{**request.__dict__, "primitives": ()}))
    with pytest.raises(ValueError, match="required runtime input"):
        engine.evaluate(EvaluationRequest(**{**request.__dict__, "evidence": (
            EvidenceAvailability(A, "TransactionFact", "2"),
        )}))


def test_governance_recommendations_are_validated_but_never_executed(runtime):
    engine, _, store = runtime
    detector = engine.registries.detectors.resolve("fixture.detector", "1.0.0")
    detector.recommendation = "REQUEST_REVIEW"
    with pytest.raises(ValueError, match="not allowed by contract"):
        engine.evaluate(_request())
    assert store.count("detector_results") == 0
    assert store.count("lifecycle_recommendations") == 0


def test_only_shadow_or_active_contracts_execute(runtime):
    engine, contracts, _ = runtime
    contracts.transition("fixture.operation", "1.0.0", ContractLifecycle.DEPRECATED)
    with pytest.raises(ValueError, match="not executable"):
        engine.evaluate(_request())


def test_runtime_outputs_are_physically_immutable(runtime):
    engine, _, store = runtime
    engine.evaluate(_request())
    connection = sqlite3.connect(store.path)
    with pytest.raises(sqlite3.IntegrityError, match="immutable Detector Result"):
        connection.execute("UPDATE detector_results SET input_digest=?", (C,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable Topology Revision"):
        connection.execute("DELETE FROM topology_revisions")
    connection.close()


def test_registry_collisions_and_version_coexistence_are_deterministic(runtime):
    engine, contracts, store = runtime
    loader = OperationContractLoader(contracts)
    second = loader.load_mapping(_contract("2.0.0"))
    store.append_contract(second, registered_at=2)
    assert contracts.versions("fixture.operation") == ("1.0.0", "2.0.0")
    changed = copy.deepcopy(_contract("2.0.0"))
    changed["presentation_schema"]["section_order"] = ["changed"]
    with pytest.raises(ValueError, match="collision"):
        loader.load_mapping(changed)


def test_no_operation_or_production_authority_is_embedded():
    from pathlib import Path
    root = Path("src/evidence/operation_contracts")
    source = "\n".join(path.read_text() for path in root.glob("*.py"))
    forbidden = ("WATCHTOWER", "3SW2", "unknown_discovery", "operator_identity",
                 "governance.execute", "src.core", "requests.", "aiohttp")
    assert all(term not in source for term in forbidden)
