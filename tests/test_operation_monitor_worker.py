from src.ops.operation_monitor_worker import MonitorQueue,MonitorWorker,_read_only_connection,_watchtower_entry_inventory,reconcile_byzantine_assignment_admissions
import sqlite3
from src.ops.operator_lifecycle_projection import ensure_schema
from src.ops.operator_lifecycle_projection import persist_monitor_mode_transition


def test_read_only_connection_closes_native_uri_connection(monkeypatch):
 class NativeConnection:
  closed=False
  def close(self):self.closed=True
 native=NativeConnection()
 monkeypatch.setattr('src.ops.operation_monitor_worker.sqlite3.connect',lambda *args,**kwargs:native)
 with _read_only_connection('/tmp/ops.db') as connection:
  assert connection is native and not native.closed
 assert native.closed


def test_read_only_connection_closes_when_query_path_raises(monkeypatch):
 class NativeConnection:
  closed=False
  def close(self):self.closed=True
 native=NativeConnection()
 monkeypatch.setattr('src.ops.operation_monitor_worker.sqlite3.connect',lambda *args,**kwargs:native)
 try:
  with _read_only_connection('/tmp/ops.db'):
   raise sqlite3.OperationalError('query failed')
 except sqlite3.OperationalError:
  pass
 assert native.closed
def test_production_evidence_queue_dedup_and_worker(tmp_path):
 q=MonitorQueue(tmp_path/'q',enabled=True);a={'event':'OPERATION_ASSIGNMENT_COMMITTED'};birth={'mint':'m','entry_timestamp':1,'entry_mc_usd':100}
 one=q.enqueue_after_assignment(mint='m',operation_id='watchtower',assignment=a,canonical_birth=birth);two=q.enqueue_after_assignment(mint='m',operation_id='watchtower',assignment=a,canonical_birth=birth);assert one['job_id']==two['job_id']
 saved=[]
 def persist(item):saved.append(item);return True
 w=MonitorWorker(q,lambda p:{'candles':[{'timestamp':1,'mc':100}]},persist);assert w.process_once()==1 and len(saved)==1 and q.queue.depth()['pending']==0
def test_no_cross_operation_monitor_adapter(tmp_path):
 q=MonitorQueue(tmp_path/'q',enabled=True);assert q.enqueue_after_assignment(mint='m',operation_id='unknown',assignment={},canonical_birth={'mint':'m'})['status']=='NOT_ENQUEUED_ENTRY_UNQUALIFIED'

def test_authoritative_operation_ids_normalize_at_monitor_boundary(tmp_path):
 q=MonitorQueue(tmp_path/'q',enabled=True)
 assert q.enqueue_after_assignment(mint='m',operation_id='04265d9f-6eb2-568c-a49e-9253091a4dbb',assignment={},canonical_birth={'mint':'m'})['status']=='ENQUEUED'

def test_assignment_without_entry_remains_waiting_without_transport(tmp_path):
 q=MonitorQueue(tmp_path/'q',enabled=True);q.enqueue_after_assignment(mint='m',operation_id='watchtower',assignment={'assigned_at':1},canonical_birth={'mint':'m'})
 calls=[]; w=MonitorWorker(q,lambda p:calls.append(p))
 assert w.process_once()==1 and calls==[] and q.queue.depth()['pending']==1

def test_compact_observations_deduplicate_through_shared_writer(tmp_path):
 db=tmp_path/'monitor.db'; c=sqlite3.connect(db); ensure_schema(c); c.commit(); c.close()
 q=MonitorQueue(tmp_path/'q',enabled=True); q.enqueue_after_assignment(mint='m',operation_id='watchtower',assignment={'assigned_at':1},canonical_birth={'entry_timestamp':1,'entry_mc_usd':100})
 w=MonitorWorker(q,lambda p:{'candles':[{'timestamp':2,'mc':100},{'timestamp':3,'mc':200},{'timestamp':3,'mc':200}], 'resolution':'15m','request':{}},db_path=str(db)); w.process_once()
 c=sqlite3.connect(db); assert c.execute('select count(*) from operation_monitor_observations').fetchone()[0]==2; assert c.execute('select count(*) from operation_monitor_facts').fetchone()[0]==1

