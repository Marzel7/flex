"""Canonical, operation-agnostic observations attached to an operator identity."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any


OBSERVATION_TYPES = frozenset({
    "LAUNCH", "CREATOR", "TREASURY", "FUNDING", "WRAP_CLOSE",
    "PROVISIONING", "BUY", "SELL", "MIGRATION", "RELAY", "CAMPAIGN",
    "INFRASTRUCTURE", "COORDINATION",
})


@dataclass(frozen=True)
class OperatorObservation:
    operator_id: str
    observation_type: str
    entity: str | None
    timestamp: int
    source: str
    confidence: float
    provenance: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    observation_id: str = ""

    def __post_init__(self) -> None:
        kind = self.observation_type.upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", kind):
            raise ValueError(f"Invalid observation type: {kind}")
        object.__setattr__(self, "observation_type", kind)
        object.__setattr__(self, "confidence", max(0.0, min(1.0, float(self.confidence))))
        if not self.observation_id:
            payload = {
                "operator_id": self.operator_id,
                "observation_type": kind,
                "entity": self.entity,
                "timestamp": int(self.timestamp),
                "source": self.source,
                "provenance": self.provenance,
                "metadata": self.metadata,
            }
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
            ).hexdigest()[:32]
            object.__setattr__(self, "observation_id", f"observation-{digest}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "operator_id": self.operator_id,
            "observation_type": self.observation_type,
            "entity": self.entity,
            "timestamp": self.timestamp,
            "source": self.source,
            "confidence": self.confidence,
            "provenance": self.provenance,
            "metadata": self.metadata,
        }
