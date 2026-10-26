import ast
import contextlib
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import socket
import sqlite3
import sys
import types
from typing import Optional
import uuid
import pytest

ROOT=Path(__file__).resolve().parents[1];PKG=ROOT/'runtime/listener'
def private(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def b58(data):
 alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';n=int.from_bytes(data,'big');out=''
 while n:n,r=divmod(n,58);out=alphabet[r]+out
 return '1'*(len(data)-len(data.lstrip(b'\0')))+out

@pytest.fixture
def env(tmp_path,monkeypatch):
 old_modules={k:v for k,v in sys.modules.items() if k=='src' or k.startswith('src.')}
 for k in old_modules:sys.modules.pop(k)
 monkeypatch.syspath_prepend(str(PKG))
 def forbidden(*a,**kw):raise AssertionError('PROVIDER_FORBIDDEN')
 monkeypatch.setattr(socket.socket,'connect',forbidden);monkeypatch.setattr(socket.socket,'bind',forbidden);monkeypatch.setattr(socket,'create_connection',forbidden)
 old_limit=resource.getrlimit(resource.RLIMIT_FSIZE);resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,old_limit[1]))
 core=tmp_path/'core.sqlite';ops=tmp_path/'ops.sqlite'
 schema=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/production_profile_bootstrap/accepted_fixture_schema.json').read_text())
 for role,path in [('core',core),('ops',ops)]:
  with sqlite3.connect(path) as c:
   for item in schema[role]:
    if item['type']=='table':c.execute(item['sql'])
   for item in schema[role]:
    if item['type']!='table':c.execute(item['sql'])
 g=importlib.import_module('src.core.paired_binding_guard');pair=tmp_path/'pair.json';owner=tmp_path/'owner.json';gen='instrumentation-fixture-v2'
 def save(p,v):p.write_text(json.dumps(v,sort_keys=True,separators=(',',':')))
 manifest={'version':1,'authority':{'id':'audit-fixture','path':str(owner),'origin_generation':gen},'generation':gen,'stores':{r:g.database_identity(str(p),gen,r) for r,p in [('core',core),('ops',ops)]},'predecessor':None};save(pair,manifest);save(owner,{'version':1,'authority_id':'audit-fixture','origin_generation':gen,'generation':gen,'manifest_path':str(pair),'manifest_sha256':hashlib.sha256(pair.read_bytes()).hexdigest(),'phase':'ACTIVE','owner':'runtime','transfer_complete':True});g.seal_manifest(pair)
 for k,v in {'DB_PATH':str(core),'WT_OPS_DB_PATH':str(ops),'FLEX_DB_PAIR_MANIFEST':str(pair),'FLEX_DB_PAIR_AUTHORITY':str(owner),'DEV004_PAIRED_BINDING_REQUIRED':'1','FLEX_DB_PAIR_ROLE':'runtime','FLEX_V2_CREATE_AUDIT_ENABLED':'1','TMPDIR':str(tmp_path),'SQLITE_TMPDIR':str(tmp_path)}.items():monkeypatch.setenv(k,v)
 receipts=tmp_path/'receipts';receipts.mkdir();monkeypatch.setenv('PUMPPORTAL_COMPACT_BIRTH_RECEIPTS_PATH',str(receipts))
 # Optional connection diagnostics are unrelated to the real shared writer/
 # retirement/paired guard and must not create background diagnostic workers.
 util=types.ModuleType('src.utils');util.__path__=[str(PKG/'src/utils')];db=types.ModuleType('src.utils.db_locking');db._sqlite_lifecycle_enabled=lambda _:False;util.db_locking=db;sys.modules['src.utils']=util;sys.modules['src.utils.db_locking']=db
 writer=importlib.import_module('src.core.database_write_service');audit=importlib.import_module('src.ops.create_coverage');accepted=importlib.import_module('src.ops.walkback_create_handoff');svc=writer.DatabaseWriteService(_test_sqlite_timeout=0.1)
 def write(role,fn):
  cmd=writer._Command('audit-fixture-'+role,str(core if role=='core' else ops),'audit-fixture-callback',fn);svc._execute(cmd);return cmd.result
 # Compile actual existing owner/callback bodies; isolate unrelated attribution
 # pipeline integrations while retaining SQL and every new audit hook.
 tree=ast.parse((PKG/'src/core/walkback_queue.py').read_text());functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('enqueue_migration','ensure_create_coverage_schema')]
 ns={'os':os,'sqlite3':sqlite3,'Optional':Optional,'time':__import__('time'),'LIVE_DB_PATH':str(core),'classify_creator':lambda creator,*a:('FULL_WALKBACK',creator,None,None,'fixture'),'_ZERO_RPC_OUTCOMES':{}}
 deep=types.ModuleType('src.core.deep_walkback');deep.valid_signature=lambda s:accepted.identity(s,range(64,89),64);sys.modules['src.core.deep_walkback']=deep
 candidates=types.ModuleType('src.ops.watchtower_candidates');candidates.evaluate_and_enqueue_candidate=lambda *a,**kw:None;sys.modules['src.ops.watchtower_candidates']=candidates
 exec(compile(ast.Module(body=functions,type_ignores=[]),'accepted-enqueue','exec'),ns)
 write('ops',ns['ensure_create_coverage_schema']);write('core',audit.initialize_core_index)
 def proof(i=0,source='PUMPPORTAL'):
  m=b58(hashlib.sha256(('mint'+str(i)).encode()).digest());c=b58(hashlib.sha256(('creator'+str(i)).encode()).digest());s=b58(hashlib.sha512(('signature'+str(i)).encode()).digest());return accepted.Proof(m,c,s,source,'PUMPPORTAL_NEW_TOKEN' if source=='PUMPPORTAL' else 'DEV074_STRICT_CREATE',s,100)
 def seed(p,has_create=False):
  write('core',lambda c:c.execute('INSERT INTO token_analysis(mint,migrated_at,migration_tx,earliest_tx_creator,pf_ws_creator,create_tx_signature) VALUES(?,?,?,?,?,?)',(p.mint,100,'migration-'+p.mint,p.creator,p.creator,p.signature if has_create else None)))
 def enqueue(p):
  with sqlite3.connect(core.as_uri()+'?mode=ro',uri=True) as read:
   return write('ops',lambda c:ns['enqueue_migration'](c,mint=p.mint,creator=p.creator,live_conn=read))
 def qualify(p):
  write('core',lambda c:c.execute('UPDATE token_analysis SET create_tx_signature=? WHERE mint=?',(p.signature,p.mint)))
  ident=hashlib.sha256(audit.packed({'source':'PUMPPORTAL_NEW_TOKEN','mint':p.mint,'supplied_creator':p.creator,'supplied_birth_signature':p.signature}).encode()).hexdigest();save(receipts/(ident+'.json'),{'receipt_identity':ident,'kind':'PUMPPORTAL_BIRTH','source':'PUMPPORTAL_NEW_TOKEN','persistence_outcome':'COMMITTED','accepted_at_utc_ns':1000000000,'mint':p.mint,'supplied_creator':p.creator,'supplied_birth_signature':p.signature})
 def read():
  with sqlite3.connect(core.as_uri()+'?mode=ro',uri=True) as c,sqlite3.connect(ops.as_uri()+'?mode=ro',uri=True) as o:return audit.read_coverage(c,o,99.5,101.25)
 yield types.SimpleNamespace(a=audit,g=g,writer=writer,svc=svc,write=write,core=core,ops=ops,owner=owner,pair=pair,manifest=manifest,save=save,proof=proof,seed=seed,enqueue=enqueue,qualify=qualify,read=read,activate=accepted._activate,accepted=accepted,ns=ns,root=tmp_path,receipts=receipts)
 assert all(p.stat().st_size<500000000 for p in tmp_path.rglob('*') if p.is_file());resource.setrlimit(resource.RLIMIT_FSIZE,old_limit)
 for k in list(sys.modules):
  if k=='src' or k.startswith('src.'):sys.modules.pop(k)
 sys.modules.update(old_modules)

