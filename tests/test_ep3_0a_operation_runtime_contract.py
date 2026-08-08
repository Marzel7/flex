from __future__ import annotations

import copy
import hashlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from jsonschema import ValidationError

from src.evidence.operation_contracts.formalization import (
    BehaviourObservation, CandidateState, ContractLifecycle,
    ContractRegistryModel, DetectorInput, DetectorResult,
    LifecycleRecommendation, TopologyEdge, TopologyNode, TopologyRevision,
    Window, canonical_contract_bytes, contract_digest, validate_contract,
)
from src.evidence.operation_contracts.input_windows import EvidenceInputWindow, PrimitiveInputWindow


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def _contract(version: str = "1.0.0", *, status: str = "DRAFT") -> dict:
    return {
        "schema_version": "1", "contract_id": "fixture.operation",
        "contract_version": version, "lifecycle_status": status,
        "evidence_requirements": [
            {"fact_family": "TransactionFact", "version_constraint": "1", "required": True}
        ],
        "primitive_requirements": [
            {"primitive_type": "SYSTEM_TRANSFER", "version_constraint": "1", "required": True}
        ],
        "behaviour_modules": [{
            "module_id": "fixture.behaviour", "module_version": "1.0.0",
            "required_primitive_types": ["SYSTEM_TRANSFER"], "parameters": {"mode": "fixture"}
        }],
        "topology_contract": {
            "topology_version": "1.0.0", "local_roles": ["ROLE_A", "ROLE_B"],
            "edge_rules": [{"rule_id": "edge.one", "source_role": "ROLE_A",
                            "destination_role": "ROLE_B", "primitive_type": "SYSTEM_TRANSFER",
                            "cardinality": "ONE_TO_MANY", "temporal_constraint": None,
                            "required": True}], "parameters": {}
        },
        "detector": {"detector_id": "fixture.detector", "detector_version": "1.0.0",
                     "parameters": {}},
        "identity_contract": {"mode": "DECLARATIVE", "configuration": {"rules": []},
                              "runtime_execution": "LOAD_VALIDATE_ONLY"},
        "confidence_model": {"model_type": "DISABLED", "configuration": {},
                             "runtime_execution": "LOAD_VALIDATE_ONLY"},
        "governance_policy": {
            "allowed_recommendations": ["OBSERVE_ONLY", "NO_AUTOMATIC_GOVERNANCE"],
            "automatic_execution": False, "runtime_execution": "LOAD_VALIDATE_ONLY"
        },
        "monitoring_policy": {
            "evaluation_trigger": "MANUAL", "minimum_evidence_state": "COMPLETE",
            "reevaluation_conditions": [], "staleness_window_seconds": None,
            "dormancy_window_seconds": None, "runtime_execution": "LOAD_VALIDATE_ONLY"
        },
        "presentation_schema": {
            "schema_version": "1.0.0", "role_labels": {"ROLE_A": "Role A"},
            "topology_labels": {}, "evidence_class_labels": {},
            "section_order": ["summary"], "allowed_actions": ["open"],
            "display_contract_version": True
        }
    }


def _registry() -> ContractRegistryModel:
    return ContractRegistryModel(
        evidence_versions={"TransactionFact": ["1"]},
        primitive_versions={"SYSTEM_TRANSFER": ["1"]},
        behaviour_versions={"fixture.behaviour": ["1.0.0"]},
        detector_versions={"fixture.detector": ["1.0.0"]},
        presentation_versions=["1.0.0"],
    )


def test_contract_canonical_serialization_and_digest_are_deterministic():
    first = _contract()
    second = {key: first[key] for key in reversed(list(first))}
    assert canonical_contract_bytes(first) == canonical_contract_bytes(second)
    assert contract_digest(first) == contract_digest(second)
    validated = validate_contract(first)
    assert validated["contract_digest"] == contract_digest(first)
    assert validate_contract(validated) == validated


def test_schema_rejects_executable_declarative_policy_and_unknown_fields():
    invalid = _contract()
    invalid["governance_policy"]["automatic_execution"] = True
    with pytest.raises(ValidationError): validate_contract(invalid)
    invalid = _contract()
    invalid["identity_contract"]["runtime_execution"] = "EXECUTE"
    with pytest.raises(ValidationError): validate_contract(invalid)
    invalid = _contract(); invalid["implementation_class"] = "unsafe.Plugin"
    with pytest.raises(ValidationError): validate_contract(invalid)


