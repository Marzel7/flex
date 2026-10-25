"""Provider-free seven-day disposable lifecycle; exact pair and writer leases."""
import ast,fcntl,hashlib,importlib.util,json,os,pathlib,resource,socket,sqlite3,sys,types
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
NATIVE=sqlite3.connect
DAY=86400
NOW=1800000000

def load(path,name):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def publish(path,obj):path.write_text(json.dumps(obj,sort_keys=True,separators=(',',':')))

@pytest.fixture
def case(tmp_path,monkeypatch):
 def forbid(*a,**k):raise AssertionError('NETWORK_FORBIDDEN')
 monkeypatch.setattr(socket.socket,'connect',forbid)
 # Resolve actual isolated listener package, never main checkout or installed workers.
 for name in list(sys.modules):
  if name=='src' or name.startswith('src.'):monkeypatch.delitem(sys.modules,name)
 monkeypatch.syspath_prepend(str(ROOT/'runtime/listener'))
 monkeypatch.setattr(sqlite3,'connect',NATIVE)
 old=resource.getrlimit(resource.RLIMIT_FSIZE);resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,old[1]))
 g=load(ROOT/'runtime/listener/src/core/paired_binding_guard.py','lifecycle_pair_fixture')
 paths={k:tmp_path/(k+'.sqlite') for k in ['core','ops']}
 for role,p in paths.items():
  c=NATIVE(p)
  c.execute('CREATE TABLE rpc_response_cache(cache_key TEXT PRIMARY KEY,response_json TEXT NOT NULL,method TEXT NOT NULL,cached_at REAL NOT NULL,ttl_seconds INTEGER NOT NULL,hit_count INTEGER NOT NULL DEFAULT 0)')
  for table in ['token_analysis','operation_memberships','treasury_routes','walkback_attribution','potential_assessments','token_market_cap_peaks','pending_retries','quarantine','migration_terminal_evidence_v1','provider_budget_checkpoint']:
   c.execute('CREATE TABLE '+table+'(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
   c.execute('INSERT INTO '+table+' VALUES(?,?)',('protected','canonical-'+table))
  if role=='core':
   c.execute('CREATE TABLE wss_metrics(id INTEGER PRIMARY KEY AUTOINCREMENT,ts REAL NOT NULL,subscription TEXT NOT NULL,source_file TEXT NOT NULL,msg_count INTEGER NOT NULL DEFAULT 1,est_bytes INTEGER NOT NULL DEFAULT 0,note TEXT)')
   c.execute('CREATE INDEX idx_wss_ts ON wss_metrics(ts)')
  c.commit();c.close()
 pair=tmp_path/'pair.json';authority=tmp_path/'owner.json'
 m={'version':1,'authority':{'id':'lifecycle-fixture','path':str(authority),'origin_generation':'v2'},'generation':'v2','stores':{k:g.database_identity(str(p),'v2',k) for k,p in paths.items()},'predecessor':None}
 publish(pair,m);publish(authority,{'version':1,'authority_id':'lifecycle-fixture','origin_generation':'v2','generation':'v2','manifest_path':str(pair),'manifest_sha256':hashlib.sha256(pair.read_bytes()).hexdigest(),'phase':'ACTIVE','owner':'runtime','transfer_complete':True});g.seal_manifest(pair)
 profile={'version':1,'generation':'v2','core':str(paths['core']),'ops':str(paths['ops']),'pair_manifest':str(pair),'pair_authority':str(authority),'pair_sha256':hashlib.sha256(pair.read_bytes()).hexdigest(),'lock':str(tmp_path/'lifecycle.lock')}
 manifest=tmp_path/'manifest.json';publish(manifest,profile);digest=hashlib.sha256(manifest.read_bytes()).hexdigest()
 module=load(ROOT/'runtime/listener/src/ops/v2_storage_lifecycle.py','lifecycle_fixture')
 owner=module.Lifecycle(manifest,digest);owner.initialize()
 def add_cache(key,age,ttl=60,payload='{}',role='core'):
  c=NATIVE(paths[role]);c.execute('INSERT INTO rpc_response_cache VALUES(?,?,?,?,?,0)',(key,payload,'synthetic',NOW-age*DAY,ttl));c.commit();c.close()
 def add_wss(age,note='detail'):
  c=NATIVE(paths['core']);c.execute('INSERT INTO wss_metrics(ts,subscription,source_file,note) VALUES(?,?,?,?)',(NOW-age*DAY,'synthetic','fixture',note));c.commit();c.close()
 def keys(role='core'):
  c=NATIVE(paths[role]);x=c.execute('SELECT cache_key FROM rpc_response_cache ORDER BY cache_key').fetchall();c.close();return [v[0] for v in x]
 yield types.SimpleNamespace(root=tmp_path,paths=paths,g=g,manifest=manifest,digest=digest,profile=profile,module=module,owner=owner,cache=add_cache,wss=add_wss,keys=keys)
 resource.setrlimit(resource.RLIMIT_FSIZE,old)


def test_six_seven_eight_days_and_valid_cache(case):
 for age in [6,7,8]:case.cache(str(age),age);case.wss(age)
 case.cache('valid-eight',8,ttl=20*DAY)
 result=case.owner.run_once(now=NOW)
 assert case.keys()==['6','7','valid-eight']
 c=NATIVE(case.paths['core']);assert c.execute('SELECT count(*) FROM wss_metrics').fetchone()[0]==2;c.close()
 assert sum(v['deleted'] for v in result)==2


@pytest.mark.parametrize('family',['token_analysis','operation_memberships','treasury_routes','walkback_attribution','potential_assessments','token_market_cap_peaks','pending_retries','quarantine','migration_terminal_evidence_v1','provider_budget_checkpoint'])
def test_protected_evidence_never_selected_by_age(case,family):
 with pytest.raises(case.module.Hold,match='UNQUALIFIED_FAMILY'):case.owner.step('core',family,now=NOW)
 case.cache('expired',8);case.owner.run_once(now=NOW)
 for role,p in case.paths.items():
  c=NATIVE(p);assert c.execute('SELECT payload FROM '+family).fetchone()[0]=='canonical-'+family;c.close()


def test_restart_midcycle_and_bounded_idempotent_batches(case):
 for i in range(150):case.cache(f'{i:03}',8)
 first=case.owner.step('core','rpc_response_cache',now=NOW,batch=20);assert first['examined']==first['deleted']==20
 restarted=case.module.Lifecycle(case.manifest,case.digest)
 for _ in range(7):assert restarted.step('core','rpc_response_cache',now=NOW,batch=20)['examined']<=20
 assert case.keys()==[]
 assert restarted.step('core','rpc_response_cache',now=NOW)['deleted']==0
 c=NATIVE(case.paths['core']);assert c.execute('SELECT deleted FROM v2_storage_lifecycle_state WHERE family="rpc_response_cache"').fetchone()[0]==150;c.close()


@pytest.mark.parametrize('point',['before_commit','after_commit'])
def test_crash_accounting_is_atomic(case,point):
 case.cache('expired',8)
 def fail(stage):
  if stage==point:raise RuntimeError('CRASH')
 with pytest.raises(RuntimeError,match='CRASH'):case.owner.step('core','rpc_response_cache',now=NOW,crash=fail)
 assert case.keys()==(['expired'] if point=='before_commit' else [])
 restarted=case.module.Lifecycle(case.manifest,case.digest);restarted.step('core','rpc_response_cache',now=NOW)
 c=NATIVE(case.paths['core']);assert c.execute('SELECT deleted FROM v2_storage_lifecycle_state WHERE family="rpc_response_cache"').fetchone()[0]==1;c.close()


def test_simulated_intake_and_late_identity_not_skipped_forever(case):
 for key in ['b','c','d']:case.cache(key,8)
 case.owner.step('core','rpc_response_cache',now=NOW,batch=1)
 case.cache('a',8);case.cache('z',0)
 for _ in range(5):case.owner.step('core','rpc_response_cache',now=NOW,batch=2)
 assert case.keys()==['z']


@pytest.mark.parametrize('kind',['disk','wal','filecap','pair'])
def test_storage_and_authority_refusal(case,monkeypatch,kind):
 case.cache('expired',8)
 if kind=='disk':monkeypatch.setattr(case.module.os,'statvfs',lambda p:types.SimpleNamespace(f_bavail=0,f_frsize=4096))
 elif kind=='wal':monkeypatch.setattr(case.module,'MAX_WAL_START_BYTES',0);Path(str(case.paths['core'])+'-wal').write_bytes(b'x')
 elif kind=='filecap':monkeypatch.setattr(case.module.resource,'getrlimit',lambda _: (resource.RLIM_INFINITY,resource.RLIM_INFINITY))
 else:case.owner.m['generation']='wrong'
 with pytest.raises(RuntimeError):case.owner.run_once(now=NOW)
 assert case.keys()==['expired']


def test_single_scheduled_owner_and_bounded_command(case):
 fd=os.open(case.profile['lock'],os.O_WRONLY|os.O_CREAT,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 try:
  with pytest.raises(case.module.Hold,match='DUPLICATE_CLEANUP_OWNER'):case.owner.run_once(now=NOW)
 finally:os.close(fd)
 command=case.module.render_schedule(str(Path(sys.executable).resolve()),str(ROOT/'runtime/listener'),str(case.manifest),case.digest)
 assert command.count('src.ops.v2_storage_lifecycle')==1 and '--quiet' in command
 assert 'V1' not in command and '>/dev/null' in command


def test_physical_freelist_page_reuse_without_vacuum(case):
 for i in range(64):case.cache(f'old-{i:03}',8,payload='x'*12000)
 c=NATIVE(case.paths['core']);before=c.execute('PRAGMA page_count').fetchone()[0];c.close()
 result=case.owner.step('core','rpc_response_cache',now=NOW)
 assert result['deleted']==64
 c=NATIVE(case.paths['core']);free=c.execute('PRAGMA freelist_count').fetchone()[0];size=c.execute('PRAGMA page_count').fetchone()[0];c.close();assert free>0 and size==before
 for i in range(64):case.cache(f'new-{i:03}',0,payload='x'*12000)
 c=NATIVE(case.paths['core']);after=c.execute('PRAGMA page_count').fetchone()[0];free_after=c.execute('PRAGMA freelist_count').fetchone()[0];c.close();assert after<=before+2 and free_after<free
 (case.root/'reuse.json').write_text(json.dumps({'before_pages':before,'after_delete_pages':size,'freelist_after_delete':free,'after_reinsert_pages':after,'freelist_after_reinsert':free_after,'deleted':result['deleted']}))


def test_unknown_dependency_trigger_and_oversized_rows_fail_protected(case):
 case.cache('large',8,payload='x'*1000001)
 result=case.owner.step('core','rpc_response_cache',now=NOW);assert result['large_rows_protected']==1 and case.keys()==['large']
 c=NATIVE(case.paths['core']);c.execute("CREATE TRIGGER unknown_dependency BEFORE DELETE ON rpc_response_cache BEGIN DELETE FROM token_analysis;END");c.commit();c.close()
 with pytest.raises(case.module.Hold,match='UNKNOWN_DELETE_DEPENDENCY'):case.owner.step('core','rpc_response_cache',now=NOW)
 assert case.keys()==['large']


@pytest.mark.parametrize('role',['listener','creator_resolution','walkback'])
def test_cache_validity_preserved_and_old_age_deleters_retired(case,role,monkeypatch):
 m=load(ROOT/f'runtime/{role}/src/core/rpc_cache.py','cache_'+role)
 instance=object.__new__(m.RPCCache);instance.db_path=str(case.paths['core']);instance._get_conn=lambda:NATIVE(case.paths['core'])
 case.cache('expired',6);case.cache('valid',0,ttl=1000,payload='{"ok":true}')
 monkeypatch.setattr(m.time,'time',lambda:NOW)
 assert instance.get('expired') is None and 'expired' in case.keys()
 assert instance.get('valid')=={'ok':True}
 assert instance.cleanup_expired()==0
 if hasattr(instance,'cleanup_expired_batch'):assert instance.cleanup_expired_batch(10,now=NOW)==0
 assert set(case.keys())=={'expired','valid'}


def test_concurrent_intake_owns_existing_writer_lane(case):
 import threading
 ready=threading.Event();release=threading.Event();errors=[]
 def intake():
  lease=case.owner.writer.acquire_write_lease('core',str(case.paths['core']),'intake-fixture','synthetic-intake',timeout=.5,priority=0)
  try:
   c=NATIVE(case.paths['core']);c.execute('INSERT INTO rpc_response_cache VALUES(?,?,?,?,?,0)',('new-intake','{}','synthetic',NOW,60));c.commit();c.close();ready.set();release.wait(1)
  except BaseException as error:errors.append(error)
  finally:case.owner.writer.release_write_lease(lease)
 case.cache('old',8);worker=threading.Thread(target=intake);worker.start();assert ready.wait(1)
 try:
  with pytest.raises((RuntimeError,TimeoutError)):case.owner.step('core','rpc_response_cache',now=NOW)
 finally:release.set();worker.join(2)
 assert not worker.is_alive() and not errors
 case.owner.step('core','rpc_response_cache',now=NOW);assert case.keys()==['new-intake']


def test_fresh_compact_pair_rollback_keeps_protected_state_and_continuation(case):
 case.cache('old',8);case.cache('young',6)
 case.owner.step('core','rpc_response_cache',now=NOW,batch=1)
 original=json.loads(Path(case.profile['pair_manifest']).read_text())
 targets={k:case.root/('fresh-'+k+'.sqlite') for k in ['core','ops']}
 for role,old in case.paths.items():
  src=NATIVE(old.as_uri()+'?mode=ro',uri=True);dst=NATIVE(targets[role]);src.backup(dst,pages=20);src.close();dst.close()
  publish(Path(str(old)+'.retired'),{'version':1,'path':str(old),'generation':'v2','retired':True})
 pair=dict(original,generation='fresh',stores={k:case.g.database_identity(str(p),'fresh',k) for k,p in targets.items()},predecessor={'generation':'v2','stores':original['stores']})
 pairpath=case.root/'fresh-pair.json';publish(pairpath,pair)
 authority=Path(case.profile['pair_authority']);publish(authority,{'version':1,'authority_id':original['authority']['id'],'origin_generation':'v2','generation':'fresh','manifest_path':str(pairpath),'manifest_sha256':hashlib.sha256(pairpath.read_bytes()).hexdigest(),'phase':'ACTIVE','owner':'runtime','transfer_complete':True});case.g.seal_manifest(pairpath)
 profile=dict(case.profile,generation='fresh',core=str(targets['core']),ops=str(targets['ops']),pair_manifest=str(pairpath),pair_sha256=hashlib.sha256(pairpath.read_bytes()).hexdigest())
 p=case.root/'fresh-config.json';publish(p,profile);owner=case.module.Lifecycle(p,hashlib.sha256(p.read_bytes()).hexdigest());owner.run_once(now=NOW)
 for role,path in targets.items():
  c=NATIVE(path);assert c.execute('SELECT payload FROM migration_terminal_evidence_v1').fetchone()[0]=='canonical-migration_terminal_evidence_v1';assert c.execute('SELECT payload FROM provider_budget_checkpoint').fetchone()[0]=='canonical-provider_budget_checkpoint';c.close()
 c=NATIVE(targets['core']);assert c.execute('SELECT cache_key FROM rpc_response_cache').fetchall()==[('young',)];c.close()


def test_missing_owner_state_and_duplicate_initialization(case):
 case.owner.initialize();case.cache('expired',8)
 c=NATIVE(case.paths['core']);c.execute('DROP TABLE v2_storage_lifecycle_state');c.commit();c.close()
 with pytest.raises(case.module.Hold,match='STATE_NOT_INITIALIZED'):case.owner.step('core','rpc_response_cache',now=NOW)
 assert case.keys()==['expired']


@pytest.mark.parametrize('role',['listener','creator_resolution','walkback'])
def test_paired_v2_disables_legacy_maintenance_before_database_open(monkeypatch,role):
 import asyncio
 source=(ROOT/'runtime'/role/'src/core/pumpfun_curve_listener.py').read_text()
 tree=ast.parse(source)
 method=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_db_maintenance_periodic')
 def forbidden(*a,**k):raise AssertionError('LEGACY_MAINTENANCE_MUST_NOT_OPEN_DATABASE')
 namespace={'os':os,'db_connect':forbidden}
 exec(compile(ast.Module(body=[method],type_ignores=[]),'<maintenance-guard>','exec'),namespace)
 monkeypatch.setenv('DEV004_PAIRED_BINDING_REQUIRED','1')
 asyncio.run(namespace['_db_maintenance_periodic'](object()))
