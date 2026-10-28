"""Compact disposable metadata/SQLite tests; never production or provider I/O."""
import copy,hashlib,importlib.util,json,os,pathlib,socket,sqlite3,types
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1]
GUARD=ROOT/'runtime/listener/src/core/paired_binding_guard.py'
def load():
 spec=importlib.util.spec_from_file_location('retirement_fixture_guard',GUARD);g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g);return g
def save(path,value):path.write_text(json.dumps(value,sort_keys=True,separators=(',',':')))
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
@pytest.fixture
def case(tmp_path,monkeypatch):
 monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN')))
 g=load()
 for k in [g.RETIREMENT_PATH_ENV,g.RETIREMENT_SHA_ENV,'FLEX_DB_AUXILIARY_MANIFEST','FLEX_DB_AUXILIARY_SHA256','FLEX_DB_PAIR_OWNER']:monkeypatch.delenv(k,raising=False)
 old={r:tmp_path/('old-'+r+'.sqlite') for r in ['core','ops']};current={r:tmp_path/(r+'.sqlite') for r in old}
 for p in [*old.values(),*current.values()]:
  with sqlite3.connect(p) as c:c.execute('CREATE TABLE protected_evidence(id TEXT PRIMARY KEY,payload TEXT)');c.execute("INSERT INTO protected_evidence VALUES('immutable','EXACT_PROVENANCE')")
 for role in old:save(pathlib.Path(str(old[role])+'.retired'),{'version':1,'path':str(old[role]),'generation':'legacy','retired':True})
 authority=tmp_path/'owner.json';pair=tmp_path/'pair.json';m={'version':1,'authority':{'id':'existing-pair-owner','path':str(authority),'origin_generation':'legacy'},'generation':'v2','stores':{r:g.database_identity(str(p),'v2',r) for r,p in current.items()},'predecessor':{'generation':'legacy','stores':{r:g.database_identity(str(p),'legacy',r) for r,p in old.items()}}};save(pair,m)
 state={'version':1,'authority_id':'existing-pair-owner','origin_generation':'legacy','generation':'v2','manifest_path':str(pair),'manifest_sha256':digest(pair),'phase':'ACTIVE','owner':'runtime','transfer_complete':True};save(authority,state);g.seal_manifest(pair)
 env={'DB_PATH':str(current['core']),'WT_OPS_DB_PATH':str(current['ops']),'FLEX_DB_PAIR_MANIFEST':str(pair),'FLEX_DB_PAIR_AUTHORITY':str(authority),'DEV004_PAIRED_BINDING_REQUIRED':'1','FLEX_DB_PAIR_ROLE':'runtime'}
 for k,v in env.items():monkeypatch.setenv(k,v)
 ledger=tmp_path/'helius.json';save(ledger,{'reserved':73920,'uncertain_requests':['a','b','c','d','e'],'lifetime_limit':100000,'ownership':'original'})
 yield types.SimpleNamespace(g=g,root=tmp_path,old=old,current=current,pair=pair,m=m,state=state,authority=authority,certificate=tmp_path/'owner.json.predecessor-retirement-v1.json',env=env,ledger=ledger,monkeypatch=monkeypatch)
 assert all(p.stat().st_size<500000000 for p in tmp_path.rglob('*') if p.is_file())
def certify(e):
 result=e.g.publish_predecessor_retirement(e.pair,e.certificate,authorize=True)
 e.monkeypatch.setenv(e.g.RETIREMENT_PATH_ENV,result['path']);e.monkeypatch.setenv(e.g.RETIREMENT_SHA_ENV,result['sha256']);return result

def test_legacy_present_and_original_guard_unchanged(case):assert case.g.validate_pair()=='v2'
@pytest.mark.parametrize('roles',[['core'],['ops'],['core','ops']])
def test_missing_files_without_authority_are_not_retired(case,roles):
 for r in roles:case.old[r].unlink()
 with pytest.raises(case.g.PairedBindingError,match='IDENTITY_MISMATCH'):case.g.validate_pair()
def test_explicit_authorization_required_and_no_automatic_publication(case):
 with pytest.raises(case.g.PairedBindingError,match='EXPLICIT_AUTHORIZATION'):case.g.publish_predecessor_retirement(case.pair,case.certificate)
 assert not case.certificate.exists()
def test_verified_retirement_absent_files_restart_and_history(case):
 e=case;before=e.ledger.read_bytes();manifest=e.pair.read_bytes();certificate=certify(e);raw=e.certificate.read_bytes()
 assert certificate['predecessor']==e.m['predecessor']
 for p in e.old.values():p.unlink()
 for _ in range(3):assert load().validate_pair()=='v2'
 assert e.ledger.read_bytes()==before and e.pair.read_bytes()==manifest and e.certificate.read_bytes()==raw
 with sqlite3.connect(e.current['core']) as c:assert c.execute('SELECT payload FROM protected_evidence').fetchone()==('EXACT_PROVENANCE',)
