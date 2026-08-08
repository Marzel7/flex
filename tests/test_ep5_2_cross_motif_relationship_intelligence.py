from __future__ import annotations

from dataclasses import replace

from src.evidence.discovery.change_intelligence import OperationalChangeEngine,OperationalLandscapeSnapshot
from src.evidence.discovery.evolution_intelligence import OperationalEvolutionEngine
from src.evidence.discovery.dominant_analysis import DominantMotifAnalysis
from src.evidence.discovery.intelligence import MotifIntelligence
from src.evidence.discovery.motifs import MotifOccurrence,OperationMotif
from src.evidence.discovery.relationship_intelligence import (
    CrossMotifRelationshipEngine,RelationshipEvolutionEngine,
)
from src.evidence.discovery.relationship_storage import CrossMotifRelationshipStore


def motif(name,candidates,kind="SYSTEM_TRANSFER"):
    graph={"nodes":[{"class":0,"multiplicity":1},{"class":1,"multiplicity":1}],
        "directed_edges":[{"source_class":0,"target_class":1,"primitive_type":kind,
            "primitive_version":"1","role_order":"SOURCE>DESTINATION","count":1}],
        "primitive_versions":[f"{kind}@1"]}
    occurrences=tuple(MotifOccurrence(f"o-{name}-{value}",name,value,(f"e-{value}",),
        (f"p-{value}",),(value,),100,100) for value in candidates)
    return OperationMotif(name,"1","1",graph,occurrences,tuple(candidates),
        tuple(f"e-{value}" for value in candidates),tuple(f"p-{value}" for value in candidates),
        tuple((value,) for value in candidates),100,100)


def profile(value,rank,boundary):
    count=len(value.occurrences)
    return MotifIntelligence(f"i-{value.motif_id}-{count}",value.motif_id,"1","1",count,
        tuple(value.supporting_candidate_ids),{"structure":{"primitive_distribution":{
            value.canonical_graph["directed_edges"][0]["primitive_type"]:count}},
        "completeness":{"evidence_complete_occurrences":count,"evidence_total_occurrences":count,
            "primitive_complete_observations":count,"primitive_total_observations":count,
            "evidence_completeness_ppm":1_000_000,"primitive_completeness_ppm":1_000_000},
        "behaviour":{"cadence":count}},
        {"first_observed":100,"last_observed":boundary,"dormancy_duration":0},
        {"state":"STABLE","absolute_change":0},{},value.supporting_evidence_ids,
        value.supporting_primitive_ids,f"in-{value.motif_id}-{count}",rank)


def snapshot(motifs,boundary,neighbourhoods=(),relationships=()):
    profiles=tuple(profile(value,index+1,boundary) for index,value in enumerate(motifs))
    total=sum(len(value.occurrences) for value in motifs)
    analysis=DominantMotifAnalysis(f"analysis-{boundary}","1",len(motifs),1_000_000,total,total,
        (),tuple(relationships),tuple(neighbourhoods),(),{})
    return OperationalLandscapeSnapshot.create(observation_boundary=boundary,motifs=motifs,
        profiles=profiles,dominant_analysis=analysis)


def link(source,target,*,evidence=(),primitives=(),infrastructure=(),topology=False,
         behaviour=False,temporal=True):
    return {"relationship_id":f"raw-{source}-{target}-{len(evidence)}-{len(primitives)}",
        "source_motif_id":source,"target_motif_id":target,
        "shared_evidence_ids":list(evidence),"shared_primitive_ids":list(primitives),
        "shared_infrastructure_subjects":list(infrastructure),
        "exact_topology_fingerprint":topology,"exact_behaviour_fingerprint":behaviour,
        "observation_windows_overlap":temporal}


def relationships(previous,current):
    change=OperationalChangeEngine().compare(previous,current)
    evolution=OperationalEvolutionEngine().reconstruct(previous,current,change)
    engine=CrossMotifRelationshipEngine()
    return engine.materialize(previous),engine.materialize(current),evolution