def test_mode_transition_shared_writer_is_idempotent(tmp_path):
 db=tmp_path/'ops.db'; c=sqlite3.connect(db); ensure_schema(c); c.commit(); c.close()
 assert persist_monitor_mode_transition(db,'MONITOR',config_source='test',provenance={'source':'test'})
 assert not persist_monitor_mode_transition(db,'MONITOR',config_source='test',provenance={'source':'test'})
 c=sqlite3.connect(db); assert c.execute('select count(*) from operation_monitor_mode_transitions').fetchone()[0]==1

def test_watchtower_missing_entry_creates_only_a_bounded_one_second_plan(tmp_path):
 db=tmp_path/'ops.db'; c=sqlite3.connect(db)
 c.execute('create table migrated_tokens(mint text primary key,migration_tx text,migration_time integer,pool text)')
 c.execute('insert into migrated_tokens values(?,?,?,?)',('mint','sig',100,'pool')); c.commit(); c.close()
 plan=_watchtower_entry_inventory(str(db),'mint')
 assert plan['result']=='ENTRY_EVIDENCE_ACQUISITION_DUE'
 assert plan['entry_acquisition']['time_from']==100 and plan['entry_acquisition']['time_to']==102
 assert plan['missing_evidence']==['FIRST_FULL_POST_MIGRATION_SECOND_MC']

def test_byzantine_missing_live_scenario_d_evidence_remains_visible_waiting_fact(tmp_path):
 db=tmp_path/'monitor.db'; c=sqlite3.connect(db); ensure_schema(c); c.commit(); c.close()
 q=MonitorQueue(tmp_path/'q',enabled=True); q.enqueue_after_assignment(mint='m',operation_id='byzantine',assignment={'assigned_at':1},canonical_birth={'mint':'m'})
 calls=[]; w=MonitorWorker(q,lambda p:calls.append(p),db_path=str(db))
 assert w.process_once()==1 and calls==[]
 assert q.queue.depth()['pending']==1 and q.queue.depth()['dead_letter']==0
 c=sqlite3.connect(db); fact=c.execute('select entry_status,monitor_state,evidence_status from operation_monitor_facts').fetchone()
 assert fact==('WAITING_FOR_ENTRY_REFERENCE','WAITING_FOR_ENTRY_REFERENCE','BYZANTINE_ENTRY_EVIDENCE_DUE')

def test_post_activation_byzantine_reconciliation_restores_same_waiting_identity(tmp_path):
 db=tmp_path/'ops.db'; c=sqlite3.connect(db); ensure_schema(c)
 c.execute('create table operators(operator_id text primary key,display_name text,status text)')
 c.execute('create table operator_launch_membership(mint text primary key,operator_id text,source_population_id text,assigned_at integer,event_id text)')
 c.execute("insert into operators(operator_id,display_name,status) values(?,?,?)",('d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334','Byzantine','CONFIRMED'))
 c.execute("insert into operation_monitor_mode_transitions(mode,effective_at,previous_mode,config_source,contract_version,provenance,created_at) values('MONITOR',10,'OFF','test','v1','{}',10)")
 c.execute("insert into operator_launch_membership(mint,operator_id,source_population_id,assigned_at,event_id) values('m','d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334','source',11,'event')");c.commit();c.close()
 q=MonitorQueue(tmp_path/'q',enabled=True); one=q.enqueue_after_assignment(mint='m',operation_id='byzantine',assignment={'assigned_at':11,'event_id':'event'},canonical_birth={'mint':'m'})['job_id']
 claim=q.queue.claim(1)[0]; claim.payload['envelope'].update({'entry_evaluation_result':'INSUFFICIENT_EVIDENCE','monitor_state':'INSUFFICIENT_EVIDENCE'});q.queue._replace_payload(claim.path,claim.payload)
 import os
 os.replace(claim.path,q.queue.root/'dead_letter'/claim.path.name)
 result=reconcile_byzantine_assignment_admissions(str(db),q)
 assert result=={'examined':1,'enqueued':0,'restored_waiting':1,'already_present':0}
 pending=next((q.queue.root/'pending').glob('*.json')); import json
 payload=json.loads(pending.read_text())
 assert payload['message_id']==one and payload['envelope']['monitor_state']=='WAITING_FOR_ENTRY_REFERENCE'

