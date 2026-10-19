"""3SW2 Operation Contract v1 shadow-only implementations.

This operation intentionally models a controller-to-creator topology and does
not reuse WATCHTOWER behaviour or topology implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .formalization import (
    BehaviourModuleInput, BehaviourObservation, DetectorInput, DetectorResult,
    TopologyEdge, TopologyModuleInput, TopologyNode, TopologyRevision,
)


@dataclass(frozen=True)
class ThreeSw2Behaviour:
    module_id: str
    primitive_types: tuple[str, ...]
    module_version: str = "1.0.0"

    def evaluate(self, value: BehaviourModuleInput) -> BehaviourObservation:
        observations = value.primitive_window.observations
        proven = tuple(item for item in observations if item.quality_state == "PROVEN")
        missing = tuple(sorted({part for item in observations for part in item.missing_inputs}))
        evidence_refs = tuple(sorted({ref for item in observations for ref in item.evidence_ids}))
        counts = {kind: sum(item.primitive_type == kind for item in observations)
                  for kind in self.primitive_types}
        quality = "PROVEN" if proven and not missing else ("INCOMPLETE" if observations else "UNVERIFIABLE")
        return BehaviourObservation.create(
            contract_id=value.contract_id, contract_version=value.contract_version,
            module_id=self.module_id, module_version=self.module_version,
            subjects=value.subjects, parameters=value.parameters,
            observation_window=value.observation_window,
            measured_values={"observation_count": len(observations),
                             "proven_count": len(proven), "by_primitive_type": counts},
            evidence_refs=evidence_refs, primitive_refs=value.primitive_refs,
            missing_inputs=missing, quality_state=quality,
            input_digest=value.snapshot_digest, generated_at=value.generated_at,
        )


class ThreeSw2Topology:
    topology_version = "3.2.0"

    def generate(self, value: TopologyModuleInput) -> TopologyRevision:
        controller = str(value.contract["topology_contract"]["parameters"]["controller_subject"])
        nodes: dict[tuple[str, str], TopologyNode] = {}
        edges: list[TopologyEdge] = []
        for item in value.primitive_window.observations:
            payload, refs, primitive_refs = item.output_payload, tuple(item.evidence_ids), (item.primitive_id,)
            if item.quality_state != "PROVEN":
                continue
            if item.primitive_type == "SYSTEM_TRANSFER" and payload.get("source") == controller:
                creator = payload.get("destination")
                if creator:
                    nodes[(controller, "controller")] = TopologyNode(
                        controller, "controller", value.contract["contract_id"],
                        value.contract["contract_version"], refs, primitive_refs)
                    nodes[(str(creator), "creator")] = TopologyNode(
                        str(creator), "creator", value.contract["contract_id"],
                        value.contract["contract_version"], refs, primitive_refs)
                    edges.append(TopologyEdge(controller, str(creator), "SYSTEM_TRANSFER",
                                              "ONE_TO_MANY", None, True, refs, primitive_refs))
            elif item.primitive_type == "LAUNCH_SIGNER" and payload.get("signer") is True:
                creator, mint = payload.get("wallet"), payload.get("mint")
                if creator and mint:
                    nodes[(str(creator), "creator")] = TopologyNode(
                        str(creator), "creator", value.contract["contract_id"],
                        value.contract["contract_version"], refs, primitive_refs)
                    nodes[(str(mint), "launch")] = TopologyNode(
                        str(mint), "launch", value.contract["contract_id"],
                        value.contract["contract_version"], refs, primitive_refs)
                    edges.append(TopologyEdge(str(creator), str(mint), "LAUNCH_SIGNER",
                                              "ONE_TO_ONE", None, True, refs, primitive_refs))
        return TopologyRevision.create(
            contract_id=value.contract["contract_id"], contract_version=value.contract["contract_version"],
            topology_version=self.topology_version, subjects=value.subjects,
            nodes=tuple(nodes.values()), edges=tuple(edges),
            behaviour_observation_refs=tuple(item.observation_id for item in value.behaviour_observations),
            input_digest=value.snapshot_digest, generated_at=value.generated_at,
        )


class ThreeSw2Detector:
    detector_id = "three_sw2_detector"
    detector_version = "1.0.0"

    def evaluate(self, value: DetectorInput) -> DetectorResult:
        observations = value.primitive_window.observations
        proven = tuple(item for item in observations if item.quality_state == "PROVEN")
        contacts = tuple(item for item in proven if item.primitive_type == "DIRECT_COUNTERPARTY")
        missing = tuple(sorted({part for item in observations for part in item.missing_inputs}))
        supporting = tuple(sorted({ref for item in proven for ref in item.evidence_ids}))
        return DetectorResult.create(
            contract_id=value.contract_id, contract_version=value.contract_version,
            detector_version=self.detector_version, subjects=value.subjects,
            observation_window=value.observation_window,
            identity_evidence={"mode": "SHADOW_ONLY", "canonical_identity_claim": False,
                               "contact_promotes_identity": False},
            topology_evidence={"nodes": len(value.topology_revision.nodes),
                               "edges": len(value.topology_revision.edges)},
            behaviour_evidence={item.module_id: dict(item.measured_values)
                                for item in value.behaviour_observations},
            operational_contact={"direct_transaction_observations": len(contacts),
                                 "identity_effect": "NONE"},
            infrastructure_overlap={}, funding_overlap={}, temporal_overlap={},
            supporting_evidence_ids=supporting, contradictory_evidence_ids=(),
            primitive_refs=value.primitive_refs,
            behaviour_observation_refs=value.behaviour_observation_refs,
            topology_revision_ref=value.topology_revision_ref or "", missing_inputs=missing,
            confidence_output=None, candidate_lifecycle_recommendation=None,
            governance_recommendation="NO_AUTOMATIC_GOVERNANCE",
            input_watermark={"evidence": value.evidence_watermark,
                             "primitive": value.primitive_watermark},
            input_digest=value.input_digest, generated_at=value.generated_at,
        )


BEHAVIOURS = (
    ThreeSw2Behaviour("three_sw2_direct_activation", ("SYSTEM_TRANSFER", "LAUNCH_ACTIVATION")),
    ThreeSw2Behaviour("three_sw2_creator_freshness", ("WALLET_FRESH_AT_EVENT",)),
    ThreeSw2Behaviour("three_sw2_activation_timing", ("BEHAVIOURAL_TIMING",)),
    ThreeSw2Behaviour("three_sw2_economic_funding", ("ECONOMIC_FUNDING",)),
    ThreeSw2Behaviour("three_sw2_direct_creator_launch", ("LAUNCH_SIGNER",)),
    ThreeSw2Behaviour("three_sw2_controller_reuse", ("REPEATED_COUNTERPARTY",)),
    ThreeSw2Behaviour("three_sw2_no_intermediate_provisioner", ("SYSTEM_TRANSFER", "LAUNCH_SIGNER")),
)


def register_three_sw2_v1(registries: Any) -> None:
    for behaviour in BEHAVIOURS:
        registries.behaviours.register(behaviour)
    registries.topologies.register(ThreeSw2Topology())
    registries.detectors.register(ThreeSw2Detector())
    registries.presentations.register("3.2.0", {"contract": "three_sw2.v1", "authority": "SHADOW"})