def test_relationship_types_are_exact_and_fully_supported():
    a=motif("a",("x",));b=motif("b",("y",))
    raw=link("a","b",evidence=("shared-e",),primitives=("shared-p",),
        infrastructure=("shared-i",),topology=True,behaviour=True)
    landscape=snapshot((a,b),100,({"neighbourhood_id":"n","motif_ids":["a","b"]},),(raw,))
    engine=CrossMotifRelationshipEngine();result=engine.materialize(landscape)
    assert {item.relationship_type for item in result.relationships}=={
        "SHARED_EVIDENCE_PROVENANCE","SHARED_PRIMITIVE_OBSERVATION","SHARED_INFRASTRUCTURE",
        "SHARED_TOPOLOGY","SHARED_BEHAVIOUR","SHARED_TEMPORAL_OBSERVATION",
        "SHARED_NEIGHBOURHOOD"}
    evidence=next(item for item in result.relationships
                  if item.relationship_type=="SHARED_EVIDENCE_PROVENANCE")
    assert evidence.supporting_evidence_ids==("shared-e",)
    assert evidence.measurements["supporting_observation_count"]==1
    assert evidence.measurements["supporting_motif_count"]==2
    assert engine.health()["coverage_ppm"]==1_000_000


def test_no_recorded_objective_support_means_no_relationship():
    a=motif("a",("x",));b=motif("b",("y",))
    landscape=snapshot((a,b),100,(),())
    assert CrossMotifRelationshipEngine().materialize(landscape).relationships==()


def test_exact_primitive_types_surface_funding_and_counterparty_observations():
    a=motif("a",("x",));b=motif("b",("y",))
    raw=link("a","b",primitives=("funding","counterparty"),temporal=False)
    landscape=snapshot((a,b),100,(),(raw,))
    result=CrossMotifRelationshipEngine().materialize(landscape,primitive_types={
        "funding":"ECONOMIC_FUNDING","counterparty":"DIRECT_COUNTERPARTY"})
    assert {item.relationship_type for item in result.relationships}=={
        "SHARED_PRIMITIVE_OBSERVATION","SHARED_FUNDING_OBSERVATION",
        "SHARED_COUNTERPARTY_OBSERVATION"}


def test_relationship_evolution_detects_strengthening_and_persistence():
    old_a=motif("a",("x",));old_b=motif("b",("y",))
    new_a=motif("a",("x","z"));new_b=motif("b",("y",))
    previous=snapshot((old_a,old_b),100,(),(link("a","b",evidence=("e1",),temporal=False),))
    current=snapshot((new_a,new_b),200,(),(link("a","b",evidence=("e1","e2"),temporal=False),))
    old_relationships,new_relationships,evolution=relationships(previous,current)
    result=RelationshipEvolutionEngine().compare(old_relationships,new_relationships,evolution)
    assert {item.event_type for item in result.observations}=={"STRENGTHENED"}
    assert result.observations[0].measurements["supporting_observation_delta"]==1
    assert result.observations[0].supporting_evolution_edge_ids


def test_relationship_evolution_health_is_non_authoritative():
    a=motif("a",("x",));b=motif("b",("y",));raw=link(
        "a","b",evidence=("e",),temporal=False)
    previous=snapshot((a,b),100,(),(raw,));current=snapshot((a,b),200,(),(raw,))
    old_relationships,new_relationships,evolution=relationships(previous,current)
    engine=RelationshipEvolutionEngine();engine.compare(old_relationships,new_relationships,evolution)
    health=engine.health()
    assert health["relationship_changes"]==1 and health["coverage_ppm"]==1_000_000
    assert health["authoritative"] is False and health["identity_enabled"] is False
    assert health["ownership_inference_enabled"] is False


