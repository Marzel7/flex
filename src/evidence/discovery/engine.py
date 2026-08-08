"""Label-blind recurring-structure discovery from immutable observations."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Sequence

from .contracts import CandidateLifecycle, DiscoveryCandidate, DiscoverySnapshot


class DiscoveryEngine:
    VERSION = "1.0.0"

    def __init__(self, *, minimum_observations: int = 2,
                 maximum_population: int = 10_000) -> None:
        if minimum_observations < 1:
            raise ValueError("minimum_observations must be positive")
        self.minimum_observations = minimum_observations
        self.maximum_population = maximum_population
        self._metrics: Counter[str] = Counter()

    def discover(self, snapshot: DiscoverySnapshot) -> tuple[DiscoveryCandidate, ...]:
        candidate_generated_at = (
            snapshot.primitive_window.end
            if snapshot.primitive_window.end is not None
            else snapshot.evidence_window.end
            if snapshot.evidence_window.end is not None
            else 0
        )
        if snapshot.discovery_version != self.VERSION:
            raise ValueError("unsupported discovery version")
        by_subject: dict[str, list[Any]] = defaultdict(list)
        for primitive in snapshot.primitive_window.observations:
            if len(primitive.subjects) < 2:
                continue
            for subject in primitive.subjects:
                by_subject[subject].append(primitive)
        behaviours_by_subject: dict[str, list[Any]] = defaultdict(list)
        for observation in snapshot.behaviour_observations:
            for subject in observation.subjects:
                behaviours_by_subject[subject].append(observation)
        topology_by_subject: dict[str, list[Any]] = defaultdict(list)
        for revision in snapshot.topology_revisions:
            for subject in revision.subjects:
                topology_by_subject[subject].append(revision)

        candidates: dict[str, DiscoveryCandidate] = {}
        for subject, raw_observations in sorted(by_subject.items()):
            observations = sorted(
                {item.primitive_id: item for item in raw_observations}.values(),
                key=lambda item: item.primitive_id,
            )
            if len(observations) < self.minimum_observations:
                continue
            peers = sorted({peer for item in observations for peer in item.subjects
                            if peer != subject})
            if not peers:
                continue
            population = [subject, *peers]
            if len(population) > self.maximum_population:
                self._metrics["clusters_over_population_bound"] += 1
                continue
            primitive_counts = Counter(item.primitive_type for item in observations)
            quality_counts = Counter(item.quality_state for item in observations)
            directions = Counter()
            for item in observations:
                payload = item.output_payload
                if payload.get("source") == subject or payload.get("activation_sender") == subject:
                    directions["OUTBOUND"] += 1
                if payload.get("destination") == subject or payload.get("creator") == subject:
                    directions["INBOUND"] += 1
            dimensions = {"PATTERN", "TOPOLOGY"}
            if any(kind in primitive_counts for kind in
                   ("PROGRAM_INTERACTION", "SHARED_TRANSACTION", "REPEATED_COUNTERPARTY")):
                dimensions.add("INFRASTRUCTURE")
            if behaviours_by_subject.get(subject) or "BEHAVIOURAL_TIMING" in primitive_counts:
                dimensions.add("BEHAVIOUR")
            evidence_ids = sorted({ref for item in observations for ref in item.evidence_ids})
            contradictory = sorted({ref for item in observations
                                    if item.quality_state == "CONFLICTING"
                                    for ref in item.evidence_ids})
            missing = sorted({value for item in observations for value in item.missing_inputs})
            quality = ("CONFLICTING" if contradictory else
                       "INCOMPLETE" if missing else "PROVEN")
            starts = [item.observation_window.start for item in observations
                      if item.observation_window.start is not None]
            ends = [item.observation_window.end for item in observations
                    if item.observation_window.end is not None]
            behaviour_refs = [item.observation_id
                              for item in behaviours_by_subject.get(subject, ())]
            topology_refs = [item.revision_id
                             for item in topology_by_subject.get(subject, ())]
            pattern = {
                "pattern_type": "SUBJECT_CENTRIC_RECURRING_PRIMITIVE_MOTIF",
                "cluster_dimensions": sorted(dimensions),
                "observation_count": len(observations),
                "counterparty_count": len(peers),
                "primitive_type_counts": dict(sorted(primitive_counts.items())),
                "direction_counts": dict(sorted(directions.items())),
                "quality_counts": dict(sorted(quality_counts.items())),
            }
            lifecycle = (CandidateLifecycle.RECURRING_PATTERN.value
                         if len(observations) >= 2 else CandidateLifecycle.OBSERVED.value)
            candidate = DiscoveryCandidate.create(
                discovery_version=self.VERSION,
                supporting_evidence_ids=evidence_ids,
                supporting_primitive_ids=[item.primitive_id for item in observations],
                supporting_behaviour_observation_ids=behaviour_refs,
                supporting_topology_revision_ids=topology_refs,
                observed_recurring_pattern=pattern, population=population,
                time_start=min(starts) if starts else None,
                time_end=max(ends) if ends else None, quality_state=quality,
                missing_evidence=missing, contradictory_evidence=contradictory,
                lifecycle=lifecycle, input_digest=snapshot.input_digest,
                generated_at=candidate_generated_at,
            )
            candidates[candidate.candidate_id] = candidate
        result = tuple(candidates[key] for key in sorted(candidates))
        self._metrics["evaluations"] += 1
        self._metrics["candidates_generated"] += len(result)
        self._metrics["supporting_primitives"] += sum(
            len(item.supporting_primitive_ids) for item in result
        )
        return result

    def health(self) -> dict[str, Any]:
        return {
            "status": "HEALTHY", "discovery_version": self.VERSION,
            "metrics": dict(sorted(self._metrics.items())),
            "authoritative": False, "governance_enabled": False,
            "identity_enabled": False,
        }
