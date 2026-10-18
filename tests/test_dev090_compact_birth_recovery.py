"""Compact recovery uses exact listener code and disposable SQLite only."""
import ast,asyncio,copy,hashlib,json,sqlite3,time,types
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
import pytest
from src.core.pumpportal_birth_audit import configured_birth_audit
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'src/core/pumpfun_curve_listener.py'
M='fixture-mint';C='fixture-creator';S='fixture-signature';T=1792357618123456789
DATA={'txType':'create','mint':M,'traderPublicKey':C,'bondingCurveKey':None,'symbol':'FIX','name':'Fixture','unneeded_raw_field':{'secret':'SYNTHETIC_ONLY_DO_NOT_RETAIN'}}
class StopCycle(BaseException):pass

@pytest.fixture
def f(tmp_path,monkeypatch):
 db=tmp_path/'core.db';ops=tmp_path/'ops.db';logs=[];receipts=[]
 with sqlite3.connect(db) as c:
  c.executescript('''CREATE TABLE webhook_birth_queue(id INTEGER PRIMARY KEY AUTOINCREMENT,signature TEXT UNIQUE,payload TEXT,source TEXT,consumed INTEGER DEFAULT0,retry_count INTEGER DEFAULT0);
CREATE TABLE token_analysis(mint TEXT PRIMARY KEY,created_at TEXT,analyzed_at REAL,earliest_tx_creator TEXT,pf_ws_creator TEXT,bonding_curve_pda TEXT,create_tx_signature TEXT,source_platform TEXT,lifecycle_stage TEXT,is_new INTEGER,migration_signal_source TEXT,migration_signal_updated_at INTEGER,first_pre_migration_signal_at INTEGER,migrated_at INTEGER);
CREATE TABLE birth_persist_queue(mint TEXT PRIMARY KEY,creator TEXT,created_at TEXT,bonding_curve_pda TEXT,create_tx_signature TEXT,symbol TEXT,name TEXT,received_at INTEGER,status TEXT,last_error TEXT,last_attempt_at INTEGER,wb_birth_source TEXT);
CREATE TABLE wb_create_control(id INTEGER PRIMARY KEY,core_base INTEGER,ops_base INTEGER,enabled INTEGER);INSERT INTO wb_create_control VALUES(1,0,0,0);'''.replace('DEFAULT0','DEFAULT 0'))
 with sqlite3.connect(ops) as c:c.execute('CREATE TABLE marker(id INTEGER)')
 @contextmanager
 def writer(path,**kwargs):
  c=sqlite3.connect(path)
  try:yield c
  except BaseException:c.rollback();raise
  finally:c.close()
 async def noop(*a,**k):pass
 async def stop(*a,**k):raise StopCycle()
 ns={'json':json,'time':time,'asyncio':asyncio,'Optional':Optional,'DB_PATH':str(db),'PRIORITY_P0_CRITICAL_INGESTION':0,'managed_db_connect':writer,'_listener_ops_db_path':lambda:str(ops),'_birth_telemetry_incr':lambda *a:None,'log_print':lambda *a,**k:logs.append(' '.join(map(str,a))),'premig_log':lambda s:logs.append(s),'TOKEN_PREDICTION_RUNTIME_ENABLED':False,'_spawn_symbol_fetch':lambda *a:None,'_broadcast_to_flask':lambda *a:None,'CrossProcessDatabaseWriteTimeout':type('WriteTimeout',(Exception,),{}),'NestedDatabaseWriteError':type('NestedWrite',(Exception,),{})}
 tree=ast.parse(SOURCE.read_text());names={'_compact_pumpportal_birth_retry','_decode_compact_pumpportal_birth_retry','_compact_birth_retry_storage_check','_ensure_webhook_birth_queue_schema','_handle_pumpportal_create_frame','_insert_bonding_curve_token','drain_webhook_birth_queue','_mark_consumed'}
 nodes=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'_PP_BIRTH_RETRY_MAX_ROWS','_PP_BIRTH_RETRY_MAX_BYTES'} for t in n.targets)]
 nodes += [n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
 exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),ns)
 monkeypatch.setenv('PUMPPORTAL_CANONICAL_BIRTH_RETENTION_ENABLED','0');monkeypatch.setenv('PUMPPORTAL_CANONICAL_BIRTH_RETENTION_PATH',str(tmp_path/'raw-forbidden'))
 def actor():
  obj=types.SimpleNamespace(_eb_birth_audit=configured_birth_audit(),_pumpfun_delivery_latency=types.SimpleNamespace(observe_pumpportal=lambda **kw:receipts.append(kw)),_byzantine_birth_signal=None,_portal_vsol={},completed_launches=set(),seen_mints=set(),_ensure_pf_ws_creator=noop,_upsert_birth_metadata_cache=noop,_remember_recent_birth_token=lambda *a:None,_remember_bonding_curve_token=lambda *a:None,handle_birth=lambda *a:(_ for _ in ()).throw(AssertionError('unexpected provider acquisition')))
  obj._insert_bonding_curve_token=types.MethodType(ns['_insert_bonding_curve_token'],obj);return obj
 def produce(obj,data=None,received=T):
  async def work():
   await ns['_handle_pumpportal_create_frame'](obj,DATA if data is None else data,signature=S,mint=M,receive_utc_ns=received,receive_monotonic_ns=123,tracked_trade_mints=set(),migration_sol_threshold=100,ws=None);await asyncio.sleep(0)
  asyncio.run(work())
 def consume(obj):
  real=ns['asyncio'];ns['asyncio']=types.SimpleNamespace(sleep=stop,to_thread=real.to_thread,get_running_loop=real.get_running_loop,create_task=real.create_task)
  try:
   with pytest.raises(StopCycle):asyncio.run(ns['drain_webhook_birth_queue'](obj))
  finally:ns['asyncio']=real
  # drainer imports asyncio only indirectly through the namespace; inserts still need real API.
 def rows():
  with sqlite3.connect(db) as c:return c.execute('SELECT signature,payload,source,consumed,retry_count FROM webhook_birth_queue').fetchall()
 async def fail(*a,**k):raise RuntimeError('synthetic raw error MUST NOT BE LOGGED')
 yield types.SimpleNamespace(db=db,ops=ops,ns=ns,actor=actor,produce=produce,consume=consume,rows=rows,fail=fail,logs=logs,receipts=receipts,tmp=tmp_path)
 assert not (tmp_path/'raw-forbidden').exists()
 assert all(p.stat().st_size<8388608 for p in tmp_path.rglob('*') if p.is_file())


