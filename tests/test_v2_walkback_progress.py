"""Actual DEV owners, disposable indexed stores, network forbidden."""
import ast, contextlib, importlib.util, json, pathlib, sqlite3, sys, types
from concurrent.futures import ThreadPoolExecutor
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1]

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
 import socket
 def forbidden(*a,**k):raise AssertionError('provider calls forbidden')
 monkeypatch.setattr(socket.socket,'connect',forbidden);monkeypatch.setattr(socket,'create_connection',forbidden)

@pytest.fixture
def recon(tmp_path,monkeypatch):
 from src import ops as ops_package
 create_event_ledger=load('actual_ledger','runtime/walkback/src/ops/create_event_ledger.py')
 monkeypatch.setattr(ops_package,'create_event_ledger',create_event_ledger,raising=False)
 m=load('actual_recon','runtime/walkback/src/ops/anchor_reconciliation.py')
 from src.core import walkback_queue
 p=tmp_path/'ops.sqlite';o=sqlite3.connect(p);o.row_factory=sqlite3.Row
 walkback_queue.ensure_schema(o);m.ensure_schema(o);create_event_ledger.ensure_schema(o)
 live=sqlite3.connect(':memory:');live.row_factory=sqlite3.Row
 live.executescript('CREATE TABLE token_analysis(mint TEXT PRIMARY KEY,create_tx_signature TEXT);CREATE TABLE creator_funding_queue(mint TEXT,create_tx_signature TEXT,updated_at INTEGER);')
 def seed(n,start=0):
  for i in range(start,start+n):o.execute("INSERT INTO wt_walkback_queue(mint,creator,status,path_state,create_anchor_audit_state,walkback_class,attempts,enqueued_at) VALUES(?,?,'waiting','WAITING_FOR_CREATE_ANCHOR','MISSING_OR_MALFORMED','FULL_WALKBACK',0,?)",(f'm{i:05}',f'c{i}',i))
  o.commit()
 yield types.SimpleNamespace(m=m,o=o,live=live,seed=seed,p=p,ledger=create_event_ledger)
 o.close();live.close()

def test_fair_1550_restart_and_bounded_sweep(recon,monkeypatch):
 e=recon;e.seed(1550)
 monkeypatch.setattr(e.m,'resolve_anchor_with_priority',lambda *a,**k:dict(confidence='NONE',source=None))
 seen=[]
 for i in range(49):
  if i==10:
   e.o.close();e.o=sqlite3.connect(e.p);e.o.row_factory=sqlite3.Row
  result=e.m.reconcile_waiting_create_anchors(e.o,e.live,limit=32,ensure_schema_first=False)
  seen += [r['mint'] for r in result['skipped']]
 assert len(seen)==1550 and len(set(seen))==1550 and seen[0]=='m00000'
 assert e.o.execute('SELECT SUM(attempts),MIN(anchor_lookup_attempts),MAX(anchor_lookup_attempts) FROM wt_walkback_queue').fetchone()[:]==(0,1,1)
 e.o.execute("INSERT INTO wt_walkback_queue(mint,creator,status,path_state,create_anchor_audit_state,walkback_class,attempts,enqueued_at) VALUES('m01550','c1550','waiting','WAITING_FOR_CREATE_ANCHOR','MISSING_OR_MALFORMED','FULL_WALKBACK',0,1550)");e.o.commit()
 result=e.m.reconcile_waiting_create_anchors(e.o,e.live,limit=1,ensure_schema_first=False)
 assert result['skipped'][0]['mint']=='m00000'
 assert e.o.execute('SELECT COUNT(*) FROM wt_anchor_reconciliation_cursor_v1').fetchone()[0]==1

@pytest.mark.parametrize('confidence,source,expected',[('SAFE','creator_funding_queue',0),('SAFE','token_analysis',0),('CONFLICT','canonical_create_ledger',0),('SAFE','canonical_create_ledger',277)])
def test_277_candidates_need_ops_authority(recon,monkeypatch,confidence,source,expected):
 e=recon;e.seed(277);sig='5'*88
 monkeypatch.setattr(e.m,'resolve_anchor_with_priority',lambda *a,**k:dict(confidence=confidence,source=source,signature=sig))
 for _ in range(3):e.m.reconcile_waiting_create_anchors(e.o,e.live,limit=128,ensure_schema_first=False)
 assert e.o.execute("SELECT COUNT(*) FROM wt_walkback_queue WHERE status='pending'").fetchone()[0]==expected
 assert e.o.execute('SELECT COUNT(*) FROM wt_anchor_reconciliation_log').fetchone()[0]==expected
 assert e.o.execute('SELECT SUM(attempts) FROM wt_walkback_queue').fetchone()[0]==0

