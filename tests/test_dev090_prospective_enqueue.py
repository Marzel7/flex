"""Actual exact-mint enqueue and listener transaction with compact offline SQLite."""
import ast,asyncio,sqlite3,time,os,types
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
import pytest
from src.ops import walkback_create_handoff as h
from tests.test_dev090_handoff import encoded
ROOT=Path(__file__).resolve().parents[1]
M=encoded(109); C=encoded(99); S=encoded(115,64)
@pytest.fixture
def f(tmp_path,monkeypatch):
 core=tmp_path/'core.db';ops=tmp_path/'ops.db';start=int(time.time())-100
 c=sqlite3.connect(core);c.row_factory=sqlite3.Row
 c.executescript("""CREATE TABLE token_analysis(mint TEXT PRIMARY KEY,earliest_tx_creator TEXT,pf_ws_creator TEXT,create_tx_signature TEXT,migrated_at INTEGER,lifecycle_stage TEXT,analyzed_at REAL,created_at REAL,source_platform TEXT,migration_tx TEXT,dex TEXT,pumpswap_pool_address TEXT,pool_address TEXT,migration_slot INTEGER,is_new INTEGER);
CREATE TABLE creator_resolution_queue(mint TEXT PRIMARY KEY,status TEXT,priority INTEGER,reason TEXT,source TEXT,next_attempt_at INTEGER,locked_until INTEGER,attempts INTEGER,last_error TEXT,create_tx_signature TEXT,migrated_at INTEGER,created_at INTEGER,updated_at INTEGER,resolved_creator TEXT);
CREATE TABLE migration_persist_queue(signature TEXT UNIQUE,source TEXT,mint TEXT,received_at INTEGER,status TEXT,last_error TEXT);""")
 c.execute('BEGIN IMMEDIATE');h.initialize(c,epoch='offline',activated_at=start,core_base=0,ops_base=0);c.execute('UPDATE wb_create_control SET enabled=1');c.commit()
 with sqlite3.connect(ops) as o:o.execute('CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY,status TEXT,enqueued_at INTEGER,creator TEXT,create_anchor_signature TEXT,create_anchor_audit_state TEXT)')
 source=ROOT/'src/core/pumpfun_curve_listener.py';names={'enqueue_prospective_migration'}
 ns={'sqlite3':sqlite3,'time':time,'P0_PRIORITY':200,'Dict':dict,'Any':object}
 nodes=[n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name in names]
 exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),ns)
 monkeypatch.setenv('CRQ_HISTORICAL_DISCOVERY_ENABLED','0')
 def token(creator=None,sig=None,at=None,other=None):
  c.execute("INSERT INTO token_analysis(mint,earliest_tx_creator,pf_ws_creator,create_tx_signature,migrated_at,lifecycle_stage) VALUES(?,?,?,?,?,'migrated')",(M,creator,other if other is not None else creator,sig,start+10 if at is None else at));c.commit()
 def enqueue(**kw):return ns['enqueue_prospective_migration'](c,M,core_path=core,ops_path=ops,**kw)
 def run(**kw):
  c.execute('BEGIN IMMEDIATE')
  try:r=enqueue(**kw);c.commit();return r
  except BaseException:c.rollback();raise
 def jobs():return c.execute('select status,attempts,next_attempt_at,source from creator_resolution_queue').fetchall()
 yield types.SimpleNamespace(c=c,core=core,ops=ops,start=start,token=token,enqueue=enqueue,run=run,jobs=jobs,ns=ns)
 c.close()
 assert all(p.stat().st_size<8388608 for p in tmp_path.iterdir() if p.is_file())
@pytest.mark.parametrize('creator,sig',[(None,None),(C,None),(None,S)])
def test_missing_authority_enqueued_with_discovery_disabled(f,creator,sig):
 f.token(creator,sig);assert f.run()=='ENQUEUED';assert tuple(f.jobs()[0])[:2]==('pending',0)
def test_qualified_pumpportal_fast_path(f):
 f.token(C,S);assert f.run(accepted_birth=(C,S))=='QUALIFIED_PUMPPORTAL';assert not f.jobs()
def test_plain_signer_fields_are_not_qualified_proof(f):
 f.token(C,S);assert f.run()=='ENQUEUED';assert f.c.execute('select earliest_tx_creator from token_analysis').fetchone()[0]==C
@pytest.mark.parametrize('status',['skipped','complete','failed'])
def test_ops_terminal_never_reactivated(f,status):
 f.token();assert f.run(ops_row=(status,f.start+10))=='OPS_PROTECTED';assert not f.jobs()
@pytest.mark.parametrize('kind',['historical-queued','historical-unqueued','missing-time'])
def test_historical_original_boundary(f,kind):
 f.token(at=f.start-1)
 if kind=='missing-time':f.c.execute('update token_analysis set migrated_at=NULL');f.c.commit()
 assert f.run()=='HISTORICAL';assert not f.jobs()
def test_boundary_collision_is_inclusive(f):
 f.token(at=f.start);assert f.run()=='ENQUEUED'
def test_duplicate_restart_keeps_attempt_backoff(f):
 f.token();assert f.run()=='ENQUEUED';f.c.execute("update creator_resolution_queue set status='retry',attempts=3,next_attempt_at=9999999999");f.c.commit()
 for _ in range(3):assert f.run()=='ALREADY_ACTIVE'
 assert tuple(f.jobs()[0])[:3]==('retry',3,9999999999)
@pytest.mark.parametrize('status',['skipped','complete','failed','ignored'])
def test_creator_terminal_never_reactivated(f,status):
 f.token();f.run();f.c.execute('update creator_resolution_queue set status=?',(status,));f.c.commit();assert f.run()=='QUEUE_TERMINAL'
