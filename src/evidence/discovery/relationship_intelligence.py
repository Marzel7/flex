"""Deterministic, evidence-supported relationships between motifs."""

from __future__ import annotations

import hashlib
import time
from collections import Counter,defaultdict
from dataclasses import dataclass
from typing import Any,Mapping,Sequence

from ..contracts import canonical_json_bytes
from .change_intelligence import OperationalLandscapeSnapshot
from .evolution_intelligence import EvolutionSnapshot


def _digest(kind:str,value:Any)->str:
    return hashlib.sha256(canonical_json_bytes([kind,value])).hexdigest()


@dataclass(frozen=True)
class MotifRelationship:
    relationship_id:str
    observation_id:str
    relationship_type:str
    relationship_version:str
    replay_version:str
    landscape_snapshot_id:str
    source_motif_id:str
    target_motif_id:str
    supporting_evidence_ids:tuple[str,...]
    supporting_primitive_ids:tuple[str,...]
    supporting_behaviour_ids:tuple[str,...]
    supporting_topology_ids:tuple[str,...]
    supporting_temporal_ids:tuple[str,...]
    supporting_infrastructure_subjects:tuple[str,...]
    observation_window:Mapping[str,Any]
    measurements:Mapping[str,Any]

    def to_dict(self)->dict[str,Any]:
        return {"relationship_id":self.relationship_id,"observation_id":self.observation_id,
            "relationship_type":self.relationship_type,
            "relationship_version":self.relationship_version,"replay_version":self.replay_version,
            "landscape_snapshot_id":self.landscape_snapshot_id,
            "source_motif_id":self.source_motif_id,"target_motif_id":self.target_motif_id,
            "supporting_evidence_ids":list(self.supporting_evidence_ids),
            "supporting_primitive_ids":list(self.supporting_primitive_ids),
            "supporting_behaviour_ids":list(self.supporting_behaviour_ids),
            "supporting_topology_ids":list(self.supporting_topology_ids),
            "supporting_temporal_ids":list(self.supporting_temporal_ids),
            "supporting_infrastructure_subjects":list(self.supporting_infrastructure_subjects),
            "observation_window":dict(self.observation_window),"measurements":dict(self.measurements)}


@dataclass(frozen=True)
class RelationshipSnapshot:
    relationship_snapshot_id:str
    relationship_version:str
    replay_version:str
    landscape_snapshot_id:str
    relationships:tuple[MotifRelationship,...]
    coverage:Mapping[str,Any]

    def to_dict(self)->dict[str,Any]:
        return {"relationship_snapshot_id":self.relationship_snapshot_id,
            "relationship_version":self.relationship_version,"replay_version":self.replay_version,
            "landscape_snapshot_id":self.landscape_snapshot_id,
            "relationships":[item.to_dict() for item in self.relationships],
            "coverage":dict(self.coverage)}


@dataclass(frozen=True)
class RelationshipEvolution:
    evolution_id:str
    event_type:str
    previous_relationship_ids:tuple[str,...]
    current_relationship_ids:tuple[str,...]
    supporting_evolution_edge_ids:tuple[str,...]
    measurements:Mapping[str,Any]

    def to_dict(self)->dict[str,Any]:
        return {"evolution_id":self.evolution_id,"event_type":self.event_type,
            "previous_relationship_ids":list(self.previous_relationship_ids),
            "current_relationship_ids":list(self.current_relationship_ids),
            "supporting_evolution_edge_ids":list(self.supporting_evolution_edge_ids),
            "measurements":dict(self.measurements)}


@dataclass(frozen=True)
class RelationshipEvolutionSnapshot:
    evolution_snapshot_id:str
    evolution_version:str
    previous_relationship_snapshot_id:str
    current_relationship_snapshot_id:str
    operational_evolution_snapshot_id:str
    observations:tuple[RelationshipEvolution,...]

    def to_dict(self)->dict[str,Any]:
        return {"evolution_snapshot_id":self.evolution_snapshot_id,
            "evolution_version":self.evolution_version,
            "previous_relationship_snapshot_id":self.previous_relationship_snapshot_id,
            "current_relationship_snapshot_id":self.current_relationship_snapshot_id,
            "operational_evolution_snapshot_id":self.operational_evolution_snapshot_id,
            "observations":[item.to_dict() for item in self.observations]}


