"""Test-only independent generic Living writer (Actor B)."""
from __future__ import annotations
import sqlite3, threading, time, statistics
from tests.test_generic_living_multi_member_fixture import build_fixture, members
from src.ops import living_potential_operations as living
from src.ops.generic_living_pipeline_v2 import compute_generic_living_assessment
from src.ops.generic_living_active_components import publish_generic_assessment_atomic

def _result(spec, mint, n):
 return compute_generic_living_assessment(spec,{"members":[mint],"funders":["f"+mint],"highwaters":{"queue":n,"edges":n,"atomic":n}})

def run_independent_living_writer(path, limit=None):
 out={"attempts":0,"new":0,"replay":0,"stale":0,"errors":[],"timings":[],"threads":[],"wsol":0,"eight":0}
 def actor():
  out["threads"].append(threading.get_ident()); c=sqlite3.connect(path,timeout=10); out["threads"].append(threading.get_ident())
  registry=living._generic_living_registry(); sequence=[]
  for n in range(1,16): sequence.append((registry[living.WSOL_CANDIDATE_ID],members("wsol")[n-1],n,"new"))
  for n in range(1,16): sequence.append((registry[living.TRANSFER_CANDIDATE_ID],members("eight")[n-1],n,"new"))
  sequence += [(registry[living.WSOL_CANDIDATE_ID],members("wsol")[14],15,"replay")]*5
  sequence += [(registry[living.TRANSFER_CANDIDATE_ID],members("eight")[0],1,"stale")]*5
  for spec,mint,n,kind in sequence[:limit]:
   result=_result(spec,mint,n); ass=[{"potential_operation_id":spec["potential_operation_id"],"evidence_identity":a["evidence_key"],"evidence_type":a["evidence_type"],"association_state":a["state"],"source_key":"derived","provenance":{}} for a in result["associations"]]
   t=time.monotonic()
   try: publish_generic_assessment_atomic(c,result,ass); out["timings"].append((time.monotonic()-t)*1000); out["attempts"]+=1; out["wsol"]+=spec["potential_operation_id"]==living.WSOL_POTENTIAL_OPERATION_ID; out["eight"]+=spec["potential_operation_id"]==living.TRANSFER_POTENTIAL_OPERATION_ID; out[kind]+=1
   except Exception as e: out["errors"].append(str(e))
  out["threads"].append(threading.get_ident()); c.close()
 t=threading.Thread(target=actor); t.start(); t.join(); return out

def test_independent_living_writer_actor_b(tmp_path):
 path=str(tmp_path/"actor-b.db"); c=build_fixture(path); c.close(); out=run_independent_living_writer(path)
 assert out["attempts"]==40 and out["wsol"] and out["eight"] and not out["errors"] and len(set(out["threads"]))==1
 c=sqlite3.connect(path); assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"; assert c.execute("SELECT COUNT(*) FROM potential_operation_current").fetchone()[0]==2; c.close()
