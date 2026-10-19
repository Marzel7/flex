"""Deterministic, identity-free EP4 discovery contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from ..contracts import canonical_json_bytes
from ..operation_contracts.formalization import BehaviourObservation, TopologyRevision
from ..operation_contracts.input_windows import EvidenceInputWindow, PrimitiveInputWindow, plain


class CandidateLifecycle(str, Enum):
    OBSERVED = "OBSERVED"
    RECURRING_PATTERN = "RECURRING_PATTERN"
    INVESTIGATE = "INVESTIGATE"
    DISMISSED = "DISMISSED"


TRANSITIONS = {
    CandidateLifecycle.OBSERVED: {
        CandidateLifecycle.RECURRING_PATTERN, CandidateLifecycle.DISMISSED,
    },
    CandidateLifecycle.RECURRING_PATTERN: {
        CandidateLifecycle.INVESTIGATE, CandidateLifecycle.DISMISSED,
    },
    CandidateLifecycle.INVESTIGATE: {CandidateLifecycle.DISMISSED},
    CandidateLifecycle.DISMISSED: set(),
}


def _digest(kind: str, value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes([kind, value])).hexdigest()


@dataclass(frozen=True)
class DiscoverySnapshot:
    discovery_version: str
    evidence_window: EvidenceInputWindow
    primitive_window: PrimitiveInputWindow
    behaviour_observations: tuple[BehaviourObservation, ...]
    topology_revisions: tuple[TopologyRevision, ...]
    runtime_snapshot_digests: tuple[str, ...]
    input_digest: str
    generated_at: int

    @classmethod
    def create(cls, *, discovery_version: str,
               evidence_window: EvidenceInputWindow,
               primitive_window: PrimitiveInputWindow,
               behaviour_observations: Sequence[BehaviourObservation] = (),
               topology_revisions: Sequence[TopologyRevision] = (),
               runtime_snapshot_digests: Sequence[str] = (),
               generated_at: int = 0) -> "DiscoverySnapshot":
        behaviours = tuple(sorted(behaviour_observations,
                                  key=lambda item: item.observation_id))
        topologies = tuple(sorted(topology_revisions,
                                 key=lambda item: item.revision_id))
        runtime_digests = tuple(sorted(set(runtime_snapshot_digests)))
        body = {
            "discovery_version": discovery_version,
            "evidence_window_digest": evidence_window.digest,
            "primitive_window_digest": primitive_window.digest,
            "behaviour_observation_ids": [item.observation_id for item in behaviours],
            "topology_revision_ids": [item.revision_id for item in topologies],
            "runtime_snapshot_digests": list(runtime_digests),
        }
        return cls(
            discovery_version, evidence_window, primitive_window, behaviours,
            topologies, runtime_digests, _digest("DiscoverySnapshot", body),
            int(generated_at),
        )


@dataclass(frozen=True)
class DiscoveryCandidate:
    candidate_id: str
    discovery_version: str
    supporting_evidence_ids: tuple[str, ...]
    supporting_primitive_ids: tuple[str, ...]
    supporting_behaviour_observation_ids: tuple[str, ...]
    supporting_topology_revision_ids: tuple[str, ...]
    observed_recurring_pattern: Mapping[str, Any]
    population: tuple[str, ...]
    time_start: Optional[int]
    time_end: Optional[int]
    quality_state: str
    missing_evidence: tuple[str, ...]
    contradictory_evidence: tuple[str, ...]
    lifecycle: str
    input_digest: str
    generated_at: int

    @classmethod
    def create(cls, *, discovery_version: str,
               supporting_evidence_ids: Sequence[str],
               supporting_primitive_ids: Sequence[str],
               supporting_behaviour_observation_ids: Sequence[str],
               supporting_topology_revision_ids: Sequence[str],
               observed_recurring_pattern: Mapping[str, Any],
               population: Sequence[str], time_start: Optional[int],
               time_end: Optional[int], quality_state: str,
               missing_evidence: Sequence[str],
               contradictory_evidence: Sequence[str], lifecycle: str,
               input_digest: str, generated_at: int) -> "DiscoveryCandidate":
        if lifecycle not in {item.value for item in CandidateLifecycle}:
            raise ValueError("invalid discovery lifecycle")
        body = {
            "discovery_version": discovery_version,
            "supporting_evidence_ids": sorted(set(supporting_evidence_ids)),
            "supporting_primitive_ids": sorted(set(supporting_primitive_ids)),
            "supporting_behaviour_observation_ids": sorted(set(supporting_behaviour_observation_ids)),
            "supporting_topology_revision_ids": sorted(set(supporting_topology_revision_ids)),
            "observed_recurring_pattern": plain(observed_recurring_pattern),
            "population": sorted(set(population)),
            "time_start": time_start, "time_end": time_end,
            "quality_state": quality_state,
            "missing_evidence": sorted(set(missing_evidence)),
            "contradictory_evidence": sorted(set(contradictory_evidence)),
            "lifecycle": lifecycle, "input_digest": input_digest,
        }
        return cls(
            candidate_id=_digest("DiscoveryCandidate", body),
            discovery_version=discovery_version,
            supporting_evidence_ids=tuple(body["supporting_evidence_ids"]),
            supporting_primitive_ids=tuple(body["supporting_primitive_ids"]),
            supporting_behaviour_observation_ids=tuple(body["supporting_behaviour_observation_ids"]),
            supporting_topology_revision_ids=tuple(body["supporting_topology_revision_ids"]),
            observed_recurring_pattern=body["observed_recurring_pattern"],
            population=tuple(body["population"]), time_start=time_start,
            time_end=time_end, quality_state=quality_state,
            missing_evidence=tuple(body["missing_evidence"]),
            contradictory_evidence=tuple(body["contradictory_evidence"]),
            lifecycle=lifecycle, input_digest=input_digest,
            generated_at=int(generated_at),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "discovery_version": self.discovery_version,
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "supporting_primitive_ids": list(self.supporting_primitive_ids),
            "supporting_behaviour_observation_ids": list(self.supporting_behaviour_observation_ids),
            "supporting_topology_revision_ids": list(self.supporting_topology_revision_ids),
            "observed_recurring_pattern": dict(self.observed_recurring_pattern),
            "population": list(self.population),
            "time_window": {"start": self.time_start, "end": self.time_end},
            "quality_state": self.quality_state,
            "missing_evidence": list(self.missing_evidence),
            "contradictory_evidence": list(self.contradictory_evidence),
            "lifecycle": self.lifecycle, "input_digest": self.input_digest,
            "generated_at": self.generated_at,
        }
