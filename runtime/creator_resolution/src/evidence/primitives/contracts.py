"""Immutable deterministic primitive observation contracts."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from ..contracts import canonical_json_bytes, payload_digest


class PrimitiveType(str, Enum):
    SYSTEM_TRANSFER = "SYSTEM_TRANSFER"
    LAUNCH_SIGNER = "LAUNCH_SIGNER"
    WSOL_CLOSE = "WSOL_CLOSE"
    DIRECT_COUNTERPARTY = "DIRECT_COUNTERPARTY"
    PROGRAM_INTERACTION = "PROGRAM_INTERACTION"
    WALLET_FRESH_AT_EVENT = "WALLET_FRESH_AT_EVENT"
    LAUNCH_ACTIVATION = "LAUNCH_ACTIVATION"
    ECONOMIC_FUNDING = "ECONOMIC_FUNDING"
    SHARED_TRANSACTION = "SHARED_TRANSACTION"
    REPEATED_COUNTERPARTY = "REPEATED_COUNTERPARTY"
    BEHAVIOURAL_TIMING = "BEHAVIOURAL_TIMING"


class PrimitiveQuality(str, Enum):
    PROVEN = "PROVEN"
    DISPROVEN = "DISPROVEN"
    INCOMPLETE = "INCOMPLETE"
    CONFLICTING = "CONFLICTING"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True)
class ObservationWindow:
    start: Optional[int]
    end: Optional[int]


@dataclass(frozen=True)
class PrimitiveObservation:
    primitive_id: str
    primitive_type: str
    primitive_version: str
    evidence_ids: tuple[str, ...]
    subjects: tuple[str, ...]
    parameters: Mapping[str, Any]
    observation_window: ObservationWindow
    output_payload: Mapping[str, Any]
    output_digest: str
    quality_state: str
    missing_inputs: tuple[str, ...]
    failure_state: Optional[str]
    generated_at: int

    @classmethod
    def create(cls, *, primitive_type: PrimitiveType, primitive_version: str,
               evidence_ids: Sequence[str], subjects: Sequence[str],
               parameters: Mapping[str, Any], observation_window: ObservationWindow,
               output_payload: Mapping[str, Any], quality_state: PrimitiveQuality,
               missing_inputs: Sequence[str] = (), failure_state: Optional[str] = None,
               generated_at: int = 0) -> "PrimitiveObservation":
        ordered_evidence = tuple(sorted(set(evidence_ids)))
        ordered_subjects = tuple(sorted(set(subjects)))
        ordered_missing = tuple(sorted(set(missing_inputs)))
        output = dict(output_payload)
        identity = [
            primitive_type.value, primitive_version, dict(parameters),
            list(ordered_evidence), output,
        ]
        primitive_id = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
        return cls(
            primitive_id=primitive_id, primitive_type=primitive_type.value,
            primitive_version=primitive_version, evidence_ids=ordered_evidence,
            subjects=ordered_subjects, parameters=dict(parameters),
            observation_window=observation_window, output_payload=output,
            output_digest=payload_digest(output), quality_state=quality_state.value,
            missing_inputs=ordered_missing, failure_state=failure_state,
            generated_at=int(generated_at),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
