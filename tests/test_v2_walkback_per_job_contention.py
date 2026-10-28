"""Actual RPC/deferral functions, real fixture flock, blocked network."""
import contextlib,os,sqlite3,subprocess,sys,time
import pytest
from tests.test_v2_walkback_progress import actual_rpc,v,legacy

@pytest.fixture(autouse=True)
def blocked_network(monkeypatch):
 import socket
 def no(*a,**k):raise AssertionError('No provider connections permitted')
 monkeypatch.setattr(socket.socket,'connect',no)
 monkeypatch.setattr(socket,'create_connection',no)

@contextlib.contextmanager
def scope(e):
 token=e.h._scope.set((e.h._hash('mint:root'),))
 try:yield
 finally:e.h._scope.reset(token)

@pytest.mark.parametrize('where',['current','owner'])
def test_transient_authority_contention_rechecks_before_one_dispatch(actual_rpc,monkeypatch,where):
 e=actual_rpc;before=e.g.read();calls=[];sleeps=[]
 original=e.m.current if where=='current' else e.g.investigation_owner
 def busy(*args):
  calls.append(1)
  if len(calls)<3:raise e.m.BudgetDeferred('HELIUS_GATE_BUSY')
  return original(*args)
 monkeypatch.setattr(e.m if where=='current' else e.g,'current' if where=='current' else 'investigation_owner',busy)
 monkeypatch.setattr(time,'sleep',lambda t:sleeps.append(t))
 # Reserve transport performs further current() calls; only first two fail.
 with scope(e):e.ns['_get_tx']('retained-signature')
 assert sleeps==[.025,.025] and len(e.wire)==1
 after=e.g.read();root=e.h._hash('mint:root')
 assert after['rpc_budget']['scopes'][root]['credits']==before['rpc_budget']['scopes'][root]['credits']+1
 assert after['rpc_budget']['owners']==before['rpc_budget']['owners']
 assert after['rpc_budget']['policy']['lifetime_credits']==100000 and not after['rpc_budget']['inflight']