@pytest.mark.parametrize('crash',['before_progress','after_progress'])
def test_crash_rolls_back_cursor_and_examination(recon,monkeypatch,crash):
 e=recon;e.seed(2);original=e.m._fair_stuck_rows
 def fail(*a,**k):
  if crash=='after_progress':original(*a,**k)
  raise RuntimeError('simulated crash')
 monkeypatch.setattr(e.m,'_fair_stuck_rows',fail)
 with pytest.raises(RuntimeError):e.m.reconcile_waiting_create_anchors(e.o,e.live,ensure_schema_first=False)
 assert e.o.execute('SELECT COUNT(*) FROM wt_anchor_reconciliation_cursor_v1').fetchone()[0]==0
 assert e.o.execute('SELECT SUM(anchor_lookup_attempts) FROM wt_walkback_queue').fetchone()[0]==0

def test_dry_run_is_readonly(recon,monkeypatch):
 e=recon;e.seed(2);monkeypatch.setattr(e.m,'resolve_anchor_with_priority',lambda *a,**k:dict(confidence='NONE',source=None))
 before=e.o.iterdump();before=list(before)
 e.m.reconcile_waiting_create_anchors(e.o,e.live,dry_run=True,ensure_schema_first=False)
 assert list(e.o.iterdump())==before

def test_two_concurrent_reconcilers_do_not_examine_same_head(recon,monkeypatch):
 e=recon;e.seed(40);monkeypatch.setattr(e.m,'resolve_anchor_with_priority',lambda *a,**k:dict(confidence='NONE',source=None))
 def poll(_):
  with contextlib.closing(sqlite3.connect(e.p,timeout=3)) as o:
   o.row_factory=sqlite3.Row
   return e.m.reconcile_waiting_create_anchors(o,None,limit=20,ensure_schema_first=False)
 with ThreadPoolExecutor(2) as pool:results=list(pool.map(poll,range(2)))
 assert len({x['mint'] for r in results for x in r['skipped']})==40

def test_indexed_keyset_plan(recon):
 e=recon;e.seed(2)
 plan=' '.join(str(tuple(x)) for x in e.o.execute("EXPLAIN QUERY PLAN SELECT mint FROM wt_walkback_queue WHERE status='waiting' AND path_state='WAITING_FOR_CREATE_ANCHOR' AND (enqueued_at,mint)>(?,?) AND (enqueued_at,mint)<=(?,?) ORDER BY enqueued_at,mint LIMIT 32",(0,'',100,'z')))
 assert 'ix_anchor_waiting_cursor_v1' in plan and 'TEMP B-TREE' not in plan

def memo():
 import time
 tree=ast.parse((ROOT/'runtime/walkback/src/core/walkback_worker.py').read_text());node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='QualifiedResultMemo');ns={'time':time,'json':json};exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-result-memo>','exec'),ns);return ns['QualifiedResultMemo']

def tx(sig='sig'):
 return dict(slot=123,transaction=dict(signatures=[sig],message=dict(accountKeys=['a'],instructions=[])),meta=dict(err=None,preBalances=[1],postBalances=[1]))

def test_exact_reuse_and_copies_and_expiry():
 now=[1];m=memo()(clock=lambda:now[0]);calls=[]
 def acquire():calls.append(1);return tx()
 a=('generation','owner');p=['sig',dict(commitment='finalized')]
 first=m.call(a,'endpoint','getTransaction',p,acquire);first['slot']=999
 assert m.call(a,'endpoint','getTransaction',p,acquire)['slot']==123 and len(calls)==1
 for aa,ep,pp in [(('other','owner'),'endpoint',p),(a,'other',p),(a,'endpoint',['sig',dict(commitment='confirmed')])]:m.call(aa,ep,'getTransaction',pp,acquire)
 assert len(calls)==4
 now[0]+=3601;m.call(a,'endpoint','getTransaction',p,acquire);assert len(calls)==5
 # A new process/restart starts empty, never creates spending authority.
 memo()().call(a,'endpoint','getTransaction',p,acquire);assert len(calls)==6

