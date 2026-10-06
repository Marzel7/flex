import importlib.util
import sqlite3
from types import SimpleNamespace
import pytest

P="scripts/run_watchtower_historical_executor.py"
spec=importlib.util.spec_from_file_location("cook_launcher",P); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
def payload(r):
 p=r["request_parameters"]; return {"data":{"items":[{"unixTime":t,"o":100,"h":300 if t==p["time_from"] else 200,"l":10 if t==p["time_to"] else 100,"c":10 if t==p["time_to"] else 200} for t in range(p["time_from"],p["time_to"]+1,900)]}}
def test_launcher_admits_exact_cook_and_invokes_existing_executor(tmp_path):
 calls=[]
 def bind(r): calls.append(r); return SimpleNamespace(status_code=200,payload=payload(r))
 out=mod.launch_cook(db=str(tmp_path/"cook.db"),binding=bind)
 assert out["state"]=="COMPLETED" and len(calls)==7
def test_launcher_rejects_boundary_mismatch_before_executor(tmp_path):
 bad=dict(mod.COOK_BOUNDARY); bad["migration_slot"]+=1
 with pytest.raises(ValueError,match="BOUNDARY_MISMATCH"): mod.launch_cook(db=str(tmp_path/"x.db"),boundary=bad)

def test_launcher_handles_generic_admission_refusal_without_job_dereference(tmp_path,monkeypatch):
 class Executor:
  def __init__(self,*_args): pass
  def acquire_opening(self,_boundary): return {"entry_timestamp":1,"entry_mc_usd":1,"entry_method":"MIGRATION_SECOND_MC_FALLBACK","entry_exactness":"MIGRATION_SECOND_MC_FALLBACK"}
 monkeypatch.setattr("src.ops.watchtower_historical_executor.HistoricalExecutor",Executor)
 monkeypatch.setattr("src.ops.watchtower_historical_backfill.admit",lambda *_args,**_kwargs:{"status":"REFUSED_BATCH1_CAPACITY","provider_calls":0})
 # launch_cook imports admit at call time, so this refusal must return intact.
 assert mod.launch_cook(db=str(tmp_path/"x.db")) == {"status":"REFUSED_BATCH1_CAPACITY","provider_calls":0}

def test_qualified_opening_is_reused_without_a_second_opening_call(tmp_path):
 db=str(tmp_path/"cook.db"); calls=[]
 with sqlite3.connect(db) as conn:
  conn.execute("CREATE TABLE watchtower_historical_openings(mint TEXT PRIMARY KEY,state TEXT NOT NULL,result_json TEXT,created_at INTEGER NOT NULL)")
  conn.execute("INSERT INTO watchtower_historical_openings VALUES(?,?,?,?)",(mod.COOK_BOUNDARY["mint"],"QUALIFIED",'{"entry_exactness":"MIGRATION_SECOND_MC_FALLBACK","entry_mc_usd":100.0,"entry_method":"MIGRATION_SECOND_MC_FALLBACK","entry_timestamp":1791269958}',0))
 def bind(r): calls.append(r); return SimpleNamespace(status_code=200,payload=payload(r))
 out=mod.launch_cook(db=db,binding=bind)
 assert out["state"] == "COMPLETED" and len(calls)==6
 assert all(c["request_parameters"]["type"] == "15m" for c in calls)
