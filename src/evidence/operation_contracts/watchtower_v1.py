"""WATCHTOWER Operation Contract v1 shadow-only implementations.

The implementations in this module are pure functions over the immutable EP3.0B
runtime input.  They perform no storage, RPC, legacy, or governance access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .formalization import (
    BehaviourModuleInput, BehaviourObservation, DetectorInput, DetectorResult,
    TopologyEdge, TopologyModuleInput, TopologyNode, TopologyRevision,
)


@dataclass(frozen=True)
class WatchtowerPrimitiveBehaviour:
    module_id: str
    primitive_types: tuple[str, ...]
    module_version: str = "1.0.0"

    def evaluate(self, value: BehaviourModuleInput) -> BehaviourObservation:
        observations = value.primitive_window.observations
        proven = tuple(item for item in observations if item.quality_state == "PROVEN")
        missing = sorted({missing for item in observations for missing in item.missing_inputs})
        by_type = {kind: sum(item.primitive_type == kind for item in observations)
                   for kind in self.primitive_types}
        evidence_refs = sorted({ref for item in observations for ref in item.evidence_ids})
        quality = "PROVEN" if proven and not missing else ("INCOMPLETE" if observations else "UNVERIFIABLE")
        return BehaviourObservation.create(
            contract_id=value.contract_id, contract_version=value.contract_version,
            module_id=self.module_id, module_version=self.module_version,
            subjects=value.subjects, parameters=value.parameters,
            observation_window=value.observation_window,
            measured_values={"observation_count": len(observations),
                             "proven_count": len(proven), "by_primitive_type": by_type},
            evidence_refs=evidence_refs, primitive_refs=value.primitive_refs,
            missing_inputs=missing, quality_state=quality,
            input_digest=value.snapshot_digest, generated_at=value.generated_at,
        )


class WatchtowerTopology:
    topology_version = "1.0.0"

    def generate(self, value: TopologyModuleInput) -> TopologyRevision:
        nodes: dict[tuple[str, str], TopologyNode] = {}
        edges: list[TopologyEdge] = []
        for item in value.primitive_window.observations:
            if item.primitive_type != "SYSTEM_TRANSFER" or item.quality_state != "PROVEN":
                continue
            payload = item.output_payload
            source, destination = payload.get("source"), payload.get("destination")
            if not source or not destination:
                continue
            refs = tuple(item.evidence_ids)
            nodes[(str(source), "funding_source")] = TopologyNode(
                str(source), "funding_source", value.contract["contract_id"],
                value.contract["contract_version"], refs, (item.primitive_id,))
            nodes[(str(destination), "funded_wallet")] = TopologyNode(
                str(destination), "funded_wallet", value.contract["contract_id"],
                value.contract["contract_version"], refs, (item.primitive_id,))
            edges.append(TopologyEdge(str(source), str(destination), "SYSTEM_TRANSFER",
                                      "ONE_TO_MANY", None, True, refs, (item.primitive_id,)))
        return TopologyRevision.create(
            contract_id=value.contract["contract_id"],
            contract_version=value.contract["contract_version"],
            topology_version=self.topology_version, subjects=value.subjects,
            nodes=tuple(nodes.values()), edges=tuple(edges),
            behaviour_observation_refs=tuple(item.observation_id for item in value.behaviour_observations),
            input_digest=value.snapshot_digest, generated_at=value.generated_at,
        )


class WatchtowerDetector:
    detector_id = "watchtower_detector"
    detector_version = "1.0.0"

    def evaluate(self, value: DetectorInput) -> DetectorResult:
        observations = value.primitive_window.observations
        proven = tuple(item for item in observations if item.quality_state == "PROVEN")
        missing = sorted({missing for item in observations for missing in item.missing_inputs})
        behaviour = {item.module_id: dict(item.measured_values) for item in value.behaviour_observations}
        supporting = sorted({ref for item in proven for ref in item.evidence_ids})
        return DetectorResult.create(
            contract_id=value.contract_id, contract_version=value.contract_version,
            detector_version=self.detector_version, subjects=value.subjects,
            observation_window=value.observation_window,
            identity_evidence={"mode": "SHADOW_ONLY", "canonical_identity_claim": False},
            topology_evidence={"nodes": len(value.topology_revision.nodes),
                               "edges": len(value.topology_revision.edges)},
            behaviour_evidence=behaviour, operational_contact={"observed": False},
            infrastructure_overlap={}, funding_overlap={}, temporal_overlap={},
            supporting_evidence_ids=supporting, contradictory_evidence_ids=(),
            primitive_refs=value.primitive_refs,
            behaviour_observation_refs=value.behaviour_observation_refs,
            topology_revision_ref=value.topology_revision_ref or "",
            missing_inputs=missing, confidence_output=None,
            candidate_lifecycle_recommendation=None,
            governance_recommendation="NO_AUTOMATIC_GOVERNANCE",
            input_watermark={"evidence": value.evidence_watermark,
                             "primitive": value.primitive_watermark},
            input_digest=value.input_digest, generated_at=value.generated_at,
        )


BEHAVIOURS = (
    WatchtowerPrimitiveBehaviour("watchtower_wrap_close", ("WSOL_CLOSE",)),
    WatchtowerPrimitiveBehaviour("watchtower_creator_freshness", ("WALLET_FRESH_AT_EVENT",)),
    WatchtowerPrimitiveBehaviour("watchtower_funding", ("SYSTEM_TRANSFER", "DIRECT_COUNTERPARTY", "LAUNCH_ACTIVATION", "ECONOMIC_FUNDING")),
    WatchtowerPrimitiveBehaviour("watchtower_launch_identity", ("LAUNCH_SIGNER",)),
    WatchtowerPrimitiveBehaviour("watchtower_timing", ("BEHAVIOURAL_TIMING",)),
    WatchtowerPrimitiveBehaviour("watchtower_reuse", ("REPEATED_COUNTERPARTY",)),
)


def register_watchtower_v1(registries: Any) -> None:
    for behaviour in BEHAVIOURS:
        registries.behaviours.register(behaviour)
    registries.topologies.register(WatchtowerTopology())
    registries.detectors.register(WatchtowerDetector())
    registries.presentations.register("1.0.0", {"contract": "watchtower.v1", "authority": "SHADOW"})