@pytest.mark.parametrize('value',[None,{},dict(slot=1,meta=dict(err='failure')),dict(value=None),[]])
def test_incomplete_negative_and_failed_not_reused(value):
 m=memo()();calls=[]
 def acquire():calls.append(1);return value
 for _ in range(2):m.call(('g','owner'),'endpoint','getTransaction',['s'],acquire)
 assert len(calls)==2

def test_transport_failures_not_reused():
 m=memo()();calls=[]
 def fail():calls.append(1);raise TimeoutError()
 for _ in range(2):
  with pytest.raises(TimeoutError):m.call(('g','o'),'e','getTransaction',['s'],fail)
 assert len(calls)==2

def test_cache_bounds_and_concurrent_reuse():
 m=memo()();calls=[]
 def acquire():calls.append(1);return tx()
 with ThreadPoolExecutor(8) as pool:list(pool.map(lambda _:m.call(('g','o'),'e','getTransaction',['sig'],acquire),range(20)))
 assert len(calls)==1
 for i in range(1000):m.call(('g','o'),'e','getTransaction',[str(i)],lambda i=i:tx(str(i)))
 assert len(m.entries)==256 and m.bytes<=m.MAX_BYTES
 huge=tx();huge['extra']='x'*(m.MAX_RESULT_BYTES+1)
 before=len(m.entries);m.call(('g','o'),'e','getTransaction',['big'],lambda:huge)
 assert len(m.entries)==before


def test_real_ops_ledger_authority_and_duplicate_job(recon):
 e=recon;e.seed(3)
 e.ledger.record_create_event(e.o,signature='5'*88,mint='m00001',creator=None,source='WEBSOCKET')
 e.ledger.record_create_event(e.o,signature='6'*88,mint='m00002',creator='c2',source='WEBSOCKET')
 e.ledger.record_create_event(e.o,signature='7'*88,mint='m00002',creator='c2',source='WEBSOCKET')
 # Local signature candidates alone must not progress.
 e.live.execute("INSERT INTO creator_funding_queue VALUES('m00000',?,1)",('8'*88,));e.live.commit()
 for _ in range(2):e.m.reconcile_waiting_create_anchors(e.o,e.live,limit=32,ensure_schema_first=False)
 assert [tuple(x) for x in e.o.execute('SELECT mint,status FROM wt_walkback_queue ORDER BY mint')]==[('m00000','waiting'),('m00001','pending'),('m00002','waiting')]
 assert e.o.execute('SELECT COUNT(*) FROM wt_anchor_reconciliation_log').fetchone()[0]==1
 with pytest.raises(sqlite3.IntegrityError):e.o.execute("INSERT INTO wt_walkback_queue(mint,creator,status,walkback_class,enqueued_at) VALUES('m00000','c','waiting','FULL_WALKBACK',1)")
 e.o.rollback()


def test_disk_or_schema_refusal_preserves_cursor_and_queue(recon,monkeypatch):
 e=recon;e.seed(2)
 monkeypatch.setattr(e.m,'resolve_anchor_with_priority',lambda *a,**k:dict(confidence='NONE',source=None))
 e.o.execute("CREATE TRIGGER refuse_progress BEFORE INSERT ON wt_anchor_reconciliation_cursor_v1 BEGIN SELECT RAISE(ABORT,'DISK_FLOOR_REFUSAL'); END");e.o.commit()
 with pytest.raises(sqlite3.IntegrityError,match='DISK_FLOOR'):e.m.reconcile_waiting_create_anchors(e.o,e.live,ensure_schema_first=False)
 assert e.o.execute('SELECT SUM(anchor_lookup_attempts) FROM wt_walkback_queue').fetchone()[0]==0
 assert e.o.execute('SELECT COUNT(*) FROM wt_anchor_reconciliation_cursor_v1').fetchone()[0]==0