def test_creator_conflict_fail_closed(f):
 f.token(C,None,other=encoded(98));assert f.run()=='CREATOR_CONFLICT';assert not f.jobs()
def test_crash_before_and_after_commit(f):
 f.token();f.c.execute('BEGIN IMMEDIATE');assert f.enqueue()=='ENQUEUED';f.c.rollback();assert not f.jobs()
 assert f.run()=='ENQUEUED';assert f.run()=='ALREADY_ACTIVE';assert len(f.jobs())==1
def test_qualified_core_proof_not_reacquired(f):
 f.token(C,S);f.c.execute('BEGIN IMMEDIATE');h.stage(f.c,h.Proof(M,C,S,'STRICT_RPC','DEV074_STRICT_CREATE',S,f.start+10),core_path=f.core,ops_path=f.ops);f.c.commit();assert f.run()=='QUALIFIED_CORE_PROOF';assert not f.jobs()
def test_migration_before_birth_then_strict_create_stages_unchanged_handoff(f):
 f.token();assert f.run()=='ENQUEUED';f.c.execute('BEGIN IMMEDIATE');f.c.execute('update token_analysis set earliest_tx_creator=?,pf_ws_creator=?,create_tx_signature=?',(C,C,S));data={'creator':C,'create_tx_signature':S,'is_pumpfun_create':True,'creator_source':'rpc_pump_create','validation_status':'validated'};assert h.stage_rpc_completion(f.c,M,data,core_path=f.core,ops_path=f.ops)=='PENDING';f.c.commit();assert f.c.execute('select state from wb_create_delivery').fetchone()[0]=='PENDING'
def test_existing_helpers_require_no_population_scan(f):
 f.token();plans=f.c.execute('EXPLAIN QUERY PLAN SELECT * FROM creator_resolution_queue WHERE mint=?',(M,)).fetchall();assert all('SCAN ' not in row[3] for row in plans)
def test_listener_enqueues_in_same_commit_and_before_duplicate_guard():
 source=(ROOT/'src/core/pumpfun_curve_listener.py').read_text();tree=ast.parse(source);method=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_mark_token_migrated_in_db');text=ast.unparse(method)
 assert text.index('enqueue_prospective_migration(conn')<text.index('conn.commit()');assert 'read_only=True' in text
 # The primary path marks migration before RPC discovery; duplicate retry uses the same method.
 assert source.count('await self._mark_token_migrated_in_db(')>=2

@pytest.mark.parametrize('crash',['none','before-commit','after-commit'])
def test_actual_listener_migration_transaction_and_restart(f,monkeypatch,crash):
 import sys
 class Crash(BaseException):pass
 events=[]
 class Connection:
  def __init__(self,c):self.c=c
  def __getattr__(self,k):return getattr(self.c,k)
  def commit(self):
   if crash=='before-commit':raise Crash()
   self.c.commit();events.append('commit')
   if crash=='after-commit':raise Crash()
 @contextmanager
 def managed(path,read_only=False,**kw):
  c=sqlite3.connect(path);c.row_factory=sqlite3.Row
  if read_only:c.execute('pragma query_only=on');events.append('ops-read')
  else:events.append('shared-lane')
  try:yield c if read_only else Connection(c)
  except BaseException:c.rollback();raise
  finally:c.close();events.append('close')
 # Actual listener-local helper; standalone worker module is untouched.
 source=ROOT/'src/core/pumpfun_curve_listener.py';method=next(n for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_mark_token_migrated_in_db')
 ns={'enqueue_prospective_migration':f.ns['enqueue_prospective_migration'],'TOKEN_PREDICTION_RUNTIME_ENABLED':False,'time':time,'asyncio':asyncio,'Optional':Optional,'DB_PATH':str(f.core),'managed_db_connect':managed,'PRIORITY_P0_CRITICAL_INGESTION':0,'_listener_ops_db_path':lambda:str(f.ops),'log_print':lambda *a,**k:None}
 exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),ns)
 actor=types.SimpleNamespace(_portal_vsol={})
 def run():return asyncio.run(ns['_mark_token_migrated_in_db'](actor,M,migrated_at=f.start+10,migration_tx=S))
 if crash=='none':run()
 else:
  with pytest.raises(Crash):run()
 assert events.index('ops-read')<events.index('shared-lane')
 token=f.c.execute('select mint from token_analysis').fetchone()
 assert bool(token)==(crash!='before-commit');assert len(f.jobs())==(0 if crash=='before-commit' else 1)
 if crash!='before-commit':assert f.run()=='ALREADY_ACTIVE'

def test_existing_valid_ops_anchor_not_reacquired_after_restart(f):
 f.token(C,S);assert f.run(ops_row=('pending',f.start+10,C,S,'VALID'))=='QUALIFIED_OPS_ANCHOR';assert not f.jobs()


def test_trade_metadata_cannot_replace_original_birth_authority(f):
 tree=ast.parse((ROOT/'src/core/pumpfun_curve_listener.py').read_text());method=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='listen_pumpportal_websocket')
 trade=next(n.value for n in ast.walk(method) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Subscript) and ast.unparse(t.value)=='self._portal_vsol' for t in n.targets) and isinstance(n.value,ast.Dict))
 row=eval(compile(ast.Expression(trade),'fixture','eval'),{'existing':{'creator':C,'birth_creator':None,'birth_signature':S},'data':{'traderPublicKey':C},'v_sol':0,'mc_sol':0,'time':time})
 assert row['birth_creator'] is None and row['birth_signature']==S
 f.token(C,S);assert f.run(accepted_birth=(row['birth_creator'],row['birth_signature']))=='ENQUEUED'
