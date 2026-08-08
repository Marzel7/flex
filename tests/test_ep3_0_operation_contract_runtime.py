from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from src.evidence.contracts import EvidenceProvenance, EvidenceRecord, FactFamily
from src.evidence.operation_contracts import (
    BehaviourObservation, CandidateState, ContractLifecycle, ContractRegistryModel,
    DetectorResult, TopologyEdge, TopologyNode, TopologyRevision, Window,
)
from src.evidence.primitives.contracts import (
    ObservationWindow, PrimitiveObservation, PrimitiveQuality, PrimitiveType,
)
from src.evidence.operation_contracts.loader import OperationContractLoader
from src.evidence.operation_contracts.registry import RuntimeRegistries
from src.evidence.operation_contracts.runtime import EvaluationRequest, OperationRuntime
from src.evidence.operation_contracts.storage import OperationRuntimeStore


A = "a" * 64
B = "b" * 64
C = "c" * 64


def _evidence(version="1"):
    return EvidenceRecord.create(
        family=FactFamily.TRANSACTION, chain="solana", network="mainnet",
        natural_key="fixture-signature", payload={"signature": "fixture-signature"},
        raw_artifact_digest="d" * 64, observed_at=1, acquired_at=1,
        source_id="fixture", source_version="1", provider="fixture",
        provider_request_id="fixture-request", parser_id="fixture-parser",
        parser_version="1", replay_version="1", verification_state="VERIFIED",
        provenance_quality="DIRECT",
        provenance=EvidenceProvenance(
            endpoint_method="fixture", request_parameters_digest="e" * 64,
            upstream_dependency=None, acquisition_path="SYNTHETIC",
            cache_source="NONE", dependency_group="fixture",
        ),
        fact_schema_version=version, created_at=1,
    )


def _primitive(evidence_id, *, quality=PrimitiveQuality.PROVEN, missing=()):
    return PrimitiveObservation.create(
        primitive_type=PrimitiveType.SYSTEM_TRANSFER, primitive_version="1",
        evidence_ids=(evidence_id,), subjects=("subject",), parameters={},
        observation_window=ObservationWindow(1, 2),
        output_payload={"source": "source", "destination": "destination",
                        "amount_lamports": 1, "signature": "fixture-signature",
                        "signers": ["source"], "timestamp": 1},
        quality_state=quality, missing_inputs=missing, generated_at=2,
    )


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
        primitive = value.primitive_window.observations[0]
        return BehaviourObservation.create(
            contract_id=value.contract_id, contract_version=value.contract_version,
            module_id=self.module_id, module_version=self.module_version,
            subjects=value.subjects, parameters=value.parameters,
            observation_window=value.observation_window,
            measured_values={
                "source": primitive.output_payload["source"],
                "destination": primitive.output_payload["destination"],
                "amount_lamports": primitive.output_payload["amount_lamports"],
                "signature": primitive.output_payload["signature"],
                "signers": primitive.output_payload["signers"],
                "timestamp": primitive.output_payload["timestamp"],
            },
            evidence_refs=primitive.evidence_ids, primitive_refs=value.primitive_refs, missing_inputs=(),
            quality_state=primitive.quality_state, input_digest=value.snapshot_digest,
            generated_at=value.generated_at,
        )


class Topology:
    topology_version = "1.0.0"

    def generate(self, value):
        contract = value.contract
        primitive = value.primitive_window.observations[0]
        primitive_refs = (primitive.primitive_id,)
        evidence_refs = primitive.evidence_ids
        nodes = (
            TopologyNode("source", "SOURCE", contract["contract_id"], contract["contract_version"], evidence_refs, primitive_refs),
            TopologyNode("destination", "DESTINATION", contract["contract_id"], contract["contract_version"], evidence_refs, primitive_refs),
        )
        edge = TopologyEdge("source", "destination", "SYSTEM_TRANSFER", "ONE_TO_ONE", None, True, evidence_refs, primitive_refs)
        return TopologyRevision.create(
            contract_id=contract["contract_id"], contract_version=contract["contract_version"],
            topology_version=self.topology_version, subjects=value.subjects, nodes=nodes, edges=(edge,),
            behaviour_observation_refs=tuple(item.observation_id for item in value.behaviour_observations),
            input_digest=value.snapshot_digest, generated_at=value.generated_at,
        )


