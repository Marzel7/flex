"""Objective intelligence and relationship analysis for dominant motifs."""

from __future__ import annotations

import hashlib
import math
from collections import Counter,defaultdict,deque
from dataclasses import dataclass
from statistics import median
from typing import Any,Mapping,Sequence

from ..contracts import canonical_json_bytes
from ..primitives.contracts import PrimitiveObservation
from .intelligence import MotifIntelligence
from .motifs import OperationMotif


def _ppm(numerator:int,denominator:int)->int|None:
    return round((numerator*1_000_000)/denominator) if denominator else None


def _digest(kind:str,value:Any)->str:
    return hashlib.sha256(canonical_json_bytes([kind,value])).hexdigest()


@dataclass(frozen=True)
class DominantMotifAnalysis:
    analysis_id:str
    analysis_version:str
    dominant_count:int
    occurrence_threshold_ppm:int
    dominant_occurrences:int
    total_occurrences:int
    profiles:tuple[Mapping[str,Any],...]
    relationships:tuple[Mapping[str,Any],...]
    neighbourhoods:tuple[Mapping[str,Any],...]
    pareto:tuple[Mapping[str,Any],...]
    replay:Mapping[str,Any]

    def to_dict(self)->dict[str,Any]:
        return {"analysis_id":self.analysis_id,"analysis_version":self.analysis_version,
            "dominant_count":self.dominant_count,
            "occurrence_threshold_ppm":self.occurrence_threshold_ppm,
            "dominant_occurrences":self.dominant_occurrences,
            "total_occurrences":self.total_occurrences,"profiles":list(self.profiles),
            "relationship_graph":{"nodes":[item["motif_id"] for item in self.profiles],
                                  "edges":list(self.relationships)},
            "neighbourhoods":list(self.neighbourhoods),"pareto":list(self.pareto),
            "replay":dict(self.replay)}


