from __future__ import annotations

from src.evidence.discovery.change_intelligence import (
    OperationalChangeEngine,OperationalLandscapeSnapshot,
)
from src.evidence.discovery.change_storage import OperationalChangeStore
from src.evidence.discovery.dominant_analysis import DominantMotifAnalysis
from src.evidence.discovery.intelligence import MotifIntelligence
from src.evidence.discovery.motifs import MotifOccurrence,OperationMotif


def graph(kind):
    return {"nodes":[{"class":0,"multiplicity":1,"role_counts":{"SOURCE":1}},
        {"class":1,"multiplicity":1,"role_counts":{"DESTINATION":1}}],
        "directed_edges":[{"source_class":0,"target_class":1,"primitive_type":kind,
            "primitive_version":"1","role_order":"SOURCE>DESTINATION","count":1}],
        "primitive_versions":[f"{kind}@1"]}


def motif(name,candidates,*,kind="SYSTEM_TRANSFER",start=100):
    occurrences=tuple(MotifOccurrence(f"occ-{name}-{value}",name,value,
        (f"e-{value}",),(f"p-{value}",),(f"subject-{value}",),start+index,start+index)
        for index,value in enumerate(candidates))
    return OperationMotif(name,"1.0.0","1",graph(kind),occurrences,
        tuple(candidates),tuple(value for item in occurrences for value in item.supporting_evidence_ids),
        tuple(value for item in occurrences for value in item.supporting_primitive_ids),
        tuple(item.observed_population for item in occurrences),start,start+len(occurrences))


def profile(value,rank,*,dormancy=0,growth="STABLE"):
    count=len(value.occurrences);primitive_distribution={
        edge["primitive_type"]:count for edge in value.canonical_graph["directed_edges"]}
    return MotifIntelligence(f"intel-{value.motif_id}-{count}",value.motif_id,"1.0.0","1",count,
        tuple(subject for item in value.observed_populations for subject in item),
        {"structure":{"primitive_distribution":primitive_distribution},
         "completeness":{"evidence_complete_occurrences":count,"evidence_total_occurrences":count,
            "primitive_complete_observations":count,"primitive_total_observations":count,
            "evidence_completeness_ppm":1_000_000,"primitive_completeness_ppm":1_000_000}},
        {"first_observed":value.time_start,"last_observed":value.time_end,
         "dormancy_duration":dormancy},{"state":growth},{"replay":"DETERMINISTIC"},
        value.supporting_evidence_ids,value.supporting_primitive_ids,f"input-{value.motif_id}-{count}",rank)


def relationship(source,target,label):
    return {"relationship_id":label,"source_motif_id":source,"target_motif_id":target}


def analysis(motifs,relationships,neighbourhoods):
    total=sum(len(item.occurrences) for item in motifs)
    pareto=tuple({"top":limit,"occurrences":total,"occurrence_share_ppm":1_000_000}
                 for limit in (10,25,50,69,100,250))
    return DominantMotifAnalysis(f"analysis-{'-'.join(sorted(item.motif_id for item in motifs))}","1.0.0",
        len(motifs),1_000_000,total,total,(),tuple(relationships),tuple(neighbourhoods),pareto,
        {"state":"MATCH"})


def snapshot(motifs,profiles,relationships,neighbourhoods,boundary):
    return OperationalLandscapeSnapshot.create(observation_boundary=boundary,motifs=motifs,
        profiles=profiles,dominant_analysis=analysis(motifs,relationships,neighbourhoods))


def evolving_snapshots():
    old_a=motif("a",("c1","c2"));old_b=motif("b",("c3",),kind="LAUNCH_SIGNER")
    previous=snapshot((old_a,old_b),(profile(old_a,1,dormancy=10),profile(old_b,2)),
        (relationship("a","b","rel-old"),),
        ({"neighbourhood_id":"n-old","motif_ids":["a","b"]},),100)
    new_a=motif("a",("c1","c2","c4"));new_c=motif("c",("c3",),kind="WSOL_CLOSE")
    new_d=motif("d",("c5",))
    current=snapshot((new_a,new_c,new_d),(profile(new_a,1,growth="GROWING"),
        profile(new_c,3),profile(new_d,2)),
        (relationship("a","c","rel-new"),relationship("c","d","rel-new-2")),
        ({"neighbourhood_id":"n-new","motif_ids":["a","c","d"]},),200)
    return previous,current