def test_new_arrivals_cannot_extend_current_sweep(recon,monkeypatch):
 e=recon;e.seed(10);monkeypatch.setattr(e.m,'resolve_anchor_with_priority',lambda *a,**k:dict(confidence='NONE',source=None))
 first=e.m.reconcile_waiting_create_anchors(e.o,e.live,limit=5,ensure_schema_first=False)
 e.seed(20,10)
 second=e.m.reconcile_waiting_create_anchors(e.o,e.live,limit=5,ensure_schema_first=False)
 third=e.m.reconcile_waiting_create_anchors(e.o,e.live,limit=5,ensure_schema_first=False)
 assert [x['mint'] for x in first['skipped']]==[f'm{i:05}' for i in range(5)]
 assert [x['mint'] for x in second['skipped']]==[f'm{i:05}' for i in range(5,10)]
 assert third['skipped'][0]['mint']=='m00000'


from tests.test_v2_helius_investigation_budget import v,legacy

@pytest.fixture
def actual_rpc(v,monkeypatch):
 import typing,urllib.request,os
 e=v;e.root_id('root');e.open();e.h._role='walkback';wire=[]
 @contextlib.contextmanager
 def fake(req,*,timeout):
  reservation=e.h._admit(req.full_url,'POST',req.data);payload=json.loads(req.data);wire.append(payload)
  result={'value':{'owner':'11111111111111111111111111111111'}} if payload['method']=='getAccountInfo' else tx(payload['params'][0])
  try:yield types.SimpleNamespace(read=lambda:json.dumps({'result':result}).encode())
  finally:e.h._finish(reservation,200)
 monkeypatch.setattr(e.m,'urlopen',fake)
 source=ROOT/'runtime/walkback/src/core/walkback_worker.py';tree=ast.parse(source.read_text())
 names={'_rpc','_rpc_uncached','_get_tx','_get_account_owner','_collect_hop1_evidence'}
 ns={'Optional':typing.Optional,'RpcTransport':typing.Callable,'json':json,'os':os,'urllib':urllib,'RPC_URL':'https://mainnet.helius-rpc.com/?api-key=FIXTURE_NOT_REAL','RPC_TIMEOUT':1,'QualifiedResultMemo':memo(),'AcquisitionUnavailable':type('AcquisitionUnavailable',(RuntimeError,),{})}
 exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),str(source),'exec'),ns)
 e.ns=ns;e.wire=wire
 row_fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_process_row');handler=next(n for n in row_fn.body if isinstance(n,ast.Try)).handlers[-1]
 mini=ast.parse('def retain_deferral(ops,mint,attempts,rpc,problem):\n try:\n  raise problem\n except Exception:\n  pass\n');mini.body[0].body[0].handlers=[handler];ast.fix_missing_locations(mini);ns['time']=types.SimpleNamespace(time=lambda:e.now[0]);ns['MAX_ATTEMPTS']=3;exec(compile(mini,'<actual-deferral-handler>','exec'),ns)
 return e

@pytest.mark.parametrize('kind',['account','hop1_transaction'])
def test_actual_repeated_success_reuses_without_new_spend(actual_rpc,kind):
 e=actual_rpc;alias=e.h._hash('mint:root')
 with e.h.resume_investigation(alias):
  if kind=='account':
   one=e.ns['_get_account_owner']('same-funder',[0]);two=e.ns['_get_account_owner']('same-funder',[0]);assert one==two
  else:
   e.ns['_get_tx']('retained-sig');e.ns['_collect_hop1_evidence']('WSOL_WRAP_CLOSE','retained-sig')
 assert len(e.wire)==1
 budget=e.g.read()['rpc_budget'];assert budget['scopes'][alias]['credits']==1 and not budget['inflight']
 assert budget['policy']['lifetime_credits']==100000


def test_actual_restart_preserves_paid_history_and_cooldown(actual_rpc):
 e=actual_rpc;alias=e.h._hash('mint:root')
 with e.h.resume_investigation(alias):e.ns['_get_tx']('retained-sig')
 before=e.g.read();e.ns.pop('_WALKBACK_RPC_MEMO')
 with e.h.resume_investigation(alias):
  with pytest.raises(e.m.BudgetDeferred,match='HELIUS_REQUEST_COOLDOWN'):e.ns['_get_tx']('retained-sig')
 assert e.g.read()==before and len(e.wire)==1
 e.now[0]+=61
 with e.h.resume_investigation(alias):e.ns['_get_tx']('retained-sig')
 assert len(e.wire)==2 and e.g.read()['rpc_budget']['scopes'][alias]['credits']==2