@pytest.mark.parametrize('fault',['one_binding','missing','corrupt','checksum','stage','authority','predecessor','state','unknown','marker','seal','oversized'])
def test_missing_corrupt_or_ambiguous_evidence_fails_closed(case,fault):
 e=case;certify(e)
 for p in e.old.values():p.unlink()
 if fault=='one_binding':e.monkeypatch.delenv(e.g.RETIREMENT_SHA_ENV)
 elif fault=='missing':e.certificate.unlink()
 elif fault=='corrupt':e.certificate.write_bytes(b'{')
 elif fault=='checksum':e.monkeypatch.setenv(e.g.RETIREMENT_SHA_ENV,'0'*64)
 elif fault=='stage':pathlib.Path(str(e.certificate)+'.stage').write_bytes(b'ambiguous')
 elif fault=='marker':pathlib.Path(str(e.old['core'])+'.retired').unlink()
 elif fault=='seal':pathlib.Path(str(e.current['core'])+'.pair-bound').unlink()
 elif fault=='oversized':e.certificate.write_bytes(b'x'*32769)
 else:
  value=json.loads(e.certificate.read_text())
  if fault=='authority':value['authority']['id']='another-owner'
  elif fault=='predecessor':value['predecessor']['stores']['ops']['inode']+=1
  elif fault=='state':value['state']='PENDING'
  else:value['unknown']='not-qualified'
  save(e.certificate,value);e.monkeypatch.setenv(e.g.RETIREMENT_SHA_ENV,digest(e.certificate))
 with pytest.raises(e.g.PairedBindingError):e.g.validate_pair()
@pytest.mark.parametrize('fault',['active_missing','active_replaced','wrong_generation','mixed_binding','retired_active','retired_predecessor_replaced','transfer'])
def test_active_identity_generation_and_retirement_checks_remain(case,fault):
 e=case;certify(e)
 if fault=='active_missing':e.current['ops'].unlink()
 elif fault=='active_replaced':
  e.current['ops'].rename(e.root/'saved-ops.sqlite');e.current['ops'].write_bytes(b'replaced')
 elif fault=='wrong_generation':v=json.loads(e.authority.read_text());v['generation']='wrong';save(e.authority,v)
 elif fault=='mixed_binding':e.monkeypatch.setenv('WT_OPS_DB_PATH',str(e.old['ops']))
 elif fault=='retired_active':save(pathlib.Path(str(e.current['core'])+'.retired'),{'retired':True})
 elif fault=='retired_predecessor_replaced':
  e.old['core'].rename(e.root/'saved-old.sqlite');e.old['core'].write_bytes(b'replacement')
 else:v=json.loads(e.authority.read_text());v['phase']='TRANSFER';save(e.authority,v)
 with pytest.raises(e.g.PairedBindingError):e.g.validate_pair()
@pytest.mark.parametrize('boundary',['before_commit','after_commit'])
def test_interrupted_publication_requires_verified_owner_retry(case,boundary):
 e=case
 def crash(at):
  if at==boundary:raise RuntimeError('SIMULATED_CRASH')
 with pytest.raises(RuntimeError,match='SIMULATED_CRASH'):e.g.publish_predecessor_retirement(e.pair,e.certificate,authorize=True,crash=crash)
 assert pathlib.Path(str(e.certificate)+'.stage').exists()
 # A partial publish cannot be selected as runtime authority.
 e.monkeypatch.setenv(e.g.RETIREMENT_PATH_ENV,str(e.certificate));e.monkeypatch.setenv(e.g.RETIREMENT_SHA_ENV,digest(e.certificate) if e.certificate.exists() else '0'*64)
 with pytest.raises(e.g.PairedBindingError,match='AMBIGUOUS'):e.g.validate_pair()
 e.monkeypatch.delenv(e.g.RETIREMENT_PATH_ENV);e.monkeypatch.delenv(e.g.RETIREMENT_SHA_ENV);certify(e)
 assert not pathlib.Path(str(e.certificate)+'.stage').exists() and e.g.validate_pair()=='v2'
def test_write_once_repeated_publication_and_original_evidence(case):
 e=case;a=e.g.publish_predecessor_retirement(e.pair,e.certificate,authorize=True);raw=e.certificate.read_bytes();b=e.g.publish_predecessor_retirement(e.pair,e.certificate,authorize=True);assert a==b and e.certificate.read_bytes()==raw
 e.certificate.write_text('{}')
 with pytest.raises(e.g.PairedBindingError,match='CONFLICT'):e.g.publish_predecessor_retirement(e.pair,e.certificate,authorize=True)
