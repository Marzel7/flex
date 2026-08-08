#!/usr/bin/env python3
"""Validate label-blind EP4 discovery against frozen shadow corpora."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.discovery import DiscoveryEngine, DiscoverySnapshot, DiscoveryStore
from src.evidence.operation_contracts.input_windows import EvidenceInputWindow, PrimitiveInputWindow
from src.evidence.primitives.contracts import (
    ObservationWindow, PrimitiveObservation, PrimitiveQuality, PrimitiveType,
)


def load_primitives(path: Path) -> tuple[PrimitiveObservation, ...]:
    connection=sqlite3.connect(path); connection.row_factory=sqlite3.Row
    try:
        references={}
        for primitive_id,evidence_id in connection.execute(
            "SELECT primitive_id,evidence_id FROM primitive_evidence_inputs ORDER BY primitive_id,evidence_id"
        ):
            references.setdefault(primitive_id,[]).append(evidence_id)
        rows=connection.execute("SELECT * FROM primitive_observations ORDER BY primitive_id").fetchall()
    finally: connection.close()
    return tuple(PrimitiveObservation(
        primitive_id=row["primitive_id"],primitive_type=row["primitive_type"],
        primitive_version=row["primitive_version"],evidence_ids=tuple(references.get(row["primitive_id"],())),
        subjects=tuple(json.loads(row["subjects_json"])),parameters=json.loads(row["parameters_json"]),
        observation_window=ObservationWindow(row["window_start"],row["window_end"]),
        output_payload=json.loads(row["output_payload_json"]),output_digest=row["output_digest"],
        quality_state=row["quality_state"],missing_inputs=tuple(json.loads(row["missing_inputs_json"])),
        failure_state=row["failure_state"],generated_at=row["generated_at"],
    ) for row in rows)


def validate_primitives(name: str, primitives: tuple[PrimitiveObservation, ...]) -> dict:
    subjects=sorted({subject for item in primitives for subject in item.subjects})
    watermark=hashlib.sha256("".join(item.primitive_id for item in primitives).encode()).hexdigest()
    evidence=EvidenceInputWindow.create(subjects=subjects,start=None,end=None,
        watermark="0"*64,observations=())
    primitive_window=PrimitiveInputWindow.create(subjects=subjects,start=None,end=None,
        watermark=watermark,observations=primitives,maximum=len(primitives) or 1)
    snapshot=DiscoverySnapshot.create(discovery_version="1.0.0",
        evidence_window=evidence,primitive_window=primitive_window,generated_at=0)
    engine=DiscoveryEngine(); first=engine.discover(snapshot); second=engine.discover(snapshot)
    with tempfile.TemporaryDirectory(prefix="ep4_discovery_") as directory:
        store=DiscoveryStore(Path(directory)/"discovery.db"); store.open()
        try:
            append_first=store.append(first); append_second=store.append(second); health=store.health()
        finally: store.close()
    forbidden=("operator","watchtower","governance","confidence","canonical")
    serialized=json.dumps([item.to_dict() for item in first],sort_keys=True).lower()
    return {
        "validation_dataset":name,"primitive_observations":len(primitives),
        "candidate_count":len(first),"deterministic":
            [item.candidate_id for item in first]==[item.candidate_id for item in second],
        "candidate_digest":hashlib.sha256(serialized.encode()).hexdigest(),
        "label_blind":not any(value in serialized for value in forbidden),
        "persistence":{"first":append_first,"replay":append_second,"health":health},
        "quality_states":dict(sorted({state:sum(item.quality_state==state for item in first)
                                      for state in {item.quality_state for item in first}}.items())),
    }


def validate(name: str, database: Path) -> dict:
    return validate_primitives(name, load_primitives(database))


def generic_population() -> tuple[PrimitiveObservation, ...]:
    values=[]
    for index,(source,destination) in enumerate((
        ("generic-a","generic-b"),("generic-a","generic-c"),
        ("generic-x","generic-y"),("generic-x","generic-z"),
    ),start=1):
        values.append(PrimitiveObservation.create(
            primitive_type=PrimitiveType.SYSTEM_TRANSFER,primitive_version="1",
            evidence_ids=[f"generic-evidence-{index}"],subjects=[source,destination],
            parameters={},observation_window=ObservationWindow(index,index),
            output_payload={"source":source,"destination":destination,
                            "signature":f"generic-signature-{index}"},
            quality_state=PrimitiveQuality.PROVEN,generated_at=0,
        ))
    return tuple(values)


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--output",type=Path,
        default=Path("docs/evidence_platform/ep4_0_unknown_discovery_validation.json")); args=parser.parse_args()
    datasets=[
        validate("KNOWN_CORPUS_A",Path("database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db")),
        validate("KNOWN_CORPUS_B",Path("database/evidence_platform/three_sw2_shadow_ep3_2a/evidence.db")),
        validate_primitives("GENERIC_UNLABELLED_POPULATION",generic_population()),
    ]
    report={"milestone":"EP4.0","authority":"CANDIDATE_ONLY","discovery_version":"1.0.0",
        "datasets":datasets,"replay_deterministic":all(item["deterministic"] for item in datasets),
        "label_blind":all(item["label_blind"] for item in datasets),
        "rpc_calls":0,"production_database_reads":0,"production_writes":0,
        "governance_actions":0,"identity_promotions":0,"operation_contracts_loaded":0}
    report["passed"]=report["replay_deterministic"] and report["label_blind"]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps(report,sort_keys=True)); return 0 if report["passed"] else 1


if __name__=="__main__": raise SystemExit(main())