def test_snapshot_comparison_detects_new_growth_topology_and_reactivation():
    previous,current=evolving_snapshots();result=OperationalChangeEngine().compare(previous,current)
    a=next(item for item in result.motif_deltas if item.current_motif_ids==("a",))
    assert "GROWING" in a.change_types and "REACTIVATED" in a.change_types
    assert a.population_delta["new_occurrences"]==1
    evolved=next(item for item in result.motif_deltas if item.previous_motif_ids==("b",))
    assert evolved.current_motif_ids==("c",)
    assert {"TOPOLOGY_CHANGED","PRIMITIVE_COMPOSITION_CHANGED"}<set(evolved.change_types)
    new=next(item for item in result.motif_deltas if item.current_motif_ids==("d",))
    assert new.change_types==("NEW_MOTIF",)


def test_relationship_and_neighbourhood_changes_are_exact():
    previous,current=evolving_snapshots();result=OperationalChangeEngine().compare(previous,current)
    assert {(item.relationship_id,item.change_type) for item in result.relationship_deltas}=={
        ("rel-old","RELATIONSHIP_REMOVED"),("rel-new","RELATIONSHIP_CREATED"),
        ("rel-new-2","RELATIONSHIP_CREATED")}
    delta=result.neighbourhood_deltas[0]
    assert "EXPANSION" in delta.change_types
    assert delta.measurements["size_delta"]==1


def test_split_and_merge_are_based_on_occurrence_assignment_overlap():
    old=motif("old",("x1","x2"));left=motif("left",("x1",));right=motif("right",("x2",))
    previous=snapshot((old,),(profile(old,1),),(),
        ({"neighbourhood_id":"old-n","motif_ids":["old"]},),100)
    current=snapshot((left,right),(profile(left,1),profile(right,2)),(),
        ({"neighbourhood_id":"left-n","motif_ids":["left"]},
         {"neighbourhood_id":"right-n","motif_ids":["right"]}),200)
    split=OperationalChangeEngine().compare(previous,current).motif_deltas[0]
    assert {"SPLIT_MOTIF","FRAGMENTING"}<set(split.change_types)
    merge=OperationalChangeEngine().compare(current,previous).motif_deltas[0]
    assert {"MERGED_MOTIF","MERGING"}<set(merge.change_types)


def test_replay_and_input_order_are_deterministic():
    previous,current=evolving_snapshots();engine=OperationalChangeEngine()
    first=engine.compare(previous,current)
    previous_reordered=snapshot(tuple(reversed(previous.motifs)),tuple(reversed(previous.profiles)),
        tuple(reversed(previous.dominant_analysis.relationships)),
        tuple(reversed(previous.dominant_analysis.neighbourhoods)),100)
    current_reordered=snapshot(tuple(reversed(current.motifs)),tuple(reversed(current.profiles)),
        tuple(reversed(current.dominant_analysis.relationships)),
        tuple(reversed(current.dominant_analysis.neighbourhoods)),200)
    second=OperationalChangeEngine().compare(previous_reordered,current_reordered)
    assert previous.snapshot_id==previous_reordered.snapshot_id
    assert current.snapshot_id==current_reordered.snapshot_id
    assert first.to_dict()==second.to_dict()


def test_health_and_output_have_no_identity_governance_or_confidence():
    previous,current=evolving_snapshots();engine=OperationalChangeEngine()
    result=engine.compare(previous,current);health=engine.health()
    assert health["snapshots_compared"]==1 and health["coverage_ppm"]==1_000_000
    assert health["authoritative"] is False and health["governance_enabled"] is False
    encoded=str(result.to_dict()).lower()
    for forbidden in ("watchtower","3sw2","governance","confidence"):
        assert forbidden not in encoded


def test_change_snapshots_and_records_are_append_only(tmp_path):
    previous,current=evolving_snapshots();change=OperationalChangeEngine().compare(previous,current)
    store=OperationalChangeStore(tmp_path/"changes.db");store.open()
    try:
        first=store.append(previous,current,change);second=store.append(previous,current,change)
        assert first["inserted_snapshots"]==2 and first["inserted_changes"]==1
        assert first["inserted_records"]>0
        assert second=={"inserted_snapshots":0,"duplicate_snapshots":2,
            "inserted_changes":0,"duplicate_changes":1,"inserted_records":0}
        health=store.health()
        assert health["snapshots"]==2 and health["changes"]==1
        assert health["authoritative"] is False
    finally:store.close()
