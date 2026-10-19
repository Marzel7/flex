"""Deterministic two-snapshot Operational Change Intelligence."""

from __future__ import annotations

import hashlib
import time
from collections import Counter,defaultdict,deque
from dataclasses import dataclass
from typing import Any,Mapping,Sequence

from ..contracts import canonical_json_bytes
from .dominant_analysis import DominantMotifAnalysis
from .intelligence import MotifIntelligence
from .motifs import OperationMotif


def _digest(kind:str,value:Any)->str:
    return hashlib.sha256(canonical_json_bytes([kind,value])).hexdigest()


def _ppm(numerator:int,denominator:int)->int|None:
    return round((numerator*1_000_000)/denominator) if denominator else None


@dataclass(frozen=True)
class OperationalLandscapeSnapshot:
    snapshot_id:str
    snapshot_version:str
    observation_boundary:int|None
    motifs:tuple[OperationMotif,...]
    profiles:tuple[MotifIntelligence,...]
    dominant_analysis:DominantMotifAnalysis
    input_digest:str

    @classmethod
    def create(cls,*,observation_boundary:int|None,motifs:Sequence[OperationMotif],
               profiles:Sequence[MotifIntelligence],
               dominant_analysis:DominantMotifAnalysis)->"OperationalLandscapeSnapshot":
        motif_values=tuple(sorted(motifs,key=lambda item:item.motif_id))
        profile_values=tuple(sorted(profiles,key=lambda item:item.motif_id))
        body={"snapshot_version":"1.0.0","observation_boundary":observation_boundary,
            "motifs":[{"motif_id":item.motif_id,
                "occurrence_ids":[value.occurrence_id for value in item.occurrences],
                "supporting_primitives":list(item.supporting_primitive_ids),
                "supporting_evidence":list(item.supporting_evidence_ids),
                "canonical_graph":item.canonical_graph} for item in motif_values],
            "profiles":[{"intelligence_id":item.intelligence_id,"motif_id":item.motif_id,
                         "rank":item.rank} for item in profile_values],
            "dominant_analysis_id":dominant_analysis.analysis_id}
        input_digest=_digest("OperationalLandscapeSnapshotInput",body)
        return cls(_digest("OperationalLandscapeSnapshot",body),"1.0.0",
            observation_boundary,motif_values,profile_values,dominant_analysis,input_digest)

    def identity_payload(self)->dict[str,Any]:
        return {"snapshot_id":self.snapshot_id,"snapshot_version":self.snapshot_version,
            "observation_boundary":self.observation_boundary,"input_digest":self.input_digest,
            "motif_count":len(self.motifs),"profile_count":len(self.profiles),
            "dominant_analysis_id":self.dominant_analysis.analysis_id}


@dataclass(frozen=True)
class MotifDelta:
    delta_id:str
    previous_motif_ids:tuple[str,...]
    current_motif_ids:tuple[str,...]
    change_types:tuple[str,...]
    structural_delta:Mapping[str,Any]
    primitive_delta:Mapping[str,Any]
    relationship_delta:Mapping[str,Any]
    topology_delta:Mapping[str,Any]
    population_delta:Mapping[str,Any]
    temporal_delta:Mapping[str,Any]
    ranking_delta:Mapping[str,Any]

    def to_dict(self)->dict[str,Any]:
        return {"delta_id":self.delta_id,"previous_motif_ids":list(self.previous_motif_ids),
            "current_motif_ids":list(self.current_motif_ids),"change_types":list(self.change_types),
            "structural_delta":dict(self.structural_delta),"primitive_delta":dict(self.primitive_delta),
            "relationship_delta":dict(self.relationship_delta),"topology_delta":dict(self.topology_delta),
            "population_delta":dict(self.population_delta),"temporal_delta":dict(self.temporal_delta),
            "ranking_delta":dict(self.ranking_delta)}


