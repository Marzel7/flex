from __future__ import annotations

import json
from pathlib import Path

from src.evidence.operation_contracts.formalization import (
    BehaviourModuleInput, DetectorInput, TopologyModuleInput, Window, validate_contract,
)
from src.evidence.operation_contracts.input_windows import EvidenceInputWindow, PrimitiveInputWindow
from src.evidence.operation_contracts.registry import RuntimeRegistries
from src.evidence.operation_contracts.three_sw2_v1 import (
    BEHAVIOURS, ThreeSw2Detector, ThreeSw2Topology, register_three_sw2_v1,
)
from src.evidence.operation_contracts.watchtower_v1 import register_watchtower_v1
from src.evidence.primitives.contracts import ObservationWindow, PrimitiveObservation, PrimitiveQuality, PrimitiveType


PATH = Path("src/evidence/operation_contracts/contracts/three_sw2_v1.json")
CONTROLLER = "3SW2zquY2mVTbNuw1ZCGgtoehq2evfU36PFd6TTqSXdK"


def _primitive(kind, payload, subjects):
    return PrimitiveObservation.create(
        primitive_type=kind, primitive_version="1", evidence_ids=("e" * 64,),
        subjects=subjects, parameters={}, observation_window=ObservationWindow(1, 2),
        output_payload=payload, quality_state=PrimitiveQuality.PROVEN, generated_at=3,
    )


def _inputs():
    primitives = (
        _primitive(PrimitiveType.SYSTEM_TRANSFER,
                   {"source": CONTROLLER, "destination": "creator", "amount": 1000,
                    "signature": "activation", "timestamp": 1}, (CONTROLLER, "creator")),
        _primitive(PrimitiveType.LAUNCH_SIGNER,
                   {"wallet": "creator", "mint": "mint", "signer": True,
                    "launch_signature": "launch"}, ("creator", "mint")),
        _primitive(PrimitiveType.DIRECT_COUNTERPARTY,
                   {"source": "contact", "destination": CONTROLLER, "signature": "contact"},
                   ("contact", CONTROLLER)),
    )
    evidence = EvidenceInputWindow.create(subjects=(CONTROLLER,), start=1, end=2,
                                          watermark="a" * 64, observations=())
    primitive = PrimitiveInputWindow.create(subjects=(CONTROLLER,), start=1, end=2,
                                            watermark="b" * 64, observations=primitives)
    return evidence, primitive


def test_contract_is_shadow_only_and_models_distinct_topology():
    contract = validate_contract(json.loads(PATH.read_text()))
    assert contract["lifecycle_status"] == "SHADOW"
    assert contract["topology_contract"]["local_roles"] == ["controller", "creator", "launch"]
    assert "funding_source" not in contract["topology_contract"]["local_roles"]
    assert contract["governance_policy"]["automatic_execution"] is False


def test_topology_contact_identity_and_replay_are_deterministic():
    contract = validate_contract(json.loads(PATH.read_text()))
    evidence, primitives = _inputs()
    declarations = {item["module_id"]: item for item in contract["behaviour_modules"]}
    behaviours = []
    for implementation in BEHAVIOURS:
        selected = primitives.select(declarations[implementation.module_id]["required_primitive_types"])
        value = BehaviourModuleInput("three_sw2", "1.0.0", implementation.module_id,
                                     "1.0.0", (CONTROLLER,), Window(1, 2), evidence,
                                     selected, selected.refs, {}, "c" * 64, 4)
        behaviours.append(implementation.evaluate(value))
    topology_input = TopologyModuleInput(contract, (CONTROLLER,), Window(1, 2), evidence,
                                         primitives, tuple(behaviours), "c" * 64, 4)
    topology = ThreeSw2Topology().generate(topology_input)
    assert [(edge.source, edge.destination) for edge in topology.edges] == [
        (CONTROLLER, "creator"), ("creator", "mint")]
    assert topology.revision_id == ThreeSw2Topology().generate(topology_input).revision_id
    detector_input = DetectorInput.create(
        contract_id="three_sw2", contract_version="1.0.0", detector_version="1.0.0",
        subjects=(CONTROLLER,), evidence_watermark="a" * 64, primitive_watermark="b" * 64,
        observation_window=Window(1, 2), evidence_refs=(), primitive_refs=primitives.refs,
        behaviour_observation_refs=tuple(item.observation_id for item in behaviours),
        topology_revision_ref=topology.revision_id, evidence_window=evidence,
        primitive_window=primitives, behaviour_observations=tuple(behaviours),
        topology_revision=topology, snapshot_digest="c" * 64, generated_at=4,
        input_digest="c" * 64,
    )
    result = ThreeSw2Detector().evaluate(detector_input)
    assert result.operational_contact == {"direct_transaction_observations": 1, "identity_effect": "NONE"}
    assert result.identity_evidence["canonical_identity_claim"] is False
    assert result.result_id == ThreeSw2Detector().evaluate(detector_input).result_id


def test_watchtower_and_three_sw2_register_without_platform_changes():
    registries = RuntimeRegistries()
    register_watchtower_v1(registries)
    register_three_sw2_v1(registries)
    assert len(registries.behaviours.entries()) == 13
    assert registries.topologies.versions() == ("1.0.0", "3.2.0")
    assert registries.detectors.versions("watchtower_detector") == ("1.0.0",)
    assert registries.detectors.versions("three_sw2_detector") == ("1.0.0",)