def sql(e,text,args=()):
 with sqlite3.connect(e.ops) as c:return c.execute(text,args).fetchall()

def test_real_writer_handoff_delayed_activation_and_reader(env):
 e=env;p=e.proof();e.seed(p);e.enqueue(p);assert e.read()['source']['NO_QUALIFIED_ANCHOR']==1;e.qualify(p);assert e.write('ops',lambda c:e.activate(c,p,0))=='ACTIVATED';r=e.read();assert r['total_canonical_migrations']==1 and r['source']['PUMPPORTAL_DIRECT']==1 and r['timing']['AFTER_HANDOFF']==1
 assert not sql(e,'PRAGMA foreign_key_check')

def test_atomic_same_shared_transaction_and_original_source(env):
 e=env;p=e.proof(source='STRICT_RPC');e.seed(p)
 def body(c):
  with sqlite3.connect(e.core.as_uri()+'?mode=ro',uri=True) as read:e.ns['enqueue_migration'](c,mint=p.mint,creator=p.creator,live_conn=read)
  e.activate(c,p,0)
 # Core signature qualification is normally established before dispatch; the
 # enqueue fixture deliberately has no initial signature for this same-TX case.
 e.write('ops',body)
 e.write('core',lambda c:c.execute('UPDATE token_analysis SET create_tx_signature=? WHERE mint=?',(p.signature,p.mint)))
 r=e.read();assert r['source']['RPC_RECOVERED']==1 and r['timing']['AT_HANDOFF']==1
 before=sql(e,"SELECT * FROM create_coverage_events_v1 WHERE kind='ANCHOR'");e.qualify(p);e.write('ops',lambda c:e.activate(c,e.proof(),0));assert sql(e,"SELECT * FROM create_coverage_events_v1 WHERE kind='ANCHOR'")==before

