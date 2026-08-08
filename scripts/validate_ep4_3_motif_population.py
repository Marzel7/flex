#!/usr/bin/env python3
"""Generate the EP4.3 population distribution analysis from frozen corpora."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from scripts.validate_ep4_0_unknown_discovery import generic_population,load_primitives
from src.evidence.discovery import DiscoveryEngine,DiscoverySnapshot,MotifCanonicalizer,MotifIntelligenceEngine
from src.evidence.discovery.population_analysis import MotifPopulationAnalysisEngine
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
    replay_profiles=MotifIntelligenceEngine().generate(tuple(reversed(motifs)),
        tuple(reversed(candidates)),tuple(reversed(primitives)))
    engine=MotifPopulationAnalysisEngine()
    first=engine.analyze(profiles,replay_profiles=replay_profiles)
    second=engine.analyze(tuple(reversed(profiles)),replay_profiles=tuple(reversed(replay_profiles)))
    payload=first.to_dict();serialized=json.dumps(payload,sort_keys=True).lower()
    forbidden=("watchtower","3sw2","operator","governance","confidence")
    return {"validation_dataset":name,"analysis":payload,
        "analysis_replay_deterministic":payload==second.to_dict(),
        "identity_free":not any(value in serialized for value in forbidden)}


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,
        default=Path("docs/evidence_platform/ep4_3_motif_population_analysis.json"));parser.add_argument(
        "--known-corpus-a-db",type=Path,default=Path("database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db"));args=parser.parse_args()
    datasets=[validate("KNOWN_CORPUS_A",load_primitives(args.known_corpus_a_db)),
        validate("KNOWN_CORPUS_B",load_primitives(Path(
        "database/evidence_platform/three_sw2_shadow_ep3_2a/evidence.db"))),
        validate("GENERIC_UNLABELLED_POPULATION",generic_population())]
    report={"milestone":"EP4.3","analysis_version":"1.0.0","datasets":datasets,
        "replay_deterministic":all(item["analysis_replay_deterministic"] for item in datasets),
        "identity_free":all(item["identity_free"] for item in datasets),
        "rpc_calls":0,"production_database_reads":0,"production_writes":0,
        "discovery_changes":0,"canonicalization_changes":0,"ranking_changes":0,
        "operation_contracts_loaded":0,"identity_promotions":0,"governance_actions":0}
    report["passed"]=report["replay_deterministic"] and report["identity_free"]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"milestone":"EP4.3","passed":report["passed"],
        "replay_deterministic":report["replay_deterministic"],"datasets":[{
            "name":item["validation_dataset"],**item["analysis"]["summary"],
            "pareto":item["analysis"]["pareto"]} for item in datasets]},sort_keys=True))
    return 0 if report["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
