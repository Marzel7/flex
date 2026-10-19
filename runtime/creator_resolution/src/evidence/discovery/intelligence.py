"""Objective, deterministic intelligence profiles for canonical motifs."""

from __future__ import annotations

import hashlib
import math
import time
from collections import Counter
from dataclasses import dataclass, replace
from statistics import mean, median
from typing import Any, Mapping, Sequence

from ..contracts import canonical_json_bytes
from ..operation_contracts.input_windows import plain
from ..primitives.contracts import PrimitiveObservation
from .contracts import DiscoveryCandidate
from .motifs import OperationMotif


def _digest(kind: str, value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes([kind, value])).hexdigest()


def _ratio_ppm(numerator: int, denominator: int) -> int | None:
    return round((numerator * 1_000_000) / denominator) if denominator else None


@dataclass(frozen=True)
class MotifIntelligence:
    intelligence_id: str
    motif_id: str
    intelligence_version: str
    replay_version: str
    occurrence_count: int
    observed_population: tuple[str, ...]
    measurements: Mapping[str, Any]
    timeline: Mapping[str, Any]
    growth: Mapping[str, Any]
    stability: Mapping[str, Any]
    supporting_evidence_ids: tuple[str, ...]
    supporting_primitive_ids: tuple[str, ...]
    input_digest: str
    rank: int | None = None

    @classmethod
    def create(cls, *, motif: OperationMotif, intelligence_version: str,
               replay_version: str, measurements: Mapping[str, Any],
               timeline: Mapping[str, Any], growth: Mapping[str, Any],
               stability: Mapping[str, Any]) -> "MotifIntelligence":
        population = tuple(sorted({value for group in motif.observed_populations for value in group}))
        body = {
            "motif_id": motif.motif_id, "intelligence_version": intelligence_version,
            "replay_version": replay_version, "occurrence_count": len(motif.occurrences),
            "observed_population": list(population), "measurements": plain(measurements),
            "timeline": plain(timeline), "growth": plain(growth),
            "stability": plain(stability),
            "supporting_evidence_ids": list(motif.supporting_evidence_ids),
            "supporting_primitive_ids": list(motif.supporting_primitive_ids),
        }
        input_digest = _digest("MotifIntelligenceInput", body)
        return cls(
            _digest("MotifIntelligence", body), motif.motif_id, intelligence_version,
            replay_version, len(motif.occurrences), population, body["measurements"],
            body["timeline"], body["growth"], body["stability"],
            motif.supporting_evidence_ids, motif.supporting_primitive_ids, input_digest,
        )

    def with_rank(self, rank: int) -> "MotifIntelligence":
        return replace(self, rank=int(rank))

    def to_dict(self) -> dict[str, Any]:
        return {
            "intelligence_id": self.intelligence_id, "motif_id": self.motif_id,
            "intelligence_version": self.intelligence_version,
            "replay_version": self.replay_version,
            "ranking":{"rank":self.rank,"ordered_by":[
                "occurrences_desc","growth_delta_desc","evidence_completeness_desc",
                "primitive_completeness_desc","graph_complexity_desc","motif_id_asc"]},
            "occurrences": self.occurrence_count,
            "observed_population": list(self.observed_population),
            "measurements": dict(self.measurements), "timeline": dict(self.timeline),
            "growth": dict(self.growth), "stability": dict(self.stability),
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "supporting_primitive_ids": list(self.supporting_primitive_ids),
            "input_digest": self.input_digest,
        }


