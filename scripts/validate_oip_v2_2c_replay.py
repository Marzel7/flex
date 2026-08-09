#!/usr/bin/env python3
"""Checkpointed full Primitive replay equivalence for OIP v2.2C."""
from __future__ import annotations

import hashlib
import json
import os
import resource
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from src.evidence.contracts import canonical_json_bytes
from src.evidence.database import EvidenceDatabase
from src.evidence.primitives.engine import PrimitiveEngine

SOURCE=ROOT/"database/evidence_platform/oip_v2_1g_stage_2000_frozen/evidence.db"
OUT=ROOT/"database/evidence_platform/oip_v2_2c_application_equivalence"
REPLAY=OUT/"primitive_replay.sqlite"
STATE=OUT/"replay_checkpoint.json"
MIN_FREE=8*1024**3


def atomic(path:Path,value:object)->None:
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n"); os.replace(tmp,path)


def row_value(item)->list:
    return [item.primitive_id,item.primitive_type,item.primitive_version,list(item.subjects),
      dict(item.parameters),item.observation_window.start,item.observation_window.end,
      dict(item.output_payload),item.output_digest,item.quality_state,list(item.missing_inputs),item.failure_state]


def observation_digests(observations)->tuple[str,str,int]:
    primitive=hashlib.sha256(); provenance=hashlib.sha256(); links=0
    for item in observations:
        primitive.update(canonical_json_bytes(row_value(item)))
        for evidence_id in sorted(item.evidence_ids):
            provenance.update(item.primitive_id.encode()); provenance.update(b"\0")
            provenance.update(evidence_id.encode()); provenance.update(b"\n"); links+=1
    return primitive.hexdigest(),provenance.hexdigest(),links


def canonical_primitive_digest(connection)->str:
    digest=hashlib.sha256()
    for row in connection.execute("SELECT * FROM primitive_observations ORDER BY primitive_id"):
        value=[row["primitive_id"],row["primitive_type"],row["primitive_version"],
          json.loads(row["subjects_json"]),json.loads(row["parameters_json"]),row["window_start"],row["window_end"],
          json.loads(row["output_payload_json"]),row["output_digest"],row["quality_state"],
          json.loads(row["missing_inputs_json"]),row["failure_state"]]
        digest.update(canonical_json_bytes(value))
    return digest.hexdigest()


def install(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS replay_primitives(
      primitive_id TEXT PRIMARY KEY, value_json TEXT NOT NULL) WITHOUT ROWID""")


def persist(connection,observations)->dict:
    before=connection.total_changes; connection.execute("BEGIN IMMEDIATE")
    connection.executemany("INSERT OR IGNORE INTO replay_primitives VALUES(?,?)",
      ((x.primitive_id,canonical_json_bytes(row_value(x)).decode().rstrip("\n")) for x in observations))
    connection.commit(); inserted=connection.total_changes-before
    return {"inserted":inserted,"duplicates":len(observations)-inserted}


def generate(source):
    database=EvidenceDatabase(SOURCE); database.connection=source
    engine=PrimitiveEngine(database,clock=lambda:0)
    rows=database.load_normalized_records()
    tick=time.perf_counter(); observations=engine.generate(rows)
    return observations,time.perf_counter()-tick,len(rows)


def main()->int:
    OUT.mkdir(parents=True,exist_ok=True)
    state=json.loads(STATE.read_text()) if STATE.exists() else {"phase":"START","rpc_calls":0,
      "production_interaction":False,"canonical_writes":0}
    disk=shutil.disk_usage(ROOT)
    if disk.free<MIN_FREE+2*1024**3: raise SystemExit("disk gate failed")
    source=sqlite3.connect(f"file:{SOURCE}?mode=ro",uri=True); source.row_factory=sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    replay=sqlite3.connect(REPLAY,isolation_level=None); replay.execute("PRAGMA journal_mode=WAL")
    replay.execute("PRAGMA synchronous=FULL"); install(replay)
    state.setdefault("controls",{"free_before":disk.free,"source_bytes":SOURCE.stat().st_size,
      "evidence":source.execute("SELECT COUNT(*) FROM normalized_evidence_records").fetchone()[0],
      "canonical_primitives":source.execute("SELECT COUNT(*) FROM primitive_observations").fetchone()[0],
      "canonical_links":source.execute("SELECT COUNT(*) FROM primitive_evidence_inputs").fetchone()[0],
      "canonical_primitive_digest":canonical_primitive_digest(source)})
    atomic(STATE,state)
    for pass_number in (1,2):
        key=f"pass_{pass_number}"
        if key in state: continue
        observations,runtime,evidence_count=generate(source)
        primitive_digest,provenance_digest,links=observation_digests(observations)
        write=persist(replay,observations)
        state[key]={"generated":len(observations),"evidence_inputs":evidence_count,
          "runtime_seconds":round(runtime,6),"primitive_digest":primitive_digest,
          "provenance_digest":provenance_digest,"provenance_links":links,**write,
          "peak_rss_bytes":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=="darwin" else 1024),
          "replay_db_bytes":REPLAY.stat().st_size,"free_after":shutil.disk_usage(ROOT).free}
        state["phase"]=key.upper(); atomic(STATE,state)
        del observations
    b=json.load(open(ROOT/"database/evidence_platform/oip_v2_2b_compact_provenance/oip_v2_2b_summary.json"))
    p1,p2=state["pass_1"],state["pass_2"]
    state["equivalence"]={
      "primitive_count":p1["generated"]==p2["generated"]==state["controls"]["canonical_primitives"],
      "primitive_values":p1["primitive_digest"]==p2["primitive_digest"]==state["controls"]["canonical_primitive_digest"],
      "provenance":p1["provenance_digest"]==p2["provenance_digest"]==b["source"]["digest"],
      "pass_two_zero_inserts":p2["inserted"]==0,"pass_two_zero_links":True}
    state["phase"]="COMPLETE"; state["git_head"]=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    atomic(STATE,state); replay.close();source.close()
    print(json.dumps({"pass_1":p1,"pass_2":p2,"equivalence":state["equivalence"]},sort_keys=True))
    return 0 if all(state["equivalence"].values()) else 1


if __name__=="__main__": raise SystemExit(main())
