#!/usr/bin/env python3
"""Generate deterministic EP3.2 parity from frozen local comparison inputs."""

from __future__ import annotations

import argparse, hashlib, json, sqlite3
from pathlib import Path

MINTS = (
    "GkXUvai4Hk3XnhKbevAibRygvU5GASzHFNcjJcqpump", "Lt5a2XWZXgFiYrNaqwQzSVT3tPRarT3rQRiyMWPpump",
    "SPof24S7YxtfVBo9hN8utCf47c6j4fgVQfod2xmpump", "Y5bGdNx6BFDdRYCHuxZ6EyFuqxr4xS535KjiVE8pump",
    "YDxw5V4rMYzDFomPaxGbUSBUdiNgBJikGBPbBW2pump", "dDqcg6kAfrJ39D3uKDRaRaAugbZav5efevKPCnmpump",
    "hYDJmMxa3CrPmXzaDyatVoRQxZ3zJTPuvLNBQnWpump", "iUXa5BUbZ4EY3BReD4EFYU1eC75eVHzu8L9H1ZVpump",
    "kYkR6zZvgo7vpptX1F2eXgojzmRACox3be2upKupump", "qDKdQJT4WeLoAcdrTybWyP4N966XCaRBkjXm1Cxpump",
    "uxDBFdzJbmZkthhUQYFQ6unSfMAfdKxSuoKq8gCpump", "wAQpxAZRSspX3xG7RKDXZoPqKBoc67qnD7jtm5gpump",
    "wiW7UmNiE2Aud3GxmHtCN8UXfrFu1ombuyDyyYMpump",
)

def main():
    p=argparse.ArgumentParser(); p.add_argument("--db",type=Path,default=Path("database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db")); p.add_argument("--output",type=Path,default=Path("docs/evidence_platform/ep3_2_three_sw2_parity.json")); a=p.parse_args()
    c=sqlite3.connect(a.db); counts={}; covered=set()
    for typ,subjects,payload in c.execute("SELECT primitive_type,subjects_json,output_payload_json FROM primitive_observations"):
        subject_set=set(json.loads(subjects)); body=json.loads(payload)
        hits=subject_set.intersection(MINTS)
        if body.get("mint") in MINTS: hits.add(body["mint"])
        if hits: counts[typ]=counts.get(typ,0)+1; covered.update(hits)
    areas=("canonical controller","historical launches","activation transfers","launch signers","creator freshness","economic funding","topology","dormancy","explicit exclusions","known non-members")
    differences=[{"area":area,"classification":"Missing Evidence","detail":"No immutable 3SW2 comparison observations are materialized in the available shadow corpus."} for area in areas]
    report={"milestone":"EP3.2","authority":"SHADOW_ONLY","baseline":{"controller":"3SW2zquY2mVTbNuw1ZCGgtoehq2evfU36PFd6TTqSXdK","historical_launches":13,"launch_mints":list(MINTS)},"shadow":{"covered_launches":len(covered),"missing_launches":13-len(covered),"primitive_counts":counts},"parity":{"status":"BLOCKED_BY_MISSING_EVIDENCE","differences":differences,"unexplained_differences":0},"health":{"contract_version":"1.0.0","detector_version":"1.0.0","evaluations":0,"replay":"DETERMINISTIC_SYNTHETIC_VALIDATION","shadow_health":"MISSING_CORPUS","evaluation_latency_ms":None},"invariants":{"runtime_modified":False,"watchtower_modified":False,"production_authority_changed":False,"governance_executed":False,"rpc_performed":False}}
    report["report_digest"]=hashlib.sha256(json.dumps(report,sort_keys=True,separators=(",",":")).encode()).hexdigest(); a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n"); print(json.dumps(report,sort_keys=True))

if __name__ == "__main__": main()
