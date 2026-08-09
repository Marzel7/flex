#!/usr/bin/env python3
"""Checkpoint-visible scan for canonical-only Primitive IDs in relationship output."""
import gzip,json,sqlite3,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/"database/evidence_platform/oip_v2_2c1_divergence_audit/analysis.sqlite"
REPORT=ROOT/"database/evidence_platform/oip_v2_1g_stage_2000_frozen/reports/relationships.json.gz"
CHECKPOINT=ROOT/"database/evidence_platform/oip_v2_2c1_divergence_audit/relationship_scan.json"

def main():
    db=sqlite3.connect(DB)
    wanted={r[0] for r in db.execute("SELECT primitive_id FROM canonical_only WHERE discovery_participant=1")}
    found=set();lines=0;started=time.perf_counter()
    with gzip.open(REPORT,"rt") as handle:
        for line in handle:
            lines+=1;value=line.strip().rstrip(",").strip('"')
            if value in wanted:found.add(value)
            if lines%1_000_000==0:
                CHECKPOINT.write_text(json.dumps({"complete":False,"lines":lines,"found":len(found),
                  "elapsed_seconds":round(time.perf_counter()-started,3)},sort_keys=True)+"\n")
                print(json.dumps({"lines":lines,"found":len(found)}),flush=True)
    db.executemany("UPDATE canonical_only SET relationship_participant=1 WHERE primitive_id=?",((x,) for x in found));db.commit();db.close()
    result={"complete":True,"lines":lines,"candidate_ids":len(wanted),"relationship_participants":len(found),
      "elapsed_seconds":round(time.perf_counter()-started,3)}
    CHECKPOINT.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n");print(json.dumps(result));return 0

if __name__=="__main__":raise SystemExit(main())