def test_fresh_compact_pair_recovery_preserves_certificate_and_v2_identity(case):
 e=case;certify(e);before=e.ledger.read_bytes();certificate=e.certificate.read_bytes()
 for p in e.old.values():p.unlink()
 fresh={r:e.root/('fresh-'+r+'.sqlite') for r in e.current}
 for role,p in fresh.items():
  with sqlite3.connect(e.current[role]) as src,sqlite3.connect(p) as dst:src.backup(dst,pages=16)
  save(pathlib.Path(str(e.current[role])+'.retired'),{'version':1,'path':str(e.current[role]),'generation':'v2','retired':True})
 value=copy.deepcopy(e.m);value.update(generation='fresh',stores={r:e.g.database_identity(str(p),'fresh',r) for r,p in fresh.items()},predecessor={'generation':'v2','stores':e.m['stores']});pair=e.root/'fresh-pair.json';save(pair,value);e.g.seal_manifest(pair);state=dict(e.state,generation='fresh',manifest_path=str(pair),manifest_sha256=digest(pair));save(e.authority,state)
 for k,v in {'DB_PATH':str(fresh['core']),'WT_OPS_DB_PATH':str(fresh['ops']),'FLEX_DB_PAIR_MANIFEST':str(pair)}.items():e.monkeypatch.setenv(k,v)
 for _ in range(3):assert load().validate_pair()=='fresh'
 assert e.ledger.read_bytes()==before and e.certificate.read_bytes()==certificate
 # Explicit pin remains required and missing historical proof still fails.
 e.certificate.unlink()
 fresh_guard=load()
 with pytest.raises(fresh_guard.PairedBindingError):fresh_guard.validate_pair()
def test_configuration_opt_in_requires_paired_authority(case):
 e=case;certify(e);e.monkeypatch.delenv('DB_PATH');e.monkeypatch.delenv('WT_OPS_DB_PATH');e.monkeypatch.delenv('DEV004_PAIRED_BINDING_REQUIRED');e.monkeypatch.delenv('FLEX_DB_PAIR_MANIFEST');e.monkeypatch.delenv('FLEX_DB_PAIR_AUTHORITY')
 with pytest.raises(e.g.PairedBindingError,match='EXPLICIT_BINDINGS'):e.g.validate_pair()
def test_all_active_and_dormant_guards_identical_and_golden_recovery_unchanged():
 expected=GUARD.read_bytes()
 for p in [ROOT/'src/core/paired_binding_guard.py',ROOT/'runtime/creator_resolution/src/core/paired_binding_guard.py',ROOT/'runtime/walkback/src/core/paired_binding_guard.py',ROOT/'integration_sources/monitor_worker/src/core/paired_binding_guard.py',ROOT/'integration_sources/monitor_bridge/src/core/paired_binding_guard.py']:assert p.read_bytes()==expected
 assert digest(ROOT/'scripts/flex_v2_fresh_pair_recovery.py')=='50967f4cd1109dd8cdae78acb629fa586b7ad2733e55b0bc5dd5e257871edcc5'


def test_seven_day_owner_uses_same_certificate_without_new_schema(case):
 import subprocess,sys
 e=case;certify(e)
 for p in e.old.values():p.unlink()
 for role in ['core','ops']:
  with sqlite3.connect(e.current[role]) as c:
   c.execute('CREATE TABLE rpc_response_cache(cache_key TEXT PRIMARY KEY,response_json TEXT NOT NULL,method TEXT NOT NULL,cached_at REAL NOT NULL,ttl_seconds INTEGER NOT NULL,hit_count INTEGER NOT NULL DEFAULT 0)')
   c.execute("INSERT INTO rpc_response_cache VALUES('old','{}','synthetic',100,60,0)")
   c.execute("INSERT INTO rpc_response_cache VALUES('protected','{}','synthetic',2000000000,60,0)")
  if role=='core':
   with sqlite3.connect(e.current[role]) as c:c.execute('CREATE TABLE wss_metrics(id INTEGER PRIMARY KEY AUTOINCREMENT,ts REAL NOT NULL,subscription TEXT NOT NULL,source_file TEXT NOT NULL,msg_count INTEGER NOT NULL DEFAULT 1,est_bytes INTEGER NOT NULL DEFAULT 0,note TEXT)')
 profile={'version':1,'generation':'v2','core':str(e.current['core']),'ops':str(e.current['ops']),'pair_manifest':str(e.pair),'pair_authority':str(e.authority),'pair_sha256':digest(e.pair),'lock':str(e.root/'lifecycle.lock')};p=e.root/'lifecycle.json';save(p,profile)
 code="""import sys,resource,socket
resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,499000000))
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
sys.path.insert(0,sys.argv[1])
from src.ops.v2_storage_lifecycle import Lifecycle
owner=Lifecycle(sys.argv[2],sys.argv[3]);owner.initialize();result=owner.run_once(now=1800000000)
assert sum(r['deleted'] for r in result)==2
print('QUALIFIED_RETIREMENT_LIFECYCLE_PASS')
"""
 z=subprocess.run([sys.executable,'-B','-c',code,str(ROOT/'runtime/listener'),str(p),digest(p)],env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=30);assert z.returncode==0,z.stderr[-2000:]
 for role in ['core','ops']:
  with sqlite3.connect(e.current[role]) as c:
   assert c.execute('SELECT cache_key FROM rpc_response_cache').fetchall()==[('protected',)]
   assert c.execute('SELECT payload FROM protected_evidence').fetchone()==('EXACT_PROVENANCE',)