@pytest.mark.parametrize('code',['HELIUS_GATE_BUSY','HELIUS_INVESTIGATION_CREDIT_LIMIT','HELIUS_REQUEST_COOLDOWN','HELIUS_INFLIGHT_LIMIT'])
def test_actual_handler_retains_exact_reason_and_refunds_only_claim(actual_rpc,code):
 import os
 e=actual_rpc;o=sqlite3.connect(':memory:');o.execute('CREATE TABLE wt_walkback_queue(mint TEXT,status TEXT,attempts INTEGER,lease_expires_at INTEGER,next_retry_at INTEGER,last_error TEXT,updated_at INTEGER,claimed_by TEXT,rpc_used INTEGER)')
 worker=os.environ.get('WALKBACK_WORKER_ID',f'walkback-{os.getpid()}');o.execute("INSERT INTO wt_walkback_queue VALUES('root','running',1,2000,0,NULL,0,?,0)",(worker,));o.commit()
 before=e.g.read();e.ns['retain_deferral'](o,'root',0,[2],e.m.BudgetDeferred(code))
 assert o.execute('SELECT status,attempts,last_error,rpc_used FROM wt_walkback_queue').fetchone()==('pending',0,code,2)
 assert e.g.read()==before


def test_wrong_generation_and_unowned_cannot_use_memo(actual_rpc,monkeypatch):
 e=actual_rpc
 with e.h.resume_investigation(e.h._hash('mint:root')):e.ns['_get_tx']('retained-sig')
 with pytest.raises(e.m.BudgetDeferred):e.ns['_get_tx']('retained-sig')
 def wrong():raise e.m.Integrity('PAIR_GENERATION_MISMATCH')
 monkeypatch.setattr(e.m,'current',wrong)
 with pytest.raises(e.m.Integrity,match='PAIR_GENERATION'):e.ns['_get_tx']('retained-sig')
 assert len(e.wire)==1


def test_all_277_synthetic_candidates_against_real_ledger(recon):
 e=recon;e.seed(277)
 alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
 def sig(i):return '5'*85+alphabet[i//(58*58)]+alphabet[(i//58)%58]+alphabet[i%58]
 for i in range(277):
  signature=sig(i);mint=f'm{i:05}'
  e.live.execute('INSERT INTO creator_funding_queue VALUES(?,?,1)',(mint,signature))
  # Only 100 have accepted OPS CREATE authority; the other 177 must wait.
  if i<100:e.ledger.record_create_event(e.o,signature=signature,mint=mint,creator=f'c{i}',source='WEBSOCKET')
 e.live.commit()
 for _ in range(9):e.m.reconcile_waiting_create_anchors(e.o,e.live,limit=32,ensure_schema_first=False)
 assert e.o.execute("SELECT COUNT(*) FROM wt_walkback_queue WHERE status='pending'").fetchone()[0]==100
 assert e.o.execute("SELECT COUNT(*) FROM wt_walkback_queue WHERE status='waiting'").fetchone()[0]==177
 assert e.o.execute('SELECT SUM(attempts) FROM wt_walkback_queue').fetchone()[0]==0
 assert e.o.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_caller_transaction_not_committed_and_schema_init_repeat(recon):
 e=recon;e.seed(1);e.m.ensure_schema(e.o);e.m.ensure_schema(e.o)
 assert e.m.validate_schema(e.o)=='VALID'
 e.o.execute("UPDATE wt_walkback_queue SET attempts=1")
 with pytest.raises(RuntimeError,match='OWN_TRANSACTION'):e.m.reconcile_waiting_create_anchors(e.o,e.live)
 e.o.rollback();assert e.o.execute('SELECT attempts FROM wt_walkback_queue').fetchone()[0]==0
 e.o.execute('DROP INDEX ix_anchor_waiting_cursor_v1');e.o.commit()
 assert e.m.validate_schema(e.o)=='ANCHOR_RECONCILIATION_CURSOR_INDEX_MISMATCH'


def test_tied_enqueued_keys_are_fair(recon,monkeypatch):
 e=recon;e.seed(40);e.o.execute('UPDATE wt_walkback_queue SET enqueued_at=1');e.o.commit()
 monkeypatch.setattr(e.m,'resolve_anchor_with_priority',lambda *a,**k:dict(confidence='NONE',source=None))
 seen=[]
 for _ in range(4):seen.extend(x['mint'] for x in e.m.reconcile_waiting_create_anchors(e.o,e.live,limit=10,ensure_schema_first=False)['skipped'])
 assert len(set(seen))==40
