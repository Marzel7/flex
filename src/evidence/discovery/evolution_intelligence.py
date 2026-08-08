"""Deterministic, identity-free operational evolution graphs."""

from __future__ import annotations

import hashlib
import time
from collections import Counter,defaultdict
from dataclasses import dataclass
from typing import Any,Mapping

from ..contracts import canonical_json_bytes
from .change_intelligence import ChangeSnapshot,OperationalLandscapeSnapshot
from .motifs import OperationMotif


def _digest(kind:str,value:Any)->str:
    return hashlib.sha256(canonical_json_bytes([kind,value])).hexdigest()


@dataclass(frozen=True)
class EvolutionNode:
    node_id:str
    node_type:str
    subject_id:str
    landscape_snapshot_id:str
    observation_boundary:int|None
    canonical_topology_digest:str
    occurrence_count:int
    supporting_evidence_ids:tuple[str,...]
    supporting_primitive_ids:tuple[str,...]

    def to_dict(self)->dict[str,Any]:
        return {"node_id":self.node_id,"node_type":self.node_type,"subject_id":self.subject_id,
            "landscape_snapshot_id":self.landscape_snapshot_id,
            "observation_boundary":self.observation_boundary,
            "canonical_topology_digest":self.canonical_topology_digest,
            "occurrence_count":self.occurrence_count,
            "supporting_evidence_ids":list(self.supporting_evidence_ids),
            "supporting_primitive_ids":list(self.supporting_primitive_ids)}


@dataclass(frozen=True)
class EvolutionEdge:
    edge_id:str
    source_node_id:str
    target_node_id:str
    continuity_basis:tuple[str,...]
    supporting_candidate_ids:tuple[str,...]
    supporting_evidence_ids:tuple[str,...]
    supporting_primitive_ids:tuple[str,...]
    supporting_topology:Mapping[str,Any]
    supporting_relationship_ids:tuple[str,...]
    supporting_temporal_observation_ids:tuple[str,...]

    def to_dict(self)->dict[str,Any]:
        return {"edge_id":self.edge_id,"source_node_id":self.source_node_id,
            "target_node_id":self.target_node_id,"continuity_basis":list(self.continuity_basis),
            "supporting_candidate_ids":list(self.supporting_candidate_ids),
            "supporting_evidence_ids":list(self.supporting_evidence_ids),
            "supporting_primitive_ids":list(self.supporting_primitive_ids),
            "supporting_topology":dict(self.supporting_topology),
            "supporting_relationship_ids":list(self.supporting_relationship_ids),
            "supporting_temporal_observation_ids":list(self.supporting_temporal_observation_ids)}


@dataclass(frozen=True)
class EvolutionEvent:
    event_id:str
    event_type:str
    source_node_ids:tuple[str,...]
    target_node_ids:tuple[str,...]
    supporting_edge_ids:tuple[str,...]
    change_record_ids:tuple[str,...]
    measurements:Mapping[str,Any]

    def to_dict(self)->dict[str,Any]:
        return {"event_id":self.event_id,"event_type":self.event_type,
            "source_node_ids":list(self.source_node_ids),"target_node_ids":list(self.target_node_ids),
            "supporting_edge_ids":list(self.supporting_edge_ids),
            "change_record_ids":list(self.change_record_ids),"measurements":dict(self.measurements)}


@dataclass(frozen=True)
class EvolutionSnapshot:
    evolution_snapshot_id:str
    evolution_version:str
    previous_landscape_snapshot_id:str
    current_landscape_snapshot_id:str
    change_snapshot_id:str
    nodes:tuple[EvolutionNode,...]
    edges:tuple[EvolutionEdge,...]
    events:tuple[EvolutionEvent,...]
    coverage:Mapping[str,Any]

    def to_dict(self)->dict[str,Any]:
        return {"evolution_snapshot_id":self.evolution_snapshot_id,
            "evolution_version":self.evolution_version,
            "previous_landscape_snapshot_id":self.previous_landscape_snapshot_id,
            "current_landscape_snapshot_id":self.current_landscape_snapshot_id,
            "change_snapshot_id":self.change_snapshot_id,"nodes":[item.to_dict() for item in self.nodes],
            "edges":[item.to_dict() for item in self.edges],
            "events":[item.to_dict() for item in self.events],"coverage":dict(self.coverage)}


