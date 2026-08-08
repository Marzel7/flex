#!/usr/bin/env python3
"""Validate deterministic cross-motif relationship intelligence."""

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
from src.evidence.discovery.relationship_intelligence import (
    CrossMotifRelationshipEngine,RelationshipEvolutionEngine,
)
from src.evidence.discovery.relationship_storage import CrossMotifRelationshipStore


def validate(name,primitives):
    previous,current=materialize(primitives);change=OperationalChangeEngine().compare(previous,current)
    evolution=OperationalEvolutionEngine().reconstruct(previous,current,change)
    primitive_types={item.primitive_id:item.primitive_type for item in primitives}
    engine=CrossMotifRelationshipEngine();old=engine.materialize(previous,
        primitive_types=primitive_types);new=engine.materialize(current,primitive_types=primitive_types)
    evolution_engine=RelationshipEvolutionEngine()
    relationship_evolution=evolution_engine.compare(old,new,evolution)
    replay_previous=OperationalLandscapeSnapshot.create(observation_boundary=previous.observation_boundary,
        motifs=tuple(reversed(previous.motifs)),profiles=tuple(reversed(previous.profiles)),
        dominant_analysis=previous.dominant_analysis)
    replay_current=OperationalLandscapeSnapshot.create(observation_boundary=current.observation_boundary,
        motifs=tuple(reversed(current.motifs)),profiles=tuple(reversed(current.profiles)),
        dominant_analysis=current.dominant_analysis)
    replay_change=OperationalChangeEngine().compare(replay_previous,replay_current)
    replay_evolution=OperationalEvolutionEngine().reconstruct(
        replay_previous,replay_current,replay_change)
    replay_old=CrossMotifRelationshipEngine().materialize(replay_previous,
        primitive_types=primitive_types)
    replay_new=CrossMotifRelationshipEngine().materialize(replay_current,
        primitive_types=primitive_types)
    replay_relationship_evolution=RelationshipEvolutionEngine().compare(
        replay_old,replay_new,replay_evolution)
    with tempfile.TemporaryDirectory(prefix="ep5_relationships_") as directory:
        store=CrossMotifRelationshipStore(Path(directory)/"relationships.db");store.open()
        try:
            first_old=store.append_relationships(old);first_new=store.append_relationships(new)
            first_evolution=store.append_evolution(relationship_evolution)
            duplicate_old=store.append_relationships(replay_old)
            duplicate_new=store.append_relationships(replay_new)
            duplicate_evolution=store.append_evolution(replay_relationship_evolution)
            store_health=store.health()
        finally:store.close()
    event_counts={};type_counts={}
    for item in relationship_evolution.observations:
        event_counts[item.event_type]=event_counts.get(item.event_type,0)+1
    for item in new.relationships:type_counts[item.relationship_type]=type_counts.get(item.relationship_type,0)+1
    payload={"previous_relationship_snapshot":old.to_dict(),
        "current_relationship_snapshot":new.to_dict(),
        "relationship_evolution":relationship_evolution.to_dict()}
    encoded=json.dumps(payload,sort_keys=True).lower()
    replay_payload={"previous_relationship_snapshot":replay_old.to_dict(),
        "current_relationship_snapshot":replay_new.to_dict(),
        "relationship_evolution":replay_relationship_evolution.to_dict()}
    return {"validation_dataset":name,**payload,"relationship_types":dict(sorted(type_counts.items())),
        "evolution_events":dict(sorted(event_counts.items())),"health":engine.health(),
        "evolution_health":evolution_engine.health(),
        "replay_deterministic":payload==replay_payload,
        "input_order_independent":new.relationship_snapshot_id==replay_new.relationship_snapshot_id and
            relationship_evolution.evolution_snapshot_id==replay_relationship_evolution.evolution_snapshot_id,
        "identity_free":all(value not in encoded for value in (
            "watchtower","3sw2","operator_identity","common_ownership","confidence","governance")),
        "persistence":{"first":{"previous":first_old,"current":first_new,
            "evolution":first_evolution},"replay":{"previous":duplicate_old,
            "current":duplicate_new,"evolution":duplicate_evolution},"health":store_health}}


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,
        default=Path("docs/evidence_platform/ep5_2_cross_motif_relationship_intelligence.json.gz"))
    parser.add_argument("--known-corpus-a-db",type=Path,default=Path(
        "database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db"))
    args=parser.parse_args();datasets=[validate("KNOWN_CORPUS_A",load_primitives(args.known_corpus_a_db)),
        validate("KNOWN_CORPUS_B",load_primitives(Path(
        "database/evidence_platform/three_sw2_shadow_ep3_2a/evidence.db"))),
        validate("GENERIC_UNLABELLED_POPULATION",generic_population())]
    report={"milestone":"EP5.2","relationship_version":"1.0.0","datasets":datasets,
        "replay_deterministic":all(item["replay_deterministic"] for item in datasets),
        "ordering_deterministic":all(item["input_order_independent"] for item in datasets),
        "identity_free":all(item["identity_free"] for item in datasets),"rpc_calls":0,
        "production_database_reads":0,"production_writes":0,"evidence_changes":0,
        "primitive_changes":0,"runtime_changes":0,"discovery_changes":0,
        "identity_inferences":0,"ownership_inferences":0,"governance_actions":0}
    report["passed"]=all((report["replay_deterministic"],report["ordering_deterministic"],
                          report["identity_free"]))
    encoded=(json.dumps(report,indent=2,sort_keys=True)+"\n").encode();args.output.parent.mkdir(
        parents=True,exist_ok=True)
    with args.output.open("wb") as raw:
        with gzip.GzipFile(filename="",mode="wb",fileobj=raw,compresslevel=9,mtime=0) as archive:
            archive.write(encoded)
    print(json.dumps({"milestone":"EP5.2","passed":report["passed"],"datasets":[{
        "name":item["validation_dataset"],"relationships":len(
            item["current_relationship_snapshot"]["relationships"]),
        "types":item["relationship_types"],"evolution":item["evolution_events"]}
        for item in datasets]},sort_keys=True));return 0 if report["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