@dataclass(frozen=True)
class NeighbourhoodDelta:
    delta_id:str
    previous_neighbourhood_ids:tuple[str,...]
    current_neighbourhood_ids:tuple[str,...]
    change_types:tuple[str,...]
    measurements:Mapping[str,Any]

    def to_dict(self)->dict[str,Any]:
        return {"delta_id":self.delta_id,
            "previous_neighbourhood_ids":list(self.previous_neighbourhood_ids),
            "current_neighbourhood_ids":list(self.current_neighbourhood_ids),
            "change_types":list(self.change_types),"measurements":dict(self.measurements)}


@dataclass(frozen=True)
class RelationshipDelta:
    delta_id:str
    relationship_id:str
    change_type:str
    relationship:Mapping[str,Any]

    def to_dict(self)->dict[str,Any]:
        return {"delta_id":self.delta_id,"relationship_id":self.relationship_id,
                "change_type":self.change_type,"relationship":dict(self.relationship)}


@dataclass(frozen=True)
class TrendObservation:
    observation_id:str
    motif_delta_id:str
    states:tuple[str,...]
    measurements:Mapping[str,Any]

    def to_dict(self)->dict[str,Any]:
        return {"observation_id":self.observation_id,"motif_delta_id":self.motif_delta_id,
                "states":list(self.states),"measurements":dict(self.measurements)}


@dataclass(frozen=True)
class ChangeSnapshot:
    change_snapshot_id:str
    change_version:str
    previous_snapshot_id:str
    current_snapshot_id:str
    motif_deltas:tuple[MotifDelta,...]
    neighbourhood_deltas:tuple[NeighbourhoodDelta,...]
    relationship_deltas:tuple[RelationshipDelta,...]
    trend_observations:tuple[TrendObservation,...]
    pareto_movement:tuple[Mapping[str,Any],...]
    concentration_delta:Mapping[str,Any]
    coverage:Mapping[str,Any]

    def to_dict(self)->dict[str,Any]:
        return {"change_snapshot_id":self.change_snapshot_id,
            "change_version":self.change_version,
            "previous_snapshot_id":self.previous_snapshot_id,
            "current_snapshot_id":self.current_snapshot_id,
            "motif_deltas":[item.to_dict() for item in self.motif_deltas],
            "neighbourhood_deltas":[item.to_dict() for item in self.neighbourhood_deltas],
            "relationship_deltas":[item.to_dict() for item in self.relationship_deltas],
            "trend_observations":[item.to_dict() for item in self.trend_observations],
            "pareto_movement":list(self.pareto_movement),
            "concentration_delta":dict(self.concentration_delta),"coverage":dict(self.coverage)}