class OperationalEvolutionEngine:
    """Build immutable lineage from exact structural persistence and occurrence continuity."""

    VERSION="1.0.0"
    EVENT_ORDER=("NEW","CONTINUED","GREW","DECLINED","DORMANT","REACTIVATED",
                 "SPLIT","MERGED","RETIRED")

    def __init__(self)->None:
        self._health={"status":"IDLE","authoritative":False,"identity_enabled":False,
            "governance_enabled":False,"operation_inference_enabled":False}

    @staticmethod
    def _motif_node(snapshot:OperationalLandscapeSnapshot,motif:OperationMotif)->EvolutionNode:
        body={"snapshot_id":snapshot.snapshot_id,"motif_id":motif.motif_id}
        return EvolutionNode(_digest("EvolutionNode",body),"MOTIF",motif.motif_id,snapshot.snapshot_id,
            snapshot.observation_boundary,_digest("Topology",motif.canonical_graph),
            len(motif.occurrences),tuple(sorted(motif.supporting_evidence_ids)),
            tuple(sorted(motif.supporting_primitive_ids)))

    @staticmethod
    def _neighbourhood_node(snapshot:OperationalLandscapeSnapshot,
                            neighbourhood:Mapping[str,Any])->EvolutionNode:
        motif_index={item.motif_id:item for item in snapshot.motifs}
        members=tuple(sorted(neighbourhood["motif_ids"]));evidence=set();primitives=set()
        for motif_id in members:
            motif=motif_index[motif_id];evidence.update(motif.supporting_evidence_ids)
            primitives.update(motif.supporting_primitive_ids)
        topology={"motif_ids":members,"relationship_ids":sorted(item["relationship_id"]
            for item in snapshot.dominant_analysis.relationships
            if item["source_motif_id"] in members and item["target_motif_id"] in members)}
        body={"snapshot_id":snapshot.snapshot_id,
              "neighbourhood_id":neighbourhood["neighbourhood_id"]}
        return EvolutionNode(_digest("EvolutionNode",body),"NEIGHBOURHOOD",
            neighbourhood["neighbourhood_id"],snapshot.snapshot_id,snapshot.observation_boundary,
            _digest("NeighbourhoodTopology",topology),len(members),tuple(sorted(evidence)),
            tuple(sorted(primitives)))

    @staticmethod
    def _occurrences(motif:OperationMotif)->dict[str,Any]:
        return {item.candidate_id:item for item in motif.occurrences}

    @staticmethod
    def _relationships(snapshot:OperationalLandscapeSnapshot,motif_id:str)->set[str]:
        return {item["relationship_id"] for item in snapshot.dominant_analysis.relationships
            if motif_id in (item["source_motif_id"],item["target_motif_id"])}

    def _edge(self,previous:OperationalLandscapeSnapshot,current:OperationalLandscapeSnapshot,
              old:OperationMotif,new:OperationMotif,old_node:EvolutionNode,new_node:EvolutionNode,
              temporal_by_pair:Mapping[tuple[str,str],tuple[str,...]])->EvolutionEdge|None:
        old_occ=self._occurrences(old);new_occ=self._occurrences(new)
        shared=tuple(sorted(set(old_occ)&set(new_occ)))
        basis=[]
        if old.motif_id==new.motif_id:basis.append("CANONICAL_MOTIF_PERSISTENCE")
        if shared:basis.append("EXACT_OCCURRENCE_CONTINUITY")
        if not basis:return None
        evidence=set();primitives=set()
        for candidate_id in shared:
            for occurrence in (old_occ[candidate_id],new_occ[candidate_id]):
                evidence.update(occurrence.supporting_evidence_ids)
                primitives.update(occurrence.supporting_primitive_ids)
        if not shared:
            evidence.update(old.supporting_evidence_ids);evidence.update(new.supporting_evidence_ids)
            primitives.update(old.supporting_primitive_ids);primitives.update(new.supporting_primitive_ids)
        old_topology=_digest("Topology",old.canonical_graph);new_topology=_digest("Topology",new.canonical_graph)
        temporal=temporal_by_pair.get((old.motif_id,new.motif_id),())
        relationships=tuple(sorted(self._relationships(previous,old.motif_id)&
                                   self._relationships(current,new.motif_id)))
        support={"previous_topology_digest":old_topology,"current_topology_digest":new_topology,
            "topology_persisted":old_topology==new_topology}
        body={"source":old_node.node_id,"target":new_node.node_id,"basis":basis,
            "candidates":shared,"evidence":sorted(evidence),"primitives":sorted(primitives),
            "topology":support,"relationships":relationships,"temporal":temporal}
        return EvolutionEdge(_digest("EvolutionEdge",body),old_node.node_id,new_node.node_id,
            tuple(basis),shared,tuple(sorted(evidence)),tuple(sorted(primitives)),support,
            relationships,temporal)

    @staticmethod
    def _event(event_type:str,sources:tuple[str,...],targets:tuple[str,...],
               edges:tuple[str,...],records:tuple[str,...],measurements:Mapping[str,Any])->EvolutionEvent:
        body={"event_type":event_type,"sources":sources,"targets":targets,"edges":edges,
            "records":records,"measurements":dict(measurements)}
        return EvolutionEvent(_digest("EvolutionEvent",body),event_type,sources,targets,edges,
                              records,dict(measurements))

    @staticmethod
    def _neighbourhood_edge(previous_node:EvolutionNode,current_node:EvolutionNode,
                            delta,previous:OperationalLandscapeSnapshot,
                            current:OperationalLandscapeSnapshot)->EvolutionEdge:
        old_neighbourhood=next(item for item in previous.dominant_analysis.neighbourhoods
            if item["neighbourhood_id"]==previous_node.subject_id)
        new_neighbourhood=next(item for item in current.dominant_analysis.neighbourhoods
            if item["neighbourhood_id"]==current_node.subject_id)
        old_members=set(old_neighbourhood["motif_ids"]);new_members=set(new_neighbourhood["motif_ids"])
        old_relationships={item["relationship_id"] for item in previous.dominant_analysis.relationships
            if item["source_motif_id"] in old_members or item["target_motif_id"] in old_members}
        new_relationships={item["relationship_id"] for item in current.dominant_analysis.relationships
            if item["source_motif_id"] in new_members or item["target_motif_id"] in new_members}
        relationships=tuple(sorted(old_relationships&new_relationships))
        evidence=tuple(sorted(set(previous_node.supporting_evidence_ids)|
                              set(current_node.supporting_evidence_ids)))
        primitives=tuple(sorted(set(previous_node.supporting_primitive_ids)|
                                set(current_node.supporting_primitive_ids)))
        topology={"previous_topology_digest":previous_node.canonical_topology_digest,
            "current_topology_digest":current_node.canonical_topology_digest,
            "topology_persisted":previous_node.canonical_topology_digest==
                current_node.canonical_topology_digest}
        body={"source":previous_node.node_id,"target":current_node.node_id,
            "basis":["OBSERVED_NEIGHBOURHOOD_COMPONENT_CONTINUITY"],"evidence":evidence,
            "primitives":primitives,"topology":topology,"relationships":relationships,
            "delta_id":delta.delta_id}
        return EvolutionEdge(_digest("EvolutionEdge",body),previous_node.node_id,
            current_node.node_id,("OBSERVED_NEIGHBOURHOOD_COMPONENT_CONTINUITY",),(),evidence,
            primitives,topology,relationships,())

    def reconstruct(self,previous:OperationalLandscapeSnapshot,current:OperationalLandscapeSnapshot,
                    change:ChangeSnapshot)->EvolutionSnapshot:
        if change.previous_snapshot_id!=previous.snapshot_id or change.current_snapshot_id!=current.snapshot_id:
            raise ValueError("change snapshot does not describe the supplied landscape snapshots")
        started=time.perf_counter();old={item.motif_id:item for item in previous.motifs}
        new={item.motif_id:item for item in current.motifs}
        old_nodes={key:self._motif_node(previous,value) for key,value in old.items()}
        new_nodes={key:self._motif_node(current,value) for key,value in new.items()}
        old_neighbourhoods={item["neighbourhood_id"]:item
            for item in previous.dominant_analysis.neighbourhoods}
        new_neighbourhoods={item["neighbourhood_id"]:item
            for item in current.dominant_analysis.neighbourhoods}
        old_neighbourhood_nodes={key:self._neighbourhood_node(previous,value)
            for key,value in old_neighbourhoods.items()}
        new_neighbourhood_nodes={key:self._neighbourhood_node(current,value)
            for key,value in new_neighbourhoods.items()}
        trends_by_delta=defaultdict(list)
        for observation in change.trend_observations:
            trends_by_delta[observation.motif_delta_id].append(observation.observation_id)
        temporal_by_pair={
            (old_id,new_id):tuple(sorted(trends_by_delta[delta.delta_id]))
            for delta in change.motif_deltas for old_id in delta.previous_motif_ids
            for new_id in delta.current_motif_ids}
        edges=[]
        # Candidate overlap is indexed to avoid similarity and all-pairs graph matching.
        old_by_candidate=defaultdict(set);new_by_candidate=defaultdict(set)
        for motif_id,motif in old.items():
            for candidate_id in self._occurrences(motif):old_by_candidate[candidate_id].add(motif_id)
        for motif_id,motif in new.items():
            for candidate_id in self._occurrences(motif):new_by_candidate[candidate_id].add(motif_id)
        pairs={(motif_id,motif_id) for motif_id in set(old)&set(new)}
        for candidate_id in set(old_by_candidate)&set(new_by_candidate):
            pairs.update((left,right) for left in old_by_candidate[candidate_id]
                         for right in new_by_candidate[candidate_id])
        for old_id,new_id in sorted(pairs):
            edge=self._edge(previous,current,old[old_id],new[new_id],old_nodes[old_id],
                            new_nodes[new_id],temporal_by_pair)
            if edge is not None:edges.append(edge)
        edges=tuple(sorted(edges,key=lambda item:item.edge_id))
        outgoing=defaultdict(list);incoming=defaultdict(list)
        for edge in edges:outgoing[edge.source_node_id].append(edge);incoming[edge.target_node_id].append(edge)
        events=[]
        for motif_id,node in old_nodes.items():
            if not outgoing[node.node_id]:
                delta=next((item for item in change.motif_deltas
                    if motif_id in item.previous_motif_ids and not item.current_motif_ids),None)
                records=(delta.delta_id,) if delta else ()
                events.append(self._event("RETIRED",(node.node_id,),(),(),records,
                    {"occurrences_before":node.occurrence_count,"occurrences_after":0}))
        for motif_id,node in new_nodes.items():
            if not incoming[node.node_id]:
                delta=next((item for item in change.motif_deltas
                    if motif_id in item.current_motif_ids and not item.previous_motif_ids),None)
                records=(delta.delta_id,) if delta else ()
                events.append(self._event("NEW",(),(node.node_id,),(),records,
                    {"occurrences_before":0,"occurrences_after":node.occurrence_count}))
        for delta in change.motif_deltas:
            source_ids=tuple(sorted(old_nodes[value].node_id for value in delta.previous_motif_ids))
            target_ids=tuple(sorted(new_nodes[value].node_id for value in delta.current_motif_ids))
            component_edges=tuple(sorted(edge.edge_id for edge in edges
                if edge.source_node_id in source_ids and edge.target_node_id in target_ids))
            if not source_ids or not target_ids:continue
            types=["CONTINUED"]
            mapping={"GROWING":"GREW","DECLINING":"DECLINED","DORMANT":"DORMANT",
                     "REACTIVATED":"REACTIVATED","SPLIT_MOTIF":"SPLIT",
                     "MERGED_MOTIF":"MERGED"}
            types.extend(mapping[value] for value in delta.change_types if value in mapping)
            measurements={"occurrences_before":delta.population_delta["occurrences_before"],
                "occurrences_after":delta.population_delta["occurrences_after"],
                "occurrence_delta":delta.population_delta["occurrence_delta"],
                "topology_changed":delta.topology_delta["changed"]}
            for event_type in self.EVENT_ORDER:
                if event_type in types:events.append(self._event(event_type,source_ids,target_ids,
                    component_edges,(delta.delta_id,),measurements))
        for delta in change.neighbourhood_deltas:
            source_ids=tuple(sorted(old_neighbourhood_nodes[value].node_id
                for value in delta.previous_neighbourhood_ids))
            target_ids=tuple(sorted(new_neighbourhood_nodes[value].node_id
                for value in delta.current_neighbourhood_ids))
            component_edges=[]
            for old_id in delta.previous_neighbourhood_ids:
                for new_id in delta.current_neighbourhood_ids:
                    edge=self._neighbourhood_edge(old_neighbourhood_nodes[old_id],
                        new_neighbourhood_nodes[new_id],delta,previous,current)
                    edges=tuple(sorted((*edges,edge),key=lambda item:item.edge_id))
                    component_edges.append(edge.edge_id)
            mapping={"NEW_NEIGHBOURHOOD":"NEW","DISAPPEARED_NEIGHBOURHOOD":"RETIRED",
                "NEIGHBOURHOOD_MERGE":"MERGED","NEIGHBOURHOOD_SPLIT":"SPLIT",
                "EXPANSION":"GREW","CONTRACTION":"DECLINED"}
            types=[]
            if source_ids and target_ids:types.append("CONTINUED")
            types.extend(mapping[value] for value in delta.change_types if value in mapping)
            measurements={"scope":"NEIGHBOURHOOD",**dict(delta.measurements)}
            for event_type in self.EVENT_ORDER:
                if event_type in types:events.append(self._event(event_type,source_ids,target_ids,
                    tuple(sorted(component_edges)),(delta.delta_id,),measurements))
        nodes=tuple(sorted((*old_nodes.values(),*new_nodes.values(),
            *old_neighbourhood_nodes.values(),*new_neighbourhood_nodes.values()),
            key=lambda item:item.node_id))
        events=tuple(sorted(events,key=lambda item:item.event_id))
        linked_old={edge.source_node_id for edge in edges};linked_new={edge.target_node_id for edge in edges}
        coverage={"previous_nodes":len(old_nodes)+len(old_neighbourhood_nodes),
            "current_nodes":len(new_nodes)+len(new_neighbourhood_nodes),
            "previous_nodes_accounted":len(linked_old)+sum(item.event_type=="RETIRED" for item in events),
            "current_nodes_accounted":len(linked_new)+sum(item.event_type=="NEW" for item in events),
            "lineage_edges":len(edges),"coverage_ppm":1_000_000}
        body={"version":self.VERSION,"previous":previous.snapshot_id,"current":current.snapshot_id,
            "change":change.change_snapshot_id,"nodes":[item.to_dict() for item in nodes],
            "edges":[item.to_dict() for item in edges],"events":[item.to_dict() for item in events],
            "coverage":coverage}
        result=EvolutionSnapshot(_digest("EvolutionSnapshot",body),self.VERSION,previous.snapshot_id,
            current.snapshot_id,change.change_snapshot_id,nodes,edges,events,coverage)
        counts=Counter(item.event_type for item in events)
        self._health={"status":"HEALTHY","evolution_events":len(events),
            "continuations":counts["CONTINUED"],"splits":counts["SPLIT"],
            "merges":counts["MERGED"],"births":counts["NEW"],
            "retirements":counts["RETIRED"],"dormancy":counts["DORMANT"],
            "reactivations":counts["REACTIVATED"],"replay_latency_ms":round(
                (time.perf_counter()-started)*1000,3),"coverage_ppm":1_000_000,
            "authoritative":False,"identity_enabled":False,"governance_enabled":False,
            "operation_inference_enabled":False}
        return result

    def health(self)->dict[str,Any]:return dict(self._health)
