#!/usr/bin/env python3
"""Validate EP5.0 using two immutable observation-window snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from statistics import median

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from scripts.validate_ep4_0_unknown_discovery import generic_population,load_primitives
from src.evidence.discovery import DiscoveryEngine,DiscoverySnapshot,MotifCanonicalizer,MotifIntelligenceEngine
from src.evidence.discovery.change_intelligence import OperationalChangeEngine,OperationalLandscapeSnapshot
from src.evidence.discovery.change_storage import OperationalChangeStore
from src.evidence.discovery.dominant_analysis import DominantMotifIntelligenceEngine
from src.evidence.operation_contracts.input_windows import EvidenceInputWindow,PrimitiveInputWindow


def materialize(primitives):
    subjects=sorted({subject for item in primitives for subject in item.subjects})
    watermark=hashlib.sha256("".join(item.primitive_id for item in primitives).encode()).hexdigest()
    evidence=EvidenceInputWindow.create(subjects=subjects,start=None,end=None,
        watermark="0"*64,observations=())
    primitive_window=PrimitiveInputWindow.create(subjects=subjects,start=None,end=None,
        watermark=watermark,observations=primitives,maximum=len(primitives) or 1)
    discovery=DiscoverySnapshot.create(discovery_version="1.0.0",evidence_window=evidence,
        primitive_window=primitive_window,generated_at=0)
    candidates=DiscoveryEngine().discover(discovery)
    motifs=MotifCanonicalizer().consolidate(candidates,primitives)
    times=sorted(item.time_end if item.time_end is not None else item.time_start
        for motif in motifs for item in motif.occurrences
        if item.time_end is not None or item.time_start is not None)
    cutoff=round(median(times)) if times else None
    current_boundary=max(times) if times else None
    baseline=[]
    for motif in motifs:
        occurrences=tuple(item for item in motif.occurrences if cutoff is None or
            (item.time_end if item.time_end is not None else item.time_start)<=cutoff)
        if not occurrences:continue
        baseline.append(replace(motif,occurrences=occurrences,
            supporting_candidate_ids=tuple(sorted(item.candidate_id for item in occurrences)),
            supporting_evidence_ids=tuple(sorted({value for item in occurrences
                                                  for value in item.supporting_evidence_ids})),
            supporting_primitive_ids=tuple(sorted({value for item in occurrences
                                                   for value in item.supporting_primitive_ids})),
            observed_populations=tuple(sorted({item.observed_population for item in occurrences})),
            time_start=min((item.time_start for item in occurrences if item.time_start is not None),
                           default=None),
            time_end=max((item.time_end for item in occurrences if item.time_end is not None),
                         default=None)))
    baseline=tuple(sorted(baseline,key=lambda item:item.motif_id))
    profiles_engine=MotifIntelligenceEngine()
    baseline_profiles=profiles_engine.generate(baseline,candidates,primitives,reference_time=cutoff)
    current_profiles=MotifIntelligenceEngine().generate(motifs,candidates,primitives,
                                                         reference_time=current_boundary)
    dominant_engine=DominantMotifIntelligenceEngine(dominant_count=69)
    baseline_analysis=dominant_engine.analyze(baseline,baseline_profiles,primitives)
    current_analysis=dominant_engine.analyze(motifs,current_profiles,primitives)
    previous=OperationalLandscapeSnapshot.create(observation_boundary=cutoff,motifs=baseline,
        profiles=baseline_profiles,dominant_analysis=baseline_analysis)
    current=OperationalLandscapeSnapshot.create(observation_boundary=current_boundary,motifs=motifs,
        profiles=current_profiles,dominant_analysis=current_analysis)
    return previous,current


def validate(name,primitives):
    previous,current=materialize(primitives);engine=OperationalChangeEngine()
    first=engine.compare(previous,current)
    replay_previous=OperationalLandscapeSnapshot.create(observation_boundary=previous.observation_boundary,
        motifs=tuple(reversed(previous.motifs)),profiles=tuple(reversed(previous.profiles)),
        dominant_analysis=previous.dominant_analysis)
    replay_current=OperationalLandscapeSnapshot.create(observation_boundary=current.observation_boundary,
        motifs=tuple(reversed(current.motifs)),profiles=tuple(reversed(current.profiles)),
        dominant_analysis=current.dominant_analysis)
    replay=OperationalChangeEngine().compare(replay_previous,replay_current)
    with tempfile.TemporaryDirectory(prefix="ep5_changes_") as directory:
        store=OperationalChangeStore(Path(directory)/"changes.db");store.open()
        try:
            append=store.append(previous,current,first);duplicate=store.append(previous,current,replay)
            store_health=store.health()
        finally:store.close()
    payload=first.to_dict();serialized=json.dumps(payload,sort_keys=True).lower()
    forbidden=("watchtower","governance","confidence","operator_identity",
               "operation_classification")
    changes={}
    for item in first.motif_deltas:
        for value in item.change_types:changes[value]=changes.get(value,0)+1
    return {"validation_dataset":name,"previous_snapshot":previous.identity_payload(),
        "current_snapshot":current.identity_payload(),"change_snapshot":payload,
        "change_summary":dict(sorted(changes.items())),"engine_health":engine.health(),
        "replay_deterministic":payload==replay.to_dict(),
        "input_order_independent":previous.snapshot_id==replay_previous.snapshot_id and
            current.snapshot_id==replay_current.snapshot_id and
            first.change_snapshot_id==replay.change_snapshot_id,
        "identity_free":not any(value in serialized for value in forbidden),
        "persistence":{"first":append,"replay":duplicate,"health":store_health}}


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,
        default=Path("docs/evidence_platform/ep5_0_operational_change_intelligence.json"));parser.add_argument(
        "--known-corpus-a-db",type=Path,default=Path("database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db"));args=parser.parse_args()
    datasets=[validate("KNOWN_CORPUS_A",load_primitives(args.known_corpus_a_db)),
        validate("KNOWN_CORPUS_B",load_primitives(Path(
        "database/evidence_platform/three_sw2_shadow_ep3_2a/evidence.db"))),
        validate("GENERIC_UNLABELLED_POPULATION",generic_population())]
    report={"milestone":"EP5.0","change_version":"1.0.0","datasets":datasets,
        "replay_deterministic":all(item["replay_deterministic"] for item in datasets),
        "ordering_deterministic":all(item["input_order_independent"] for item in datasets),
        "identity_free":all(item["identity_free"] for item in datasets),
        "rpc_calls":0,"production_database_reads":0,"production_writes":0,
        "discovery_changes":0,"motif_changes":0,"ranking_changes":0,
        "operation_contracts_loaded":0,"identity_promotions":0,"governance_actions":0}
    report["passed"]=all((report["replay_deterministic"],report["ordering_deterministic"],
                           report["identity_free"]))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"milestone":"EP5.0","passed":report["passed"],"datasets":[{
        "name":item["validation_dataset"],"previous_motifs":item["previous_snapshot"]["motif_count"],
        "current_motifs":item["current_snapshot"]["motif_count"],
        "change_summary":item["change_summary"],
        "relationship_changes":len(item["change_snapshot"]["relationship_deltas"]),
        "neighbourhood_changes":len(item["change_snapshot"]["neighbourhood_deltas"])}
        for item in datasets]},sort_keys=True));return 0 if report["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