def test_normal_birth_preserves_direct_contract(f):
 a=f.actor();f.produce(a)
 with sqlite3.connect(f.db) as c:r=c.execute('SELECT earliest_tx_creator,create_tx_signature,source_platform FROM token_analysis WHERE mint=?',(M,)).fetchone()
 assert r==(C,S,'pumpfun') and not f.rows() and f.receipts[0]['receive_utc_ns']==T


def seed_failure(f):
 a=f.actor();a._insert_bonding_curve_token=f.fail;f.produce(a)
 assert S not in a.completed_launches
 return a


def test_compact_failure_record_and_duplicate_keep_first_receipt(f):
 a=seed_failure(f);f.produce(a,received=T+1000)
 r=f.rows();assert len(r)==1 and r[0][2:]==('pumpportal_retry',0,0)
 payload=json.loads(r[0][1]);assert 'unneeded_raw_field' not in payload
 assert payload['mint']==M and payload['traderPublicKey']==C and payload['signature']==S
 assert payload['receive_utc_ns']==T and payload['provenance']=='PUMPPORTAL_NEW_TOKEN'
 assert len(r[0][1].encode())<=4096 and not any('MUST NOT BE LOGGED' in x for x in f.logs)


def test_retry_restart_success_requires_real_commit(f):
 seed_failure(f);a=f.actor();f.consume(a)
 r=f.rows()[0];assert r[3:]==(1,0)
 with sqlite3.connect(f.db) as c:
  assert c.execute('SELECT mint,earliest_tx_creator,create_tx_signature,created_at FROM token_analysis').fetchone()==(M,C,S,str(T//1000000000))
 f.consume(f.actor());assert len(f.rows())==1 and json.loads(f.rows()[0][1])['receive_utc_ns']==T


def test_false_commit_result_cannot_ack_even_matching_existing_token(f):
 seed_failure(f)
 with sqlite3.connect(f.db) as c:c.execute('INSERT INTO token_analysis(mint,earliest_tx_creator,pf_ws_creator,create_tx_signature) VALUES(?,?,?,?)',(M,C,C,S))
 a=f.actor()
 async def unconfirmed(*a,**k):return False
 a._insert_bonding_curve_token=unconfirmed;a.completed_launches.add(S);f.consume(a)
 assert f.rows()[0][3:]==(0,1)
 assert any('RETRY_PENDING' in x for x in f.logs)


def test_failure_remains_pending_then_recovers(f):
 seed_failure(f);a=f.actor();a._insert_bonding_curve_token=f.fail;f.consume(a)
 assert f.rows()[0][3:]==(0,1)
 f.consume(f.actor());assert f.rows()[0][3:]==(1,1)


def test_crash_after_persist_before_queue_ack_is_idempotent(f):
 seed_failure(f);a=f.actor();original=f.ns['_mark_consumed']
 def crash(*args):raise StopCycle()
 f.ns['_mark_consumed']=crash;f.consume(a)
 assert f.rows()[0][3]==0
 with sqlite3.connect(f.db) as c:assert c.execute('SELECT count(*) FROM token_analysis').fetchone()[0]==1
 f.ns['_mark_consumed']=original;f.consume(a)
 assert f.rows()[0][3]==1
 with sqlite3.connect(f.db) as c:assert c.execute('SELECT count(*) FROM token_analysis').fetchone()[0]==1


def test_retry_store_failure_explicit_unrecoverable_without_raw_file(f,monkeypatch):
 a=f.actor();a._insert_bonding_curve_token=f.fail
 def fail_connect(*a,**kw):raise sqlite3.OperationalError('fixture unavailable')
 with monkeypatch.context() as m:
  m.setattr(sqlite3,'connect',fail_connect);f.produce(a)
 assert not f.rows()
 assert any('UNRECOVERABLE_BIRTH_RETRY' in x for x in f.logs)
 assert not any('COMPACT_BIRTH_RETRY_PENDING' in x for x in f.logs)


def test_lifetime_quota_is_bounded_and_does_not_delete_or_reopen(f):
 f.ns['_PP_BIRTH_RETRY_MAX_ROWS']=2
 with sqlite3.connect(f.db) as c:c.executemany('INSERT INTO webhook_birth_queue(signature,payload,source,consumed) VALUES(?,?,?,1)',[('old1','{}','existing'),('old2','{}','existing')])
 a=f.actor();a._insert_bonding_curve_token=f.fail;f.produce(a)
 with sqlite3.connect(f.db) as c:assert c.execute('SELECT signature,consumed FROM webhook_birth_queue ORDER BY id').fetchall()==[('old1',1),('old2',1)]
 assert any('UNRECOVERABLE_BIRTH_RETRY' in x for x in f.logs)

@pytest.mark.parametrize('change',[{'_birth_retry_schema':2},{'signature':'wrong'},{'mint':[]},{'provenance':'signer_inference'},{'receive_utc_ns':None},{'extra_raw':DATA}])
def test_malformed_compact_record_never_calls_provider_or_is_consumed(f,change):
 payload=json.loads(f.ns['_compact_pumpportal_birth_retry'](DATA,mint=M,signature=S,receive_utc_ns=T));payload.update(change)
 with sqlite3.connect(f.db) as c:c.execute('INSERT INTO webhook_birth_queue(signature,payload,source) VALUES(?,?,?)',(S,json.dumps(payload),'pumpportal_retry'))
 a=f.actor();a.completed_launches.add(S);f.consume(a)
 assert f.rows()[0][3:]==(0,1)
 with sqlite3.connect(f.db) as c:assert c.execute('SELECT count(*) FROM token_analysis').fetchone()[0]==0

@pytest.mark.parametrize('payload',[None,'not-json','[]','x'*4097])
def test_missing_or_oversized_record_stays_pending_without_rpc(f,payload):
 with sqlite3.connect(f.db) as c:c.execute('INSERT INTO webhook_birth_queue(signature,payload,source) VALUES(?,?,?)',(S,payload,'pumpportal_retry'))
 f.consume(f.actor());assert f.rows()[0][3:]==(0,1)


def test_oversized_or_malformed_event_fails_closed_without_truncation(f):
 a=f.actor();a._insert_bonding_curve_token=f.fail
 f.produce(a,{**DATA,'name':'x'*1025});assert not f.rows()
 assert any('UNRECOVERABLE_BIRTH_RETRY' in x for x in f.logs)


def test_storage_pressure_cannot_admit_retry(f):
 a=f.actor();a._insert_bonding_curve_token=f.fail
 def pressure(*args):raise RuntimeError('qualified storage stop')
 f.ns['_compact_birth_retry_storage_check']=pressure
 f.produce(a);assert not f.rows() and any('UNRECOVERABLE_BIRTH_RETRY' in x for x in f.logs)


def test_ack_change_has_exact_statement_reversal():
 d=json.loads((ROOT/'docs/audits/dev090/compact-birth-recovery.json').read_text())
 n=next(n for n in ast.walk(ast.parse(SOURCE.read_text())) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_insert_bonding_curve_token')
 assert isinstance(n.body[-1],ast.Return) and n.body[-1].value.value is True
 n.body.pop();n.returns=ast.Constant(value=None)
 changed=0
 for ret in ast.walk(n):
  if isinstance(ret,ast.Return) and isinstance(ret.value,ast.Constant) and ret.value.value is False:ret.value=None;changed+=1
 assert changed==1
 assert hashlib.sha256(ast.dump(n,include_attributes=False).encode()).hexdigest()==d['insert_preimage_ast_sha256']


def test_all_unrelated_listener_code_matches_preimage():
 d=json.loads((ROOT/'docs/audits/dev090/compact-birth-recovery.json').read_text())
 class Filter(ast.NodeTransformer):
  def visit_FunctionDef(self,n):
   if n.name in d['new_helpers']:return None
   if n.name in d['changed_existing_functions']:
    n.body=[ast.Pass()]
    if n.name=='_insert_bonding_curve_token':n.returns=ast.Constant(value=None)
    return n
   return self.generic_visit(n)
  visit_AsyncFunctionDef=visit_FunctionDef
  def visit_Assign(self,n):
   if any(isinstance(t,ast.Name) and t.id in d['new_constants'] for t in n.targets):return None
   return self.generic_visit(n)
 actual=hashlib.sha256(ast.dump(Filter().visit(ast.parse(SOURCE.read_text())),include_attributes=False).encode()).hexdigest()
 assert actual==d['unchanged_ast_sha256']

def test_retained_legacy_retry_remains_readable(f):
 legacy={k:v for k,v in DATA.items() if k!='unneeded_raw_field'}
 with sqlite3.connect(f.db) as c:c.execute('INSERT INTO webhook_birth_queue(signature,payload,source) VALUES(?,?,?)',(S,json.dumps(legacy),'pumpportal_retry'))
 f.consume(f.actor());assert f.rows()[0][3]==1
 with sqlite3.connect(f.db) as c:assert c.execute('SELECT earliest_tx_creator,create_tx_signature FROM token_analysis').fetchone()==(C,S)


def test_actual_inner_persistence_failure_returns_no_ack(f):
 a=f.actor()
 @contextmanager
 def unavailable(*a,**kw):raise RuntimeError('fixture commit unavailable');yield
 f.ns['managed_db_connect']=unavailable
 result=asyncio.run(a._insert_bonding_curve_token(M,C,str(T//1000000000),create_tx_signature=S,birth_evidence_source='PUMPPORTAL_NEW_TOKEN'))
 assert result is False
 with sqlite3.connect(f.db) as c:assert c.execute('SELECT count(*) FROM token_analysis').fetchone()[0]==0


def test_shared_sql_admission_cannot_exceed_capacity(f):
 import concurrent.futures
 f.ns['_PP_BIRTH_RETRY_MAX_ROWS']=2
 def produce(index):
  obj=f.actor();obj._insert_bonding_curve_token=f.fail
  async def work():
   await f.ns['_handle_pumpportal_create_frame'](obj,DATA,signature='signature-'+str(index),mint='mint-'+str(index),receive_utc_ns=T,receive_monotonic_ns=1,tracked_trade_mints=set(),migration_sol_threshold=100,ws=None)
  asyncio.run(work())
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(produce,range(3)))
 assert len(f.rows())==2
 assert any('UNRECOVERABLE_BIRTH_RETRY' in x for x in f.logs)


def test_handled_primary_failure_is_not_announced_as_success(f):
 a=f.actor()
 @contextmanager
 def unavailable(*args,**kw):raise RuntimeError('fixture primary unavailable');yield
 f.ns['managed_db_connect']=unavailable
 f.produce(a)
 assert len(f.rows())==1 and f.rows()[0][3]==0
 assert any('COMPACT_BIRTH_RETRY_PENDING' in x for x in f.logs)
 assert not any('🟢 Birth:' in x for x in f.logs)
