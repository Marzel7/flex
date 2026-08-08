from __future__ import annotations

import json
from pathlib import Path

from src.evidence.operation_contracts.formalization import (
    BehaviourModuleInput, DetectorInput, TopologyModuleInput, Window, validate_contract,
)
from src.evidence.operation_contracts.input_windows import EvidenceInputWindow, PrimitiveInputWindow
from src.evidence.operation_contracts.registry import RuntimeRegistries
from src.evidence.operation_contracts.watchtower_v1 import (
    BEHAVIOURS, WatchtowerDetector, WatchtowerTopology, register_watchtower_v1,
)
from src.evidence.primitives.contracts import ObservationWindow, PrimitiveObservation, PrimitiveQuality, PrimitiveType


CONTRACT_PATH = Path("src/evidence/operation_contracts/contracts/watchtower_v1.json")


def primitive(kind: PrimitiveType, payload: dict, subjects=("watchtower", "creator")):
    return PrimitiveObservation.create(
        primitive_type=kind, primitive_version="1", evidence_ids=("e" * 64,),
        subjects=subjects, parameters={}, observation_window=ObservationWindow(1, 2),
        output_payload=payload, quality_state=PrimitiveQuality.PROVEN, generated_at=3,
    )


def test_contract_is_valid_shadow_only_and_non_authoritative():
    contract = validate_contract(json.loads(CONTRACT_PATH.read_text()))
    assert contract["lifecycle_status"] == "SHADOW"
    assert contract["governance_policy"] == {
        "allowed_recommendations": ["NO_AUTOMATIC_GOVERNANCE"],
        "automatic_execution": False, "runtime_execution": "LOAD_VALIDATE_ONLY",
    }
    assert contract["confidence_model"]["model_type"] == "DISABLED"


def test_behaviour_topology_detector_are_deterministic_and_snapshot_only():
    contract = validate_contract(json.loads(CONTRACT_PATH.read_text()))
    observations = (
        primitive(PrimitiveType.SYSTEM_TRANSFER, {"source":"treasury","destination":"creator","amount":1,"signature":"sig","timestamp":1}),
        primitive(PrimitiveType.LAUNCH_SIGNER, {"wallet":"creator","mint":"mint","signer":True,"launch_signature":"launch"}, ("creator","mint")),
    )
    evidence_window = EvidenceInputWindow.create(subjects=("watchtower",), start=1, end=2,
                                                  watermark="a" * 64, observations=())
    primitive_window = PrimitiveInputWindow.create(subjects=("watchtower",), start=1, end=2,
                                                    watermark="b" * 64, observations=observations)
    behaviours = []
    declarations = {item["module_id"]: item for item in contract["behaviour_modules"]}
    for implementation in BEHAVIOURS:
        declaration = declarations[implementation.module_id]
        selected = primitive_window.select(declaration["required_primitive_types"])
        value = BehaviourModuleInput("watchtower", "1.0.0", implementation.module_id,
                                     "1.0.0", ("watchtower",), Window(1, 2), evidence_window,
                                     selected, selected.refs, {}, "c" * 64, 4)
        first, second = implementation.evaluate(value), implementation.evaluate(value)
        assert first.observation_id == second.observation_id
        behaviours.append(first)
    topology_input = TopologyModuleInput(contract, ("watchtower",), Window(1, 2), evidence_window,
                                         primitive_window, tuple(behaviours), "c" * 64, 4)
    first_topology = WatchtowerTopology().generate(topology_input)
    second_topology = WatchtowerTopology().generate(topology_input)
    assert first_topology.revision_id == second_topology.revision_id
    assert [(edge.source, edge.destination) for edge in first_topology.edges] == [("treasury", "creator")]
    detector_input = DetectorInput.create(
        contract_id="watchtower", contract_version="1.0.0", detector_version="1.0.0",
        subjects=("watchtower",), evidence_watermark="a" * 64, primitive_watermark="b" * 64,
        observation_window=Window(1, 2), evidence_refs=(), primitive_refs=primitive_window.refs,
        behaviour_observation_refs=tuple(item.observation_id for item in behaviours),
        topology_revision_ref=first_topology.revision_id, evidence_window=evidence_window,
        primitive_window=primitive_window, behaviour_observations=tuple(behaviours),
        topology_revision=first_topology, snapshot_digest="c" * 64, generated_at=4,
        input_digest="c" * 64,
    )
    result = WatchtowerDetector().evaluate(detector_input)
    assert result.identity_evidence["canonical_identity_claim"] is False
    assert result.governance_recommendation == "NO_AUTOMATIC_GOVERNANCE"
    assert result.result_id == WatchtowerDetector().evaluate(detector_input).result_id


def test_all_implementations_register_without_runtime_changes():
    registries = RuntimeRegistries()
    register_watchtower_v1(registries)
    assert len(registries.behaviours.entries()) == 6
    assert registries.topologies.versions() == ("1.0.0",)
    assert registries.detectors.versions("watchtower_detector") == ("1.0.0",)