class Detector:
    detector_id = "fixture.detector"
    detector_version = "1.0.0"

    def __init__(self):
        self.recommendation = "OBSERVE_ONLY"

    def evaluate(self, value):
        contradictory = value.evidence_refs if any(
            item.quality_state == "CONFLICTING" for item in value.primitive_window.observations
        ) else ()
        return DetectorResult.create(
            contract_id=value.contract_id, contract_version=value.contract_version,
            detector_version=self.detector_version, subjects=value.subjects,
            observation_window=value.observation_window, identity_evidence={}, topology_evidence={},
            behaviour_evidence={"quality_states": [
                item.quality_state for item in value.behaviour_observations
            ]}, operational_contact={}, infrastructure_overlap={},
            funding_overlap={}, temporal_overlap={},
            supporting_evidence_ids=() if contradictory else value.evidence_refs,
            contradictory_evidence_ids=contradictory,
            missing_inputs=tuple(sorted({missing for item in value.primitive_window.observations
                                         for missing in item.missing_inputs})), confidence_output=None,
            candidate_lifecycle_recommendation={"recommended_state": {
                "maturity": "OBSERVED", "governance_identity": "UNCONFIRMED", "activity": "ACTIVE"
            }, "reason": "observed"}, governance_recommendation=self.recommendation,
            input_watermark={"evidence": value.evidence_watermark, "primitive": value.primitive_watermark},
            primitive_refs=value.primitive_refs,
            behaviour_observation_refs=value.behaviour_observation_refs,
            topology_revision_ref=value.topology_revision_ref,
            input_digest=value.input_digest, generated_at=value.generated_at,
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
    evidence = _evidence()
    primitive = _primitive(evidence.evidence_id)
    return EvaluationRequest(
        contract_id="fixture.operation", contract_version=version, subjects=("subject",),
        observation_window=Window(1, 2),
        evidence=(evidence,), primitives=(primitive,),
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


def test_runtime_receives_complete_immutable_observations(runtime):
    engine, _, _ = runtime
    request = _request()
    snapshot = engine.materialize_snapshot(request)
    primitive = snapshot.primitive_window.observations[0]
    evidence = snapshot.evidence_window.observations[0]
    assert primitive.output_payload == {
        "source": "source", "destination": "destination", "amount_lamports": 1,
        "signature": "fixture-signature", "signers": ("source",), "timestamp": 1,
    }
    assert evidence.provenance.acquisition_path == "SYNTHETIC"
    with pytest.raises(TypeError):
        primitive.output_payload["amount_lamports"] = 2
    with pytest.raises(TypeError):
        evidence.payload["signature"] = "changed"
    result = engine.evaluate_snapshot(
        snapshot, current_candidate_state=request.current_candidate_state,
    )
    assert result.behaviours[0].measured_values["amount_lamports"] == 1
    assert result.detector_input.primitive_window.digest == snapshot.primitive_window.digest
    assert result.detector_input.topology_revision.revision_id == result.topology.revision_id
    assert result.topology.edges[0].source == "source"
    assert result.topology.edges[0].destination == "destination"


def test_snapshot_order_and_quality_states_are_preserved(runtime):
    engine, _, _ = runtime
    evidence = _evidence()
    primitive = _primitive(
        evidence.evidence_id, quality=PrimitiveQuality.CONFLICTING,
        missing=("recipient_pre_balance",),
    )
    request = EvaluationRequest(
        **{**_request().__dict__, "evidence": (evidence,), "primitives": (primitive,)}
    )
    first = engine.materialize_snapshot(request)
    second = engine.materialize_snapshot(request)
    assert first.input_digest == second.input_digest
    assert first.primitive_window.observations[0].quality_state == "CONFLICTING"
    assert first.primitive_window.observations[0].missing_inputs == ("recipient_pre_balance",)
    result = engine.evaluate_snapshot(
        first, current_candidate_state=request.current_candidate_state,
    )
    assert result.behaviours[0].quality_state == "CONFLICTING"
    assert result.detector_result.contradictory_evidence_ids == (evidence.evidence_id,)
    assert result.detector_result.missing_inputs == ("recipient_pre_balance",)


def test_materialized_snapshot_replay_opens_no_data_connection(runtime, monkeypatch):
    engine, _, _ = runtime
    request = _request()
    snapshot = engine.materialize_snapshot(request)

    def forbidden_connection(*args, **kwargs):
        raise AssertionError("runtime module attempted direct storage access")

    monkeypatch.setattr(sqlite3, "connect", forbidden_connection)
    result = engine.evaluate_snapshot(
        snapshot, current_candidate_state=request.current_candidate_state,
    )
    assert result.detector_result.result_id


def test_required_inputs_and_versions_fail_closed(runtime):
    engine, _, _ = runtime
    request = _request()
    with pytest.raises(ValueError, match="required runtime input"):
        engine.evaluate(EvaluationRequest(**{**request.__dict__, "primitives": ()}))
    with pytest.raises(ValueError, match="required runtime input"):
        engine.evaluate(EvaluationRequest(**{**request.__dict__, "evidence": (
            _evidence("2"),
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
    # EP3.0 freezes the generic runtime boundary. Later contract implementations
    # live beside it, so this assertion deliberately audits only the generic
    # runtime modules rather than forbidding all future Operation contracts.
    generic_modules = (
        "formalization.py", "input_windows.py", "loader.py", "registry.py",
        "runtime.py", "storage.py",
    )
    source = "\n".join((root / name).read_text() for name in generic_modules)
    forbidden = ("WATCHTOWER", "3SW2", "unknown_discovery", "operator_identity",
                 "governance.execute", "src.core", "requests.", "aiohttp")
    assert all(term not in source for term in forbidden)
