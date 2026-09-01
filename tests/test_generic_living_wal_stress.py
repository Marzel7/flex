"""Thread-owned disposable WAL stress for the qualified worker/Living path."""
from __future__ import annotations
import json, os, sqlite3, statistics, threading, time
from pathlib import Path
from tests.test_generic_living_multi_member_fixture import build_fixture, members, append_disposable_walkback_event
from tests.test_generic_living_independent_writer import run_independent_living_writer
import src.core.walkback_worker as worker

def open_stress_connection(path, role, read_only=False):
    uri = f"file:{Path(path).absolute()}?mode=ro" if read_only else path
    c = sqlite3.connect(uri, uri=read_only, timeout=10, check_same_thread=True)
    c.row_factory = sqlite3.Row
    if not read_only: c.execute("PRAGMA busy_timeout=10000")
    return c

def test_thread_owned_worker_living_wal_stress(tmp_path, monkeypatch):
    path=str(tmp_path/"stress.db"); seed=build_fixture(path); seed.close()
    monkeypatch.setattr(worker,"OPS_DB_PATH",path); monkeypatch.setenv("LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED","true")
    lock=threading.Lock(); metrics={"source":[],"living":[],"invalid":0,"errors":[],"observer":0,"cross_thread":0,"commits":0}
    events=[*members("wsol")[:30],*members("eight")[:30],*[f"unrelated-{i}" for i in range(10)],*[f"global-{i}" for i in range(10)]]
    start=threading.Event(); done=threading.Event()
    def source():
        c=open_stress_connection(path,"source"); start.wait()
        for mint in events:
            t=time.monotonic(); append_disposable_walkback_event(c,mint,"global" if mint.startswith("global-") else "relevant"); d=time.monotonic()-t
            out=worker._notify_living_after_walkback_commit(c,mint)
            with lock: metrics["source"].append(d); metrics["living"].append(0.0); metrics["commits"]+=1
        c.close(); done.set()
    def observer():
        c=open_stress_connection(path,"observer",True); start.wait()
        while not done.is_set():
            try:
                rows=c.execute("SELECT c.potential_operation_id,c.assessment_id,v.potential_operation_id,v.freshness_key,c.freshness_key FROM potential_operation_current c JOIN potential_operation_assessment_version v ON v.assessment_id=c.assessment_id").fetchall()
                for r in rows:
                    if r[0]!=r[2] or r[3]!=r[4]: metrics["invalid"]+=1
                with lock: metrics["observer"]+=1
            except sqlite3.Error as e:
                with lock: metrics["errors"].append(str(e))
        c.close()
    a=threading.Thread(target=source); b=threading.Thread(target=observer); a.start(); b.start(); start.set(); a.join(); b.join()
    check=open_stress_connection(path,"check"); assert check.execute("PRAGMA integrity_check").fetchone()[0]=="ok"; check.close()
    assert metrics["commits"]==80 and metrics["invalid"]==0 and not metrics["errors"]

def test_held_reader_wal_longevity(tmp_path, monkeypatch):
    path=str(tmp_path/'held-reader.db'); seed=build_fixture(path); seed.close()
    monkeypatch.setattr(worker,'OPS_DB_PATH',path); monkeypatch.setenv('LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED','true')
    held=sqlite3.connect(f'file:{path}?mode=ro',uri=True); held.row_factory=sqlite3.Row; held.execute('BEGIN'); assert held.execute('SELECT count(*) FROM potential_operation_current').fetchone()[0]==2
    metrics={'source':[],'living':[],'errors':[],'observer_invalid':0,'reader_errors':0,'wal':[os.path.getsize(path+'-wal') if os.path.exists(path+'-wal') else 0]}; start=threading.Event(); done_a=threading.Event(); done_b=threading.Event()
    def source():
        c=sqlite3.connect(path,timeout=10); c.row_factory=sqlite3.Row; start.wait()
        for i in range(100):
            mint=members('wsol')[i] if i<30 else members('eight')[i-30] if i<60 else f'held-unrelated-{i}'
            t=time.monotonic(); append_disposable_walkback_event(c,mint,'relevant'); metrics['source'].append((time.monotonic()-t)*1000)
            t=time.monotonic(); out=worker._notify_living_after_walkback_commit(c,mint); metrics['living'].append((time.monotonic()-t)*1000)
            if not out.get('success'): metrics['errors'].append(out)
            if i%20==0: metrics['wal'].append(os.path.getsize(path+'-wal') if os.path.exists(path+'-wal') else 0)
        c.close(); done_a.set()
    def living_writer():
        start.wait(); first=run_independent_living_writer(path,40); second=run_independent_living_writer(path,20); metrics['b']=(first,second); done_b.set()
    a=threading.Thread(target=source); b=threading.Thread(target=living_writer); a.start(); b.start(); start.set()
    while not (done_a.is_set() and done_b.is_set()):
        try: assert held.execute('SELECT count(*) FROM potential_operation_current').fetchone()[0]==2
        except Exception as exc: metrics['reader_errors']+=1; metrics['errors'].append(str(exc)); break
    a.join(30); b.join(30); metrics['wal'].append(os.path.getsize(path+'-wal') if os.path.exists(path+'-wal') else 0)
    assert not a.is_alive() and not b.is_alive() and not metrics['errors'] and not metrics['b'][0]['errors'] and not metrics['b'][1]['errors']
    assert len(metrics['source'])==100 and metrics['b'][0]['attempts']+metrics['b'][1]['attempts']==60 and metrics['reader_errors']==0
    held.rollback(); held.close(); metrics['wal_after_release']=os.path.getsize(path+'-wal') if os.path.exists(path+'-wal') else 0
    c=sqlite3.connect(path); c.row_factory=sqlite3.Row; assert c.execute('SELECT count(*) FROM potential_operation_current').fetchone()[0]==2; append_disposable_walkback_event(c,'held-post-release','relevant'); assert worker._notify_living_after_walkback_commit(c,'held-post-release')['success']; assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'; c.close()
    def summary(xs): return {'count':len(xs),'min_ms':min(xs),'median_ms':statistics.median(xs),'p95_ms':sorted(xs)[round(.95*(len(xs)-1))],'max_ms':max(xs)}
    print('HELD_READER_METRICS='+json.dumps({'source':summary(metrics['source']),'living':summary(metrics['living']),'attempts':60,'wal':metrics['wal'],'wal_after_release':metrics['wal_after_release']}))