def test_duplicate_handoff_and_activation_are_idempotent(env):
 e=env;p=e.proof();e.seed(p);e.enqueue(p);e.qualify(p);e.write('ops',lambda c:e.activate(c,p,0));before=sql(e,'SELECT * FROM create_coverage_events_v1 ORDER BY event_key')
 for _ in range(3):e.enqueue(p);assert e.write('ops',lambda c:e.activate(c,p,0))=='ALREADY_COMMITTED'
 assert sql(e,'SELECT * FROM create_coverage_events_v1 ORDER BY event_key')==before

def test_legacy_existing_anchor_is_unknown_not_backfilled(env):
 e=env;p=e.proof();e.seed(p,True);e.enqueue(p);e.qualify(p);assert e.write('ops',lambda c:e.activate(c,p,0))=='ALREADY_COMMITTED'
 r=e.read();assert r['source']['ANCHOR_PRESENT_SOURCE_UNKNOWN']==1 and r['timing']['UNVERIFIABLE']==1;assert not sql(e,"SELECT 1 FROM create_coverage_events_v1 WHERE kind='ANCHOR'")

def test_missing_receipt_preserves_processing_but_unknown_provenance(env):
 e=env;p=e.proof();e.seed(p);e.enqueue(p);e.write('core',lambda c:c.execute('UPDATE token_analysis SET create_tx_signature=? WHERE mint=?',(p.signature,p.mint)));e.write('ops',lambda c:e.activate(c,p,0));assert e.read()['source']['ANCHOR_PRESENT_SOURCE_UNKNOWN']==1

def test_crash_rolls_back_anchor_and_receipt(env):
 e=env;p=e.proof();e.seed(p);e.enqueue(p);e.qualify(p)
 class Crash(BaseException):pass
 def fail(c):e.activate(c,p,0);raise Crash()
 with pytest.raises(Crash):e.write('ops',fail)
 assert sql(e,'SELECT create_anchor_signature FROM wt_walkback_queue')==[(None,)];assert not sql(e,"SELECT 1 FROM create_coverage_events_v1 WHERE kind='ANCHOR'")
 e.write('ops',lambda c:e.activate(c,p,0));assert e.read()['source']['PUMPPORTAL_DIRECT']==1

def test_committed_anchor_before_ack_readback_is_stable(env):
 e=env;p=e.proof();e.seed(p);e.enqueue(p);e.qualify(p);e.write('ops',lambda c:e.activate(c,p,0));old=sql(e,"SELECT * FROM create_coverage_events_v1 WHERE kind='ANCHOR'")
 # New connection/transaction models restart; acknowledgement is not evidence.
 assert e.write('ops',lambda c:e.activate(c,p,0))=='ALREADY_COMMITTED';assert old==sql(e,"SELECT * FROM create_coverage_events_v1 WHERE kind='ANCHOR'")

@pytest.mark.parametrize('case',['wrong_generation','retired','missing_ops','no_v1_fallback','flag_off'])
def test_rejects_invalid_authority_before_mutation(env,monkeypatch,case):
 e=env;p=e.proof();e.seed(p)
 if case=='wrong_generation':d=json.loads(e.owner.read_text());d['generation']='wrong';e.save(e.owner,d)
 elif case=='retired':Path(str(e.ops)+'.retired').write_text(json.dumps({'version':1,'path':str(e.ops),'generation':'instrumentation-fixture-v2','retired':True}))
 elif case=='missing_ops':monkeypatch.delenv('WT_OPS_DB_PATH')
 elif case=='no_v1_fallback':monkeypatch.setenv('DB_PATH',str(e.root/'not-authorized.sqlite'))
 else:monkeypatch.setenv('FLEX_V2_CREATE_AUDIT_ENABLED','0')
 with pytest.raises(Exception):e.enqueue(p)
 assert not sql(e,'SELECT 1 FROM wt_walkback_queue')

