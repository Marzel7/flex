"""Prospective creator authority, compact offline queues; never run a worker loop."""
import ast
import asyncio
import contextlib
import importlib
import json
import sqlite3
import time
import types
from pathlib import Path
import pytest

MINT='BwMMU8aFzdCFyhyFFBp7Ao6RaKnF1iRAbsbCeZBqpump'
CREATOR='7KX5aXKoU5HtaHnLdgAY21nyKpxEcahxwv9rvExKSxFD'
INFERRED='27HFmP7ccLadGswvQfvea4o3juLw75cPF4V6jWpHM3MX'
SIG='3wtMLEFAejTCjqN5koydeFdSSQzppmqqvqbELgRYJtKg7a9q6eT1GwWMUYcnoA7Hbggz1JM4YjirCFTCtaNF7Dxz'
FALLBACK='jBrN8WSMzfja6UdTqvma2Pa6wZzr5BMc8chFsLx3AUVvHYkvi7gfgz4QV18Pm6X6f81QKPGPCiHEmbDEin7vj7y'
ROOT=Path(__file__).resolve().parents[1]

def qualified():
 return dict(creator=CREATOR,creator_source='rpc_pump_create',is_pumpfun_create=True,validation_status='unproven',create_tx_signature=SIG)

@pytest.fixture
def queue(tmp_path,monkeypatch):
 from src.core import creator_resolution_queue as q
 # Import the exact deployed worker composition for dependency qualification,
 # but never invoke its startup, scheduler, heartbeat or loop.
 import src.core.creator_resolution_worker as worker
 assert callable(worker.run_loop)
 db=str(tmp_path/'creator.db');events=[];crash=[None]
 class Conn(sqlite3.Connection):
  def execute(self,sql,*args,**kwargs):
   if crash[0]=='race' and sql.lstrip().startswith('UPDATE token_analysis'):
    crash[0]=None
    other=sqlite3.connect(db)
    other.execute('UPDATE token_analysis SET earliest_tx_creator=?,pf_ws_creator=?',(INFERRED,INFERRED));other.commit();other.close()
   return super().execute(sql,*args,**kwargs)
  def commit(self):
   complete=self.execute("SELECT COUNT(*) FROM creator_resolution_queue WHERE status='complete'").fetchone()[0] if self.execute("SELECT 1 FROM sqlite_master WHERE name='creator_resolution_queue'").fetchone() else 0
   if complete and crash[0]=='before':crash[0]=None;raise sqlite3.OperationalError('fixture crash before commit')
   super().commit();events.append('commit_ack')
   if complete and crash[0]=='after':crash[0]=None;raise SystemExit('fixture process stops after commit')
 @contextlib.contextmanager
 def boundary(path):
  assert path==db
  c=sqlite3.connect(path,factory=Conn);c.row_factory=sqlite3.Row
  try:yield c
  finally:c.rollback();c.close()
 monkeypatch.setattr(q,'_db',boundary);monkeypatch.setattr(q,'_read_db',boundary)
 monkeypatch.setattr(q,'_is_high_priority',lambda *a:False)
 monkeypatch.setattr(q,'record_event_fail_open',lambda *a,**kw:True)
 with boundary(db) as c:
  c.execute('CREATE TABLE token_analysis(mint TEXT PRIMARY KEY,migrated_at,created_at,analyzed_at,lifecycle_stage,create_tx_signature,earliest_tx_creator,pf_ws_creator,bonding_curve_pda,creator_resolved_slot,migration_slot,curve_completed_slot,funding_job_enqueued_slot)')
  c.execute('CREATE TABLE creator_funders(creator_address)')
  c.execute('CREATE TABLE creator_funding_queue(creator_address,mint,migration_timestamp,create_tx_signature,status,source,next_attempt_at,locked_until,attempts,last_error,funding_enqueued_at,curve_completed_slot,enqueued_slot,job_priority,priority_reason,created_at,updated_at,UNIQUE(creator_address,mint))')
  q.ensure_schema(c)
  c.execute("INSERT INTO token_analysis(mint,migrated_at,lifecycle_stage) VALUES(?,100,'migrated')",(MINT,))
  q.enqueue_missing_creator(c,MINT,source='fixture',schema_ready=True);c.commit()
 def get():
  with boundary(db) as c:
   return dict(c.execute('SELECT * FROM creator_resolution_queue WHERE mint=?',(MINT,)).fetchone()),dict(c.execute('SELECT * FROM token_analysis WHERE mint=?',(MINT,)).fetchone()),c.execute('SELECT COUNT(*) FROM creator_funding_queue').fetchone()[0]
 return q,db,boundary,get,crash,events