def test_dependencies_and_operation_local_topology_are_machine_validated():
    registry = _registry()
    registry.register(_contract())
    missing = _contract("2.0.0")
    missing["primitive_requirements"][0]["primitive_type"] = "UNAVAILABLE"
    missing["behaviour_modules"][0]["required_primitive_types"] = ["UNAVAILABLE"]
    missing["topology_contract"]["edge_rules"][0]["primitive_type"] = "UNAVAILABLE"
    with pytest.raises(ValueError, match="missing or incompatible dependency"):
        registry.register(missing)
    invalid_role = _contract("3.0.0")
    invalid_role["topology_contract"]["edge_rules"][0]["source_role"] = "UNDECLARED"
    with pytest.raises(ValueError, match="undeclared operation-local role"):
        validate_contract(invalid_role)


def test_versions_coexist_duplicate_active_is_rejected_and_rollback_is_lossless():
    registry = _registry()
    registry.register(_contract("1.0.0")); registry.register(_contract("2.0.0"))
    assert registry.versions("fixture.operation") == ("1.0.0", "2.0.0")
    registry.transition("fixture.operation", "1.0.0", ContractLifecycle.SHADOW)
    registry.transition("fixture.operation", "1.0.0", ContractLifecycle.ACTIVE)
    registry.transition("fixture.operation", "2.0.0", ContractLifecycle.SHADOW)
    with pytest.raises(ValueError, match="one ACTIVE"):
        registry.transition("fixture.operation", "2.0.0", ContractLifecycle.ACTIVE)
    registry.transition("fixture.operation", "1.0.0", ContractLifecycle.DEPRECATED)
    registry.transition("fixture.operation", "2.0.0", ContractLifecycle.ACTIVE)
    registry.rollback("fixture.operation", "1.0.0")
    assert registry.active("fixture.operation")["contract_version"] == "1.0.0"
    with pytest.raises(TypeError):
        registry.active("fixture.operation")["presentation_schema"]["section_order"] = []
    assert registry.versions("fixture.operation") == ("1.0.0", "2.0.0")


def test_same_id_version_different_content_is_a_collision_even_for_draft():
    registry = _registry(); registry.register(_contract())
    changed = _contract(); changed["presentation_schema"]["section_order"] = ["different"]
    with pytest.raises(ValueError, match="collision"):
        registry.register(changed)


def test_behaviour_and_topology_outputs_are_deterministic_and_immutable():
    behaviour_values = dict(
        contract_id="fixture.operation", contract_version="1.0.0",
        module_id="fixture.behaviour", module_version="1.0.0", subjects=("subject",),
        parameters={}, observation_window=Window(1, 2), measured_values={"count": 1},
        evidence_refs=(DIGEST_A,), primitive_refs=(DIGEST_B,), missing_inputs=(),
        quality_state="PROVEN", input_digest=DIGEST_C, generated_at=10,
    )
    first = BehaviourObservation.create(**behaviour_values)
    second = BehaviourObservation.create(**{**behaviour_values, "generated_at": 99})
    assert first.observation_id == second.observation_id
    assert first.measured_values == second.measured_values
    with pytest.raises(TypeError):
        first.measured_values["count"] = 2
    with pytest.raises(FrozenInstanceError): first.contract_id = "changed"

    node_a = TopologyNode("a", "ROLE_A", "fixture.operation", "1.0.0", (DIGEST_A,), (DIGEST_B,))
    node_b = TopologyNode("b", "ROLE_B", "fixture.operation", "1.0.0", (), (DIGEST_B,))
    edge = TopologyEdge("a", "b", "SYSTEM_TRANSFER", "ONE_TO_MANY", None, True,
                        (DIGEST_A,), (DIGEST_B,))
    topology = TopologyRevision.create(
        contract_id="fixture.operation", contract_version="1.0.0",
        topology_version="1.0.0", subjects=("subject",), nodes=(node_b, node_a),
        edges=(edge,), behaviour_observation_refs=(DIGEST_C,),
        input_digest=DIGEST_C, generated_at=10,
    )
    replay = TopologyRevision.create(
        contract_id="fixture.operation", contract_version="1.0.0",
        topology_version="1.0.0", subjects=("subject",), nodes=(node_a, node_b),
        edges=(edge,), behaviour_observation_refs=(DIGEST_C,),
        input_digest=DIGEST_C, generated_at=20,
    )
    assert topology.revision_id == replay.revision_id