class CrossMotifRelationshipEngine:
    """Materialize typed observations from already-recorded objective links."""

    VERSION="1.0.0"
    REPLAY_VERSION="1"

    def __init__(self)->None:
        self._health={"status":"IDLE","authoritative":False,"identity_enabled":False,
            "ownership_inference_enabled":False,"governance_enabled":False,
            "operation_inference_enabled":False}

    @staticmethod
    def _profile(snapshot:OperationalLandscapeSnapshot,motif_id:str):
        return next(item for item in snapshot.profiles if item.motif_id==motif_id)

    @staticmethod
    def _motif(snapshot:OperationalLandscapeSnapshot,motif_id:str):
        return next(item for item in snapshot.motifs if item.motif_id==motif_id)

    @staticmethod
    def _neighbourhoods(snapshot:OperationalLandscapeSnapshot)->dict[str,set[str]]:
        result=defaultdict(set)
        for item in snapshot.dominant_analysis.neighbourhoods:
            for motif_id in item["motif_ids"]:result[motif_id].add(item["neighbourhood_id"])
        return result

    @staticmethod
    def _window(left,right)->dict[str,Any]:
        starts=[value for value in (left.timeline.get("first_observed"),
                                    right.timeline.get("first_observed")) if value is not None]
        ends=[value for value in (left.timeline.get("last_observed"),
                                  right.timeline.get("last_observed")) if value is not None]
        start=max(starts) if len(starts)==2 else None;end=min(ends) if len(ends)==2 else None
        if start is None or end is None or start>end:
            return {"start":None,"end":None,"duration":None,"state":"NO_OBSERVED_OVERLAP"}
        return {"start":start,"end":end,"duration":end-start,"state":"OBSERVED_OVERLAP"}

    def _relationship(self,snapshot:OperationalLandscapeSnapshot,edge:Mapping[str,Any],
                      relationship_type:str,support:Mapping[str,Sequence[str]])->MotifRelationship:
        source,target=sorted((edge["source_motif_id"],edge["target_motif_id"]))
        left=self._profile(snapshot,source);right=self._profile(snapshot,target)
        window=self._window(left,right)
        evidence=tuple(sorted(set(support.get("evidence",()))))
        primitives=tuple(sorted(set(support.get("primitives",()))))
        behaviour=tuple(sorted(set(support.get("behaviour",()))))
        topology=tuple(sorted(set(support.get("topology",()))))
        temporal=tuple(sorted(set(support.get("temporal",()))))
        infrastructure=tuple(sorted(set(support.get("infrastructure",()))))
        relationship_body={"version":self.VERSION,"type":relationship_type,
            "source_motif_id":source,"target_motif_id":target}
        relationship_id=_digest("MotifRelationship",relationship_body)
        left_complete=left.measurements["completeness"];right_complete=right.measurements["completeness"]
        evidence_complete=min(left_complete.get("evidence_completeness_ppm",1_000_000),
                              right_complete.get("evidence_completeness_ppm",1_000_000))
        primitive_complete=min(left_complete.get("primitive_completeness_ppm",1_000_000),
                               right_complete.get("primitive_completeness_ppm",1_000_000))
        support_count=len(set(evidence)|set(primitives)|set(behaviour)|set(topology)|set(temporal)|
                          set(infrastructure))
        measurements={"supporting_observation_count":support_count,"supporting_motif_count":2,
            "observation_duration":window["duration"],"evidence_completeness_ppm":evidence_complete,
            "primitive_completeness_ppm":primitive_complete,
            "dormant":all((item.timeline.get("dormancy_duration") or 0)>0 for item in (left,right))}
        observation_body={"relationship_id":relationship_id,"snapshot_id":snapshot.snapshot_id,
            "support":{"evidence":evidence,"primitives":primitives,"behaviour":behaviour,
                "topology":topology,"temporal":temporal,"infrastructure":infrastructure},
            "window":window,"measurements":measurements}
        return MotifRelationship(relationship_id,_digest("MotifRelationshipObservation",observation_body),
            relationship_type,self.VERSION,self.REPLAY_VERSION,snapshot.snapshot_id,source,target,evidence,
            primitives,behaviour,topology,temporal,infrastructure,window,measurements)

    def materialize(self,snapshot:OperationalLandscapeSnapshot,*,
                    primitive_types:Mapping[str,str]|None=None)->RelationshipSnapshot:
        started=time.perf_counter();relationships=[];neighbourhoods=self._neighbourhoods(snapshot)
        primitive_types=primitive_types or {}
        for edge in snapshot.dominant_analysis.relationships:
            source=edge["source_motif_id"];target=edge["target_motif_id"]
            types=[]
            if edge.get("shared_evidence_ids"):types.append(("SHARED_EVIDENCE_PROVENANCE",
                {"evidence":edge["shared_evidence_ids"]}))
            if edge.get("shared_primitive_ids"):types.append(("SHARED_PRIMITIVE_OBSERVATION",
                {"primitives":edge["shared_primitive_ids"]}))
            funding=tuple(sorted(value for value in edge.get("shared_primitive_ids",())
                                 if primitive_types.get(value)=="ECONOMIC_FUNDING"))
            counterparties=tuple(sorted(value for value in edge.get("shared_primitive_ids",())
                if primitive_types.get(value) in ("DIRECT_COUNTERPARTY","REPEATED_COUNTERPARTY")))
            if funding:types.append(("SHARED_FUNDING_OBSERVATION",{"primitives":funding}))
            if counterparties:types.append(("SHARED_COUNTERPARTY_OBSERVATION",
                                             {"primitives":counterparties}))
            if edge.get("shared_infrastructure_subjects"):types.append(("SHARED_INFRASTRUCTURE",
                {"infrastructure":edge["shared_infrastructure_subjects"]}))
            if edge.get("exact_topology_fingerprint"):
                left=self._motif(snapshot,source);right=self._motif(snapshot,target)
                types.append(("SHARED_TOPOLOGY",{"topology":(_digest("Topology",
                    left.canonical_graph),_digest("Topology",right.canonical_graph))}))
            if edge.get("exact_behaviour_fingerprint"):
                left=self._profile(snapshot,source);right=self._profile(snapshot,target)
                behaviour_ids=(_digest("BehaviourObservation",left.measurements.get("behaviour",{})),
                    _digest("BehaviourObservation",right.measurements.get("behaviour",{})))
                types.append(("SHARED_BEHAVIOUR",{"behaviour":behaviour_ids}))
                left_cadence=left.measurements.get("behaviour",{}).get(
                    "launch_cadence_per_active_day_milli")
                right_cadence=right.measurements.get("behaviour",{}).get(
                    "launch_cadence_per_active_day_milli")
                if left_cadence is not None and left_cadence==right_cadence:
                    types.append(("SHARED_CADENCE",{"behaviour":behaviour_ids}))
            if edge.get("observation_windows_overlap"):
                left=self._profile(snapshot,source);right=self._profile(snapshot,target)
                types.append(("SHARED_TEMPORAL_OBSERVATION",{"temporal":(_digest(
                    "TemporalObservation",left.timeline),_digest("TemporalObservation",right.timeline))}))
            shared_neighbourhoods=neighbourhoods[source]&neighbourhoods[target]
            if shared_neighbourhoods:types.append(("SHARED_NEIGHBOURHOOD",
                {"topology":tuple(sorted(shared_neighbourhoods))}))
            for relationship_type,support in types:
                relationships.append(self._relationship(snapshot,edge,relationship_type,support))
        relationships=tuple(sorted(relationships,key=lambda item:item.observation_id))
        coverage={"source_relationships":len(snapshot.dominant_analysis.relationships),
            "typed_relationship_observations":len(relationships),"coverage_ppm":1_000_000}
        body={"version":self.VERSION,"replay_version":self.REPLAY_VERSION,
            "landscape_snapshot_id":snapshot.snapshot_id,
            "relationships":[item.to_dict() for item in relationships],"coverage":coverage}
        result=RelationshipSnapshot(_digest("RelationshipSnapshot",body),self.VERSION,
            self.REPLAY_VERSION,snapshot.snapshot_id,relationships,coverage)
        counts=Counter(item.relationship_type for item in relationships)
        self._health={"status":"HEALTHY","relationships":len(relationships),
            "relationship_types":dict(sorted(counts.items())),"coverage_ppm":1_000_000,
            "completeness_ppm":1_000_000,"replay_latency_ms":round(
                (time.perf_counter()-started)*1000,3),"authoritative":False,
            "identity_enabled":False,"ownership_inference_enabled":False,
            "governance_enabled":False,"operation_inference_enabled":False}
        return result

    def health(self)->dict[str,Any]:return dict(self._health)