@pytest.mark.parametrize('case',['signer','missing_creator','missing_birth','multiple_signers','missing_signature','no_strict','conflict','signature_conflict'])
def test_unqualified_no_authoritative_writes(queue,monkeypatch,case):
 q,db,boundary,get,*_=queue;data=qualified()
 if case in ('signer','missing_birth'):data.update(creator=INFERRED,creator_source='signer_inference',is_pumpfun_create=False,validation_status='inferred_create',create_tx_signature=None,inference_signature=FALLBACK)
 if case in ('missing_creator','multiple_signers'):data['creator']=None
 if case=='missing_signature':data['create_tx_signature']=None
 if case=='no_strict':data['is_pumpfun_create']=False
 if case in ('conflict','signature_conflict'):
  with boundary(db) as c:
   if case=='conflict':c.execute('UPDATE token_analysis SET earliest_tx_creator=?,pf_ws_creator=?',(INFERRED,INFERRED))
   else:c.execute('UPDATE token_analysis SET create_tx_signature=?',(FALLBACK,))
   c.commit()
 monkeypatch.setattr(q,'_resolve_creator_rpc',lambda *a,**kw:data)
 result=q.process_queue(db,schema_ready=True);job,token,funding=get()
 assert result['resolved']==0 and funding==0 and job['status']=='retry'
 assert token['earliest_tx_creator']==(INFERRED if case=='conflict' else None)
 assert token['pf_ws_creator']==(INFERRED if case=='conflict' else None)
 assert token['create_tx_signature']==(FALLBACK if case=='signature_conflict' else None)
 assert job['resolved_creator'] is None and job['create_tx_signature'] is None
 evidence=json.loads(job['last_error'].split(':',1)[1]);assert evidence['creator']==data['creator']
 if case=='signer':assert evidence['source_tx']==FALLBACK and evidence['status']=='inferred_create'
 assert len(job['last_error'])<=500

@pytest.mark.parametrize('preexisting',[False,True])
def test_strict_commit_idempotency_restart(queue,monkeypatch,preexisting):
 q,db,boundary,get,_,events=queue
 if preexisting:
  with boundary(db) as c:c.execute('UPDATE token_analysis SET earliest_tx_creator=?,pf_ws_creator=?,create_tx_signature=?',(CREATOR,CREATOR,SIG));c.commit()
 monkeypatch.setattr(q,'_resolve_creator_rpc',lambda *a,**kw:qualified())
 assert q.process_queue(db,schema_ready=True)['resolved']==1
 job,token,n=get();assert job['status']=='complete' and n==1
 assert token['earliest_tx_creator']==token['pf_ws_creator']==CREATOR and token['create_tx_signature']==SIG
 assert q.process_queue(db,schema_ready=True)['processed']==0
 with boundary(db) as c:assert not q.enqueue_missing_creator(c,MINT,schema_ready=True)
 assert get()[2]==1 and events.count('commit_ack')>=3

@pytest.mark.parametrize('crashpoint',['before','after'])
def test_crash_atomicity(queue,monkeypatch,crashpoint):
 q,db,boundary,get,crash,_=queue;monkeypatch.setattr(q,'_resolve_creator_rpc',lambda *a,**kw:qualified());crash[0]=crashpoint
 if crashpoint=='after':
  with pytest.raises(SystemExit):q.process_queue(db,schema_ready=True)
  assert get()[0]['status']=='complete' and get()[2]==1
  assert q.process_queue(db,schema_ready=True)['processed']==0
 else:
  assert q.process_queue(db,schema_ready=True)['resolved']==0
  assert get()[0]['status']=='retry' and get()[1]['earliest_tx_creator'] is None and get()[2]==0

