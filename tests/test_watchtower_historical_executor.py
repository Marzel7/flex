import subprocess
from pathlib import Path
from types import SimpleNamespace
from src.ops.watchtower_historical_backfill import admit, ranges
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