class MotifIntelligenceEngine:
    VERSION = "1.0.0"
    REPLAY_VERSION = "1"
    _COMPLETE_QUALITIES = {"PROVEN", "DISPROVEN"}

    def __init__(self) -> None:
        self._health: dict[str, Any] = {}

    @staticmethod
    def _addresses(primitives: Sequence[PrimitiveObservation], keys: Sequence[str]) -> set[str]:
        values = set()
        for item in primitives:
            for key in keys:
                value = item.output_payload.get(key)
                if isinstance(value, str): values.add(value)
        return values

    @staticmethod
    def _distribution(values: Sequence[int]) -> dict[str, Any]:
        if not values:
            return {"count":0,"minimum":None,"maximum":None,
                    "mean_milli":None,"median_milli":None}
        ordered = sorted(values)
        return {"count":len(ordered),"minimum":ordered[0],"maximum":ordered[-1],
                "mean_milli":round(mean(ordered)*1000),
                "median_milli":round(median(ordered)*1000)}

    @staticmethod
    def _occurrence_times(motif: OperationMotif) -> list[int]:
        return sorted(item.time_end if item.time_end is not None else item.time_start
                      for item in motif.occurrences
                      if item.time_end is not None or item.time_start is not None)

    def _timeline(self, motif: OperationMotif,
                  reference_time: int | None) -> tuple[dict[str, Any],dict[str, Any]]:
        times=self._occurrence_times(motif)
        if not times:
            timeline={"first_observed":None,"last_observed":None,"active_duration":None,
                      "dormancy_duration":None,"spacing":self._distribution([])}
            growth={"state":"NOT_COMPARABLE","basis":"NO_OBSERVATION_TIMES",
                    "earlier_occurrences":None,"later_occurrences":None,
                    "absolute_change":None,"growth_rate_per_active_day_milli":None,
                    "recurrence_frequency_per_active_day_milli":None}
            return timeline,growth
        first,last=times[0],times[-1]; duration=max(0,last-first)
        spacing=[right-left for left,right in zip(times,times[1:])]
        timeline={"first_observed":first,"last_observed":last,"active_duration":duration,
                  "dormancy_duration":max(0,reference_time-last)
                    if reference_time is not None else None,
                  "reference_time":reference_time,"spacing":self._distribution(spacing)}
        if len(times)<2 or duration==0:
            growth={"state":"NOT_COMPARABLE","basis":"INSUFFICIENT_TEMPORAL_SPAN",
                    "earlier_occurrences":None,"later_occurrences":None,
                    "absolute_change":None,"growth_rate_per_active_day_milli":None,
                    "recurrence_frequency_per_active_day_milli":None}
            return timeline,growth
        midpoint=first+(duration/2)
        earlier=sum(value<=midpoint for value in times); later=len(times)-earlier
        change=later-earlier
        state="GROWING" if change>0 else "COLLAPSING" if change<0 else "STABLE"
        days=duration/86400
        growth={"state":state,"basis":"HALF_WINDOW_OCCURRENCE_DELTA",
                "earlier_occurrences":earlier,"later_occurrences":later,
                "absolute_change":change,
                "growth_rate_per_active_day_milli":round((change*1000)/days) if days else None,
                "recurrence_frequency_per_active_day_milli":round((len(times)*1000)/days)
                    if days else None}
        return timeline,growth

    def _measure(self, motif: OperationMotif,
                 candidates: Mapping[str,DiscoveryCandidate],
                 primitives: Mapping[str,PrimitiveObservation]) -> dict[str,Any]:
        candidate_values=[candidates[value] for value in motif.supporting_candidate_ids]
        primitive_values=[primitives[value] for value in motif.supporting_primitive_ids]
        graph=motif.canonical_graph
        graph_nodes=sum(int(item["multiplicity"]) for item in graph.get("nodes",()))
        graph_edges=sum(int(item["count"]) for item in graph.get("directed_edges",()))
        primitive_distribution=Counter(item.primitive_type for item in primitive_values)
        relationship_distribution=Counter(
            item.get("role_order","OBSERVATION") for item in graph.get("directed_edges",())
            for _ in range(int(item.get("count",1)))
        )
        quality_distribution=Counter(item.quality_state for item in primitive_values)
        complete_primitives=sum(item.quality_state in self._COMPLETE_QUALITIES
                                and not item.missing_inputs for item in primitive_values)
        complete_evidence_occurrences=sum(not item.missing_evidence
            and not item.contradictory_evidence for item in candidate_values)
        launches=self._addresses([item for item in primitive_values if item.primitive_type in
            {"LAUNCH_SIGNER","LAUNCH_ACTIVATION"}],("mint",))
        creators=self._addresses([item for item in primitive_values if item.primitive_type==
            "LAUNCH_ACTIVATION"],("creator",)) | self._addresses(
            [item for item in primitive_values if item.primitive_type=="LAUNCH_SIGNER"],("wallet",))
        controllers=self._addresses([item for item in primitive_values if item.primitive_type==
            "LAUNCH_ACTIVATION"],("activation_sender",))
        funding=self._addresses([item for item in primitive_values if item.primitive_type in
            {"ECONOMIC_FUNDING","LAUNCH_ACTIVATION"}],("funder","activation_sender"))
        counterparties=self._addresses([item for item in primitive_values if item.primitive_type in
            {"SYSTEM_TRANSFER","DIRECT_COUNTERPARTY","REPEATED_COUNTERPARTY"}],
            ("source","destination"))
        infrastructure=set()
        for item in primitive_values:
            if item.primitive_type in {"SHARED_TRANSACTION","PROGRAM_INTERACTION",
                                       "REPEATED_COUNTERPARTY"}:
                infrastructure.update(item.subjects)
        topology_digests={_digest("CandidateTopology",item.observed_recurring_pattern)
                          for item in candidate_values}
        sequences=Counter(tuple(
            (entry.get("temporal_rank"),tuple(sorted(entry.get("primitive_types",{}).items())))
            for entry in graph.get("primitive_sequence",())
        ) for _ in motif.occurrences)
        occurrence_count=len(motif.occurrences)
        launch_times=sorted(item.observation_window.end
            if item.observation_window.end is not None else item.observation_window.start
            for item in primitive_values
            if item.primitive_type in {"LAUNCH_SIGNER","LAUNCH_ACTIVATION"}
            and (item.observation_window.end is not None
                 or item.observation_window.start is not None))
        launch_spacing=[right-left for left,right in zip(launch_times,launch_times[1:])]
        launch_duration=(launch_times[-1]-launch_times[0]) if len(launch_times)>1 else 0
        active_days=launch_duration/86400 if launch_duration else 0
        reuse=lambda distinct,total: max(0,1_000_000-_ratio_ppm(distinct,total)) \
            if total else None
        return {
            "distinct":{"launches":len(launches),"creators":len(creators),
                "controller_role_subjects":len(controllers),"funding_role_wallets":len(funding),
                "counterparties":len(counterparties),"infrastructure":len(infrastructure)},
            "structure":{"graph_nodes":graph_nodes,"graph_edges":graph_edges,
                "average_node_count_milli":graph_nodes*1000,
                "average_edge_count_milli":graph_edges*1000,
                "topological_complexity":graph_nodes+graph_edges,
                "topology_diversity":len(topology_digests),
                "primitive_diversity":len(primitive_distribution),
                "primitive_distribution":dict(sorted(primitive_distribution.items())),
                "relationship_distribution":dict(sorted(relationship_distribution.items()))},
            "completeness":{"evidence_complete_occurrences":complete_evidence_occurrences,
                "evidence_total_occurrences":occurrence_count,
                "evidence_completeness_ppm":_ratio_ppm(
                    complete_evidence_occurrences,occurrence_count),
                "primitive_complete_observations":complete_primitives,
                "primitive_total_observations":len(primitive_values),
                "primitive_completeness_ppm":_ratio_ppm(
                    complete_primitives,len(primitive_values)),
                "primitive_quality_distribution":dict(sorted(quality_distribution.items()))},
            "behaviour":{"launch_cadence_per_active_day_milli":
                           round((len(launch_times)*1000)/active_days) if active_days else None,
                "burst_gap_threshold_seconds":3600,
                "burst_gap_count":sum(value<=3600 for value in launch_spacing),
                "launch_spacing":self._distribution(launch_spacing),
                "creator_reuse_ppm":reuse(len(creators),len(launches)) if launches else None,
                "infrastructure_reuse_ppm":reuse(len(infrastructure),occurrence_count)
                    if infrastructure else None,
                "funding_reuse_ppm":reuse(len(funding),occurrence_count) if funding else None,
                "wallet_churn_subjects_per_occurrence_milli":round(
                    (len(set().union(*(set(item.subjects) for item in primitive_values)))*1000)
                    /occurrence_count) if occurrence_count else None,
                "primitive_sequence_stability_ppm":_ratio_ppm(
                    max(sequences.values(),default=0),occurrence_count)},
        }

    @staticmethod
    def _ranking_key(item: MotifIntelligence) -> tuple[Any,...]:
        completeness=item.measurements["completeness"]
        structure=item.measurements["structure"]
        growth=item.growth.get("absolute_change")
        return (-item.occurrence_count,-(growth if growth is not None else -math.inf),
                -(completeness["evidence_completeness_ppm"] or 0),
                -(completeness["primitive_completeness_ppm"] or 0),
                -structure["topological_complexity"],item.motif_id)

    def generate(self, motifs: Sequence[OperationMotif],
                 candidates: Sequence[DiscoveryCandidate],
                 primitives: Sequence[PrimitiveObservation],*,
                 reference_time: int|None=None) -> tuple[MotifIntelligence,...]:
        started=time.perf_counter(); candidate_index={item.candidate_id:item for item in candidates}
        primitive_index={item.primitive_id:item for item in primitives}
        if reference_time is None:
            observed_ends=[item.time_end for item in motifs if item.time_end is not None]
            reference_time=max(observed_ends) if observed_ends else None
        values=[]
        for motif in sorted(motifs,key=lambda item:item.motif_id):
            missing_candidates=set(motif.supporting_candidate_ids)-candidate_index.keys()
            missing_primitives=set(motif.supporting_primitive_ids)-primitive_index.keys()
            if missing_candidates: raise KeyError(f"missing candidates: {sorted(missing_candidates)}")
            if missing_primitives: raise KeyError(f"missing primitives: {sorted(missing_primitives)}")
            timeline,growth=self._timeline(motif,reference_time)
            values.append(MotifIntelligence.create(motif=motif,
                intelligence_version=self.VERSION,replay_version=self.REPLAY_VERSION,
                measurements=self._measure(motif,candidate_index,primitive_index),
                timeline=timeline,growth=growth,
                stability={"replay":"DETERMINISTIC_BY_CONTRACT",
                           "observation_window":growth["state"],
                           "version":"NOT_COMPARABLE","fragmentation":"NOT_MEASURED"}))
        ranked=tuple(item.with_rank(index) for index,item in enumerate(
            sorted(values,key=self._ranking_key),start=1))
        self._health={"status":"HEALTHY","motifs":len(motifs),
            "intelligence_generated":len(ranked),"ranking_count":len(ranked),
            "growth":dict(sorted(Counter(item.growth["state"] for item in ranked).items())),
            "replay":"DETERMINISTIC","latency_ms":round((time.perf_counter()-started)*1000,3),
            "coverage_ppm":_ratio_ppm(len(ranked),len(motifs)),
            "average_evidence_completeness_ppm":round(mean(
                item.measurements["completeness"]["evidence_completeness_ppm"] or 0
                for item in ranked)) if ranked else None,
            "average_primitive_completeness_ppm":round(mean(
                item.measurements["completeness"]["primitive_completeness_ppm"] or 0
                for item in ranked)) if ranked else None,
            "authoritative":False,"identity_enabled":False,"governance_enabled":False}
        return ranked

    def health(self) -> dict[str,Any]:
        return dict(self._health or {"status":"IDLE","motifs":0,
            "intelligence_generated":0,"authoritative":False,
            "identity_enabled":False,"governance_enabled":False})