@pytest.mark.parametrize('attempts,status',[(0,'retry'),(4,'retry'),(5,'failed')])
def test_existing_retry_backoff(queue,monkeypatch,attempts,status):
 q,db,boundary,get,*_=queue
 with boundary(db) as c:c.execute('UPDATE creator_resolution_queue SET attempts=?',(attempts,));c.commit()
 def fail(*a,**kw):raise RuntimeError('existing transport failure')
 monkeypatch.setattr(q,'_resolve_creator_rpc',fail);before=int(time.time());q.process_queue(db,schema_ready=True);job,_,_=get()
 assert job['attempts']==attempts+1 and job['status']==status
 assert before+min(900,30*(attempts+1))<=job['next_attempt_at']<=int(time.time())+min(900,30*(attempts+1))
 if status=='failed':
  with boundary(db) as c:
   assert q.enqueue_missing_creator(c,MINT,schema_ready=True);c.commit()
  assert get()[0]['status']=='retry'  # Existing non-authority error behavior unchanged.

def test_authority_exhaustion_not_reopened_by_worker_discovery(queue,monkeypatch):
 q,db,boundary,get,*_=queue
 monkeypatch.setattr(q,'_resolve_creator_rpc',lambda *a,**kw:dict(creator=INFERRED,is_pumpfun_create=False,inference_signature=FALLBACK,validation_status='inferred_create'))
 for attempt in range(6):
  with boundary(db) as c:c.execute('UPDATE creator_resolution_queue SET next_attempt_at=0');c.commit()
  q.process_queue(db,schema_ready=True)
  assert get()[0]['attempts']==attempt+1
 assert get()[0]['status']=='failed'
 assert q.enqueue_missing_migrated_tokens(db,schema_ready=True)==0
 with boundary(db) as c:assert not q.enqueue_missing_creator(c,MINT,schema_ready=True)
 assert q.process_queue(db,schema_ready=True)['processed']==0
 assert get()[1]['earliest_tx_creator'] is None and get()[2]==0

@pytest.mark.parametrize('kind,budget',[('inferred',False),('inferred',True),('strict',True),('missing',False)])
def test_actual_wrapper_provenance(queue,monkeypatch,kind,budget):
 q,db,boundary,get,*_=queue
 # The acquisition is an inert result fixture. The actual queue wrapper runs.
 fake=types.ModuleType('src.analysis.pump_fun_post_migration_analyzer')
 class Analyzer:
  def __init__(self,*a,**kw):self._pages_checked=1;self._sigs_examined=964;self._budget_exceeded=budget;self._create_tx_signature=SIG if kind=='strict' else None
  async def get_creator_from_earliest_tx(self,**kw):
   assert kw['max_pages_override']==1 and kw['deadline_monotonic']>time.monotonic()
   return {'creator':CREATOR if kind=='strict' else INFERRED if kind=='inferred' else None,'create_sig':SIG if kind=='strict' else FALLBACK,'is_pumpfun_create':kind=='strict','status':'unproven' if kind=='strict' else 'inferred_create' if kind=='inferred' else 'no_create_found','validation_notes':['retained fixture: no fabricated Pump argument']}
 fake.PostMigrationAnalyzer=Analyzer
 import sys
 monkeypatch.setitem(sys.modules,'src.analysis.pump_fun_post_migration_analyzer',fake)
 if budget and kind!='strict':
  out=q.process_queue(db,schema_ready=True);job,token,n=get()
  assert out['skipped']==1 and job['status']=='skipped' and token['earliest_tx_creator'] is None and n==0
  evidence=json.loads(job['last_error'].split(':',1)[1]);assert evidence['creator']==INFERRED and evidence['source_tx']==FALLBACK and evidence['budget']
  assert q.enqueue_missing_migrated_tokens(db,schema_ready=True)>=0
  assert get()[0]['status']=='skipped' and q.process_queue(db,schema_ready=True)['processed']==0
 else:
  data=q._resolve_creator_rpc(MINT,max_runtime_secs=15,max_pages=1,tier='GENERIC')
  assert data['is_pumpfun_create']==(kind=='strict')
  assert data['create_tx_signature']==(SIG if kind=='strict' else None)
  assert data['inference_signature']==(None if kind=='strict' else FALLBACK)