def test_detector_input_result_and_versioned_replay_identities():
    evidence_window = EvidenceInputWindow.create(
        subjects=("subject",), start=1, end=2, watermark=DIGEST_A, observations=(),
    )
    primitive_window = PrimitiveInputWindow.create(
        subjects=("subject",), start=1, end=2, watermark=DIGEST_B, observations=(),
    )
    topology = TopologyRevision.create(
        contract_id="fixture.operation", contract_version="1.0.0",
        topology_version="1.0.0", subjects=("subject",), nodes=(), edges=(),
        behaviour_observation_refs=(), input_digest=DIGEST_C, generated_at=10,
    )
    detector_input = DetectorInput.create(
        contract_id="fixture.operation", contract_version="1.0.0",
        detector_version="1.0.0", subjects=("subject",), evidence_watermark=DIGEST_A,
        primitive_watermark=DIGEST_B, observation_window=Window(1, 2),
        evidence_refs=(DIGEST_A,), primitive_refs=(DIGEST_B,),
        behaviour_observation_refs=(), topology_revision_ref=topology.revision_id,
        evidence_window=evidence_window, primitive_window=primitive_window,
        behaviour_observations=(), topology_revision=topology,
        snapshot_digest=DIGEST_C, input_digest=DIGEST_C, generated_at=10,
    )
    values = dict(
        contract_id="fixture.operation", contract_version="1.0.0",
        detector_version="1.0.0", subjects=("subject",), observation_window=Window(1, 2),
        identity_evidence={}, topology_evidence={}, behaviour_evidence={"observed": True},
        operational_contact={}, infrastructure_overlap={}, funding_overlap={}, temporal_overlap={},
        supporting_evidence_ids=(DIGEST_A,), contradictory_evidence_ids=(), missing_inputs=(),
        confidence_output=None, candidate_lifecycle_recommendation=None,
        governance_recommendation="OBSERVE_ONLY",
        input_watermark={"evidence": DIGEST_A, "primitive": DIGEST_B},
        primitive_refs=(DIGEST_B,), behaviour_observation_refs=(),
        topology_revision_ref=topology.revision_id,
        input_digest=detector_input.input_digest, generated_at=10,
    )
    first = DetectorResult.create(**values)
    replay = DetectorResult.create(**{**values, "generated_at": 20})
    upgraded = DetectorResult.create(**{**values, "detector_version": "2.0.0"})
    assert first.result_id == replay.result_id
    assert upgraded.result_id != first.result_id


def test_lifecycle_is_three_dimensional_and_recommendations_never_execute():
    current = CandidateState("OBSERVED", "UNCONFIRMED", "ACTIVE")
    target = CandidateState("BEHAVIOURAL_CLUSTER", "UNCONFIRMED", "ACTIVE")
    recommendation = LifecycleRecommendation.create(
        contract_id="fixture.operation", contract_version="1.0.0", subjects=("subject",),
        current_state=current, recommended_state=target, reason="fixture",
        detector_result_ref=DIGEST_A, input_digest=DIGEST_B, generated_at=1,
    )
    assert recommendation.automatic_execution is False
    with pytest.raises(ValueError, match="exactly one dimension"):
        LifecycleRecommendation.create(
            contract_id="fixture.operation", contract_version="1.0.0", subjects=("subject",),
            current_state=current,
            recommended_state=CandidateState("BEHAVIOURAL_CLUSTER", "REVIEW", "ACTIVE"),
            reason="invalid", detector_result_ref=DIGEST_A, input_digest=DIGEST_B,
            generated_at=1,
        )


def test_formalization_has_no_execution_or_production_mutation_dependencies():
    root = Path("src/evidence/operation_contracts")
    source = "\n".join(path.read_text() for path in root.glob("*.py"))
    forbidden = (
        "aiohttp", "requests.", "src.core", "creator_funding", "walkback",
        "operator_identity", "governance.execute", "confidence.score",
        "WATCHTOWER", "3SW2",
    )
    assert all(value not in source for value in forbidden)
