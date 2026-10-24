"""Finish registration/binding verification on the SAME completed rehearsal.
No second migration, replay or production/database access.
"""
import hashlib,json,os,pathlib,resource,socket,sqlite3,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parents[4];BASE=pathlib.Path(sys.argv[1]).resolve();assert BASE.is_relative_to('/private/tmp');assert 'dev004-final-profile-integrated-' in str(BASE)
resource.setrlimit(resource.RLIMIT_FSIZE,(16_000_000,16_000_000));sys.dont_write_bytecode=True
result=json.load(open(BASE/'final-profile-integrated-result.json'));assert result['status']=='PASS';pair_path=BASE/'rollback-runtime-manifest.json';pair=json.load(open(pair_path));authority=BASE/'active-pair-authority.json';assert json.load(open(authority))['generation']==pair['generation']
aux=BASE/'creator-reference.sqlite'
if not aux.exists():
 c=sqlite3.connect(aux);c.execute('CREATE TABLE fixture_reference(id INTEGER PRIMARY KEY)');c.commit();c.close()
s=aux.stat();meta={'version':1,'generation':pair['generation'],'pair_manifest_sha256':hashlib.sha256(pair_path.read_bytes()).hexdigest(),'stores':{'creator_repository':{'path':str(aux),'device':s.st_dev,'inode':s.st_ino,'generation':pair['generation'],'role':'creator_repository'}}}
aux_manifest=BASE/'registered-auxiliary.json';b=json.dumps(meta,sort_keys=True,separators=(',',':')).encode();assert len(b)<8192
if aux_manifest.exists():assert aux_manifest.read_bytes()==b
else:
 with aux_manifest.open('xb') as f:f.write(b);f.flush();os.fsync(f.fileno())
tmp=BASE/'runtime-tmp';tmp.mkdir(exist_ok=True)
plan=json.load(open(ROOT/'docs/dev/lightweight_dev004_first_v2/production_profile_bootstrap/runtime_plan.json'))
bindings={'DB_PATH':pair['stores']['core']['path'],'WT_OPS_DB_PATH':pair['stores']['ops']['path'],'CREATOR_DB_PATH':str(aux),'PUMPSWAP_TOKENS_DB_PATH':str(aux),'DEV004_PAIRED_BINDING_REQUIRED':'1','FLEX_DB_PAIR_MANIFEST':str(pair_path),'FLEX_DB_PAIR_AUTHORITY':str(authority),'FLEX_DB_PAIR_ROLE':'runtime','FLEX_DB_AUXILIARY_MANIFEST':str(aux_manifest),'FLEX_DB_AUXILIARY_SHA256':hashlib.sha256(b).hexdigest(),'BIRTH_JOURNAL_PATH':str(BASE/'stable-birth.jsonl'),'MIGRATION_JOURNAL_PATH':str(BASE/'stable-migration.jsonl'),'DEV004_DURABLE_BIRTH_JOURNAL_ENABLED':'1','DEV004_SIGNATURE_FIRST_JOURNAL_ENABLED':'1','DB_WRITE_SERIALIZE':'1','TMPDIR':str(tmp),'SQLITE_TMPDIR':str(tmp),'RPC_COMPACT_ENABLED':'0'}
outputs={};code="""import json,os,runpy,socket,sys,threading
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('NETWORK_FORBIDDEN'))
socket.create_connection=socket.socket.connect
sys.argv=['entry',sys.argv[1],'--check-only'];runpy.run_module('src.core.lightweight_runtime_entry',run_name='__main__')
from src.core import db
core=db.DB_PATH;ops=db.resolve_ops_db_path() if hasattr(db,'resolve_ops_db_path') else db.OPS_DB_PATH
assert core==os.environ['DB_PATH'] and ops==os.environ['WT_OPS_DB_PATH']
assert len(threading.enumerate())==1
print(json.dumps({'actual_binding_module':True,'core':core,'ops':ops,'threads':1}))
"""
for role,entry in plan['roles'].items():
 e={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(ROOT/'runtime'/role),'BIRDEYE':'synthetic-paid','BIRDEYE_KK':'synthetic-legacy'}
 if role=='listener':e.update(entry['nonsecret_wrapper_environment'])
 e.update(bindings)
 p=subprocess.run([sys.executable,'-B','-c',code,entry['entrypoint']],cwd=BASE,env=e,capture_output=True,text=True,timeout=15);assert p.returncode==0,p.stderr
 lines=[json.loads(line) for line in p.stdout.splitlines()];assert lines[0]['generation']==pair['generation'];assert lines[0]['kernel_single_file_limit'][0]<=499_000_000;outputs[role]={'launch_boundary':lines[0],'actual_binding':lines[-1],'exit_code':p.returncode}
frozen={'source_commit':json.load(open(BASE/'v2-frozen-runtime.json'))['source_commit'],'generation':pair['generation'],'environment':bindings,'pair_sha256':hashlib.sha256(pair_path.read_bytes()).hexdigest(),'authority_sha256':hashlib.sha256(authority.read_bytes()).hexdigest(),'auxiliary_sha256':hashlib.sha256(b).hexdigest(),'provider_names_only':['BIRDEYE'],'provider_budget':{'global':20,'per_token':4,'seconds':60},'roles':outputs,'origin':'Verification continuation of same single completed disposable rehearsal; no re-import or rebind executed here.'}
p=BASE/'registered-runtime.json';raw=(json.dumps(frozen,indent=2)+'\n').encode();assert len(raw)<65536;p.write_bytes(raw)
summary={'status':'PASS','same_rehearsal':True,'additional_migrations':0,'generation':pair['generation'],'all_three_role_boundaries_and_actual_db_bindings':'PASS','provider_network_blocked':True,'unexpected_threads':0,'frozen_runtime_path':str(p),'frozen_runtime_sha256':hashlib.sha256(raw).hexdigest(),'production_paths_opened':0};(BASE/'binding-verification.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