def test_pumpportal_first_handler_unchanged_and_direct():
 path=ROOT/'src/core/pumpfun_curve_listener.py';tree=ast.parse(path.read_text());fn=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_handle_pumpportal_create_frame')
 ns=dict(time=time,asyncio=asyncio,json=json,log_print=lambda *a,**kw:None);exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),ns)
 received=[]
 async def persist(*args,**kw):received.append((args,kw))
 async def creator_ready(*a,**kw):pass
 self=types.SimpleNamespace(_eb_birth_audit=types.SimpleNamespace(record=lambda **kw:None),_pumpfun_delivery_latency=types.SimpleNamespace(observe_pumpportal=lambda **kw:None),_byzantine_birth_signal=None,_portal_vsol={},completed_launches=set(),seen_mints=set(),_insert_bonding_curve_token=persist,_ensure_pf_ws_creator=creator_ready)
 async def run():
  await ns['_handle_pumpportal_create_frame'](self,{'traderPublicKey':CREATOR,'txType':'create'},signature=SIG,mint=MINT,receive_utc_ns=1,receive_monotonic_ns=1,tracked_trade_mints=set(),migration_sol_threshold=100,ws=None)
  await asyncio.sleep(0)
 asyncio.run(run());assert received[0][0][1]==CREATOR and received[0][1]['create_tx_signature']==SIG

def test_no_walkback_or_cross_database_writer_introduced():
 import src.core.creator_resolution_queue as q
 source=Path(q.__file__).read_text();tree=ast.parse(source)
 process=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='process_queue')
 assert 'wt_walkback_queue' not in ast.get_source_segment(source,process)
 assert 'BEGIN IMMEDIATE' not in ast.get_source_segment(source,process)
 assert q.TERMINAL_STATUSES=={'complete','ignored','skipped'}
 assert q._GENERIC_MAX_RUNTIME==15 and q._GENERIC_MAX_PAGES==1


def test_crash_after_claim_reaped_on_restart(queue,monkeypatch):
 q,db,boundary,get,*_=queue
 def crash(*a,**kw):raise SystemExit('fixture crash after selection/claim')
 monkeypatch.setattr(q,'_resolve_creator_rpc',crash)
 with pytest.raises(SystemExit):q.process_queue(db,schema_ready=True)
 assert get()[0]['status']=='running' and get()[0]['attempts']==0 and get()[2]==0
 with boundary(db) as c:c.execute('UPDATE creator_resolution_queue SET locked_until=0');c.commit()
 monkeypatch.setattr(q,'_resolve_creator_rpc',lambda *a,**kw:qualified())
 assert q.process_queue(db,schema_ready=True)['resolved']==1
 assert get()[0]['attempts']==1 and get()[2]==1


def test_concurrent_authority_arrival_prevents_wrong_funding(queue,monkeypatch):
 q,db,boundary,get,crash,_=queue
 monkeypatch.setattr(q,'_resolve_creator_rpc',lambda *a,**kw:qualified());crash[0]='race'
 assert q.process_queue(db,schema_ready=True)['resolved']==0
 job,token,funding=get()
 assert token['earliest_tx_creator']==token['pf_ws_creator']==INFERRED
 assert token['create_tx_signature'] is None and funding==0 and job['status']=='retry'
 assert 'CONCURRENT_AUTHORITY_CONFLICT' in job['last_error']
