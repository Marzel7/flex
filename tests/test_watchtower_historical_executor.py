import subprocess
from pathlib import Path
from types import SimpleNamespace
import sqlite3
from src.ops.watchtower_historical_backfill import MAX_BATCH1_CHECKPOINT_BYTES, admit, begin_next_range, complete_range, ranges, promote_recovered_opening
from src.ops.watchtower_historical_executor import HistoricalExecutor

MINT="HRinFbhZjrb2xoqSzxYCYKX7LqJ3H6pn42CURb2cpump"
def boundary(): return {"mint":MINT,"assignment_id":"a","migration_signature":"s","migration_slot":1,"migration_timestamp":1,"pumpswap_pool":"p"}
def payload(request):
 p=request["request_parameters"]; items=[]
 for ts in range(p["time_from"],p["time_to"]+1,900): items.append({"unixTime":ts,"o":1,"h":2,"l":1,"c":1})
 return {"data":{"items":items}}

def test_six_first_try_ranges_and_no_seventh(tmp_path):
 db=str(tmp_path/"x.db"); job=admit(db,boundary(),now=1)["job_id"]; calls=[]
 def bind(r): calls.append(r); return SimpleNamespace(status_code=200,payload=payload(r))
 out=HistoricalExecutor(db,bind).run(job,MINT)
 assert out["provider_calls"]==len(calls)==6 and out["completed_range_count"]==6
 assert HistoricalExecutor(db,bind).run(job,MINT)["provider_calls"]==0

def test_retry_isolated_and_third_attempt_fails_closed(tmp_path):
 db=str(tmp_path/"x.db"); job=admit(db,boundary(),now=1)["job_id"]; calls={}
 def bind(r):
  start=r["request_parameters"]["time_from"]; calls[start]=calls.get(start,0)+1
  return SimpleNamespace(status_code=500 if start==900 and calls[start]<=2 else 200,payload=payload(r))
 HistoricalExecutor(db,bind).run(job,MINT); HistoricalExecutor(db,bind).run(job,MINT)
 state={r["ordinal"]:r["state"] for r in ranges(db,job)}
 assert state[1]=="FAILED_CLOSED" and calls[900]==2 and all(v=="COMPLETED" for k,v in state.items() if k!=1)

def test_commit_pinned_launcher_refuses_sha_mismatch(tmp_path):
 root=Path(__file__).resolve().parents[1]; p=subprocess.run(["python",str(root/"scripts/run_watchtower_historical_executor.py"),"--sha","0"*40,"--mint",MINT,"--db",str(tmp_path/"x.db"),"--enable-batch1"],capture_output=True,text=True)
 assert p.returncode != 0

def test_over_one_mib_base_db_with_small_job_is_admitted(tmp_path):
 db=str(tmp_path/"large-base.db")
 with sqlite3.connect(db) as conn:
  conn.execute("CREATE TABLE unrelated_padding(value BLOB)")
  conn.execute("INSERT INTO unrelated_padding VALUES(zeroblob(?))", (MAX_BATCH1_CHECKPOINT_BYTES + 4096,))
 assert __import__("os").path.getsize(db) > MAX_BATCH1_CHECKPOINT_BYTES
 result=admit(db,boundary(),now=1)
 assert result["status"] == "PREFLIGHTED" and result["job_id"]

def test_job_storage_budget_is_durable_and_duplicate_admission_does_not_reset(tmp_path):
 db=str(tmp_path/"budget.db"); job=admit(db,boundary(),now=1)["job_id"]
 with sqlite3.connect(db) as conn:
  conn.execute("UPDATE watchtower_historical_backfill_jobs SET retained_checkpoint_bytes=? WHERE job_id=?",(MAX_BATCH1_CHECKPOINT_BYTES,job))
 assert admit(db,boundary(),now=2) == {"status":"REFUSED_STORAGE_BOUND","provider_calls":0}
 with sqlite3.connect(db) as conn:
  assert conn.execute("SELECT retained_checkpoint_bytes FROM watchtower_historical_backfill_jobs WHERE job_id=?",(job,)).fetchone()[0] == MAX_BATCH1_CHECKPOINT_BYTES

def test_range_refuses_before_per_job_budget_exceeds(tmp_path):
 db=str(tmp_path/"range-budget.db"); job=admit(db,boundary(),now=1)["job_id"]
 row=begin_next_range(db,job,now=2)
 with sqlite3.connect(db) as conn:
  conn.execute("UPDATE watchtower_historical_backfill_jobs SET retained_checkpoint_bytes=? WHERE job_id=?",(MAX_BATCH1_CHECKPOINT_BYTES-1,job))
 assert complete_range(db,row["range_id"],{"small":"evidence"},now=3) == "REFUSED_STORAGE_BOUND"
 assert {r["state"] for r in ranges(db,job) if r["range_id"]==row["range_id"]} == {"FAILED_CLOSED"}

def test_recovered_opening_promotes_waiting_fact_without_live_admission(tmp_path):
 db=str(tmp_path/"recover.db")
 with sqlite3.connect(db) as conn:
  conn.execute("CREATE TABLE operation_monitor_facts(operation_id TEXT,mint TEXT,entry_status TEXT,monitor_state TEXT,assignment_timestamp INTEGER,assignment_provenance TEXT,entry_timestamp INTEGER,entry_mc_usd REAL,entry_method TEXT,entry_exactness TEXT,monitor_started_at INTEGER,next_observation_at INTEGER,monitor_completed_at INTEGER,evidence_status TEXT,provenance_digest TEXT,updated_at INTEGER,PRIMARY KEY(operation_id,mint))")
  conn.execute("INSERT INTO operation_monitor_facts(operation_id,mint,entry_status,monitor_state,assignment_timestamp,assignment_provenance,entry_method) VALUES('watchtower',?,'WAITING_FOR_ENTRY_REFERENCE','WAITING_FOR_ENTRY_REFERENCE',1,'assignment','FIRST_FULL_POST_MIGRATION_SECOND_MC')",(MINT,))
 opening={"entry_timestamp":1,"entry_mc_usd":12.5,"entry_method":"MIGRATION_SECOND_MC_FALLBACK","entry_exactness":"MIGRATION_SECOND_MC_FALLBACK"}
 result=promote_recovered_opening(db,boundary(),opening,now=2)
 with sqlite3.connect(db) as conn:
  row=conn.execute("SELECT entry_status,monitor_state,entry_timestamp,entry_mc_usd,entry_method,entry_exactness,monitor_started_at,next_observation_at FROM operation_monitor_facts").fetchone()
 assert result["state"]=="HISTORICAL_RECOVERY_OPENING_QUALIFIED"
 assert row==("QUALIFIED","HISTORICAL_RECOVERY_ACQUIRING",1,12.5,"MIGRATION_SECOND_MC_FALLBACK","MIGRATION_SECOND_MC_FALLBACK",None,None)

def test_recovered_opening_rejects_duplicate_target_evidence(tmp_path):
 db=str(tmp_path/"duplicate-opening.db")
 def bind(_request):
  return SimpleNamespace(status_code=200,payload={"data":{"items":[
   {"timestamp":2,"o":1,"h":2,"l":1,"c":1.1},
   {"timestamp":2,"o":1,"h":2,"l":1,"c":1.2},
  ]}})
 try:
  HistoricalExecutor(db,bind).acquire_opening(boundary())
  assert False, "duplicate evidence must fail closed"
 except RuntimeError as error:
  assert str(error)=="OPENING_INSUFFICIENT_EVIDENCE"
