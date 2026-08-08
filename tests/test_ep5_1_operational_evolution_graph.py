from __future__ import annotations

from src.evidence.discovery.change_intelligence import OperationalChangeEngine,OperationalLandscapeSnapshot
from src.evidence.discovery.evolution_intelligence import OperationalEvolutionEngine
from src.evidence.discovery.evolution_storage import OperationalEvolutionStore
from src.evidence.discovery.dominant_analysis import DominantMotifAnalysis
from src.evidence.discovery.intelligence import MotifIntelligence
from src.evidence.discovery.motifs import MotifOccurrence,OperationMotif


def motif(name,candidates,kind="SYSTEM_TRANSFER"):
    graph={"nodes":[{"class":0,"multiplicity":1},{"class":1,"multiplicity":1}],
        "directed_edges":[{"source_class":0,"target_class":1,"primitive_type":kind,
            "primitive_version":"1","role_order":"SOURCE>DESTINATION","count":1}],
        "primitive_versions":[f"{kind}@1"]}
    occurrences=tuple(MotifOccurrence(f"o-{name}-{value}",name,value,(f"e-{value}",),
        (f"p-{value}",),(value,),100,100) for value in candidates)
    return OperationMotif(name,"1.0.0","1",graph,occurrences,tuple(candidates),
        tuple(f"e-{value}" for value in candidates),tuple(f"p-{value}" for value in candidates),
        tuple((value,) for value in candidates),100,100)


def profile(value,rank,dormancy=0):
    count=len(value.occurrences)
    return MotifIntelligence(f"i-{value.motif_id}-{count}",value.motif_id,"1","1",count,
        tuple(value.supporting_candidate_ids),{"structure":{"primitive_distribution":{
            value.canonical_graph["directed_edges"][0]["primitive_type"]:count}},
        "completeness":{"evidence_complete_occurrences":count,"evidence_total_occurrences":count,
            "primitive_complete_observations":count,"primitive_total_observations":count}},
        {"first_observed":100,"last_observed":100,"dormancy_duration":dormancy},
        {"state":"STABLE","absolute_change":0},{},value.supporting_evidence_ids,
        value.supporting_primitive_ids,f"in-{value.motif_id}-{count}",rank)


def snapshot(motifs,boundary,neighbourhoods=(),relationships=()):
    profiles=tuple(profile(value,index+1,10 if boundary==100 and value.motif_id=="a" else 0)
                   for index,value in enumerate(motifs))
    total=sum(len(value.occurrences) for value in motifs)
    analysis=DominantMotifAnalysis(f"analysis-{boundary}","1",len(motifs),1_000_000,total,total,
        (),tuple(relationships),tuple(neighbourhoods),(),{})
    return OperationalLandscapeSnapshot.create(observation_boundary=boundary,motifs=motifs,
        profiles=profiles,dominant_analysis=analysis)


def reconstruct(previous,current):
    change=OperationalChangeEngine().compare(previous,current)
    return OperationalEvolutionEngine().reconstruct(previous,current,change)


def test_continuity_contains_objective_support_and_no_identity():
    previous=snapshot((motif("a",("x","y")),),100)
    current=snapshot((motif("a",("x","y","z")),),200)
    graph=reconstruct(previous,current)
    assert len(graph.edges)==1
    edge=graph.edges[0]
    assert edge.continuity_basis==("CANONICAL_MOTIF_PERSISTENCE","EXACT_OCCURRENCE_CONTINUITY")
    assert edge.supporting_candidate_ids==("x","y")
    assert set(edge.supporting_evidence_ids)=={"e-x","e-y"}
    assert {item.event_type for item in graph.events}=={"CONTINUED","GREW","REACTIVATED"}
    assert "identity" not in str(graph.to_dict()).lower()