def test_quota_closes_without_rolling_back_real_enqueue(env):
 e=env;p=e.proof();e.seed(p);e.write('ops',lambda c:c.execute('UPDATE create_coverage_owner_v1 SET events=4096 WHERE id=1'))
 e.enqueue(p)
 assert sql(e,'SELECT 1 FROM wt_walkback_queue')
 assert sql(e,'SELECT state FROM create_coverage_window_v1')==[('CLOSED_CAPACITY',)]
 assert not e.read()['complete']

def test_schema_owner_repeat_and_generation_authority(env):
 e=env;e.write('ops',e.ns['ensure_create_coverage_schema']);e.write('core',e.a.initialize_core_index);assert sql(e,'SELECT version FROM create_coverage_owner_v1')==[(1,)]


def test_actual_dispatcher_crash_before_ack_and_restart(env):
 import contextlib
 e=env;p=e.proof();e.seed(p);e.enqueue(p);e.qualify(p)
 def control(c):
  for statement in e.accepted.SCHEMA.split(';'):
   if statement.strip():
    name=statement.strip().split()[2].split('(')[0]
    if not c.execute('SELECT 1 FROM sqlite_master WHERE name=?',(name,)).fetchone():c.execute(statement)
  c.execute('INSERT INTO wb_create_control(id,epoch,activated_at,enabled,core_base,ops_base) VALUES(1,?,1,1,?,?)',('instrumented-epoch',e.core.stat().st_size,e.ops.stat().st_size))
  e.accepted.stage(c,p,core_path=e.core,ops_path=e.ops,now=100)
 e.write('core',control)
 @contextlib.contextmanager
 def read_core():
  c=sqlite3.connect(e.core.as_uri()+'?mode=ro',uri=True)
  try:yield c
  finally:c.close()
 class Crash(BaseException):pass
 def fault(stage):
  if stage=='after_ops_commit':raise Crash()
 args=dict(read_core=read_core,write_core=lambda fn:e.write('core',fn),write_ops=lambda fn:e.write('ops',fn),core_path=e.core,ops_path=e.ops,now=200)
 with pytest.raises(Crash):e.accepted.dispatch(**args,fault=fault)
 with sqlite3.connect(e.core) as c:assert c.execute('SELECT state FROM wb_create_delivery').fetchone()[0]=='PENDING'
 before=sql(e,"SELECT * FROM create_coverage_events_v1 WHERE kind='ANCHOR'");assert before
 assert e.accepted.dispatch(**args)==[(p.mint,'ALREADY_COMMITTED')]
 assert before==sql(e,"SELECT * FROM create_coverage_events_v1 WHERE kind='ANCHOR'")
 with sqlite3.connect(e.core) as c:assert c.execute('SELECT state FROM wb_create_delivery').fetchone()[0]=='ACK'


def test_real_writer_concurrency(env):
 from concurrent.futures import ThreadPoolExecutor
 e=env;proofs=[e.proof(i,'STRICT_RPC') for i in (1,2)]
 for p in proofs:e.seed(p)
 with ThreadPoolExecutor(2) as workers:list(workers.map(e.enqueue,proofs))
 for p in proofs:e.write('core',lambda c,p=p:c.execute('UPDATE token_analysis SET create_tx_signature=? WHERE mint=?',(p.signature,p.mint)))
 with ThreadPoolExecutor(2) as workers:list(workers.map(lambda p:e.write('ops',lambda c:e.activate(c,p,0)),proofs))
 r=e.read();assert r['total_canonical_migrations']==2 and r['source']['RPC_RECOVERED']==2 and r['timing']['AFTER_HANDOFF']==2


def test_core_missing_handoff_still_in_exact_denominator(env):
 e=env;p=e.proof();e.seed(p);r=e.read();assert r['total_canonical_migrations']==1 and r['source']['NO_QUALIFIED_ANCHOR']==1 and r['timing']['UNVERIFIABLE']==1


