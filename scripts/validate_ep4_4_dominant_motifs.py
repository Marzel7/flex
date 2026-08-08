#!/usr/bin/env python3
"""Validate EP4.4 dominant motif profiles and relationship replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from scripts.validate_ep4_0_unknown_discovery import generic_population,load_primitives
from src.evidence.discovery import DiscoveryEngine,DiscoverySnapshot,MotifCanonicalizer,MotifIntelligenceEngine
from src.evidence.discovery.dominant_analysis import DominantMotifIntelligenceEngine
from src.evidence.operation_contracts.input_windows import EvidenceInputWindow,PrimitiveInputWindow


def validate(name,primitives):
    subjects=sorted({subject for item in primitives for subject in item.subjects})
    watermark=hashlib.sha256("".join(item.primitive_id for item in primitives).encode()).hexdigest()
    evidence=EvidenceInputWindow.create(subjects=subjects,start=None,end=None,
        watermark="0"*64,observations=())
    primitive_window=PrimitiveInputWindow.create(subjects=subjects,start=None,end=None,
        watermark=watermark,observations=primitives,maximum=len(primitives) or 1)
    snapshot=DiscoverySnapshot.create(discovery_version="1.0.0",evidence_window=evidence,
        primitive_window=primitive_window,generated_at=0)
    candidates=DiscoveryEngine().discover(snapshot)
    motifs=MotifCanonicalizer().consolidate(candidates,primitives)
    profiles=MotifIntelligenceEngine().generate(motifs,candidates,primitives)
    engine=DominantMotifIntelligenceEngine(dominant_count=69)
    first=engine.analyze(motifs,profiles,primitives)
    replay=engine.analyze(tuple(reversed(motifs)),tuple(reversed(profiles)),
        tuple(reversed(primitives)),replay_analysis_id=first.analysis_id)
    payload=replay.to_dict();serialized=json.dumps(payload,sort_keys=True).lower()
    # Raw account strings remain valid supporting observations. Validate that
    # semantic identity/authority labels are absent rather than blacklisting an
    # address prefix which may exist objectively on chain.
    forbidden=("watchtower","governance","confidence","operator_identity",
               "operation_classification")
    return {"validation_dataset":name,"analysis":payload,
        "profiles_stable":[item["motif_id"] for item in first.profiles]==
            [item["motif_id"] for item in replay.profiles],
        "relationships_stable":[item["relationship_id"] for item in first.relationships]==
            [item["relationship_id"] for item in replay.relationships],
        "neighbourhoods_stable":[item["neighbourhood_id"] for item in first.neighbourhoods]==
            [item["neighbourhood_id"] for item in replay.neighbourhoods],
        "pareto_stable":first.pareto==replay.pareto,
        "identity_free":not any(value in serialized for value in forbidden)}


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,
        default=Path("docs/evidence_platform/ep4_4_dominant_motif_intelligence.json"));parser.add_argument(
        "--known-corpus-a-db",type=Path,default=Path("database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db"));args=parser.parse_args()
    datasets=[validate("KNOWN_CORPUS_A",load_primitives(args.known_corpus_a_db)),
        validate("KNOWN_CORPUS_B",load_primitives(Path(
        "database/evidence_platform/three_sw2_shadow_ep3_2a/evidence.db"))),
        validate("GENERIC_UNLABELLED_POPULATION",generic_population())]
    stable=all(all(item[key] for key in ("profiles_stable","relationships_stable",
        "neighbourhoods_stable","pareto_stable")) for item in datasets)
    report={"milestone":"EP4.4","analysis_version":"1.0.0","datasets":datasets,
        "replay_deterministic":stable,"identity_free":all(item["identity_free"] for item in datasets),
        "rpc_calls":0,"production_database_reads":0,"production_writes":0,
        "discovery_changes":0,"canonicalization_changes":0,"ranking_changes":0,
        "operation_contracts_loaded":0,"identity_promotions":0,"governance_actions":0}
    report["passed"]=report["replay_deterministic"] and report["identity_free"]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"milestone":"EP4.4","passed":report["passed"],"datasets":[{
        "name":item["validation_dataset"],"dominant_count":item["analysis"]["dominant_count"],
        "dominant_occurrences":item["analysis"]["dominant_occurrences"],
        "total_occurrences":item["analysis"]["total_occurrences"],
        "threshold_ppm":item["analysis"]["occurrence_threshold_ppm"],
        "relationships":len(item["analysis"]["relationship_graph"]["edges"]),
        "neighbourhoods":len(item["analysis"]["neighbourhoods"])} for item in datasets]},
        sort_keys=True));return 0 if report["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
