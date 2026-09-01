"""Qualified disposable A+B+C WAL concurrency harness."""
from __future__ import annotations
import json, sqlite3, statistics, threading, time
from tests.test_generic_living_multi_member_fixture import build_fixture, members, append_disposable_walkback_event
from tests.test_generic_living_independent_writer import run_independent_living_writer
import src.core.walkback_worker as worker

def test_three_actor_composition_smoke(tmp_path, monkeypatch):
 path=str(tmp_path/"abc.db"); c=build_fixture(path); c.close(); monkeypatch.setattr(worker,"OPS_DB_PATH",path); monkeypatch.setenv("LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED","true")
 start=threading.Event(); injected_done=threading.Event(); done_a=threading.Event(); done_b=threading.Event(); stats={"a":0,"b":None,"obs":0,"invalid":0,"times":{},"errors":[],"results":[],"source_ms":[],"living_ms":[],"failed_ms":[],"stages":[],"locks":{"SQLITE_BUSY_SNAPSHOT":0,"SQLITE_BUSY":0,"SQLITE_LOCKED":0,"database is locked":0,"database table is locked":0,"retries":0,"retry_exhaustion":0}}; guard=threading.Lock()
 events=[*members("wsol")[:30],*members("eight")[:30],*[f"unrelated-{i}" for i in range(10)],*[f"global-{i}" for i in range(10)]]
 def inject(stage, _context):
  stats["stages"].append(stage)
  if stage=="after_current_before_commit": raise RuntimeError("tiny injected failure")
 def actor_a():
  stats["times"]["a_start"]=time.monotonic(); c=sqlite3.connect(path,timeout=10); c.row_factory=sqlite3.Row; start.wait()
  for i,mint in enumerate(events):
   t=time.monotonic(); append_disposable_walkback_event(c,mint,"relevant"); stats["source_ms"].append((time.monotonic()-t)*1000)
   t=time.monotonic(); result=worker._notify_living_after_walkback_commit(c,mint,test_failure_injector=inject if i==0 else None); elapsed=(time.monotonic()-t)*1000; stats["living_ms"].append(elapsed)
   for candidate in result.get("result",{}).get("candidates",()):
    for reason in candidate.get("retry_causes",()): stats["locks"][reason]+=1
    stats["locks"]["retries"]+=candidate.get("retry_count",0)
   if i==0: stats["failed_ms"].append(elapsed); injected_done.set()
   stats["results"].append(result); stats["a"]+=1
  c.close(); stats["times"]["a_end"]=time.monotonic(); done_a.set()
 def actor_b():
  stats["times"]["b_start"]=time.monotonic(); start.wait(); injected_done.wait(10); stats["b"]=run_independent_living_writer(path,40); stats["times"]["b_end"]=time.monotonic(); done_b.set()
 def actor_c():
  c=sqlite3.connect(f"file:{path}?mode=ro",uri=True); start.wait()
  while not (done_a.is_set() and done_b.is_set()):
   try:
    for r in c.execute("SELECT c.potential_operation_id,v.potential_operation_id,c.freshness_key,v.freshness_key FROM potential_operation_current c JOIN potential_operation_assessment_version v ON v.assessment_id=c.assessment_id"):
     if r[0]!=r[1] or r[2]!=r[3]: stats["invalid"]+=1
    stats["obs"]+=1
   except sqlite3.Error as e: stats["errors"].append(str(e))
  c.close()
 a=threading.Thread(target=actor_a); b=threading.Thread(target=actor_b); o=threading.Thread(target=actor_c); a.start(); b.start(); o.start(); start.set(); a.join(30); b.join(30); o.join(30)
 c=sqlite3.connect(path); assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"; c.close()
 assert stats["a"]==80 and stats["b"]["attempts"]==40 and stats["obs"]>0 and not stats["errors"] and not stats["invalid"] and not stats["b"]["errors"], stats["b"]
 assert sum(not x["success"] for x in stats["results"])==1 and stats["results"][0]["publication_attempted"] and any(x["success"] for x in stats["results"][1:]) and "after_current_before_commit" in stats["stages"]
 assert max(stats["times"]["a_start"],stats["times"]["b_start"]) < min(stats["times"]["a_end"],stats["times"]["b_end"])
 def summary(xs): return {"count":len(xs),"min_ms":min(xs),"median_ms":statistics.median(xs),"p95_ms":sorted(xs)[max(0,round(.95*(len(xs)-1)))],"max_ms":max(xs)}
 print("FULL_STRESS_METRICS="+json.dumps({"observer_iterations":stats["obs"],"source":summary(stats["source_ms"]),"living":summary(stats["living_ms"]),"failed_living_ms":stats["failed_ms"],"actor_b_timings":summary(stats["b"]["timings"]),"callback_success":sum(x["success"] for x in stats["results"]),"callback_failure":sum(not x["success"] for x in stats["results"]),"actor_b_mix":{k:stats["b"][k] for k in ("new","replay","stale")},"lock_telemetry":stats["locks"]}))
