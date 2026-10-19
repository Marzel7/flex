"""Read-only deterministic population analysis over motif intelligence profiles."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Sequence

from ..contracts import canonical_json_bytes
from .intelligence import MotifIntelligence


def _ppm(numerator:int,denominator:int)->int|None:
    return round((numerator*1_000_000)/denominator) if denominator else None


def _mean_milli(values:Sequence[int])->int|None:
    return round((sum(values)*1000)/len(values)) if values else None


def _median_milli(values:Sequence[int])->int|None:
    if not values:return None
    ordered=sorted(values);middle=len(ordered)//2
    return ordered[middle]*1000 if len(ordered)%2 else (ordered[middle-1]+ordered[middle])*500


def _percentile(values:Sequence[int],percent:int)->int|None:
    if not values:return None
    ordered=sorted(values);index=max(0,math.ceil((percent/100)*len(ordered))-1)
    return ordered[index]


def _population_stddev_milli(values:Sequence[int])->int|None:
    if not values:return None
    count=len(values);total=sum(values)
    # Variance = (n*sum(x^2)-sum(x)^2)/n^2. Scale before integer sqrt.
    numerator=count*sum(value*value for value in values)-total*total
    return math.isqrt(max(0,(numerator*1_000_000)//(count*count)))


@dataclass(frozen=True)
class MotifPopulationAnalysis:
    analysis_id:str
    analysis_version:str
    profile_ids:tuple[str,...]
    summary:dict[str,Any]
    concentration:tuple[dict[str,Any],...]
    long_tail:tuple[dict[str,Any],...]
    pareto:dict[str,Any]
    completeness:dict[str,Any]
    stability:dict[str,Any]
    motif_rows:tuple[dict[str,Any],...]

    def to_dict(self)->dict[str,Any]:
        return {"analysis_id":self.analysis_id,"analysis_version":self.analysis_version,
            "profile_ids":list(self.profile_ids),"summary":self.summary,
            "concentration":list(self.concentration),"long_tail":list(self.long_tail),
            "pareto":self.pareto,"completeness":self.completeness,
            "stability":self.stability,"motifs":list(self.motif_rows)}


class MotifPopulationAnalysisEngine:
    VERSION="1.0.0"
    BANDS=((1,1,"1"),(2,2,"2"),(3,5,"3-5"),(6,10,"6-10"),
           (11,25,"11-25"),(26,100,"26-100"),(101,500,"101-500"),
           (501,None,"501+"))
    CONCENTRATION_LIMITS=(1,5,10,25,50,100)

    @staticmethod
    def _categories(profile:MotifIntelligence)->tuple[str,...]:
        values=[];completeness=profile.measurements["completeness"]
        topology=profile.measurements["structure"]["topology_diversity"]
        if profile.occurrence_count>=101:values.append("HIGH_VOLUME")
        if profile.occurrence_count==1:values.append("SINGLETON")
        if profile.growth["state"]=="STABLE":values.append("STABLE")
        if profile.growth["state"]=="GROWING":values.append("GROWING")
        if (profile.timeline.get("dormancy_duration") or 0)>0:values.append("DORMANT")
        if topology>1:values.append("FRAGMENTED")
        if (completeness["evidence_completeness_ppm"] or 0)<1_000_000:
            values.append("EVIDENCE_LIMITED")
        if (completeness["primitive_completeness_ppm"] or 0)<1_000_000:
            values.append("PRIMITIVE_LIMITED")
        return tuple(values)

    @staticmethod
    def _current_activity(profile:MotifIntelligence)->str:
        if profile.timeline.get("last_observed") is None:return "UNKNOWN"
        return "ACTIVE_AT_WINDOW_END" if profile.timeline.get("dormancy_duration")==0 \
            else "DORMANT_AT_WINDOW_END"

    def analyze(self,profiles:Sequence[MotifIntelligence],*,
                replay_profiles:Sequence[MotifIntelligence]|None=None)->MotifPopulationAnalysis:
        ordered=tuple(sorted(profiles,key=lambda item:item.motif_id))
        sizes=[item.occurrence_count for item in ordered];total=sum(sizes);count=len(sizes)
        descending=sorted(ordered,key=lambda item:(-item.occurrence_count,item.motif_id))
        histogram=Counter()
        long_tail=[]
        for lower,upper,label in self.BANDS:
            members=[value for value in sizes if value>=lower and (upper is None or value<=upper)]
            occurrences=sum(members);histogram[label]=len(members)
            long_tail.append({"band":label,"motif_count":len(members),
                "motif_share_ppm":_ppm(len(members),count),"occurrences":occurrences,
                "occurrence_share_ppm":_ppm(occurrences,total)})
        concentration=[]
        for limit in self.CONCENTRATION_LIMITS:
            selected=descending[:limit];occurrences=sum(item.occurrence_count for item in selected)
            concentration.append({"top":limit,"motifs_included":len(selected),
                "occurrences":occurrences,"occurrence_share_ppm":_ppm(occurrences,total),
                "cumulative_percentage_basis_points":round((occurrences*10_000)/total)
                    if total else None})
        cumulative=0;motifs_for_80=0
        for profile in descending:
            if total and cumulative*100>=total*80:break
            cumulative+=profile.occurrence_count;motifs_for_80+=1
        evidence_complete=sum(item.measurements["completeness"]
            ["evidence_complete_occurrences"] for item in ordered)
        evidence_total=sum(item.measurements["completeness"]
            ["evidence_total_occurrences"] for item in ordered)
        primitive_complete=sum(item.measurements["completeness"]
            ["primitive_complete_observations"] for item in ordered)
        primitive_total=sum(item.measurements["completeness"]
            ["primitive_total_observations"] for item in ordered)
        rows=[]
        for profile in ordered:
            complete=profile.measurements["completeness"];distinct=profile.measurements["distinct"]
            structure=profile.measurements["structure"];behaviour=profile.measurements["behaviour"]
            rows.append({"motif_id":profile.motif_id,"occurrences":profile.occurrence_count,
                "first_seen":profile.timeline.get("first_observed"),
                "last_seen":profile.timeline.get("last_observed"),
                "lifetime":profile.timeline.get("active_duration"),
                "burst_count":behaviour.get("burst_gap_count"),
                "dormancy":profile.timeline.get("dormancy_duration"),
                "current_activity":self._current_activity(profile),
                "growth":profile.growth["state"],"categories":list(self._categories(profile)),
                "evidence_completeness_ppm":complete["evidence_completeness_ppm"],
                "primitive_completeness_ppm":complete["primitive_completeness_ppm"],
                "unavailable_evidence_occurrences":complete["evidence_total_occurrences"]-
                    complete["evidence_complete_occurrences"],
                "replay_quality":profile.stability["replay"],
                "diversity":{"creators":distinct["creators"],"launches":distinct["launches"],
                    "counterparties":distinct["counterparties"],
                    "funding_role_wallets":distinct["funding_role_wallets"],
                    "infrastructure":distinct["infrastructure"],
                    "primitive":structure["primitive_diversity"],
                    "topology":structure["topology_diversity"]}})
        summary={"motifs":count,"candidate_occurrences":total,
            "minimum_occurrences":min(sizes,default=None),"maximum_occurrences":max(sizes,default=None),
            "mean_occurrences_milli":_mean_milli(sizes),
            "median_occurrences_milli":_median_milli(sizes),
            "p50":_percentile(sizes,50),"p75":_percentile(sizes,75),
            "p90":_percentile(sizes,90),"p95":_percentile(sizes,95),
            "p99":_percentile(sizes,99),
            "population_standard_deviation_milli":_population_stddev_milli(sizes),
            "histogram":dict(histogram),"candidate_compression_ratio_milli":
                round((total*1000)/count) if count else None,
            "average_occurrences_per_motif_milli":_mean_milli(sizes),
            "largest_motif":max(sizes,default=None),"smallest_motif":min(sizes,default=None),
            "long_tail_ratio_ppm":_ppm(sum(value<=2 for value in sizes),count)}
        pareto={"motifs_for_80_percent":motifs_for_80,"occurrences_at_threshold":cumulative,
            "share_at_threshold_ppm":_ppm(cumulative,total),
            "remaining_occurrences":total-cumulative,
            "remaining_share_ppm":_ppm(total-cumulative,total)}
        completeness={"evidence":{"complete":evidence_complete,"total":evidence_total,
                "unavailable":evidence_total-evidence_complete,
                "completeness_ppm":_ppm(evidence_complete,evidence_total)},
            "primitives":{"complete":primitive_complete,"total":primitive_total,
                "unavailable":primitive_total-primitive_complete,
                "completeness_ppm":_ppm(primitive_complete,primitive_total)}}
        if replay_profiles is None:
            stability={"largest_motif_stable":"NOT_MEASURED","ranking_stable":"NOT_MEASURED",
                "distribution_stable":"NOT_MEASURED","occurrence_assignment_stable":"NOT_MEASURED",
                "motif_ids_stable":"NOT_MEASURED","basis":"NO_REPLAY_POPULATION_SUPPLIED"}
        else:
            replay=tuple(sorted(replay_profiles,key=lambda item:item.motif_id))
            ranked=lambda values:[item.motif_id for item in sorted(
                values,key=lambda item:item.rank if item.rank is not None else math.inf)]
            largest=lambda values:[item.motif_id for item in sorted(
                values,key=lambda item:(-item.occurrence_count,item.motif_id))[:1]]
            stability={"largest_motif_stable":largest(ordered)==largest(replay),
                "ranking_stable":ranked(ordered)==ranked(replay),
                "distribution_stable":sorted(item.occurrence_count for item in ordered)==
                    sorted(item.occurrence_count for item in replay),
                "occurrence_assignment_stable":sorted(item.intelligence_id for item in ordered)==
                    sorted(item.intelligence_id for item in replay),
                "motif_ids_stable":[item.motif_id for item in ordered]==
                    [item.motif_id for item in replay],
                "basis":"INDEPENDENT_REPLAY_PROFILE_COMPARISON"}
        identity_body={"version":self.VERSION,
            "profiles":[item.intelligence_id for item in ordered],"summary":summary,
            "concentration":concentration,"long_tail":long_tail,"pareto":pareto,
            "completeness":completeness,"stability":stability,"motifs":rows}
        analysis_id=hashlib.sha256(canonical_json_bytes(
            ["MotifPopulationAnalysis",identity_body])).hexdigest()
        return MotifPopulationAnalysis(analysis_id,self.VERSION,
            tuple(item.intelligence_id for item in ordered),summary,tuple(concentration),
            tuple(long_tail),pareto,completeness,stability,tuple(rows))
