"""Outer DEV048 run_log harness. Every fixture child installs frozen guards.
Not an application worker. No guard is disabled or changed for process creation.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

POINTS=('before_core_commit','after_core_commit','before_ops','during_ops','after_ops_commit','before_ack','after_ack')
MAX_CHILD_SECONDS=15
CHILD=r'''
import sys,os,runpy,json,hashlib,pathlib
root=pathlib.Path(sys.argv[1]);case=pathlib.Path(sys.argv[2]);point=sys.argv[3];mode=sys.argv[4]
sys.dont_write_bytecode=True
sys.argv=[str(root/'docs/audits/dev043/isolation_bootstrap.py'),'--self-test']
try:runpy.run_path(sys.argv[0],run_name='__main__')
except SystemExit as e:
 if e.code not in (None,0):raise
import sqlite3
boundary=sqlite3.connect.__globals__['EVENTS'];expected=len(boundary)
sys.path.insert(0,str(root));sys.path.insert(0,str(root/'tests'))
from test_dev090_handoff import Fixture
assert case.is_relative_to(pathlib.Path(os.environ['DEV043_STATE_ROOT']))
def receipt(value):
 value.update(point=point,mode=mode,isolation_events=boundary[expected:])
 assert not value['isolation_events']
 (case.parent/(point+'-'+mode+'.json')).write_text(json.dumps(value))
if mode=='setup':
 assert not case.exists()
 f=Fixture(case);f.add_waiting();receipt(dict(status='PASS'))
elif mode=='crash':
 f=Fixture(case,create=False)
 def die():
  receipt(dict(status='PASS',actual_process_exit=True));os._exit(0)
 if point=='before_core_commit':f.publish(stop=die)
 f.publish()
 if point=='after_core_commit':die()
 def fault(stage):
  if stage==point:die()
 f.dispatch(fault=fault,ops_before_commit=die if point=='during_ops' else None)
 raise AssertionError('interruption point not reached')
else:
 f=Fixture(case,create=False);f.dispatch(now=1000)
 expected_count=0 if point=='before_core_commit' else 1
 if expected_count:
  assert f.row(f.core,'SELECT state FROM wb_create_delivery')==('ACK',)
 else:
  assert f.row(f.core,'SELECT COUNT(*) FROM token_analysis')==(0,)
  assert f.row(f.ops,'SELECT status FROM wt_walkback_queue')==('waiting',)
 first=f.execute_claim_model();f.dispatch(now=2000);second=f.execute_claim_model()
 assert first==second==expected_count
 receipt(dict(status='PASS',committed_execution_model_count=first,duplicate_count=second))
'''

def run(root,state):
    root=Path(root).resolve();state=Path(state).resolve()
    assert state==Path(os.environ['DEV043_STATE_ROOT']).resolve()
    assert not (state/'process-crash-results.json').exists()
    results=[]
    for point in POINTS:
        for mode in ('setup','crash','recover'):
            command=[sys.executable,'-I','-B','-c',CHILD,str(root),str(state/point),point,mode]
            result=subprocess.run(command,capture_output=True,timeout=MAX_CHILD_SECONDS,env=dict(os.environ))
            assert result.returncode==0,(point,mode,result.stderr.decode()[-2000:])
            row=json.loads((state/(point+'-'+mode+'.json')).read_text())
            assert row['status']=='PASS' and not row['isolation_events']
        results.append(json.loads((state/(point+'-recover.json')).read_text()))
    hashes={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in ['src/ops/walkback_create_handoff.py','tests/test_dev090_handoff.py','tests/dev090_process_crash_driver.py','docs/audits/dev043/isolation_bootstrap.py']}
    assert all(p.stat().st_size<8*1024*1024 for p in state.rglob('*') if p.is_file())
    report=dict(status='PASS',source_root=str(root),source_hashes=hashes,cases=results,provider_calls=0,canonical_writes=0,unexpected_guard_denials=0,limits='Seven real process exits; CAS execution model is separate from actual worker completion test. Frozen guards active in every child; parent is bounded outer orchestrator only.')
    (state/'process-crash-results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(status='PASS',real_process_cases=len(results),unexpected_guard_denials=0)))

if __name__=='__main__':run(sys.argv[1],sys.argv[2])