class DominantMotifIntelligenceEngine:
    VERSION="1.0.0"
    PARETO_LIMITS=(10,25,50,69,100,250)

    def __init__(self,*,dominant_count:int=69)->None:
        if dominant_count<1:raise ValueError("dominant_count must be positive")
        self.dominant_count=dominant_count

    @staticmethod
    def _graph_measurements(motif:OperationMotif)->dict[str,Any]:
        graph=motif.canonical_graph;nodes=graph.get("nodes",());edges=graph.get("directed_edges",())
        node_count=sum(int(item["multiplicity"]) for item in nodes)
        edge_count=sum(int(item["count"]) for item in edges)
        adjacency:dict[int,set[int]]=defaultdict(set);outbound=Counter()
        for edge in edges:
            source=int(edge["source_class"]);target=int(edge["target_class"])
            adjacency[source].add(target);outbound[source]+=int(edge["count"])
        depth=0
        for start in range(len(nodes)):
            distances={start:0};queue=deque([start])
            while queue:
                current=queue.popleft()
                for target in sorted(adjacency[current]):
                    if target not in distances:
                        distances[target]=distances[current]+1;queue.append(target)
            depth=max(depth,max(distances.values(),default=0))
        symmetric=sum(int(item["multiplicity"]) for item in nodes
                      if int(item["multiplicity"])>1)
        relation_distribution=Counter()
        for edge in edges:relation_distribution[edge["role_order"]]+=int(edge["count"])
        return {"average_node_count_milli":node_count*1000,
            "average_edge_count_milli":edge_count*1000,
            "topology_depth_max_directed_shortest_path":depth,
            "branching_factor_milli":round((sum(outbound.values())*1000)/len(outbound))
                if outbound else 0,"structural_symmetry_ppm":_ppm(symmetric,node_count),
            "canonical_graph":graph,"relationship_distribution":dict(sorted(
                relation_distribution.items()))}

    @staticmethod
    def _times(motif:OperationMotif)->list[int]:
        return sorted(item.time_end if item.time_end is not None else item.time_start
            for item in motif.occurrences if item.time_end is not None or item.time_start is not None)

    @classmethod
    def _temporal(cls,motif:OperationMotif,profile:MotifIntelligence)->dict[str,Any]:
        times=cls._times(motif);gaps=[right-left for left,right in zip(times,times[1:])]
        median_gap=round(median(gaps)) if gaps else None
        dormancy_periods=sum(value>2*median_gap for value in gaps) if median_gap else 0
        thirds=[0,0,0]
        if len(times)>1 and times[-1]>times[0]:
            duration=times[-1]-times[0]
            for value in times:
                thirds[min(2,((value-times[0])*3)//(duration+1))]+=1
        elif times:thirds[0]=len(times)
        first_delta=thirds[1]-thirds[0];second_delta=thirds[2]-thirds[1]
        acceleration=second_delta-first_delta if len(times)>1 else None
        seasonality={"state":"NOT_MEASURABLE","basis":"REQUIRES_14_DAYS_AND_10_OCCURRENCES"}
        if len(times)>=10 and times[-1]-times[0]>=14*86400:
            weekdays=Counter((value//86400)%7 for value in times)
            seasonality={"state":"MEASURED","weekday_distribution":{
                str(index):weekdays[index] for index in range(7)}}
        return {"first_observed":profile.timeline.get("first_observed"),
            "last_observed":profile.timeline.get("last_observed"),
            "active_duration":profile.timeline.get("active_duration"),
            "dormancy":profile.timeline.get("dormancy_duration"),
            "dormancy_period_count":dormancy_periods,"median_gap":median_gap,
            "growth":profile.growth,"acceleration_occurrence_delta":acceleration,
            "decline_measured":profile.growth["state"]=="COLLAPSING",
            "recurrence_frequency_per_active_day_milli":profile.growth.get(
                "recurrence_frequency_per_active_day_milli"),"seasonality":seasonality}

    @staticmethod
    def _infrastructure(motif:OperationMotif,
                        primitives:Mapping[str,PrimitiveObservation])->set[str]:
        values=set()
        for primitive_id in motif.supporting_primitive_ids:
            item=primitives[primitive_id]
            if item.primitive_type=="REPEATED_COUNTERPARTY":values.update(item.subjects)
        return values

    @staticmethod
    def _topology_fingerprint(motif:OperationMotif)->str:
        graph=motif.canonical_graph
        body={"nodes":[(item["class"],item["multiplicity"],item["role_counts"])
                       for item in graph.get("nodes",())],
            "relations":[(item["source_class"],item["target_class"],
                item["primitive_type"],item["primitive_version"],item["role_order"])
                for item in graph.get("directed_edges",())]}
        return _digest("TopologyFingerprint",body)

    @staticmethod
    def _behaviour_fingerprint(profile:MotifIntelligence)->str|None:
        behaviour=profile.measurements["behaviour"]
        values=(behaviour.get("launch_cadence_per_active_day_milli"),
            behaviour.get("creator_reuse_ppm"),behaviour.get("funding_reuse_ppm"),
            behaviour.get("infrastructure_reuse_ppm"),
            behaviour.get("primitive_sequence_stability_ppm"))
        # Equality is meaningful only for a complete behaviour vector. Partial
        # vectors with shared unknowns must not create relationship edges.
        return _digest("BehaviourFingerprint",values) if all(
            value is not None for value in values) else None

    @staticmethod
    def _stability(profile:MotifIntelligence,median_lifetime:int)->list[str]:
        values=[];duration=profile.timeline.get("active_duration")
        if duration is not None:values.append("PERSISTENT" if duration>=median_lifetime else "TRANSIENT")
        acceleration=profile.growth.get("absolute_change")
        if profile.growth["state"]=="GROWING" and (acceleration or 0)>0:values.append("EXPLODING")
        if profile.growth["state"]=="COLLAPSING":values.append("DECLINING")
        if (profile.timeline.get("dormancy_duration") or 0)>0:values.append("DORMANT")
        if profile.measurements["structure"]["topology_diversity"]>1:values.append("FRAGMENTING")
        return values

    def analyze(self,motifs:Sequence[OperationMotif],profiles:Sequence[MotifIntelligence],
                primitives:Sequence[PrimitiveObservation],*,replay_analysis_id:str|None=None
                )->DominantMotifAnalysis:
        motif_index={item.motif_id:item for item in motifs};primitive_index={
            item.primitive_id:item for item in primitives}
        ordered=sorted(profiles,key=lambda item:(-item.occurrence_count,item.motif_id))
        dominant=ordered[:self.dominant_count];total=sum(item.occurrence_count for item in ordered)
        dominant_occurrences=sum(item.occurrence_count for item in dominant)
        lifetimes=[item.timeline["active_duration"] for item in dominant
                   if item.timeline.get("active_duration") is not None]
        median_lifetime=round(median(lifetimes)) if lifetimes else 0
        rows=[];infra={};topology={};behaviour={};primitive_refs={};evidence_refs={}
        for profile in dominant:
            motif=motif_index[profile.motif_id];structural=self._graph_measurements(motif)
            distinct=profile.measurements["distinct"];complete=profile.measurements["completeness"]
            infra[profile.motif_id]=self._infrastructure(motif,primitive_index)
            primitive_refs[profile.motif_id]=set(motif.supporting_primitive_ids)
            evidence_refs[profile.motif_id]=set(motif.supporting_evidence_ids)
            topology[profile.motif_id]=self._topology_fingerprint(motif)
            behaviour[profile.motif_id]=self._behaviour_fingerprint(profile)
            counterpart_reuse=(max(0,1_000_000-_ppm(distinct["counterparties"],
                profile.occurrence_count)) if distinct["counterparties"] else None)
            rows.append({"motif_id":profile.motif_id,"rank":profile.rank,
                "structure":structural,"behaviour":{**profile.measurements["behaviour"],
                    "counterparty_reuse_ppm":counterpart_reuse,
                    "behaviour_stability":profile.stability["observation_window"]},
                "temporal":self._temporal(motif,profile),
                "population":{"occurrences":profile.occurrence_count,**distinct,
                    "primitive_diversity":profile.measurements["structure"]["primitive_diversity"],
                    "evidence_completeness_ppm":complete["evidence_completeness_ppm"],
                    "primitive_completeness_ppm":complete["primitive_completeness_ppm"],
                    "observation_density_per_active_day_milli":profile.growth.get(
                        "recurrence_frequency_per_active_day_milli")},
                "stability":self._stability(profile,median_lifetime),
                "merging_state":"NOT_MEASURED_WITHOUT_PRIOR_MOTIF_ASSIGNMENT",
                "relationship_count":0,"replay_status":profile.stability["replay"]})
        relationships=[];adjacency:dict[str,set[str]]=defaultdict(set)
        for index,left in enumerate(dominant):
            left_motif=motif_index[left.motif_id]
            for right in dominant[index+1:]:
                right_motif=motif_index[right.motif_id]
                shared_primitives=sorted(primitive_refs[left.motif_id]&
                                         primitive_refs[right.motif_id])
                shared_evidence=sorted(evidence_refs[left.motif_id]&
                                       evidence_refs[right.motif_id])
                shared_infra=sorted(infra[left.motif_id]&infra[right.motif_id])
                topology_equal=topology[left.motif_id]==topology[right.motif_id]
                behaviour_equal=(behaviour[left.motif_id] is not None and
                                 behaviour[left.motif_id]==behaviour[right.motif_id])
                if not (shared_primitives or shared_evidence or shared_infra or topology_equal or
                        behaviour_equal):continue
                left_start,left_end=(left.timeline.get("first_observed"),
                                     left.timeline.get("last_observed"))
                right_start,right_end=(right.timeline.get("first_observed"),
                                       right.timeline.get("last_observed"))
                overlap=(None not in (left_start,left_end,right_start,right_end) and
                    max(left_start,right_start)<=min(left_end,right_end))
                edge={"source_motif_id":left.motif_id,"target_motif_id":right.motif_id,
                    "shared_primitive_ids":shared_primitives,"shared_evidence_ids":shared_evidence,
                    "shared_infrastructure_subjects":shared_infra,
                    "exact_topology_fingerprint":topology_equal,
                    "exact_behaviour_fingerprint":behaviour_equal,"observation_windows_overlap":overlap}
                edge["relationship_id"]=_digest("DominantMotifRelationship",edge)
                relationships.append(edge);adjacency[left.motif_id].add(right.motif_id)
                adjacency[right.motif_id].add(left.motif_id)
        relationship_counts=Counter(value for edge in relationships for value in
            (edge["source_motif_id"],edge["target_motif_id"]))
        for row in rows:row["relationship_count"]=relationship_counts[row["motif_id"]]
        neighbourhoods=[];unseen={item.motif_id for item in dominant}
        while unseen:
            seed=min(unseen);queue=[seed];members=[];unseen.remove(seed)
            while queue:
                current=queue.pop();members.append(current)
                for neighbour in sorted(adjacency[current]&unseen):
                    unseen.remove(neighbour);queue.append(neighbour)
            members=sorted(members);neighbourhoods.append({"neighbourhood_id":
                _digest("MotifNeighbourhood",members),"motif_ids":members,"motif_count":len(members)})
        pareto=[]
        for limit in self.PARETO_LIMITS:
            selected=ordered[:limit];occurrences=sum(item.occurrence_count for item in selected)
            prior_limit=self.PARETO_LIMITS[self.PARETO_LIMITS.index(limit)-1] if limit!=10 else 0
            prior=sum(item.occurrence_count for item in ordered[:prior_limit])
            pareto.append({"top":limit,"motifs_included":len(selected),"occurrences":occurrences,
                "occurrence_share_ppm":_ppm(occurrences,total),
                "marginal_occurrences":occurrences-prior,
                "marginal_share_ppm":_ppm(occurrences-prior,total)})
        rows=tuple(sorted(rows,key=lambda item:item["motif_id"]));relationships=tuple(sorted(
            relationships,key=lambda item:item["relationship_id"]));neighbourhoods=tuple(sorted(
            neighbourhoods,key=lambda item:item["neighbourhood_id"]));pareto=tuple(pareto)
        body={"version":self.VERSION,"dominant_count":len(dominant),
            "threshold_ppm":_ppm(dominant_occurrences,total),"dominant_occurrences":
            dominant_occurrences,"total_occurrences":total,"profiles":rows,
            "relationships":relationships,"neighbourhoods":neighbourhoods,"pareto":pareto}
        analysis_id=_digest("DominantMotifAnalysis",body)
        replay={"state":"MATCH" if replay_analysis_id==analysis_id else
            "NOT_MEASURED" if replay_analysis_id is None else "DIFF",
            "analysis_id":analysis_id,"compared_analysis_id":replay_analysis_id}
        return DominantMotifAnalysis(analysis_id,self.VERSION,len(dominant),
            body["threshold_ppm"],dominant_occurrences,total,rows,relationships,
            neighbourhoods,pareto,replay)