@pytest.mark.parametrize('closed',[False,True])
def test_fresh_matched_pair_preserves_original_audit_generation(env,monkeypatch,closed):
 e=env;p=e.proof();e.seed(p);e.enqueue(p);e.qualify(p);e.write('ops',lambda c:e.activate(c,p,0))
 if closed:e.write('ops',lambda c:e.a.Boundary(c).close('EVENT_OR_BYTE_QUOTA'))
 old=sql(e,'SELECT e.event_key,e.payload,c.generation FROM create_coverage_events_v1 e JOIN create_coverage_commits_v1 c USING(commit_order) ORDER BY e.event_key')
 newcore=e.root/'fresh-core.sqlite';newops=e.root/'fresh-ops.sqlite'
 # Only selected synthetic canonical/queue/evidence rows, not full DB copies.
 with sqlite3.connect(e.core) as src,sqlite3.connect(newcore) as dst:
  dst.execute(src.execute("SELECT sql FROM sqlite_master WHERE name='token_analysis'").fetchone()[0]);row=src.execute('SELECT * FROM token_analysis WHERE mint=?',(p.mint,)).fetchone();dst.execute('INSERT INTO token_analysis VALUES('+','.join('?' for _ in row)+')',row)
 with sqlite3.connect(e.ops) as src,sqlite3.connect(newops) as dst:dst.execute(src.execute("SELECT sql FROM sqlite_master WHERE name='wt_walkback_queue'").fetchone()[0])
 g=e.g;gen='instrumentation-fresh-v2';pair=e.root/'fresh-pair.json';manifest=dict(e.manifest);manifest.update(generation=gen,stores={r:g.database_identity(str(path),gen,r) for r,path in [('core',newcore),('ops',newops)]},predecessor={'generation':e.manifest['generation'],'stores':e.manifest['stores']})
 for path in (e.core,e.ops):Path(str(path)+'.retired').write_text(json.dumps({'version':1,'path':str(path),'generation':e.manifest['generation'],'retired':True}))
 e.save(pair,manifest);e.save(e.owner,{'version':1,'authority_id':'audit-fixture','origin_generation':e.manifest['generation'],'generation':gen,'manifest_path':str(pair),'manifest_sha256':hashlib.sha256(pair.read_bytes()).hexdigest(),'phase':'ACTIVE','owner':'runtime','transfer_complete':True});g.seal_manifest(pair)
 for k,v in {'DB_PATH':str(newcore),'WT_OPS_DB_PATH':str(newops),'FLEX_DB_PAIR_MANIFEST':str(pair)}.items():monkeypatch.setenv(k,v)
 def write(role,fn):
  cmd=e.writer._Command('fresh-audit-'+role,str(newcore if role=='core' else newops),'fresh-fixture-callback',fn);e.svc._execute(cmd);return cmd.result
 write('ops',e.ns['ensure_create_coverage_schema']);write('core',e.a.initialize_core_index)
 with sqlite3.connect(e.ops.as_uri()+'?mode=ro',uri=True) as src:
  tables={name:src.execute('SELECT * FROM '+name).fetchall() for name in ('wt_walkback_queue','create_coverage_commits_v1','create_coverage_events_v1')}
 def transfer(c):
  for name,rows in tables.items():
   for row in rows:c.execute('INSERT INTO '+name+' VALUES('+','.join('?' for _ in row)+')',tuple(row))
 write('ops',transfer)
 if closed:write('ops',lambda c:e.a.Boundary(c).close('EVENT_OR_BYTE_QUOTA'))
 assert write('ops',lambda c:e.activate(c,p,0))=='ALREADY_COMMITTED'
 with sqlite3.connect(newcore.as_uri()+'?mode=ro',uri=True) as c,sqlite3.connect(newops.as_uri()+'?mode=ro',uri=True) as o:
  r=e.a.read_coverage(c,o,99,101);assert r['complete']==(not closed);assert r['source']['PUMPPORTAL_DIRECT']==1 and r['timing']['AFTER_HANDOFF']==1
  assert o.execute('SELECT e.event_key,e.payload,c.generation FROM create_coverage_events_v1 e JOIN create_coverage_commits_v1 c USING(commit_order) ORDER BY e.event_key').fetchall()==old