class OperationalChangeEngine:
    VERSION="1.0.0"

    def __init__(self)->None:self._health={"status":"IDLE","authoritative":False,
        "identity_enabled":False,"governance_enabled":False}

    @staticmethod
    def _profile_index(snapshot:OperationalLandscapeSnapshot)->dict[str,MotifIntelligence]:
        return {item.motif_id:item for item in snapshot.profiles}

    @staticmethod
    def _motif_index(snapshot:OperationalLandscapeSnapshot)->dict[str,OperationMotif]:
        return {item.motif_id:item for item in snapshot.motifs}

    @staticmethod
    def _occurrences(motif:OperationMotif)->set[str]:
        return {item.candidate_id for item in motif.occurrences}

    def _motif_components(self,previous:OperationalLandscapeSnapshot,
                          current:OperationalLandscapeSnapshot)->list[tuple[set[str],set[str]]]:
        old=self._motif_index(previous);new=self._motif_index(current)
        old_occ={key:self._occurrences(value) for key,value in old.items()}
        new_occ={key:self._occurrences(value) for key,value in new.items()}
        old_to_new:dict[str,set[str]]=defaultdict(set);new_to_old:dict[str,set[str]]=defaultdict(set)
        for motif_id in set(old)&set(new):
            old_to_new[motif_id].add(motif_id);new_to_old[motif_id].add(motif_id)
        old_by_occurrence:dict[str,set[str]]=defaultdict(set)
        new_by_occurrence:dict[str,set[str]]=defaultdict(set)
        for motif_id,values in old_occ.items():
            for value in values:old_by_occurrence[value].add(motif_id)
        for motif_id,values in new_occ.items():
            for value in values:new_by_occurrence[value].add(motif_id)
        for occurrence_id in set(old_by_occurrence)&set(new_by_occurrence):
            for old_id in old_by_occurrence[occurrence_id]:
                for new_id in new_by_occurrence[occurrence_id]:
                    old_to_new[old_id].add(new_id);new_to_old[new_id].add(old_id)
        components=[];unseen_old=set(old);unseen_new=set(new)
        while unseen_old or unseen_new:
            if unseen_old:old_seed=min(unseen_old);queue=[("old",old_seed)]
            else:new_seed=min(unseen_new);queue=[("new",new_seed)]
            old_members=set();new_members=set()
            while queue:
                side,value=queue.pop()
                if side=="old":
                    if value in old_members:continue
                    old_members.add(value);unseen_old.discard(value)
                    queue.extend(("new",item) for item in old_to_new[value])
                else:
                    if value in new_members:continue
                    new_members.add(value);unseen_new.discard(value)
                    queue.extend(("old",item) for item in new_to_old[value])
            components.append((old_members,new_members))
        return components

    @staticmethod
    def _aggregate(ids:set[str],motifs:Mapping[str,OperationMotif],
                   profiles:Mapping[str,MotifIntelligence])->dict[str,Any]:
        motif_values=[motifs[value] for value in ids];profile_values=[profiles[value] for value in ids]
        graphs=[item.canonical_graph for item in motif_values]
        primitive_counts=Counter()
        for item in profile_values:
            primitive_counts.update(item.measurements["structure"]["primitive_distribution"])
        relationships=Counter()
        for graph in graphs:
            for edge in graph.get("directed_edges",()):
                relationships[edge["role_order"]]+=int(edge["count"])
        occurrences=sum(item.occurrence_count for item in profile_values)
        evidence_complete=sum(item.measurements["completeness"]["evidence_complete_occurrences"]
                              for item in profile_values)
        evidence_total=sum(item.measurements["completeness"]["evidence_total_occurrences"]
                           for item in profile_values)
        primitive_complete=sum(item.measurements["completeness"]["primitive_complete_observations"]
                               for item in profile_values)
        primitive_total=sum(item.measurements["completeness"]["primitive_total_observations"]
                            for item in profile_values)
        ranks=[item.rank for item in profile_values if item.rank is not None]
        first=[item.timeline["first_observed"] for item in profile_values
               if item.timeline.get("first_observed") is not None]
        last=[item.timeline["last_observed"] for item in profile_values
              if item.timeline.get("last_observed") is not None]
        return {"motif_count":len(ids),"occurrences":occurrences,
            "node_count":sum(sum(int(node["multiplicity"]) for node in graph.get("nodes",()))
                             for graph in graphs),
            "edge_count":sum(sum(int(edge["count"]) for edge in graph.get("directed_edges",()))
                             for graph in graphs),
            "primitive_distribution":dict(sorted(primitive_counts.items())),
            "relationship_distribution":dict(sorted(relationships.items())),
            "topology_digests":sorted(_digest("Topology",graph) for graph in graphs),
            "canonical_graphs":{item.motif_id:item.canonical_graph for item in motif_values},
            "supporting_primitives":sorted({value for item in motif_values
                                             for value in item.supporting_primitive_ids}),
            "evidence_completeness_ppm":_ppm(evidence_complete,evidence_total),
            "primitive_completeness_ppm":_ppm(primitive_complete,primitive_total),
            "best_rank":min(ranks,default=None),"first_observed":min(first,default=None),
            "last_observed":max(last,default=None),
            "dormant":all((item.timeline.get("dormancy_duration") or 0)>0
                          for item in profile_values) if profile_values else False,
            "growth_states":dict(sorted(Counter(item.growth["state"]
                                                  for item in profile_values).items())),
            "measured_growth_delta":sum(item.growth.get("absolute_change") or 0
                                         for item in profile_values),
            "burst_count":sum(item.measurements.get("behaviour",{}).get("burst_gap_count",0)
                              for item in profile_values)}

    @staticmethod
    def _map_delta(before:Mapping[str,int],after:Mapping[str,int])->dict[str,int]:
        return {key:after.get(key,0)-before.get(key,0) for key in sorted(set(before)|set(after))
                if after.get(key,0)!=before.get(key,0)}

    def _motif_delta(self,old_ids:set[str],new_ids:set[str],old_motifs,old_profiles,
                     new_motifs,new_profiles,total_old:int,total_new:int,
                     elapsed_seconds:int|None)->tuple[MotifDelta,TrendObservation]:
        before=self._aggregate(old_ids,old_motifs,old_profiles) if old_ids else {}
        after=self._aggregate(new_ids,new_motifs,new_profiles) if new_ids else {}
        changes=[]
        if not old_ids:changes.append("NEW_MOTIF")
        if not new_ids:changes.extend(("DISAPPEARED_MOTIF","RETIRED"))
        if len(old_ids)==1 and len(new_ids)>1:changes.extend(("SPLIT_MOTIF","FRAGMENTING"))
        if len(old_ids)>1 and len(new_ids)==1:changes.extend(("MERGED_MOTIF","MERGING"))
        if len(old_ids)>1 and len(new_ids)>1:changes.extend(("REASSIGNED_MOTIFS","FRAGMENTING","MERGING"))
        occurrence_delta=after.get("occurrences",0)-before.get("occurrences",0)
        if old_ids and new_ids:
            changes.append("GROWING" if occurrence_delta>0 else
                           "DECLINING" if occurrence_delta<0 else "STABLE")
            if before.get("dormant") and not after.get("dormant"):changes.append("REACTIVATED")
            if after.get("dormant"):changes.append("DORMANT")
            if before.get("topology_digests")!=after.get("topology_digests"):
                changes.append("TOPOLOGY_CHANGED")
            if before.get("primitive_distribution")!=after.get("primitive_distribution"):
                changes.append("PRIMITIVE_COMPOSITION_CHANGED")
            if before.get("evidence_completeness_ppm")!=after.get("evidence_completeness_ppm"):
                changes.append("EVIDENCE_COMPLETENESS_CHANGED")
            old_rank=before.get("best_rank");new_rank=after.get("best_rank")
            if new_rank is not None and new_rank<=69 and (old_rank is None or old_rank>69):
                changes.append("BECAME_DOMINANT")
            if old_rank is not None and old_rank<=69 and (new_rank is None or new_rank>69):
                changes.append("BECAME_IRRELEVANT")
        structural={"node_count_before":before.get("node_count",0),
            "node_count_after":after.get("node_count",0),
            "node_count_delta":after.get("node_count",0)-before.get("node_count",0),
            "edge_count_before":before.get("edge_count",0),"edge_count_after":after.get("edge_count",0),
            "edge_count_delta":after.get("edge_count",0)-before.get("edge_count",0)}
        primitive={"before":before.get("primitive_distribution",{}),
            "after":after.get("primitive_distribution",{}),"delta":self._map_delta(
                before.get("primitive_distribution",{}),after.get("primitive_distribution",{}))}
        relationship={"before":before.get("relationship_distribution",{}),
            "after":after.get("relationship_distribution",{}),"delta":self._map_delta(
                before.get("relationship_distribution",{}),after.get("relationship_distribution",{}))}
        topology={"before":before.get("topology_digests",[]),
                  "after":after.get("topology_digests",[]),
                  "changed":before.get("topology_digests",[])!=after.get("topology_digests",[]),
                  "previous_canonical_graphs":before.get("canonical_graphs",{}),
                  "current_canonical_graphs":after.get("canonical_graphs",{})}
        population={"occurrences_before":before.get("occurrences",0),
            "occurrences_after":after.get("occurrences",0),"new_occurrences":max(0,occurrence_delta),
            "lost_occurrences":max(0,-occurrence_delta),"occurrence_delta":occurrence_delta,
            "share_before_ppm":_ppm(before.get("occurrences",0),total_old),
            "share_after_ppm":_ppm(after.get("occurrences",0),total_new)}
        temporal={"first_appearance":after.get("first_observed"),
            "latest_appearance":after.get("last_observed"),
            "growth_velocity_occurrences":max(0,occurrence_delta),
            "decay_velocity_occurrences":max(0,-occurrence_delta),
            "elapsed_seconds":elapsed_seconds,
            "growth_velocity_occurrences_per_day_milli":round(
                (max(0,occurrence_delta)*86400*1000)/elapsed_seconds)
                if elapsed_seconds else None,
            "decay_velocity_occurrences_per_day_milli":round(
                (max(0,-occurrence_delta)*86400*1000)/elapsed_seconds)
                if elapsed_seconds else None,
            "burst_count_before":before.get("burst_count",0),
            "burst_count_after":after.get("burst_count",0),
            "burst_persistent":before.get("burst_count",0)>0 and after.get("burst_count",0)>0,
            "previous_growth_states":before.get("growth_states",{}),
            "current_growth_states":after.get("growth_states",{}),
            "persistent_growth_states":sorted(set(before.get("growth_states",{}))&
                                               set(after.get("growth_states",{}))),
            "growth_state_changed":before.get("growth_states",{})!=after.get("growth_states",{}),
            "acceleration_occurrence_delta":after.get("measured_growth_delta",0)-
                before.get("measured_growth_delta",0),
            "dormant":after.get("dormant",False)}
        ranking={"best_rank_before":before.get("best_rank"),"best_rank_after":after.get("best_rank"),
            "movement":(before["best_rank"]-after["best_rank"] if before.get("best_rank") is not None
                        and after.get("best_rank") is not None else None)}
        body={"previous":sorted(old_ids),"current":sorted(new_ids),"changes":sorted(set(changes)),
            "structural":structural,"primitive":primitive,"relationship":relationship,
            "topology":topology,"population":population,"temporal":temporal,"ranking":ranking}
        delta_id=_digest("MotifDelta",body)
        delta=MotifDelta(delta_id,tuple(sorted(old_ids)),tuple(sorted(new_ids)),
            tuple(body["changes"]),structural,primitive,relationship,topology,population,temporal,ranking)
        trend_states=[value for value in ("STABLE","GROWING","DECLINING","DORMANT",
            "REACTIVATED","FRAGMENTING","MERGING","RETIRED") if value in changes]
        if "GROWING" in changes and temporal["acceleration_occurrence_delta"]>0:
            trend_states.append("EXPLODING")
        trend_body={"delta_id":delta_id,"states":trend_states,"measurements":{
            "occurrence_delta":occurrence_delta,"rank_movement":ranking["movement"],
            "growth_velocity_occurrences":temporal["growth_velocity_occurrences"],
            "decay_velocity_occurrences":temporal["decay_velocity_occurrences"]}}
        trend=TrendObservation(_digest("TrendObservation",trend_body),delta_id,
            tuple(trend_states),trend_body["measurements"])
        return delta,trend

    @staticmethod
    def _relationships(snapshot:OperationalLandscapeSnapshot)->dict[str,Mapping[str,Any]]:
        return {item["relationship_id"]:item for item in snapshot.dominant_analysis.relationships}

    @staticmethod
    def _neighbourhood_measure(snapshot:OperationalLandscapeSnapshot,
                               neighbourhood:Mapping[str,Any])->dict[str,Any]:
        members=set(neighbourhood["motif_ids"]);edges=snapshot.dominant_analysis.relationships
        internal=sum(edge["source_motif_id"] in members and edge["target_motif_id"] in members
                     for edge in edges)
        external=sum((edge["source_motif_id"] in members) ^ (edge["target_motif_id"] in members)
                     for edge in edges)
        possible=len(members)*(len(members)-1)//2
        return {"size":len(members),"relationship_count":internal,
            "internal_density_ppm":_ppm(internal,possible),"external_connectivity":external}

    def _neighbourhood_deltas(self,previous,current)->tuple[NeighbourhoodDelta,...]:
        old={item["neighbourhood_id"]:item for item in previous.dominant_analysis.neighbourhoods}
        new={item["neighbourhood_id"]:item for item in current.dominant_analysis.neighbourhoods}
        old_members={key:set(value["motif_ids"]) for key,value in old.items()}
        new_members={key:set(value["motif_ids"]) for key,value in new.items()}
        old_to_new=defaultdict(set);new_to_old=defaultdict(set)
        for old_id,left in old_members.items():
            for new_id,right in new_members.items():
                if left&right:old_to_new[old_id].add(new_id);new_to_old[new_id].add(old_id)
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
        deltas=[]
        for old_ids,new_ids in components:
            before=[self._neighbourhood_measure(previous,old[value]) for value in old_ids]
            after=[self._neighbourhood_measure(current,new[value]) for value in new_ids]
            before_size=sum(value["size"] for value in before);after_size=sum(value["size"] for value in after)
            changes=[]
            if not old_ids:changes.append("NEW_NEIGHBOURHOOD")
            if not new_ids:changes.append("DISAPPEARED_NEIGHBOURHOOD")
            if len(old_ids)>1 and len(new_ids)==1:changes.append("NEIGHBOURHOOD_MERGE")
            if len(old_ids)==1 and len(new_ids)>1:changes.append("NEIGHBOURHOOD_SPLIT")
            if after_size>before_size and old_ids:changes.append("EXPANSION")
            if after_size<before_size and new_ids:changes.append("CONTRACTION")
            if after_size==1:changes.append("ISOLATION")
            if not changes:changes.append("STABLE")
            measurements={"size_before":before_size,"size_after":after_size,
                "size_delta":after_size-before_size,
                "relationship_count_before":sum(value["relationship_count"] for value in before),
                "relationship_count_after":sum(value["relationship_count"] for value in after),
                "external_connectivity_before":sum(value["external_connectivity"] for value in before),
                "external_connectivity_after":sum(value["external_connectivity"] for value in after),
                "density_before_ppm":round(sum(value["internal_density_ppm"] or 0 for value in before)/
                    len(before)) if before else None,"density_after_ppm":round(sum(
                    value["internal_density_ppm"] or 0 for value in after)/len(after)) if after else None}
            body={"old":sorted(old_ids),"new":sorted(new_ids),"changes":changes,
                  "measurements":measurements}
            deltas.append(NeighbourhoodDelta(_digest("NeighbourhoodDelta",body),
                tuple(sorted(old_ids)),tuple(sorted(new_ids)),tuple(changes),measurements))
        return tuple(sorted(deltas,key=lambda item:item.delta_id))

    @staticmethod
    def _pareto(previous,current)->tuple[dict[str,Any],...]:
        old={item["top"]:item for item in previous.dominant_analysis.pareto}
        new={item["top"]:item for item in current.dominant_analysis.pareto}
        return tuple({"top":limit,"occurrences_before":old.get(limit,{}).get("occurrences",0),
            "occurrences_after":new.get(limit,{}).get("occurrences",0),
            "occurrence_delta":new.get(limit,{}).get("occurrences",0)-old.get(limit,{}).get(
                "occurrences",0),"share_before_ppm":old.get(limit,{}).get("occurrence_share_ppm"),
            "share_after_ppm":new.get(limit,{}).get("occurrence_share_ppm"),
            "share_movement_ppm":(new.get(limit,{}).get("occurrence_share_ppm",0)-
                                  old.get(limit,{}).get("occurrence_share_ppm",0))}
            for limit in sorted(set(old)|set(new)))

    def compare(self,previous:OperationalLandscapeSnapshot,
                current:OperationalLandscapeSnapshot)->ChangeSnapshot:
        started=time.perf_counter();old_motifs=self._motif_index(previous);new_motifs=self._motif_index(current)
        old_profiles=self._profile_index(previous);new_profiles=self._profile_index(current)
        total_old=sum(item.occurrence_count for item in previous.profiles)
        total_new=sum(item.occurrence_count for item in current.profiles)
        motif_deltas=[];trends=[]
        for old_ids,new_ids in self._motif_components(previous,current):
            delta,trend=self._motif_delta(old_ids,new_ids,old_motifs,old_profiles,new_motifs,
                new_profiles,total_old,total_new,
                (current.observation_boundary-previous.observation_boundary)
                if current.observation_boundary is not None and
                   previous.observation_boundary is not None and
                   current.observation_boundary>previous.observation_boundary else None)
            motif_deltas.append(delta);trends.append(trend)
        motif_deltas=tuple(sorted(motif_deltas,key=lambda item:item.delta_id))
        trends=tuple(sorted(trends,key=lambda item:item.observation_id))
        old_rel=self._relationships(previous);new_rel=self._relationships(current)
        relationship_deltas=[]
        for relationship_id in sorted(set(old_rel)^set(new_rel)):
            change_type="RELATIONSHIP_CREATED" if relationship_id in new_rel else "RELATIONSHIP_REMOVED"
            relationship=new_rel.get(relationship_id,old_rel.get(relationship_id))
            body=[relationship_id,change_type,relationship]
            relationship_deltas.append(RelationshipDelta(_digest("RelationshipDelta",body),
                relationship_id,change_type,relationship))
        neighbourhood_deltas=self._neighbourhood_deltas(previous,current)
        pareto=self._pareto(previous,current)
        concentration={"total_occurrences_before":total_old,"total_occurrences_after":total_new,
            "total_occurrence_delta":total_new-total_old,
            "dominant_share_before_ppm":previous.dominant_analysis.occurrence_threshold_ppm,
            "dominant_share_after_ppm":current.dominant_analysis.occurrence_threshold_ppm,
            "dominant_share_movement_ppm":current.dominant_analysis.occurrence_threshold_ppm-
                previous.dominant_analysis.occurrence_threshold_ppm}
        ranked_movers=sorted((item for item in motif_deltas
            if item.ranking_delta.get("movement") is not None),key=lambda item:(
                -abs(item.ranking_delta["movement"]),item.delta_id))
        concentration["largest_rank_movers"]=[{"delta_id":item.delta_id,
            "movement":item.ranking_delta["movement"]} for item in ranked_movers[:10]]
        concentration["stable_leaders"]=[item.delta_id for item in motif_deltas
            if item.ranking_delta.get("best_rank_after") is not None
            and item.ranking_delta["best_rank_after"]<=10
            and item.ranking_delta.get("movement")==0]
        coverage={"previous_motifs":len(previous.motifs),"current_motifs":len(current.motifs),
            "motif_components":len(motif_deltas),"previous_neighbourhoods":len(
                previous.dominant_analysis.neighbourhoods),"current_neighbourhoods":len(
                current.dominant_analysis.neighbourhoods)}
        body={"version":self.VERSION,"previous":previous.snapshot_id,"current":current.snapshot_id,
            "motif_deltas":[item.to_dict() for item in motif_deltas],
            "neighbourhood_deltas":[item.to_dict() for item in neighbourhood_deltas],
            "relationship_deltas":[item.to_dict() for item in relationship_deltas],
            "trends":[item.to_dict() for item in trends],"pareto":pareto,
            "concentration":concentration,"coverage":coverage}
        change_id=_digest("ChangeSnapshot",body)
        result=ChangeSnapshot(change_id,self.VERSION,previous.snapshot_id,current.snapshot_id,
            motif_deltas,neighbourhood_deltas,tuple(relationship_deltas),trends,pareto,
            concentration,coverage)
        self._health={"status":"HEALTHY","snapshots_compared":1,
            "changes_detected":sum(len(item.change_types) for item in motif_deltas)+
                len(relationship_deltas)+sum(len(item.change_types) for item in neighbourhood_deltas),
            "motifs_changed":sum(item.change_types!=("STABLE",) for item in motif_deltas),
            "neighbourhoods_changed":sum(item.change_types!=("STABLE",)
                                           for item in neighbourhood_deltas),
            "replay_latency_ms":round((time.perf_counter()-started)*1000,3),
            "trend_generation":len(trends),"ranking_movement":sum(
                item.ranking_delta.get("movement") not in (None,0) for item in motif_deltas),
            "coverage_ppm":1_000_000,"authoritative":False,"identity_enabled":False,
            "governance_enabled":False}
        return result

    def health(self)->dict[str,Any]:return dict(self._health)