def test_split_merge_birth_and_retirement_are_reproducible():
    old=motif("old",("x","y"));retired=motif("retired",("q",))
    left=motif("left",("x",));right=motif("right",("y",));born=motif("born",("z",))
    previous=snapshot((old,retired),100);current=snapshot((left,right,born),200)
    split=reconstruct(previous,current)
    types=[item.event_type for item in split.events]
    assert types.count("SPLIT")==1 and types.count("NEW")==1 and types.count("RETIRED")==1
    assert len(split.edges)==2
    merged=reconstruct(current,previous)
    assert sum(item.event_type=="MERGED" for item in merged.events)==1


def test_structural_similarity_without_persistence_or_overlap_is_not_lineage():
    previous=snapshot((motif("old",("x",)),),100)
    current=snapshot((motif("new",("y",)),),200)
    graph=reconstruct(previous,current)
    assert graph.edges==()
    assert {item.event_type for item in graph.events}=={"NEW","RETIRED"}


def test_replay_and_input_order_are_deterministic():
    previous=snapshot((motif("a",("x",)),motif("b",("y",))),100)
    current=snapshot((motif("a",("x","z")),motif("b",("y",))),200)
    change=OperationalChangeEngine().compare(previous,current)
    first=OperationalEvolutionEngine().reconstruct(previous,current,change)
    old_replay=OperationalLandscapeSnapshot.create(observation_boundary=100,
        motifs=tuple(reversed(previous.motifs)),profiles=tuple(reversed(previous.profiles)),
        dominant_analysis=previous.dominant_analysis)
    new_replay=OperationalLandscapeSnapshot.create(observation_boundary=200,
        motifs=tuple(reversed(current.motifs)),profiles=tuple(reversed(current.profiles)),
        dominant_analysis=current.dominant_analysis)
    replay_change=OperationalChangeEngine().compare(old_replay,new_replay)
    second=OperationalEvolutionEngine().reconstruct(old_replay,new_replay,replay_change)
    assert first.to_dict()==second.to_dict()


def test_health_reports_all_objective_event_classes():
    previous=snapshot((motif("a",("x",)),),100)
    current=snapshot((motif("a",("x","y")),),200)
    engine=OperationalEvolutionEngine();engine.reconstruct(previous,current,
        OperationalChangeEngine().compare(previous,current));health=engine.health()
    assert health["continuations"]==1 and health["coverage_ppm"]==1_000_000
    assert health["authoritative"] is False and health["identity_enabled"] is False
    assert health["governance_enabled"] is False and health["operation_inference_enabled"] is False


def test_neighbourhood_evolution_has_first_class_nodes_edges_and_events():
    old=motif("a",("x",));added=motif("b",("y",))
    previous=snapshot((old,),100,({"neighbourhood_id":"n-old","motif_ids":["a"]},))
    current=snapshot((old,added),200,({"neighbourhood_id":"n-new","motif_ids":["a","b"]},))
    graph=reconstruct(previous,current)
    neighbourhood_nodes=[item for item in graph.nodes if item.node_type=="NEIGHBOURHOOD"]
    assert len(neighbourhood_nodes)==2
    neighbourhood_edges=[item for item in graph.edges
        if item.continuity_basis==("OBSERVED_NEIGHBOURHOOD_COMPONENT_CONTINUITY",)]
    assert len(neighbourhood_edges)==1
    neighbourhood_events=[item for item in graph.events
        if item.measurements.get("scope")=="NEIGHBOURHOOD"]
    assert {item.event_type for item in neighbourhood_events}=={"CONTINUED","GREW"}


def test_evolution_graph_is_append_only_and_idempotent(tmp_path):
    previous=snapshot((motif("a",("x",)),),100)
    current=snapshot((motif("a",("x","y")),),200);graph=reconstruct(previous,current)
    store=OperationalEvolutionStore(tmp_path/"evolution.db");store.open()
    try:
        first=store.append(graph);second=store.append(graph)
        assert first["inserted_snapshots"]==1 and first["inserted_records"]>0
        assert second=={"inserted_snapshots":0,"duplicate_snapshots":1,"inserted_records":0}
        assert store.health()["snapshots"]==1 and store.health()["authoritative"] is False
    finally:store.close()
