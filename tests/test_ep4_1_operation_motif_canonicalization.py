from __future__ import annotations

import json

from src.evidence.discovery import (
    DiscoveryCandidate, MotifCanonicalizer, MotifStore, OperationMotif,
)
from src.evidence.primitives.contracts import (
    ObservationWindow, PrimitiveObservation, PrimitiveQuality, PrimitiveType,
)


def primitive(kind, subjects, payload, *, evidence, start, version="1"):
    return PrimitiveObservation.create(
        primitive_type=kind, primitive_version=version, evidence_ids=[evidence],
        subjects=subjects, parameters={}, observation_window=ObservationWindow(start,start),
        output_payload=payload, quality_state=PrimitiveQuality.PROVEN, generated_at=99,
    )


def candidate(primitives, population, *, clock=100):
    return DiscoveryCandidate.create(
        discovery_version="1.0.0",
        supporting_evidence_ids=[value for item in primitives for value in item.evidence_ids],
        supporting_primitive_ids=[item.primitive_id for item in primitives],
        supporting_behaviour_observation_ids=[], supporting_topology_revision_ids=[],
        observed_recurring_pattern={"pattern_type":"test"}, population=population,
        time_start=min(item.observation_window.start for item in primitives),
        time_end=max(item.observation_window.end for item in primitives),
        quality_state="PROVEN", missing_evidence=[], contradictory_evidence=[],
        lifecycle="RECURRING_PATTERN", input_digest=f"input-{clock}", generated_at=clock,
    )


def chain(prefix, *, base_time=1, reverse=False):
    controller=f"{prefix}-controller"; creator=f"{prefix}-creator"; mint=f"{prefix}-mint"
    source,target=(creator,controller) if reverse else (controller,creator)
    values=(
        primitive(PrimitiveType.SYSTEM_TRANSFER,(controller,creator),
                  {"source":source,"destination":target,"signature":f"{prefix}-tx",
                   "amount":123456},evidence=f"{prefix}-e1",start=base_time),
        primitive(PrimitiveType.LAUNCH_SIGNER,(creator,mint),
                  {"wallet":creator,"mint":mint,"launch_signature":f"{prefix}-launch"},
                  evidence=f"{prefix}-e2",start=base_time+7),
    )
    return values,candidate(values,(controller,creator,mint),clock=base_time+20)


def test_wallet_mint_signature_time_and_amount_are_removed_from_motif_identity():
    left,left_candidate=chain("alpha",base_time=10)
    right,right_candidate=chain("omega",base_time=900)
    canonicalizer=MotifCanonicalizer()
    motifs=canonicalizer.consolidate((left_candidate,right_candidate),left+right)
    assert len(motifs)==1 and len(motifs[0].occurrences)==2
    graph=json.dumps(motifs[0].canonical_graph,sort_keys=True).lower()
    for concrete in ("alpha","omega","123456","signature","amount"):
        assert concrete not in graph


def test_direction_and_primitive_versions_remain_structurally_significant():
    forward,forward_candidate=chain("forward")
    reverse,reverse_candidate=chain("reverse",reverse=True)
    versioned,versioned_candidate=chain("versioned")
    versioned=list(versioned)
    versioned[0]=primitive(PrimitiveType.SYSTEM_TRANSFER,
        ("versioned-controller","versioned-creator"),
        {"source":"versioned-controller","destination":"versioned-creator"},
        evidence="versioned-e3",start=1,version="2")
    versioned_candidate=candidate(tuple(versioned),
        ("versioned-controller","versioned-creator","versioned-mint"))
    motifs=MotifCanonicalizer().consolidate(
        (forward_candidate,reverse_candidate,versioned_candidate),
        forward+reverse+tuple(versioned),
    )
    assert len(motifs)==3


def test_input_order_and_replay_order_do_not_change_motifs():
    first,first_candidate=chain("one")
    second,second_candidate=chain("two")
    canonicalizer=MotifCanonicalizer()
    left=canonicalizer.consolidate((first_candidate,second_candidate),first+second)
    right=canonicalizer.consolidate(
        (second_candidate,first_candidate),tuple(reversed(first+second)))
    assert [item.to_dict() for item in left]==[item.to_dict() for item in right]


def test_motif_store_preserves_incremental_occurrences_without_rewriting_definition(tmp_path):
    first,first_candidate=chain("one")
    second,second_candidate=chain("two")
    canonicalizer=MotifCanonicalizer()
    first_motif=canonicalizer.consolidate((first_candidate,),first)
    second_motif=canonicalizer.consolidate((second_candidate,),second)
    assert first_motif[0].motif_id==second_motif[0].motif_id
    store=MotifStore(tmp_path/"motifs.db"); store.open()
    try:
        assert store.append(first_motif)=={"inserted":1,"duplicates":0,"occurrences_inserted":1}
        assert store.append(second_motif)=={"inserted":0,"duplicates":1,"occurrences_inserted":1}
        assert store.append(second_motif)=={"inserted":0,"duplicates":1,"occurrences_inserted":0}
        assert store.health()=={
            "status":"HEALTHY","motif_count":1,"candidate_count":2,
            "compression_ratio":2.0,"largest_motif":2,"singleton_rate":0.0,
            "authoritative":False,
        }
    finally: store.close()


def test_motif_has_no_identity_or_governance_authority():
    primitives,value=chain("neutral")
    canonicalizer=MotifCanonicalizer(); motifs=canonicalizer.consolidate((value,),primitives)
    health=canonicalizer.health()
    assert health["authoritative"] is False
    assert health["identity_enabled"] is False
    assert health["governance_enabled"] is False
    assert isinstance(motifs[0],OperationMotif)
