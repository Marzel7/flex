from __future__ import annotations

from dataclasses import replace

from src.evidence.discovery import (
    DiscoveryCandidate, MotifCanonicalizer, MotifIntelligenceEngine,
    MotifIntelligenceStore,
)
from src.evidence.primitives.contracts import (
    ObservationWindow,PrimitiveObservation,PrimitiveQuality,PrimitiveType,
)


def primitive(kind,subjects,payload,*,evidence,start,quality=PrimitiveQuality.PROVEN,missing=()):
    return PrimitiveObservation.create(primitive_type=kind,primitive_version="1",
        evidence_ids=[evidence],subjects=subjects,parameters={},
        observation_window=ObservationWindow(start,start),output_payload=payload,
        quality_state=quality,missing_inputs=missing,generated_at=999)


def occurrence(prefix,start,*,quality=PrimitiveQuality.PROVEN,missing=()):
    source=f"{prefix}-source";creator=f"{prefix}-creator";mint=f"{prefix}-mint"
    values=(primitive(PrimitiveType.SYSTEM_TRANSFER,(source,creator),
        {"source":source,"destination":creator,"amount":100,"signature":f"{prefix}-tx"},
        evidence=f"{prefix}-e1",start=start,quality=quality,missing=missing),
        primitive(PrimitiveType.LAUNCH_SIGNER,(creator,mint),
        {"wallet":creator,"mint":mint,"launch_signature":f"{prefix}-launch"},
        evidence=f"{prefix}-e2",start=start+10))
    candidate=DiscoveryCandidate.create(discovery_version="1.0.0",
        supporting_evidence_ids=[value for item in values for value in item.evidence_ids],
        supporting_primitive_ids=[item.primitive_id for item in values],
        supporting_behaviour_observation_ids=[],supporting_topology_revision_ids=[],
        observed_recurring_pattern={"shape":"chain"},population=(source,creator,mint),
        time_start=start,time_end=start+10,quality_state=quality.value,
        missing_evidence=missing,contradictory_evidence=[],
        lifecycle="RECURRING_PATTERN",input_digest=f"input-{prefix}",generated_at=0)
    return values,candidate


def corpus():
    primitives=[];candidates=[]
    for prefix,start in (("a",100),("b",200),("c",300)):
        values,candidate=occurrence(prefix,start)
        primitives.extend(values);candidates.append(candidate)
    motifs=MotifCanonicalizer().consolidate(candidates,primitives)
    return tuple(primitives),tuple(candidates),motifs


def test_objective_profile_contains_structural_behaviour_and_completeness_measurements():
    primitives,candidates,motifs=corpus()
    profiles=MotifIntelligenceEngine().generate(motifs,candidates,primitives)
    assert len(profiles)==1 and profiles[0].occurrence_count==3
    measurements=profiles[0].measurements
    assert measurements["distinct"]=={"launches":3,"creators":3,
        "controller_role_subjects":0,"funding_role_wallets":0,
        "counterparties":6,"infrastructure":0}
    assert measurements["completeness"]["evidence_completeness_ppm"]==1_000_000
    assert measurements["completeness"]["primitive_completeness_ppm"]==1_000_000
    assert measurements["structure"]["graph_nodes"]==3
    assert measurements["behaviour"]["launch_spacing"]["count"]==2
    assert profiles[0].timeline["first_observed"]==110
    assert profiles[0].timeline["last_observed"]==310


def test_incomplete_evidence_and_primitives_use_explicit_denominators():
    complete,first=occurrence("complete",100)
    incomplete,second=occurrence("incomplete",200,quality=PrimitiveQuality.INCOMPLETE,
                                 missing=("BalanceFact",))
    primitives=complete+incomplete;candidates=(first,second)
    motifs=MotifCanonicalizer().consolidate(candidates,primitives)
    profiles=MotifIntelligenceEngine().generate(motifs,candidates,primitives)
    totals=sum(item.measurements["completeness"]["primitive_total_observations"]
               for item in profiles)
    complete_count=sum(item.measurements["completeness"]["primitive_complete_observations"]
                       for item in profiles)
    assert totals==4 and complete_count==3
    assert len(profiles)==1
    assert profiles[0].measurements["completeness"]["evidence_completeness_ppm"]==500_000


def test_ranking_and_replay_are_deterministic_without_composite_score():
    primitives,candidates,motifs=corpus();engine=MotifIntelligenceEngine()
    first=engine.generate(motifs,candidates,primitives)
    second=MotifIntelligenceEngine().generate(tuple(reversed(motifs)),
        tuple(reversed(candidates)),tuple(reversed(primitives)))
    assert [item.to_dict() for item in first]==[item.to_dict() for item in second]
    assert [item.rank for item in first]==list(range(1,len(first)+1))
    assert "score" not in str([item.to_dict() for item in first]).lower()


def test_growth_is_measured_and_missing_time_is_not_inferred():
    primitives,candidates,motifs=corpus()
    profile=MotifIntelligenceEngine().generate(motifs,candidates,primitives)[0]
    assert profile.growth["basis"]=="HALF_WINDOW_OCCURRENCE_DELTA"
    no_time=replace(motifs[0],occurrences=tuple(replace(item,time_start=None,time_end=None)
                                                for item in motifs[0].occurrences),
                    time_start=None,time_end=None)
    no_time_profile=MotifIntelligenceEngine().generate((no_time,),candidates,primitives)[0]
    assert no_time_profile.growth["state"]=="NOT_COMPARABLE"


def test_intelligence_persistence_and_health_are_non_authoritative(tmp_path):
    primitives,candidates,motifs=corpus();engine=MotifIntelligenceEngine()
    profiles=engine.generate(motifs,candidates,primitives)
    store=MotifIntelligenceStore(tmp_path/"intelligence.db");store.open()
    try:
        first=store.append(profiles);second=store.append(profiles)
        assert first["inserted"]==1 and first["duplicates"]==0
        assert second["inserted"]==0 and second["duplicates"]==1
        assert first["ranking_id"]==second["ranking_id"]
        assert store.health()["authoritative"] is False
    finally:store.close()
    health=engine.health()
    assert health["identity_enabled"] is False
    assert health["governance_enabled"] is False