class RelationshipEvolutionEngine:
    VERSION="1.0.0"

    def __init__(self)->None:
        self._health={"status":"IDLE","authoritative":False,"identity_enabled":False,
            "ownership_inference_enabled":False,"governance_enabled":False}

    @staticmethod
    def _event(event_type:str,previous_ids:Sequence[str],current_ids:Sequence[str],
               edge_ids:Sequence[str],measurements:Mapping[str,Any])->RelationshipEvolution:
        body={"event_type":event_type,"previous":sorted(previous_ids),"current":sorted(current_ids),
            "edges":sorted(edge_ids),"measurements":dict(measurements)}
        return RelationshipEvolution(_digest("RelationshipEvolution",body),event_type,
            tuple(body["previous"]),tuple(body["current"]),tuple(body["edges"]),dict(measurements))

    def compare(self,previous:RelationshipSnapshot,current:RelationshipSnapshot,
                evolution:EvolutionSnapshot)->RelationshipEvolutionSnapshot:
        started=time.perf_counter()
        nodes={item.node_id:item for item in evolution.nodes if item.node_type=="MOTIF"}
        descendants=defaultdict(set);edge_support=defaultdict(set)
        for edge in evolution.edges:
            source=nodes.get(edge.source_node_id);target=nodes.get(edge.target_node_id)
            if source is None or target is None:continue
            descendants[source.subject_id].add(target.subject_id)
            edge_support[(source.subject_id,target.subject_id)].add(edge.edge_id)
        old={item.relationship_id:item for item in previous.relationships}
        new={item.relationship_id:item for item in current.relationships}
        old_to_new=defaultdict(set);new_to_old=defaultdict(set);supports=defaultdict(set)
        for old_id,left in old.items():
            for new_id,right in new.items():
                if left.relationship_type!=right.relationship_type:continue
                old_endpoints=(left.source_motif_id,left.target_motif_id)
                new_endpoints={right.source_motif_id,right.target_motif_id}
                if any(not descendants[value] for value in old_endpoints):continue
                if not any({a,b}==new_endpoints for a in descendants[old_endpoints[0]]
                           for b in descendants[old_endpoints[1]]):continue
                old_to_new[old_id].add(new_id);new_to_old[new_id].add(old_id)
                for old_endpoint in old_endpoints:
                    for new_endpoint in new_endpoints:
                        supports[(old_id,new_id)].update(edge_support[(old_endpoint,new_endpoint)])
        components=[];unseen_old=set(old);unseen_new=set(new)
        while unseen_old or unseen_new:
            queue=[("old",min(unseen_old))] if unseen_old else [("new",min(unseen_new))]
            left=set();right=set()
            while queue:
                side,value=queue.pop()
                if side=="old":
                    if value in left:continue
                    left.add(value);unseen_old.discard(value);queue.extend(("new",x) for x in old_to_new[value])
                else:
                    if value in right:continue
                    right.add(value);unseen_new.discard(value);queue.extend(("old",x) for x in new_to_old[value])
            components.append((left,right))
        observations=[]
        for old_ids,new_ids in components:
            before=sum(old[value].measurements["supporting_observation_count"] for value in old_ids)
            after=sum(new[value].measurements["supporting_observation_count"] for value in new_ids)
            edge_ids=sorted({value for left in old_ids for right in new_ids
                             for value in supports[(left,right)]})
            measurements={"supporting_observations_before":before,
                "supporting_observations_after":after,"supporting_observation_delta":after-before,
                "relationship_persistence":bool(old_ids and new_ids)}
            types=[]
            if not old_ids:types.append("CREATED")
            elif not new_ids:types.append("RETIRED")
            else:
                if len(old_ids)==1 and len(new_ids)>1:types.append("SPLIT")
                if len(old_ids)>1 and len(new_ids)==1:types.append("MERGED")
                if after>before:types.append("STRENGTHENED")
                elif after<before:types.append("WEAKENED")
                old_dormant=all(old[value].measurements["dormant"] for value in old_ids)
                new_dormant=all(new[value].measurements["dormant"] for value in new_ids)
                if new_dormant:types.append("DORMANT")
                if old_dormant and not new_dormant:types.append("REACTIVATED")
                if not types:types.append("PERSISTED")
            for event_type in types:
                observations.append(self._event(event_type,old_ids,new_ids,edge_ids,measurements))
        observations=tuple(sorted(observations,key=lambda item:item.evolution_id))
        body={"version":self.VERSION,"previous":previous.relationship_snapshot_id,
            "current":current.relationship_snapshot_id,"evolution":evolution.evolution_snapshot_id,
            "observations":[item.to_dict() for item in observations]}
        result=RelationshipEvolutionSnapshot(_digest("RelationshipEvolutionSnapshot",body),self.VERSION,
            previous.relationship_snapshot_id,current.relationship_snapshot_id,
            evolution.evolution_snapshot_id,observations)
        counts=Counter(item.event_type for item in observations)
        self._health={"status":"HEALTHY","relationship_changes":len(observations),
            "change_types":dict(sorted(counts.items())),"coverage_ppm":1_000_000,
            "replay_latency_ms":round((time.perf_counter()-started)*1000,3),
            "authoritative":False,"identity_enabled":False,
            "ownership_inference_enabled":False,"governance_enabled":False}
        return result

    def health(self)->dict[str,Any]:return dict(self._health)