def test_real_flock_release_then_recovery_without_state_reset(actual_rpc,monkeypatch):
 e=actual_rpc;before=e.g.read();wire_before=len(e.wire)
 child=subprocess.Popen([sys.executable,'-c',"import sys,fcntl; f=open(sys.argv[1],'r+');fcntl.flock(f,fcntl.LOCK_EX);print('READY',flush=True);sys.stdin.read()",str(e.g.path)+'.lock'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
 assert child.stdout.readline().strip()=='READY'
 def current():
  with e.g.locked():e.g.read()
  return e.g
 monkeypatch.setattr(e.m,'current',current);sleeps=[]
 def release(t):
  sleeps.append(t);child.stdin.close();child.wait()
 monkeypatch.setattr(time,'sleep',release)
 try:
  with scope(e):e.ns['_get_tx']('real-flock-signature')
 finally:
  if child.poll() is None:child.stdin.close();child.wait()
 assert sleeps==[.025] and len(e.wire)==wire_before+1
 after=e.g.read();assert after['rpc_budget']['owners']==before['rpc_budget']['owners']


def test_persistent_contention_bounded_no_dispatch_or_spend(actual_rpc,monkeypatch):
 e=actual_rpc;before=e.g.path.read_bytes();calls=[];sleeps=[]
 def busy():calls.append(1);raise e.m.BudgetDeferred('HELIUS_GATE_BUSY')
 monkeypatch.setattr(e.m,'current',busy);monkeypatch.setattr(time,'sleep',lambda x:sleeps.append(x))
 with scope(e),pytest.raises(e.m.BudgetDeferred,match='^HELIUS_GATE_BUSY$'):e.ns['_get_tx']('signature')
 assert len(calls)==5 and sleeps==[.025]*4 and not e.wire and e.g.path.read_bytes()==before

@pytest.mark.parametrize('kind',['limit','cooldown','denial','generation','storage','corrupt','runtime'])
def test_other_authority_failures_never_retried(actual_rpc,monkeypatch,kind):
 e=actual_rpc;sleeps=[];calls=[]
 errors={'limit':e.m.BudgetDeferred('HELIUS_INVESTIGATION_CREDIT_LIMIT'),'cooldown':e.m.BudgetDeferred('HELIUS_REQUEST_COOLDOWN'),'denial':e.m.BudgetDeferred('HELIUS_ACQUISITION_DEFERRED'),'generation':e.m.Integrity('PAIR_GENERATION_MISMATCH'),'storage':e.m.Integrity('DISK_FLOOR'),'corrupt':e.m.Integrity('HELIUS_GATE_PUBLICATION_AMBIGUOUS'),'runtime':RuntimeError('HELIUS_GATE_BUSY')};error=errors[kind]
 def fail():calls.append(1);raise error
 monkeypatch.setattr(e.m,'current',fail);monkeypatch.setattr(time,'sleep',lambda x:sleeps.append(x));before=e.g.path.read_bytes()
 with scope(e),pytest.raises(type(error)) as caught:e.ns['_get_tx']('signature')
 assert caught.value is error and len(calls)==1 and not sleeps and not e.wire and e.g.path.read_bytes()==before

@pytest.mark.parametrize('owned',[True,False])
def test_no_retry_after_dispatch_boundary(actual_rpc,monkeypatch,owned):
 e=actual_rpc;calls=[];sleeps=[]
 def dispatched(*args):calls.append(1);raise e.m.BudgetDeferred('HELIUS_GATE_BUSY')
 e.ns['_rpc_uncached']=dispatched;monkeypatch.setattr(time,'sleep',lambda x:sleeps.append(x))
 with (scope(e) if owned else contextlib.nullcontext()),pytest.raises(e.m.BudgetDeferred):e.ns['_get_tx']('signature')
 assert len(calls)==1 and not sleeps and not e.wire

@pytest.mark.parametrize('code,delay',[('HELIUS_GATE_BUSY',5),('HELIUS_INVESTIGATION_CREDIT_LIMIT',300),('HELIUS_REQUEST_COOLDOWN',300),('HELIUS_ACQUISITION_DEFERRED',300)])
def test_durable_due_time_refund_restart_and_other_claim_protection(actual_rpc,tmp_path,code,delay):
 e=actual_rpc;p=tmp_path/'ops.sqlite';o=sqlite3.connect(p)
 o.execute('CREATE TABLE wt_walkback_queue(mint TEXT,status TEXT,attempts INTEGER,lease_expires_at INTEGER,next_retry_at INTEGER,last_error TEXT,updated_at INTEGER,claimed_by TEXT,rpc_used INTEGER)')
 owner=os.environ.get('WALKBACK_WORKER_ID',f'walkback-{os.getpid()}')
 o.execute("INSERT INTO wt_walkback_queue VALUES('root','running',1,99999,0,NULL,0,?,0)",(owner,));o.commit();before=e.g.path.read_bytes()
 e.ns['retain_deferral'](o,'root',0,[2],e.m.BudgetDeferred(code));o.close();o=sqlite3.connect(p)
 assert o.execute('SELECT status,attempts,next_retry_at,last_error,rpc_used FROM wt_walkback_queue').fetchone()==('pending',0,e.now[0]+delay,code,2)
 # Repeated stale completion cannot overwrite a pending or newly owned claim.
 o.execute("UPDATE wt_walkback_queue SET status='running',claimed_by='other-worker',attempts=2");o.commit();old=list(o.iterdump())
 e.ns['retain_deferral'](o,'root',0,[0],e.m.BudgetDeferred(code))
 assert list(o.iterdump())==old and e.g.path.read_bytes()==before and not e.wire
 o.close()


def test_only_rpc_authority_and_deferral_handler_change():
 import ast,pathlib,subprocess
 root=pathlib.Path(__file__).resolve().parents[1];rel='runtime/walkback/src/core/walkback_worker.py'
 before=ast.parse(subprocess.check_output(['git','show','3f75234d:'+rel],cwd=root,text=True));after=ast.parse((root/rel).read_text())
 b={n.name:ast.dump(n) for n in before.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))};a={n.name:ast.dump(n) for n in after.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
 assert set(a)==set(b) and {k for k in b if b[k]!=a[k]}=={'_rpc','_process_row','drain_batch'}
 for rel in ['runtime/walkback/src/core/helius_denial_gate.py','runtime/walkback/src/core/helius_http_protection.py','runtime/walkback/src/ops/anchor_reconciliation.py']:
  assert (root/rel).read_bytes()==subprocess.check_output(['git','show','3f75234d:'+rel],cwd=root)


def test_contention_before_reuse_does_not_duplicate_spend(actual_rpc,monkeypatch):
 e=actual_rpc
 with scope(e):e.ns['_get_tx']('memo-signature')
 before=e.g.path.read_bytes();original=e.m.current;calls=[]
 def busy():
  calls.append(1)
  if len(calls)==1:raise e.m.BudgetDeferred('HELIUS_GATE_BUSY')
  return original()
 monkeypatch.setattr(e.m,'current',busy);monkeypatch.setattr(time,'sleep',lambda _:None)
 with scope(e):e.ns['_get_tx']('memo-signature')
 assert len(e.wire)==1 and e.g.path.read_bytes()==before


def test_contended_head_rotates_without_changing_priority(actual_rpc,tmp_path):
 import ast,pathlib,types
 e=actual_rpc;source=pathlib.Path(__file__).resolve().parents[1]/'runtime/walkback/src/core/walkback_worker.py';tree=ast.parse(source.read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='drain_batch');ns=dict(e.ns,sqlite3=sqlite3,BATCH_SIZE=8,RPC_BUDGET_BATCH=50,deep_walkback=types.SimpleNamespace(ensure_schema=lambda _:None),promote_recurring_funders=lambda _:0)
 exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),ns)
 p=tmp_path/'jobs.sqlite';o=sqlite3.connect(p);o.row_factory=sqlite3.Row
 o.execute('CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY,creator TEXT,subprov TEXT,treasury TEXT,walkback_class TEXT,attempts INTEGER,status TEXT,lease_expires_at INTEGER,next_retry_at INTEGER,priority INTEGER,enqueued_at INTEGER,updated_at INTEGER,last_error TEXT,intelligence_outcome TEXT)')
 for i in range(24):o.execute("INSERT INTO wt_walkback_queue VALUES(?, 'creator',NULL,NULL,'FULL_WALKBACK',0,'pending',0,0,0,?,0,NULL,NULL)",(f'job-{i:02}',i+1))
 seen=[]
 def claim(ops,mint):return True
 def defer(ops,row):
  seen.append(row['mint']);ops.execute("UPDATE wt_walkback_queue SET last_error='HELIUS_GATE_BUSY',updated_at=?,next_retry_at=? WHERE mint=?",(e.now[0],e.now[0]+5,row['mint']));ops.commit();return 0
 ns['_mark_running']=claim;ns['_process_row']=defer
 before=e.g.path.read_bytes()
 for poll in range(3):
  ns['drain_batch'](o,batch_size=8);e.now[0]+=6
  if poll==0:o.close();o=sqlite3.connect(p);o.row_factory=sqlite3.Row
 assert len(seen)==len(set(seen))==24
 assert o.execute('SELECT SUM(attempts) FROM wt_walkback_queue').fetchone()[0]==0
 # A higher-priority contended job retains its priority, including after restart.
 o.execute("UPDATE wt_walkback_queue SET priority=10 WHERE mint='job-00'");o.commit();ns['drain_batch'](o,batch_size=1)
 assert seen[-1]=='job-00' and e.g.path.read_bytes()==before and not e.wire
 o.close()