def renderer_namespace(e):
 path=ROOT/'docs/dev/lightweight_dev004_first_v2/quarantine_continuation/production_control.py';tree=ast.parse(path.read_text());ns={'os':os,'pathlib':__import__('pathlib'),'sqlite3':sqlite3,'importlib':importlib,'g':e.g,'sha':lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest(),'P':{'planned_production_environment':{'FLEX_V2_CREATE_AUDIT_ENABLED':'1'},'roles':{role:{'package':str(ROOT/'runtime'/role)} for role in ('listener','creator_resolution','walkback')}}}
 for n in tree.body:
  if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'CREATE_AUDIT_FLAG','CREATE_AUDIT_PAYLOAD'} for t in n.targets):exec(compile(ast.Module(body=[n],type_ignores=[]),str(path),'exec'),ns)
  if isinstance(n,ast.FunctionDef) and n.name=='coverage_environment':exec(compile(ast.Module(body=[n],type_ignores=[]),str(path),'exec'),ns)
 return ns


def renderer_env():return {key:os.environ[key] for key in ('DB_PATH','WT_OPS_DB_PATH','FLEX_DB_PAIR_MANIFEST','FLEX_DB_PAIR_AUTHORITY','DEV004_PAIRED_BINDING_REQUIRED','FLEX_V2_CREATE_AUDIT_ENABLED')}


@pytest.mark.parametrize('role',['listener','creator_resolution','walkback'])
def test_recovery_renderer_preserves_opt_in_for_each_role(env,role):
 ns=renderer_namespace(env);assert ns['coverage_environment'](role,renderer_env())['FLEX_V2_CREATE_AUDIT_ENABLED']=='1'


@pytest.mark.parametrize('defect',['flag','schema','index','source'])
def test_recovery_rejects_audit_unaware_fallback(env,defect):
 e=env;ns=renderer_namespace(e);cfg=renderer_env()
 if defect=='flag':cfg.pop('FLEX_V2_CREATE_AUDIT_ENABLED')
 elif defect=='schema':e.write('ops',lambda c:c.execute('DROP TRIGGER create_coverage_quota'))
 elif defect=='index':e.write('core',lambda c:c.execute('DROP INDEX dev_create_migration_time_v1'))
 else:ns['CREATE_AUDIT_PAYLOAD']['listener']['src/ops/create_coverage.py']='0'*64
 with pytest.raises(Exception):ns['coverage_environment']('listener',cfg)


def test_audit_evidence_cannot_be_relabelled_or_deleted(env):
 e=env;p=e.proof();e.seed(p);e.enqueue(p);e.qualify(p);e.write('ops',lambda c:e.activate(c,p,0))
 with pytest.raises(sqlite3.IntegrityError):e.write('ops',lambda c:c.execute("UPDATE create_coverage_events_v1 SET payload='{}' WHERE kind='ANCHOR'"))
 with pytest.raises(sqlite3.IntegrityError):e.write('ops',lambda c:c.execute('DELETE FROM create_coverage_commits_v1'))
 assert e.read()['source']['PUMPPORTAL_DIRECT']==1


def test_reader_capture_overflow_never_reports_partial_denominator(env):
 e=env
 def insert(c):
  c.executemany('INSERT INTO token_analysis(mint,migrated_at) VALUES(?,100)',[(e.proof(i).mint,) for i in range(4097)])
 e.write('core',insert)
 with pytest.raises(e.a.Hold,match='AUDIT_CAPTURE_CAPACITY'):e.read()


def test_read_only_cli_matches_integrated_reader(env):
 import subprocess
 e=env;p=e.proof();e.seed(p);e.enqueue(p);e.qualify(p);e.write('ops',lambda c:e.activate(c,p,0));site=e.root/'network-block';site.mkdir();(site/'sitecustomize.py').write_text("import socket\ndef deny(*a,**k):raise RuntimeError('NETWORK_FORBIDDEN')\nsocket.socket.connect=deny\nsocket.socket.bind=deny\nsocket.create_connection=deny\n")
 args=[sys.executable,'-B',str(ROOT/'scripts/flex_v2_create_coverage_audit.py'),'--core',str(e.core),'--ops',str(e.ops),'--generation','instrumentation-fixture-v2','--pair-manifest',str(e.pair),'--authority',str(e.owner),'--start-utc','1970-01-01T00:01:39.500000Z','--end-utc','1970-01-01T00:01:41.250000Z']
 result=subprocess.run(args,capture_output=True,text=True,env=dict(os.environ,PYTHONPATH=str(site),PYTHONDONTWRITEBYTECODE='1'),timeout=10);assert result.returncode==0,result.stderr;r=json.loads(result.stdout);assert r['total_canonical_migrations']==1 and r['source']['PUMPPORTAL_DIRECT']==1


