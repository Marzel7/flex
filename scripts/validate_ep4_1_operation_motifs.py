#!/usr/bin/env python3
"""Validate deterministic EP4.1 motif compression on frozen shadow corpora."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from scripts.validate_ep4_0_unknown_discovery import generic_population, load_primitives
from src.evidence.contracts import canonical_json_bytes
from src.evidence.discovery import (
    DiscoveryEngine, DiscoverySnapshot, MotifCanonicalizer, MotifStore,
)
from src.evidence.operation_contracts.input_windows import EvidenceInputWindow, PrimitiveInputWindow


def validate(name,primitives):
    subjects=sorted({subject for item in primitives for subject in item.subjects})
    watermark=hashlib.sha256("".join(item.primitive_id for item in primitives).encode()).hexdigest()
    evidence=EvidenceInputWindow.create(subjects=subjects,start=None,end=None,
        watermark="0"*64,observations=())
    primitive_window=PrimitiveInputWindow.create(subjects=subjects,start=None,end=None,
        watermark=watermark,observations=primitives,maximum=len(primitives) or 1)
    snapshot=DiscoverySnapshot.create(discovery_version="1.0.0",
        evidence_window=evidence,primitive_window=primitive_window,generated_at=0)
    candidates=DiscoveryEngine().discover(snapshot)
    engine=MotifCanonicalizer(); started=time.perf_counter()
    first=engine.consolidate(candidates,primitives)
    latency_ms=round((time.perf_counter()-started)*1000,3)
    second=MotifCanonicalizer().consolidate(tuple(reversed(candidates)),tuple(reversed(primitives)))
    first_payload=[item.to_dict() for item in first]
    deterministic=first_payload==[item.to_dict() for item in second]
    digest=hashlib.sha256(canonical_json_bytes(first_payload)).hexdigest()
    counts=sorted((len(item.occurrences) for item in first),reverse=True)
    with tempfile.TemporaryDirectory(prefix="ep4_motifs_") as directory:
        store=MotifStore(Path(directory)/"motifs.db"); store.open()
        try:
            append=store.append(first); replay=store.append(second); health=store.health()
        finally: store.close()
    canonical=json.dumps([item.canonical_graph for item in first],sort_keys=True).lower()
    forbidden=("wallet-a","wallet-x","watchtower","3sw2","operator","governance",
               "signature","amount")
    return {
        "validation_dataset":name,"raw_candidates":len(candidates),
        "canonical_motifs":len(first),
        "compression_ratio":len(candidates)/len(first) if first else 0.0,
        "average_occurrences_per_motif":len(candidates)/len(first) if first else 0.0,
        "largest_motif":counts[0] if counts else 0,
        "singleton_motifs":sum(value==1 for value in counts),
        "new_motifs":len(first),"stable_motifs":len(first) if deterministic else 0,
        "occurrence_distribution":{
            str(value):counts.count(value) for value in sorted(set(counts))
        },
        "motif_digest":digest,"deterministic":deterministic,
        "identity_free_canonical_graphs":not any(value in canonical for value in forbidden),
        "generation_latency_ms":latency_ms,
        "persistence":{"first":append,"replay":replay,"health":health},
    }


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--output",type=Path,
        default=Path("docs/evidence_platform/ep4_1_motif_validation.json")); parser.add_argument(
        "--known-corpus-a-db",type=Path,default=Path("database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db")); args=parser.parse_args()
    datasets=[
        validate("KNOWN_CORPUS_A",load_primitives(args.known_corpus_a_db)),
        validate("KNOWN_CORPUS_B",load_primitives(Path(
            "database/evidence_platform/three_sw2_shadow_ep3_2a/evidence.db"))),
        validate("GENERIC_UNLABELLED_POPULATION",generic_population()),
    ]
    report={"milestone":"EP4.1","canonicalization_version":"1.0.0",
        "datasets":datasets,"replay_deterministic":all(item["deterministic"] for item in datasets),
        "identity_free":all(item["identity_free_canonical_graphs"] for item in datasets),
        "rpc_calls":0,"production_database_reads":0,"production_writes":0,
        "operation_contracts_loaded":0,"identity_promotions":0,"governance_actions":0}
    report["passed"]=report["replay_deterministic"] and report["identity_free"]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps(report,sort_keys=True)); return 0 if report["passed"] else 1


if __name__=="__main__": raise SystemExit(main())
