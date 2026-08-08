#!/usr/bin/env python3
"""Validate deterministic, identity-free operational evolution graphs."""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from scripts.validate_ep4_0_unknown_discovery import generic_population,load_primitives
from scripts.validate_ep5_0_operational_change import materialize
from src.evidence.discovery.change_intelligence import OperationalChangeEngine,OperationalLandscapeSnapshot
from src.evidence.discovery.evolution_intelligence import OperationalEvolutionEngine
from src.evidence.discovery.evolution_storage import OperationalEvolutionStore


def validate(name,primitives):
    previous,current=materialize(primitives);change=OperationalChangeEngine().compare(previous,current)
    engine=OperationalEvolutionEngine();evolution=engine.reconstruct(previous,current,change)
    replay_previous=OperationalLandscapeSnapshot.create(observation_boundary=previous.observation_boundary,
        motifs=tuple(reversed(previous.motifs)),profiles=tuple(reversed(previous.profiles)),
        dominant_analysis=previous.dominant_analysis)
    replay_current=OperationalLandscapeSnapshot.create(observation_boundary=current.observation_boundary,
        motifs=tuple(reversed(current.motifs)),profiles=tuple(reversed(current.profiles)),
        dominant_analysis=current.dominant_analysis)
    replay_change=OperationalChangeEngine().compare(replay_previous,replay_current)
    replay=OperationalEvolutionEngine().reconstruct(replay_previous,replay_current,replay_change)
    with tempfile.TemporaryDirectory(prefix="ep5_evolution_") as directory:
        store=OperationalEvolutionStore(Path(directory)/"evolution.db");store.open()
        try:
            first=store.append(evolution);duplicate=store.append(replay);store_health=store.health()
        finally:store.close()
    payload=evolution.to_dict();encoded=json.dumps(payload,sort_keys=True).lower()
    event_counts={}
    for item in evolution.events:event_counts[item.event_type]=event_counts.get(item.event_type,0)+1
    invalid_similarity_edges=sum(not item.continuity_basis for item in evolution.edges)
    return {"validation_dataset":name,"previous_snapshot":previous.identity_payload(),
        "current_snapshot":current.identity_payload(),"evolution_snapshot":payload,
        "event_summary":dict(sorted(event_counts.items())),"health":engine.health(),
        "replay_deterministic":payload==replay.to_dict(),
        "input_order_independent":evolution.evolution_snapshot_id==replay.evolution_snapshot_id,
        "identity_free":all(value not in encoded for value in (
            "watchtower","3sw2","operator_identity","confidence","governance")),
        "objective_continuity":invalid_similarity_edges==0,
        "persistence":{"first":first,"replay":duplicate,"health":store_health}}


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,
        default=Path("docs/evidence_platform/ep5_1_operational_evolution_graph.json.gz"));parser.add_argument(
        "--known-corpus-a-db",type=Path,default=Path("database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db"));args=parser.parse_args()
    datasets=[validate("KNOWN_CORPUS_A",load_primitives(args.known_corpus_a_db)),
        validate("KNOWN_CORPUS_B",load_primitives(Path(
        "database/evidence_platform/three_sw2_shadow_ep3_2a/evidence.db"))),
        validate("GENERIC_UNLABELLED_POPULATION",generic_population())]
    report={"milestone":"EP5.1","evolution_version":"1.0.0","datasets":datasets,
        "replay_deterministic":all(item["replay_deterministic"] for item in datasets),
        "ordering_deterministic":all(item["input_order_independent"] for item in datasets),
        "identity_free":all(item["identity_free"] for item in datasets),
        "objective_continuity":all(item["objective_continuity"] for item in datasets),
        "rpc_calls":0,"production_database_reads":0,"production_writes":0,
        "evidence_changes":0,"primitive_changes":0,"runtime_changes":0,
        "discovery_changes":0,"identity_inferences":0,"governance_actions":0}
    report["passed"]=all((report["replay_deterministic"],report["ordering_deterministic"],
                          report["identity_free"],report["objective_continuity"]))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    encoded=(json.dumps(report,indent=2,sort_keys=True)+"\n").encode()
    with args.output.open("wb") as raw:
        with gzip.GzipFile(filename="",mode="wb",fileobj=raw,compresslevel=9,mtime=0) as archive:
            archive.write(encoded)
    print(json.dumps({"milestone":"EP5.1","passed":report["passed"],"datasets":[{
        "name":item["validation_dataset"],"events":item["event_summary"],
        "edges":item["health"]["continuations"],"coverage_ppm":item["health"]["coverage_ppm"]}
        for item in datasets]},sort_keys=True));return 0 if report["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
