"""Actual installed/DEV functions; real disposable flock contention, blocked network."""
import ast,contextlib,hashlib,json,os,pathlib,sqlite3,subprocess,sys,time,types
import pytest
from tests.test_v2_optional_funder_deferral import case,budgets,queue
from tests.test_v2_helius_investigation_budget import v,legacy
ROOT=pathlib.Path(__file__).resolve().parents[1]
WORKER=ROOT/'runtime/walkback/src/core/walkback_worker.py'
BASE='b203c114887034dc77e2ee2be10d08985c6871ac'

@contextlib.contextmanager
def held_fixture_gate(e):
 code="import fcntl,os,sys; fd=os.open(sys.argv[1],os.O_RDWR); fcntl.flock(fd,fcntl.LOCK_EX); print('READY',flush=True); sys.stdin.read()"
 child=subprocess.Popen([sys.executable,'-c',code,str(e.g.path)+'.lock'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
 try:
  assert child.stdout.readline().strip()=='READY'
  yield
 finally:
  child.stdin.close();child.wait(timeout=5)


def real_current(e,monkeypatch):
 # Same current() critical section as production, on the existing disposable
 # gate. Fixture paired identity and generation are established by case/v.
 def current():
  with e.g.locked():e.g.read()
  return e.g
 monkeypatch.setattr(e.m,'current',current)


def cycle_tail(e):
 tree=ast.parse(WORKER.read_text());run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_loop')
 def marker(node):return isinstance(node,ast.Expr) and isinstance(node.value,ast.Call) and any(isinstance(a,ast.Constant) and a.value=='queue_inspection_started' for a in node.value.args)
 inner=next(n for n in ast.walk(run) if isinstance(n,ast.Try) and any(marker(x) for x in n.body));start=next(i for i,n in enumerate(inner.body) if marker(n))
 fn=ast.parse('def accepted_cycle_tail(ops):\n pass\n').body[0];fn.body=inner.body[start:];ast.fix_missing_locations(fn)
 e.events=[];e.failures=[]
 e.ns['trace_boundary']=lambda name,**kw:e.events.append(name)
 e.ns['trace_failure']=lambda name,error,**kw:e.failures.append((name,type(error).__name__,str(error)))
 for name,label in [('_write_heartbeat','heartbeat'),('_recover_committed_projections','projection'),('_review_deep_routes_after_commit','review')]:
  def observe(ops,label=label):e.events.append(label)
  e.ns[name]=observe
 exec(compile(ast.Module(body=[fn],type_ignores=[]),'<actual-run-loop-cycle-tail>','exec'),e.ns)


def seed_batch(e,on_last=None):
 e.seed('optional-a');e.seed('optional-b')
 for i in range(3):e.ops.execute("INSERT INTO wt_walkback_queue(mint,creator,status,walkback_class,attempts,enqueued_at) VALUES(?,?,'pending','FULL_WALKBACK',0,1)",(f'pending-{i}',f'creator-{i}'))
 e.ops.commit();processed=[]
 def claim(ops,mint):ops.execute("UPDATE wt_walkback_queue SET status='running',attempts=attempts+1 WHERE mint=?",(mint,));ops.commit();return True
 def process(ops,row):
  ops.execute("UPDATE wt_walkback_queue SET status='complete',intelligence_outcome='FIXTURE_MAIN_RESULT' WHERE mint=?",(row['mint'],));ops.commit();processed.append(row['mint'])
  if len(processed)==3:
   e.after_main_queue=queue(e);e.after_main_budget=budgets(e)
   if on_last:on_last()
  return 0
 e.ns['_mark_running']=claim;e.ns['_process_row']=process
 cycle_tail(e)


def test_original_gate_busy_path_aborts_after_committed_batch(case,monkeypatch,capsys):
 e=case;real_current(e,monkeypatch)
 old=ast.parse(subprocess.check_output(['git','show',BASE+':runtime/walkback/src/core/walkback_worker.py'],cwd=ROOT,text=True));fn=next(n for n in old.body if isinstance(n,ast.FunctionDef) and n.name=='promote_recurring_funders');exec(compile(ast.Module(body=[fn],type_ignores=[]),'<exact-installed-optional-handler>','exec'),e.ns)
 with contextlib.ExitStack() as stack:
  seed_batch(e,lambda:stack.enter_context(held_fixture_gate(e)))
  with pytest.raises(e.m.BudgetDeferred,match='^HELIUS_GATE_BUSY$'):e.ns['accepted_cycle_tail'](e.ops)
  assert 'heartbeat' not in e.events and 'projection' not in e.events and 'review' not in e.events
  assert queue(e)==e.after_main_queue and budgets(e)==e.after_main_budget and not e.wire
 assert 'batch done: processed=3' in capsys.readouterr().out
 assert e.failures==[('queue_claim_attempted','BudgetDeferred','HELIUS_GATE_BUSY')]

@pytest.mark.parametrize('code',['HELIUS_GATE_BUSY','HELIUS_INVESTIGATION_REQUIRED'])
def test_actual_cycle_continues_heartbeat_projection_and_review(case,monkeypatch,capsys,code):
 e=case;real_current(e,monkeypatch)
 with contextlib.ExitStack() as stack:
  seed_batch(e,(lambda:stack.enter_context(held_fixture_gate(e))) if code=='HELIUS_GATE_BUSY' else None)
  e.ns['accepted_cycle_tail'](e.ops)
  assert e.events[-3:]==['heartbeat','projection','review']
  assert not e.failures and not e.wire and queue(e)==e.after_main_queue and budgets(e)==e.after_main_budget
  assert e.ops.execute('SELECT COUNT(*) FROM wt_discovered_subprovs').fetchone()[0]==0
 output=capsys.readouterr().out
 assert 'batch done: processed=3' in output and output.count('ownership check deferred: '+code)==2


def test_remaining_optional_candidates_continue_after_gate_busy(case,monkeypatch):
 e=case;e.seed('a-deferred');e.seed('b-deferred');e.seed('z-known',3)
 e.ops.execute("INSERT INTO wt_discovered_subprovs(subprov,creator_count,treasury,treasury_known,confidence,state) VALUES('z-known',1,'retained-treasury',1,0.8,'PROVISION_CANDIDATE')");e.ops.commit();real_current(e,monkeypatch);before=budgets(e);evidence=queue(e)
 with held_fixture_gate(e):assert e.ns['promote_recurring_funders'](e.ops)==1
 assert budgets(e)==before and queue(e)==evidence and not e.wire
 rows=e.ops.execute('SELECT subprov,creator_count,treasury,confidence,state FROM wt_discovered_subprovs').fetchall()
 assert [tuple(r) for r in rows]==[('z-known',3,'retained-treasury',0.8,'PROVISION_CANDIDATE')]


def test_repeated_contention_and_restart_preserve_all_evidence(case,monkeypatch,capsys):
 e=case;e.seed('candidate');real_current(e,monkeypatch);before=budgets(e);evidence=queue(e)
 with held_fixture_gate(e):
  for _ in range(4):
   e.g=e.m.Gate(e.g.path,'fixture',clock=lambda:e.now[0]);e.g.monotonic=lambda:e.now[0]
   e.ns.pop('_WALKBACK_RPC_MEMO',None)
   assert e.ns['promote_recurring_funders'](e.ops)==0
 assert budgets(e)==before and queue(e)==evidence and not e.wire
 assert e.g.read()['rpc_budget']['policy']['lifetime_credits']==100000
 assert e.ns['promote_recurring_funders'](e.ops)==0
 assert budgets(e)==before and queue(e)==evidence and not e.wire
 output=capsys.readouterr().out;assert output.count('HELIUS_GATE_BUSY')==4 and output.count('HELIUS_INVESTIGATION_REQUIRED')==1

@pytest.mark.parametrize('kind',['runtime_text','integrity_busy','generation','schema','storage','provider_timeout','credit_limit','cooldown','qualified_code_suffix'])
def test_unrelated_failures_remain_exact_and_fail_closed(case,kind):
 e=case;e.seed('candidate')
 errors={'runtime_text':RuntimeError('HELIUS_GATE_BUSY'),'integrity_busy':e.m.Integrity('HELIUS_GATE_BUSY'),'generation':e.m.Integrity('PAIR_GENERATION_MISMATCH'),'schema':sqlite3.DatabaseError('SCHEMA_MISMATCH'),'storage':e.m.Integrity('DISK_FLOOR'),'provider_timeout':TimeoutError('fixture timeout'),'credit_limit':e.m.BudgetDeferred('HELIUS_INVESTIGATION_CREDIT_LIMIT'),'cooldown':e.m.BudgetDeferred('HELIUS_REQUEST_COOLDOWN'),'qualified_code_suffix':e.m.BudgetDeferred('HELIUS_GATE_BUSY_DETAIL')};error=errors[kind]
 def fail(*args):raise error
 e.ns['_is_program_owned']=fail;before=budgets(e);evidence=queue(e)
 with pytest.raises(type(error)) as caught:e.ns['promote_recurring_funders'](e.ops)
 assert caught.value is error and not e.wire and budgets(e)==before and queue(e)==evidence


def test_exact_two_line_runtime_delta_only():
 old=subprocess.check_output(['git','show',BASE+':runtime/walkback/src/core/walkback_worker.py'],cwd=ROOT)
 assert hashlib.sha256(old).hexdigest()=='7ec02a09c8014ba4be95825c6128a810599c5f176a1d179eb92bc3c0e0c32501'
 expected=old.decode().replace('if str(exc) != "HELIUS_INVESTIGATION_REQUIRED":','if str(exc) not in ("HELIUS_INVESTIGATION_REQUIRED", "HELIUS_GATE_BUSY"):').replace('"HELIUS_INVESTIGATION_REQUIRED", flush=True)\n                continue','f"{exc}", flush=True)\n                continue')
 assert hashlib.sha256(expected.encode()).hexdigest()==hashlib.sha256(WORKER.read_bytes()).hexdigest()