@pytest.mark.parametrize('role',['listener','creator_resolution','walkback'])
def test_retirement_launch_binds_inherited_legacy_aliases_after_validation(case,role):
 import subprocess,sys
 e=case;certify(e)
 for p in e.old.values():p.unlink()
 env={**os.environ,'CREATOR_DB_PATH':str(e.current['core']),'TMPDIR':str(e.root),'SQLITE_TMPDIR':str(e.root),'FLEX_DB_AUXILIARY_MANIFEST':str(e.pair),'FLEX_DB_PATH':str(e.old['core']),'OPS_V2_DB_PATH':str(e.old['ops']),'PYTHONDONTWRITEBYTECODE':'1'}
 code="""import os,sys,socket
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
sys.path.insert(0,sys.argv[1])
from src.core.lightweight_runtime_entry import activate
result=activate()
assert result['generation']=='v2'
assert os.environ['FLEX_DB_PATH']==os.environ['DB_PATH']
assert os.environ['OPS_V2_DB_PATH']==os.environ['WT_OPS_DB_PATH']
print('QUALIFIED_ALIAS_BINDING_PASS')
"""
 z=subprocess.run([sys.executable,'-B','-c',code,str(ROOT/'runtime'/role)],env=env,capture_output=True,text=True,timeout=20);assert z.returncode==0,z.stderr[-2000:]

def test_certificate_count_and_disk_floor_are_bounded(case,monkeypatch):
 e=case
 with pytest.raises(e.g.PairedBindingError,match='AUTHORITY_PATH'):e.g.publish_predecessor_retirement(e.pair,e.root/'another-certificate.json',authorize=True)
 monkeypatch.setattr(e.g.os,'statvfs',lambda p:types.SimpleNamespace(f_bavail=0,f_frsize=4096))
 with pytest.raises(e.g.PairedBindingError,match='DISK_FLOOR'):e.g.publish_predecessor_retirement(e.pair,e.certificate,authorize=True)
 assert not e.certificate.exists()

def test_two_successive_compact_generations_keep_original_retirement_proof(case):
 e=case;certify(e);historical=e.certificate.read_bytes()
 for p in e.old.values():p.unlink()
 current=e.current;manifest=e.m
 for number in [1,2]:
  generation='fresh-'+str(number);fresh={r:e.root/(generation+'-'+r+'.sqlite') for r in current}
  for role,p in fresh.items():
   with sqlite3.connect(current[role]) as src,sqlite3.connect(p) as dst:src.backup(dst,pages=16)
   save(pathlib.Path(str(current[role])+'.retired'),{'version':1,'path':str(current[role]),'generation':manifest['generation'],'retired':True})
  successor=copy.deepcopy(manifest);successor.update(generation=generation,stores={r:e.g.database_identity(str(p),generation,r) for r,p in fresh.items()},predecessor={'generation':manifest['generation'],'stores':manifest['stores']});pair=e.root/(generation+'-pair.json');save(pair,successor);e.g.seal_manifest(pair);state=dict(e.state,generation=generation,manifest_path=str(pair),manifest_sha256=digest(pair));save(e.authority,state)
  for k,v in {'DB_PATH':str(fresh['core']),'WT_OPS_DB_PATH':str(fresh['ops']),'FLEX_DB_PAIR_MANIFEST':str(pair)}.items():e.monkeypatch.setenv(k,v)
  assert load().validate_pair()==generation and e.certificate.read_bytes()==historical
  current=fresh;manifest=successor


def test_owner_refuses_to_certify_an_already_missing_original_file(case):
 e=case;e.old['core'].unlink()
 with pytest.raises(e.g.PairedBindingError,match='IDENTITY_MISMATCH'):e.g.publish_predecessor_retirement(e.pair,e.certificate,authorize=True)
 assert not e.certificate.exists()

def test_verified_partial_removal_uses_one_complete_certificate(case):
 e=case;certify(e);original=e.certificate.read_bytes()
 for role in ['core','ops']:
  e.old[role].unlink();assert e.g.validate_pair()=='v2' and e.certificate.read_bytes()==original
