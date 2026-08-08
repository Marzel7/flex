from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from src.evidence.contracts import EvidenceProvenance, EvidenceRecord, FactFamily
from src.evidence.operation_contracts.input_windows import (
    EvidenceInputWindow, PrimitiveInputWindow, plain,
)
from src.evidence.primitives.contracts import (
    ObservationWindow, PrimitiveObservation, PrimitiveQuality, PrimitiveType,
)


def _evidence(key: str = "signature") -> EvidenceRecord:
    return EvidenceRecord.create(
        family=FactFamily.TRANSACTION, chain="solana", network="mainnet",
        natural_key=key, payload={"signature": key}, raw_artifact_digest="a" * 64,
        observed_at=100, acquired_at=101, source_id="synthetic", source_version="1",
        provider="synthetic", provider_request_id=key, parser_id="synthetic",
        parser_version="1", replay_version="1", verification_state="VERIFIED",
        provenance_quality="DIRECT",
        provenance=EvidenceProvenance(
            endpoint_method="fixture", request_parameters_digest="b" * 64,
            upstream_dependency=None, acquisition_path="SYNTHETIC",
            cache_source="NONE", dependency_group="fixture",
        ),
    )


def _primitive(evidence_id: str, amount: int = 1) -> PrimitiveObservation:
    return PrimitiveObservation.create(
        primitive_type=PrimitiveType.SYSTEM_TRANSFER, primitive_version="1",
        evidence_ids=(evidence_id,), subjects=("creator", "controller"), parameters={},
        observation_window=ObservationWindow(100, 100),
        output_payload={
            "source": "controller", "destination": "creator",
            "amount_lamports": amount, "timestamp": 100, "signers": ["controller"],
        },
        quality_state=PrimitiveQuality.PROVEN, generated_at=102,
    )


def test_windows_are_deterministic_bounded_and_serializable():
    first_evidence, second_evidence = _evidence("one"), _evidence("two")
    first_primitive = _primitive(first_evidence.evidence_id, 1)
    second_primitive = _primitive(second_evidence.evidence_id, 2)
    first = PrimitiveInputWindow.create(
        subjects=("controller",), start=1, end=200, watermark="p",
        observations=(second_primitive, first_primitive),
    )
    replay = PrimitiveInputWindow.create(
        subjects=("controller",), start=1, end=200, watermark="p",
        observations=(first_primitive, second_primitive),
    )
    assert first.digest == replay.digest
    assert first.refs == tuple(sorted(first.refs))
    serialized = json.dumps(plain(first), separators=(",", ":")).encode()
    assert json.loads(serialized)["observations"][0]["output_payload"]
    assert len(serialized) < 8_192
    with pytest.raises(ValueError, match="exceeds bound"):
        PrimitiveInputWindow.create(
            subjects=(), start=None, end=None, watermark="p",
            observations=(first_primitive,) * 10_001,
        )


def test_window_snapshot_detaches_mutable_source_payloads():
    evidence = _evidence()
    primitive = _primitive(evidence.evidence_id)
    evidence_window = EvidenceInputWindow.create(
        subjects=("controller",), start=1, end=200, watermark="e",
        observations=(evidence,),
    )
    primitive_window = PrimitiveInputWindow.create(
        subjects=("controller",), start=1, end=200, watermark="p",
        observations=(primitive,),
    )
    evidence.payload["signature"] = "mutated-after-resolution"
    primitive.output_payload["amount_lamports"] = 999
    assert evidence_window.observations[0].payload["signature"] == "signature"
    assert primitive_window.observations[0].output_payload["amount_lamports"] == 1
    with pytest.raises(FrozenInstanceError):
        primitive_window.observations[0].quality_state = "INCOMPLETE"


def test_identity_collision_fails_closed():
    primitive = _primitive(_evidence().evidence_id)
    colliding = PrimitiveObservation(
        **{**primitive.__dict__, "output_payload": {"amount_lamports": 999}}
    )
    with pytest.raises(ValueError, match="identity collision"):
        PrimitiveInputWindow.create(
            subjects=(), start=None, end=None, watermark="p",
            observations=(primitive, colliding),
        )