def test_relationship_evolution_detects_split_created_and_retired():
    old_a=motif("a",("x","z"));old_b=motif("b",("y",));retired=motif("r",("q",))
    left=motif("left",("x",));right=motif("right",("z",));new_b=motif("b",("y",));born=motif("n",("w",))
    previous=snapshot((old_a,old_b,retired),100,(),(
        link("a","b",evidence=("e",),temporal=False),
        link("r","b",evidence=("r",),temporal=False)))
    current=snapshot((left,right,new_b,born),200,(),(
        link("left","b",evidence=("e",),temporal=False),
        link("right","b",evidence=("e",),temporal=False),
        link("n","b",evidence=("n",),temporal=False)))
    old_relationships,new_relationships,evolution=relationships(previous,current)
    result=RelationshipEvolutionEngine().compare(old_relationships,new_relationships,evolution)
    types={item.event_type for item in result.observations}
    assert {"SPLIT","CREATED","RETIRED"}<=types
    reverse_evolution=OperationalEvolutionEngine().reconstruct(current,previous,
        OperationalChangeEngine().compare(current,previous))
    reverse=RelationshipEvolutionEngine().compare(new_relationships,old_relationships,
                                                   reverse_evolution)
    assert "MERGED" in {item.event_type for item in reverse.observations}


def test_relationship_evolution_tracks_weakening_dormancy_and_reactivation():
    a=motif("a",("x",));b=motif("b",("y",))
    strong=snapshot((a,b),100,(),(link("a","b",evidence=("e1","e2"),temporal=False),))
    weak=snapshot((a,b),200,(),(link("a","b",evidence=("e1",),temporal=False),))
    old_relationships,new_relationships,evolution=relationships(strong,weak)
    weakened=RelationshipEvolutionEngine().compare(old_relationships,new_relationships,evolution)
    assert {item.event_type for item in weakened.observations}=={"WEAKENED"}

    dormant_profiles=tuple(replace(item,timeline={**item.timeline,"dormancy_duration":10})
                             for item in weak.profiles)
    dormant=OperationalLandscapeSnapshot.create(observation_boundary=200,motifs=weak.motifs,
        profiles=dormant_profiles,dominant_analysis=weak.dominant_analysis)
    active_relationships=CrossMotifRelationshipEngine().materialize(strong)
    dormant_relationships=CrossMotifRelationshipEngine().materialize(dormant)
    into_dormancy=OperationalEvolutionEngine().reconstruct(strong,dormant,
        OperationalChangeEngine().compare(strong,dormant))
    dormant_result=RelationshipEvolutionEngine().compare(active_relationships,
        dormant_relationships,into_dormancy)
    assert "DORMANT" in {item.event_type for item in dormant_result.observations}
    reactivated=OperationalEvolutionEngine().reconstruct(dormant,weak,
        OperationalChangeEngine().compare(dormant,weak))
    reactivated_result=RelationshipEvolutionEngine().compare(dormant_relationships,
        new_relationships,reactivated)
    assert "REACTIVATED" in {item.event_type for item in reactivated_result.observations}


def test_relationship_replay_and_input_order_are_deterministic():
    a=motif("a",("x",));b=motif("b",("y",));raw=link("a","b",evidence=("e",))
    first_landscape=snapshot((a,b),100,(),(raw,))
    replay_landscape=OperationalLandscapeSnapshot.create(observation_boundary=100,
        motifs=tuple(reversed(first_landscape.motifs)),profiles=tuple(reversed(first_landscape.profiles)),
        dominant_analysis=first_landscape.dominant_analysis)
    first=CrossMotifRelationshipEngine().materialize(first_landscape)
    replay=CrossMotifRelationshipEngine().materialize(replay_landscape)
    assert first.to_dict()==replay.to_dict()


def test_relationship_persistence_is_append_only(tmp_path):
    a=motif("a",("x",));b=motif("b",("y",));raw=link("a","b",evidence=("e",))
    previous=snapshot((a,b),100,(),(raw,));current=snapshot((a,b),200,(),(raw,))
    old_relationships,new_relationships,evolution=relationships(previous,current)
    relationship_evolution=RelationshipEvolutionEngine().compare(
        old_relationships,new_relationships,evolution)
    store=CrossMotifRelationshipStore(tmp_path/"relationships.db");store.open()
    try:
        first=store.append_relationships(old_relationships)
        duplicate=store.append_relationships(old_relationships)
        evolved=store.append_evolution(relationship_evolution)
        assert first["inserted_snapshots"]==1 and first["inserted_observations"]>0
        assert duplicate["inserted_snapshots"]==0 and duplicate["inserted_observations"]==0
        assert evolved["inserted_evolution"]==1
        assert store.health()["authoritative"] is False
    finally:store.close()