def test_new_pending_migration_can_handoff_after_preexisting_anchor(env):
 import dataclasses,time
 e=env;stamp=int(time.time())+2;p=dataclasses.replace(e.proof(),migrated_at=stamp)
 e.write('core',lambda c:c.execute('INSERT INTO token_analysis(mint,migrated_at,migration_tx,earliest_tx_creator,pf_ws_creator,create_tx_signature) VALUES(?,?,?,?,?,?)',(p.mint,stamp,'migration-'+p.mint,p.creator,p.creator,p.signature)))
 # Existing accepted non-migration enqueue state, before current migration handoff.
 e.write('ops',lambda c:c.execute("INSERT INTO wt_walkback_queue(mint,creator,status,path_state,enqueued_at) VALUES(?,?,'waiting','WAITING_FOR_CREATE_ANCHOR',?)",(p.mint,p.creator,stamp)))
 e.qualify(p);e.write('ops',lambda c:e.activate(c,p,0))
 e.write('core',lambda c:c.execute("INSERT INTO migration_walkback_handoff(mint,migration_tx,migrated_at,state,created_at) VALUES(?,?,?,'PENDING',?)",(p.mint,'migration-'+p.mint,stamp,stamp)))
 e.enqueue(p)
 with sqlite3.connect(e.core.as_uri()+'?mode=ro',uri=True) as c,sqlite3.connect(e.ops.as_uri()+'?mode=ro',uri=True) as o:
  r=e.a.read_coverage(c,o,stamp-1,stamp+1);assert r['source']['PUMPPORTAL_DIRECT']==1 and r['timing']['BEFORE_HANDOFF']==1


def test_existing_legacy_queue_cannot_manufacture_handoff_timing(env):
 e=env;p=e.proof();e.seed(p)
 e.write('ops',lambda c:c.execute("INSERT INTO wt_walkback_queue(mint,creator,status,path_state,enqueued_at) VALUES(?,?,'waiting','WAITING_FOR_CREATE_ANCHOR',100)",(p.mint,p.creator)))
 e.enqueue(p);assert not sql(e,"SELECT 1 FROM create_coverage_events_v1 WHERE kind='HANDOFF'")


@pytest.mark.parametrize('role',['listener','creator_resolution','walkback'])
def test_packaged_accepted_callbacks_coexist(role,env):
 e=env;package=ROOT/'runtime'/role/'src';assert (package/'ops/create_coverage.py').read_bytes()==(PKG/'src/ops/create_coverage.py').read_bytes()
 assert (package/'ops/walkback_create_handoff.py').read_bytes()==(PKG/'src/ops/walkback_create_handoff.py').read_bytes()
 tree=ast.parse((package/'core/walkback_queue.py').read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='enqueue_migration');ns=dict(e.ns);exec(compile(ast.Module(body=[fn],type_ignores=[]),'packaged-accepted-enqueue','exec'),ns)
 p=e.proof();e.seed(p)
 with sqlite3.connect(e.core.as_uri()+'?mode=ro',uri=True) as read:e.write('ops',lambda c:ns['enqueue_migration'](c,mint=p.mint,creator=p.creator,live_conn=read))
 assert len(sql(e,"SELECT 1 FROM create_coverage_events_v1 WHERE kind='HANDOFF'"))==1


@pytest.mark.parametrize('large',[False,True])
def test_actual_capacity_boundaries_preserve_evidence_and_processing(env,large):
 e=env;detail={'x':'a'*1016} if large else {'x':'a'}
 for start in range(0,4095,128):
  def fill(c,start=start):
   b=e.a.Boundary(c)
   for i in range(start,min(start+128,4095)):b.record('HANDOFF','m'+str(i),'s'+str(i),detail)
  e.write('ops',fill)
 assert sql(e,'SELECT events FROM create_coverage_owner_v1')==[(4095,)]
 assert sql(e,'SELECT state FROM create_coverage_window_v1')==[('OPEN',)]
 e.write('ops',lambda c:e.a.Boundary(c).record('HANDOFF','last','last',detail))
 assert sql(e,'SELECT events FROM create_coverage_owner_v1')==[(4096,)]
 if large:assert sql(e,'SELECT bytes FROM create_coverage_owner_v1')==[(4194304,)]
 before=sql(e,'SELECT * FROM create_coverage_events_v1 ORDER BY event_key')
 p=e.proof();e.seed(p);e.enqueue(p);e.qualify(p)
 assert e.write('ops',lambda c:e.activate(c,p,0))=='ACTIVATED'
 assert e.write('ops',lambda c:e.activate(c,p,0))=='ALREADY_COMMITTED'
 assert before==sql(e,'SELECT * FROM create_coverage_events_v1 ORDER BY event_key')
 r=e.read();assert not r['complete'] and r['observation_state']=='CLOSED_CAPACITY'
 assert r['timing']['UNMEASURED_AFTER_CLOSURE']==1
 assert r['source']['ANCHOR_PRESENT_SOURCE_UNKNOWN']==1
 e.write('ops',e.ns['ensure_create_coverage_schema'])
 assert sql(e,'SELECT state FROM create_coverage_window_v1')==[('CLOSED_CAPACITY',)]
 with pytest.raises(sqlite3.IntegrityError):e.write('ops',lambda c:c.execute("UPDATE create_coverage_window_v1 SET state='OPEN'"))


