from __future__ import annotations

from src.evidence.discovery.dominant_analysis import DominantMotifIntelligenceEngine
from src.evidence.discovery.intelligence import MotifIntelligence
from src.evidence.discovery.motifs import MotifOccurrence,OperationMotif


def graph(kind="SYSTEM_TRANSFER"):
    return {"graph_model":"STRUCTURAL_QUOTIENT_V1","nodes":[
        {"class":0,"multiplicity":1,"role_counts":{"SOURCE":1}},
        {"class":1,"multiplicity":2,"role_counts":{"DESTINATION":2}}],
        "directed_edges":[{"source_class":0,"target_class":1,"primitive_type":kind,
            "primitive_version":"1","role_order":"SOURCE>DESTINATION",
            "temporal_rank":0,"count":2}],"node_observations":[],
        "primitive_sequence":[{"temporal_rank":0,"primitive_types":{kind:2}}],
        "primitive_versions":[f"{kind}@1"]}


def motif(index,count,*,kind="SYSTEM_TRANSFER",start=100):
    motif_id=f"motif-{index}"
    occurrences=tuple(MotifOccurrence(f"occ-{index}-{value}",motif_id,
        f"candidate-{index}-{value}",(f"e-{index}-{value}",),(f"p-{index}-{value}",),
        (f"subject-{index}-{value}",),start+value*10,start+value*10+1) for value in range(count))
    return OperationMotif(motif_id,"1.0.0","1",graph(kind),occurrences,
        tuple(item.candidate_id for item in occurrences),
        tuple(value for item in occurrences for value in item.supporting_evidence_ids),
        (),tuple(item.observed_population for item in occurrences),start,start+count*10)


def profile(index,count,*,growth="STABLE",rank=None,dormancy=0,kind="SYSTEM_TRANSFER"):
    return MotifIntelligence(f"intel-{index}",f"motif-{index}","1.0.0","1",count,
        (f"subject-{index}",),{"distinct":{"launches":count,"creators":count,
            "controller_role_subjects":0,"funding_role_wallets":0,
            "counterparties":count,"infrastructure":0},
            "structure":{"primitive_diversity":1,"topology_diversity":1},
            "completeness":{"evidence_completeness_ppm":1_000_000,
                "primitive_completeness_ppm":1_000_000},
            "behaviour":{"launch_cadence_per_active_day_milli":1000,
                "creator_reuse_ppm":0,"funding_reuse_ppm":None,
                "infrastructure_reuse_ppm":None,"primitive_sequence_stability_ppm":1_000_000,
                "burst_gap_count":max(0,count-1)}},
        {"first_observed":100,"last_observed":100+count*10,"active_duration":count*10,
         "dormancy_duration":dormancy},
        {"state":growth,"absolute_change":1 if growth=="GROWING" else
            -1 if growth=="COLLAPSING" else 0,
         "recurrence_frequency_per_active_day_milli":1000},
        {"replay":"DETERMINISTIC_BY_CONTRACT","observation_window":growth},(),(),
        f"input-{index}",rank or index)


def corpus():
    motifs=(motif(1,10),motif(2,6),motif(3,2,kind="LAUNCH_SIGNER"))
    profiles=(profile(1,10,growth="GROWING",rank=1),
        profile(2,6,growth="COLLAPSING",rank=2,dormancy=50),
        profile(3,2,rank=3,kind="LAUNCH_SIGNER"))
    return motifs,profiles


def test_only_dominant_population_is_profiled_and_pareto_uses_full_population():
    motifs,profiles=corpus();engine=DominantMotifIntelligenceEngine(dominant_count=2)
    result=engine.analyze(motifs,profiles,())
    assert result.dominant_count==2 and len(result.profiles)==2
    assert result.dominant_occurrences==16 and result.total_occurrences==18
    assert result.pareto[0]=={"top":10,"motifs_included":3,"occurrences":18,
        "occurrence_share_ppm":1_000_000,"marginal_occurrences":18,
        "marginal_share_ppm":1_000_000}


def test_structural_behaviour_temporal_and_population_profiles_are_objective():
    motifs,profiles=corpus();result=DominantMotifIntelligenceEngine(
        dominant_count=2).analyze(motifs,profiles,())
    row=next(item for item in result.profiles if item["motif_id"]=="motif-1")
    assert row["structure"]["topology_depth_max_directed_shortest_path"]==1
    assert row["structure"]["structural_symmetry_ppm"]==666_667
    assert row["population"]["occurrences"]==10
    assert row["temporal"]["growth"]["state"]=="GROWING"
    assert "EXPLODING" in row["stability"]
    assert row["merging_state"]=="NOT_MEASURED_WITHOUT_PRIOR_MOTIF_ASSIGNMENT"


def test_exact_topology_relationships_form_deterministic_neighbourhoods():
    motifs,profiles=corpus();result=DominantMotifIntelligenceEngine(
        dominant_count=3).analyze(motifs,profiles,())
    assert len(result.relationships)==1
    edge=result.relationships[0]
    assert edge["exact_topology_fingerprint"] is True
    assert {edge["source_motif_id"],edge["target_motif_id"]}=={"motif-1","motif-2"}
    assert sorted(item["motif_count"] for item in result.neighbourhoods)==[1,2]


def test_replay_status_requires_and_compares_analysis_identity():
    motifs,profiles=corpus();engine=DominantMotifIntelligenceEngine(dominant_count=2)
    first=engine.analyze(motifs,profiles,())
    assert first.replay["state"]=="NOT_MEASURED"
    replay=engine.analyze(tuple(reversed(motifs)),tuple(reversed(profiles)),(),
                          replay_analysis_id=first.analysis_id)
    assert replay.analysis_id==first.analysis_id and replay.replay["state"]=="MATCH"


def test_no_identity_governance_or_confidence_output():
    motifs,profiles=corpus();payload=DominantMotifIntelligenceEngine(
        dominant_count=2).analyze(motifs,profiles,()).to_dict()
    encoded=str(payload).lower()
    for forbidden in ("watchtower","3sw2","governance","confidence"):
        assert forbidden not in encoded