def test_rate_limit_uses_shared_birdeye_backoff_without_burning_queue_attempt(tmp_path,monkeypatch):
 q=MonitorQueue(tmp_path/'q',enabled=True);q.enqueue_after_assignment(mint='m',operation_id='watchtower',assignment={'assigned_at':1},canonical_birth={'entry_timestamp':1,'entry_mc_usd':100})
 w=MonitorWorker(q,lambda p:(_ for _ in ()).throw(ConnectionError('HTTP_429:Too many requests')))
 assert w.process_once()==1 and q.queue.depth()['retry']==1
 path=next((tmp_path/'q'/'retry').glob('*.json'));import json
 p=json.loads(path.read_text());assert p['attempts']==0 and p['envelope']['monitor_state']=='PROVIDER_BACKOFF'
 monkeypatch.setattr('src.ops.operation_monitor_worker.time.time',lambda: p['envelope']['backoff_until'])
 assert q.recover_due()==1 and q.queue.depth()['pending']==1

def test_candle_high_sets_peak_while_latest_close_sets_drawdown(tmp_path):
 db=tmp_path/'monitor.db'; c=sqlite3.connect(db); ensure_schema(c); c.commit(); c.close()
 q=MonitorQueue(tmp_path/'q',enabled=True);q.enqueue_after_assignment(mint='m',operation_id='watchtower',assignment={'assigned_at':1},canonical_birth={'entry_timestamp':1,'entry_mc_usd':100})
 w=MonitorWorker(q,lambda p:{'candles':[{'timestamp':2,'mc':20,'open':30,'high':200,'low':10}], 'resolution':'15m','request':{}},db_path=str(db));w.process_once()
 c=sqlite3.connect(db);r=c.execute('select running_peak_mc_usd,latest_mc_usd,drawdown_percent,monitor_state from operation_monitor_facts').fetchone();o=c.execute('select high_mc_usd,close_mc_usd from operation_monitor_observations').fetchone()
 assert r[0]==200 and r[1]==20 and r[2]==90 and r[3]=='PRICE_MONITOR_COMPLETE_COLLAPSED' and o==(200,20)

def test_legacy_close_only_observation_does_not_invent_high(tmp_path):
 db=tmp_path/'monitor.db'; c=sqlite3.connect(db); ensure_schema(c);c.execute("insert into operation_monitor_observations(operation_id,mint,observation_timestamp,mc_usd,resolution,source,request_identity,provenance_digest,created_at) values('watchtower','m',2,20,'15m','x','r','p',1)");c.commit();c.close()
 c=sqlite3.connect(db);assert c.execute('select high_mc_usd from operation_monitor_observations').fetchone()[0] is None

def test_terminal_ath_job_is_deduplicated_and_finalized_facts_are_excluded(tmp_path):
 q=MonitorQueue(tmp_path/'q',enabled=True)
 pending={'operation_id':'watchtower','mint':'m','cohort_class':'PROSPECTIVE_MONITOR_COHORT','monitor_state':'PRICE_MONITOR_COMPLETE_COLLAPSED','next_observation_at':None,'final_proven_ath_mc':None,'entry_method':'FIRST_FULL_POST_MIGRATION_SECOND_MC','entry_timestamp':10,'entry_mc_usd':100,'monitor_completed_at':20}
 first=q.enqueue_terminal_ath_finalization(pending);second=q.enqueue_terminal_ath_finalization(pending)
 assert first['status']=='ENQUEUED_TERMINAL_ATH' and first['job_id']==second['job_id'] and q.queue.depth()['pending']==1
 assert q.enqueue_terminal_ath_finalization({**pending,'final_proven_ath_mc':200})['status']=='NOT_ENQUEUED'

def test_shared_provider_backoff_gates_all_monitor_work_types(tmp_path):
 import json
 q=MonitorQueue(tmp_path/'q',enabled=True);q.queue.enqueue({'work_type':'WATCHTOWER_TERMINAL_ATH_FINALIZATION'},message_id='ath')
 claim=q.queue.claim(1)[0];q.defer_rate_limited(claim,retry_after=17)
 payload=json.loads((q.queue.root/'retry'/'ath.json').read_text())
 assert not q.provider_eligible(now=0) and q.provider_eligible(now=10**10)
 assert payload['attempts']==0 and payload['envelope']['last_provider_status']==429 and payload['envelope']['backoff_until']>0
