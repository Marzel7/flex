"""Bounded transfer experiment, not a production coordinator/launcher."""
import ast,asyncio,contextlib,dataclasses,hashlib,importlib.util,json,os,sqlite3,sys,time,types
from pathlib import Path
from typing import Optional,Dict,Any
import pytest
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('pair_fixture_support',ROOT/'tests/dev004/test_birth_admission_retirement.py');support=importlib.util.module_from_spec(spec);spec.loader.exec_module(support)
env=support.env;no_network=support.no_network;NATIVE=support.NATIVE
CORE_TABLES=['token_analysis','creator_resolution_queue','birth_persist_queue','migration_walkback_handoff','webhook_birth_queue']
OPS_TABLES=['operators','operator_identity_events','operator_launch_membership','wt_walkback_queue','wt_attribution_outcomes']
SCHEMA=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/bounded_paired_transfer/fixture-schema.json').read_text())['ddl']

class Hold(RuntimeError):pass
class Crash(BaseException):pass

class PairCase:
 def __init__(self,env):
  self.env=env;self.w,self.d=support.setup_writer(env);self.paths={g:{k:env.path/(g+'-'+k+'.sqlite') for k in ['core','ops']} for g in ['v1','v2']}
  for pair in self.paths.values():
   for kind,p in pair.items():
    with NATIVE(p) as c:
     c.execute('PRAGMA max_page_count=256')
     for ddl in SCHEMA[kind]:c.execute(ddl)
  self.birth=env.b.BirthJournal(env.path/'stable-birth.jsonl');self.birth.initialize();self.migration=env.m.MigrationJournal(env.path/'stable-migration.jsonl');self.migration.initialize()
  self.config=env.path/'frozen-pair.json';self.steps=[];self.calls=[];self.logs=[];self.current=None;self.scope=None;self.stale=None
  self.binding('v1');self.seed()
 def conn(self,path,read=False):
  c=NATIVE('file:'+str(path)+'?mode=ro' if read else path,uri=read,factory=self.d.TrackedConnection)
  c._db_path=str(path);c._db_caller='bounded-paired-qualification';c.row_factory=sqlite3.Row;return c
 def selected(self,path,names,scope):return support.listener_functions(scope,names) if path=='listener' else self._select(ROOT/path,names,scope)
 def _select(self,path,names,scope):
  t=ast.parse(path.read_text())
  for name in names:
   n=next(n for n in ast.walk(t) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and n.name==name);exec(compile(ast.Module(body=[n],type_ignores=[]),str(path),'exec'),scope)
  return scope
 def binding(self,g,ops_generation=None):
  pair={'core':self.paths[g]['core'],'ops':self.paths[ops_generation or g]['ops']}
  self.env.patch.setenv('DB_PATH',str(pair['core']));self.env.patch.setenv('WT_OPS_DB_PATH',str(pair['ops']))
  db=support.load('pair_actual_db_paths',ROOT/'runtime/listener/src/core/db.py')
  assert db.DB_PATH==str(pair['core']) and db.resolve_ops_db_path()==str(pair['ops'])
  # Fresh immutable namespace models fresh process import; not hot file replacement.
  self.current=pair
  scope={'os':os,'json':json,'asyncio':asyncio,'time':time,'Optional':Optional,'Dict':Dict,'DB_PATH':db.DB_PATH,'_listener_ops_db_path':db.resolve_ops_db_path,
   '_birth_admission_journal':lambda:self.birth,'_signature_migration_journal':lambda:self.migration,
   'db_connect':lambda path,**kw:self.conn(path),'managed_db_connect':lambda path,**kw:self.conn(path),'PRIORITY_P0_CRITICAL_INGESTION':0,'_birth_telemetry_incr':lambda *a:None,'log_print':lambda *a,**kw:self.logs.append(' '.join(map(str,a))),
   'TOKEN_PREDICTION_RUNTIME_ENABLED':False,'_spawn_symbol_fetch':lambda *a:None,'_broadcast_to_flask':lambda *a:None}
  self.selected('listener',['_insert_bonding_curve_token','_birth_journal_canonical','_birth_journal_readback','_persist_durable_birth','_drain_durable_birth_batch','_migration_journal_readback'],scope)
  # Existing disabled prospective stage is imported without providers; no schema mutation.
  module=support.load('src.ops.walkback_create_handoff',ROOT/'runtime/listener/src/ops/walkback_create_handoff.py')
  handoff={'sqlite3':sqlite3,'time':time,'PENDING':'PENDING','COMPLETE':'COMPLETE'}
  self._select(ROOT/'runtime/listener/src/ops/migration_walkback_handoff_retry.py',['ensure_schema','record_intent','mark_complete'],handoff);self.handoff=handoff
  actor=types.SimpleNamespace();actor._remember_recent_birth_token=lambda *a:None
  async def metadata(*a):pass
  actor._upsert_birth_metadata_cache=metadata
  for name in ['_insert_bonding_curve_token','_persist_durable_birth','_drain_durable_birth_batch']:setattr(actor,name,types.MethodType(scope[name],actor))
  self.actor=actor;self.scope=scope;return db
 def seed(self):
  with self.conn(self.paths['v1']['core']) as c:
   c.execute("INSERT INTO creator_resolution_queue(mint,status,priority,reason,source,attempts,next_attempt_at,locked_until,created_at,updated_at) VALUES('pending-creator','retry',200,'fixture','fixture',2,50,0,1,1)")
   c.execute("INSERT INTO webhook_birth_queue(signature,payload,source,consumed,quarantine_reason) VALUES('quarantined-signature','{}','pumpportal_retry',0,'CORE_AUTHORITY_CONFLICT')")
  with self.conn(self.paths['v1']['ops']) as c:
   c.execute("INSERT INTO operators(operator_id,status,confidence,review_state,created_at,updated_at) VALUES('fixture-operation','CONFIRMED','HIGH','REVIEWED',1,1)")
   c.execute("INSERT INTO operator_launch_membership(mint,operator_id,assigned_at) VALUES('retained-launch','fixture-operation',1)")
   c.execute("INSERT INTO wt_walkback_queue(mint,creator,status,attempts,next_retry_at) VALUES('pending-walkback','creator','pending',1,50)")
   c.execute("INSERT INTO wt_attribution_outcomes(mint,outcome_type,stop_reason,terminal_entity,terminal_entity_type,confidence,evidence_json,operator_id,should_seed_emerging_operator,should_retry,completed_at,materialized_at) VALUES('retained-launch','CANONICAL_OPERATOR_REACHED','fixture','fixture-operation','OPERATOR','HIGH','{\"provenance\":\"synthetic-frozen\"}','fixture-operation',0,0,1,1)")
  self.birth_once(support.payload(sig='old-create',mint='old-birth'))
 def birth_once(self,payload):
  row=self.birth.admit_birth(payload);self.calls.append(('birth',row['id']));return asyncio.run(self.actor._persist_durable_birth(row))
 def admit_during_lease(self):
  p=self.paths['v1']['core'];lease=self.w.acquire_write_lease('core',str(p),'admission-lease','fixture-admission',timeout=.2)
  try:
   row=self.birth.admit_birth(support.payload(sig='pending-create',mint='new-launch'));self.migration.admit('pending-migration','PUMPSWAP_MIGRATION_ACCOUNT',1)
   self.birth.admit_birth(support.payload(sig='pending-create',mint='new-launch',time=2));self.migration.admit('pending-migration','PUMPSWAP_MIGRATION_ACCOUNT',2)
   assert row['ack'] is None;self.steps.append('V1 lease held; birth+migration durably admitted, duplicates coalesced')
  finally:self.w.release_write_lease(lease)
 def retire(self,crash_after_core=False):
  self.w.retire_database('core',self.paths['v1']['core'],generation='fixture-v1',timeout=.2);self.steps.append('Core V1 retired')
  if crash_after_core:raise Crash('between-retirements')
  self.w.retire_database('ops',self.paths['v1']['ops'],generation='fixture-v1',timeout=.2);self.steps.append('OPS V1 retired')
 def rows(self,path,table):
  with self.conn(path,True) as c:
   rows=[dict(r) for r in c.execute('SELECT * FROM "'+table+'" LIMIT 21')]
  if len(rows)>20 or any(len(json.dumps(r,default=str).encode())>65536 for r in rows):raise Hold('TRANSFER_BUDGET')
  return sorted(rows,key=lambda r:json.dumps(r,sort_keys=True,default=str))
 def reconcile(self,crash_after_core=False):
  for kind,tables in [('core',CORE_TABLES),('ops',OPS_TABLES)]:
   marker=Path(str(self.paths['v1'][kind])+'.retired')
   if not marker.exists():raise Hold('SOURCE_NOT_RETIRED')
   for table in tables:
    rows=self.rows(self.paths['v1'][kind],table)
    for r in rows:
     if (table=='creator_resolution_queue' and (r.get('locked_until',0) or r.get('status')=='running')) or (table=='wt_walkback_queue' and (r.get('claimed_by') or r.get('lease_expires_at') or r.get('status')=='running')):raise Hold('ACTIVE_LEASE_NOT_TRANSFERABLE')
    with self.conn(self.paths['v2'][kind]) as c:
     pk=[r[1] for r in sorted(c.execute('PRAGMA table_info("'+table+'")').fetchall(),key=lambda r:r[5]) if r[5]]
     if not pk:raise Hold('NO_STABLE_KEY')
     for r in rows:
      existing=c.execute('SELECT * FROM "'+table+'" WHERE '+' AND '.join('"'+k+'"=?' for k in pk),tuple(r[k] for k in pk)).fetchone()
      if existing:
       if dict(existing)!=r:raise Hold('CANONICAL_TRANSFER_CONFLICT')
      else:
       columns=list(r);c.execute('INSERT INTO "'+table+'"('+','.join('"'+x+'"' for x in columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(r[k] for k in columns))
    if self.rows(self.paths['v1'][kind],table)!=self.rows(self.paths['v2'][kind],table):raise Hold('COPY_MISMATCH')
   self.steps.append(kind+' final state reconciled/idempotently verified')
   if kind=='core' and crash_after_core:raise Crash('between-reconciliation-stores')
 def publish_frozen_config(self,partial=False):
  value={'source_revision':'91d3975e77236f601586b1bd8af86544cc97b9de','generation':'fixture-v2','core':str(self.paths['v2']['core']),'ops':str(self.paths['v1' if partial else 'v2']['ops']),'birth_journal':str(self.birth.path),'migration_journal':str(self.migration.path)}
  data=json.dumps(value,sort_keys=True).encode();assert len(data)<4096
  temp=self.config.with_suffix('.tmp');fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  try:assert os.write(fd,data)==len(data);os.fsync(fd)
  finally:os.close(fd)
  os.replace(temp,self.config);fd=os.open(self.config.parent,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)
  self.steps.append('frozen complete fixture launch configuration published' if not partial else 'fault: incomplete Core/OPS rebind configuration')
 def resume(self):
  value=json.loads(self.config.read_text());self.env.patch.setenv('DB_PATH',value['core']);self.env.patch.setenv('WT_OPS_DB_PATH',value['ops'])
  # Actual authority resolver; no invented production generation gate.
  self.binding('v2','v1' if value['ops']==str(self.paths['v1']['ops']) else 'v2')
  self.steps.append('fresh existing interfaces bind configured Core/OPS')
 def drain_births(self):asyncio.run(self.actor._drain_durable_birth_batch())
 def finish_migration(self,crash_after_core=False):
  row=self.migration.pending()[0];self.migration.resolve(row['signature'],'new-launch')
  ns={'signature':row['signature'],'migrated_at_ts':2,'source':'WEBSOCKET','mint':'new-launch','DB_PATH':str(self.current['core']),'db_connect':lambda path,**kw:self.conn(path)}
  self.selected('listener',['_store_migration_tx'],ns);ns['_store_migration_tx']()
  with self.conn(self.current['core']) as c:self.handoff['record_intent'](c,mint='new-launch',creator='creator',migration_tx=row['signature'],migrated_at=2,now=2)
  if crash_after_core:raise Crash('core-migration-before-ops')
  # Frozen classification outcome/persistence adapter only. No classifier or RPC replay.
  with self.conn(self.current['ops']) as c:c.execute("INSERT INTO wt_walkback_queue(mint,creator,status) VALUES('new-launch','creator','pending') ON CONFLICT(mint) DO NOTHING")
  with self.conn(self.current['core']) as c:self.handoff['mark_complete'](c,'new-launch',now=2)
  verify=self.scope['_migration_journal_readback'];binding=hashlib.sha256(json.dumps([str(self.current['core']),str(self.current['ops'])],separators=(',',':')).encode()).hexdigest();assert self.migration.acknowledge(row['signature'],binding,verify),self.logs
  self.steps.append('pending migration canonical + intent + durable OPS queue + Core ACK verified')

@pytest.fixture
def case(env):return PairCase(env)

def normal_prefix(c):c.admit_during_lease();c.retire();c.reconcile();c.publish_frozen_config();c.resume()

def test_complete_matched_pair_controlled_transfer(case):
 c=case;normal_prefix(c);c.drain_births();c.finish_migration()
 assert not c.birth.pending() and not c.migration.pending()
 with c.conn(c.current['core'],True) as q:
  assert q.execute('SELECT count(*) FROM token_analysis').fetchone()[0]==2
  assert tuple(q.execute("SELECT attempts,status FROM creator_resolution_queue WHERE mint='pending-creator'").fetchone())==(2,'retry')
 with c.conn(c.current['ops'],True) as q:assert q.execute("SELECT count(*) FROM operator_launch_membership WHERE mint='retained-launch'").fetchone()[0]==1
 # Duplicate notifications keep the original durable identities without another canonical row.
 assert c.birth.admit_birth(support.payload(sig='pending-create',mint='new-launch',time=3))['state']=='ACKED'
 assert c.migration.admit('pending-migration','PUMPSWAP_MIGRATION_ACCOUNT',3)['state']=='ACKED'
 (c.env.path/'sequence.json').write_text(json.dumps(c.steps,indent=2))
 continuity={'source_revision':'91d3975e77236f601586b1bd8af86544cc97b9de','birth_identities':[r['id'] for r in c.birth.snapshot()],'birth_states':[r['state'] for r in c.birth.snapshot()],'migration_identities':[r['id'] for r in c.migration.snapshot()],'migration_states':[r['state'] for r in c.migration.snapshot()],'preserved_tables':{kind:{table:len(c.rows(c.paths['v1'][kind],table)) for table in tables} for kind,tables in [('core',CORE_TABLES),('ops',OPS_TABLES)]},'canonical_birth_count':2,'new_migration_handoff_count':1,'duplicates_coalesced':True,'fixture_scope':'Retained synthetic reference rows plus accepted persistence bodies; not full daemon/intake certification'}
 (c.env.path/'data-continuity.json').write_text(json.dumps(continuity,indent=2))

def test_restart_after_core_retirement(case):
 c=case;c.admit_during_lease()
 with pytest.raises(Crash):c.retire(True)
 assert not Path(str(c.paths['v1']['ops'])+'.retired').exists()
 c.retire();c.reconcile();c.publish_frozen_config();c.resume();c.drain_births();c.finish_migration();assert not c.birth.pending()

def test_restart_during_final_state_reconciliation(case):
 c=case;c.admit_during_lease();c.retire()
 with pytest.raises(Crash):c.reconcile(True)
 c.reconcile();c.publish_frozen_config();c.resume();c.drain_births();c.finish_migration();assert not c.migration.pending()

def test_stale_source_writers_rejected_after_rebind(case):
 c=case;stale=c.conn(c.paths['v1']['core']);normal_prefix(c)
 with pytest.raises(c.w.DatabaseRetiredError):stale.execute("UPDATE token_analysis SET name='bad'")
 stale.close();with_ops=c.conn(c.paths['v1']['ops'])
 with pytest.raises(c.w.DatabaseRetiredError):with_ops.execute("DELETE FROM operator_launch_membership")
 with_ops.close()

def test_core_commit_before_ops_handoff_remains_pending_on_restart(case):
 c=case;normal_prefix(c);c.drain_births()
 with pytest.raises(Crash):c.finish_migration(True)
 assert len(c.migration.pending())==1;c.resume();c.finish_migration();assert not c.migration.pending()

def test_mixed_pair_must_fail_closed_before_canonical_acceptance(case):
 c=case;c.admit_during_lease();c.retire();c.reconcile();c.publish_frozen_config(partial=True);c.resume()
 blocked=False
 try:c.drain_births()
 except Exception:blocked=True
 with c.conn(c.paths['v2']['core'],True) as q:committed=q.execute("SELECT 1 FROM token_analysis WHERE mint='new-launch'").fetchone() is not None
 evidence={'configured_core':str(c.current['core']),'configured_ops':str(c.current['ops']),'core_committed_under_mixed_pair':committed,'birth_ack_states':[r['state'] for r in c.birth.snapshot()],'launch_rejected':blocked}
 (c.env.path/'mixed-pair-counterexample.json').write_text(json.dumps(evidence,indent=2))
 assert blocked and not committed,'Current resolver/consumer accepts Core V2 with retired OPS V1; birth can ACK before a complete pair is established'


def restart_binding_child(case):
 import subprocess
 code="""import importlib.util,json,os,sys,resource,socket
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
socket.socket.connect=lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('NETWORK_FORBIDDEN'))
c=json.load(open(sys.argv[1]));os.environ['DB_PATH']=c['core'];os.environ['WT_OPS_DB_PATH']=c['ops']
s=importlib.util.spec_from_file_location('db',sys.argv[2]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
print(json.dumps({'core':m.DB_PATH,'ops':m.resolve_ops_db_path()}),flush=True)
os._exit(17)
"""
 r=subprocess.run([sys.executable,'-B','-c',code,str(case.config),str(ROOT/'runtime/listener/src/core/db.py')],cwd=case.env.path,env={'PATH':os.environ.get('PATH',''),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=10)
 assert r.returncode==17,r.stderr
 return json.loads(r.stdout)

def test_real_restart_preserves_matched_fixture_bindings(case):
 c=case;normal_prefix(c);c.drain_births();c.finish_migration();one=restart_binding_child(c);two=restart_binding_child(c)
 assert one==two=={'core':str(c.paths['v2']['core']),'ops':str(c.paths['v2']['ops'])}
 assert not c.birth.pending() and not c.migration.pending()

def test_restart_does_not_hide_mixed_pair_counterexample(case):
 c=case;c.admit_during_lease();c.retire();c.reconcile();c.publish_frozen_config(partial=True)
 actual=restart_binding_child(c)
 assert actual=={'core':str(c.paths['v2']['core']),'ops':str(c.paths['v1']['ops'])}
 (c.env.path/'restart-mixed-pair-evidence.json').write_text(json.dumps(actual,indent=2))