def test_closure_crash_rolls_back_then_restart_closes(env):
 e=env;e.write('ops',lambda c:c.execute('UPDATE create_coverage_owner_v1 SET events=4096'))
 def interrupted(c):
  assert e.a.Boundary(c).record('HANDOFF','x','x',{})=='UNMEASURED_AFTER_CLOSURE'
  raise RuntimeError('CRASH_BEFORE_COMMIT')
 with pytest.raises(RuntimeError):e.write('ops',interrupted)
 assert sql(e,'SELECT state FROM create_coverage_window_v1')==[('OPEN',)]
 e.write('ops',lambda c:e.a.Boundary(c).record('HANDOFF','x','x',{}))
 assert sql(e,'SELECT state FROM create_coverage_window_v1')==[('CLOSED_CAPACITY',)]
 e.write('ops',lambda c:e.a.Boundary(c).record('HANDOFF','x','x',{}))
 assert sql(e,'SELECT count(*) FROM create_coverage_events_v1')==[(0,)]


def test_closed_window_fresh_pair_transfer_preserves_closure(env):
 e=env;e.write('ops',lambda c:e.a.Boundary(c).close('EVENT_OR_BYTE_QUOTA'))
 # Existing qualified matched-pair transfer copies all OPS tables; closure is
 # a singleton owner row, not connection-local state or a source-generation flag.
 with sqlite3.connect(e.ops) as c:
  row=c.execute('SELECT * FROM create_coverage_window_v1').fetchone()
  schema=c.execute("SELECT sql FROM sqlite_master WHERE name='create_coverage_window_v1'").fetchone()[0]
 with sqlite3.connect(e.root/'recovery-closure.sqlite') as c:
  c.execute(schema);c.execute('INSERT INTO create_coverage_window_v1 VALUES(?,?,?,?)',row)
 with sqlite3.connect(e.root/'recovery-closure.sqlite') as c:assert c.execute('SELECT state FROM create_coverage_window_v1').fetchone()==('CLOSED_CAPACITY',)


@pytest.mark.parametrize('defect',['schema','storage'])
def test_closed_window_does_not_bypass_essential_guards(env,monkeypatch,defect):
 e=env;e.write('ops',lambda c:e.a.Boundary(c).close('EVENT_OR_BYTE_QUOTA'));p=e.proof();e.seed(p)
 if defect=='schema':e.write('ops',lambda c:c.execute('DROP TRIGGER create_coverage_quota'))
 else:monkeypatch.setattr(e.a.os,'statvfs',lambda p:types.SimpleNamespace(f_bavail=1,f_frsize=1))
 with pytest.raises(Exception):e.enqueue(p)
 assert not sql(e,'SELECT 1 FROM wt_walkback_queue')


def test_concurrent_callbacks_at_capacity(env):
 from concurrent.futures import ThreadPoolExecutor
 e=env;p=e.proof();q=e.proof(1);e.seed(p);e.seed(q);e.enqueue(q);e.qualify(q)
 e.write('ops',lambda c:c.execute('UPDATE create_coverage_owner_v1 SET events=4095'))
 with ThreadPoolExecutor(max_workers=2) as pool:
  a=pool.submit(e.enqueue,p);b=pool.submit(e.write,'ops',lambda c:e.activate(c,q,0));a.result();assert b.result()=='ACTIVATED'
 assert sql(e,'SELECT events FROM create_coverage_owner_v1')==[(4096,)]
 assert sql(e,'SELECT state FROM create_coverage_window_v1')==[('CLOSED_CAPACITY',)]
 assert len(sql(e,'SELECT 1 FROM wt_walkback_queue'))==2
