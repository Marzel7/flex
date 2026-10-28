import ast,configparser,hashlib,importlib.util,json,os,pathlib,resource,shutil,signal,sqlite3,subprocess,sys,time,uuid
S=pathlib.Path(os.environ['FLEX_V2_RUNTIME_STATE_ROOT']);CONF=pathlib.Path(os.environ['FLEX_V2_SUPERVISOR_CONFIG']);PY='/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python';CTL='/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/supervisorctl'
resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,499000000));signal.signal(signal.SIGXFSZ,signal.SIG_IGN);sys.dont_write_bytecode=True
P=json.load(open(S/'authority/frozen-runtime-plan.json'))
def qualified_source_root(plan):
 root=pathlib.Path(plan['immutable_source_root'])
 if not root.is_absolute() or str(root.resolve())!=str(root):raise RuntimeError('IMMUTABLE_SOURCE_ROOT_REQUIRED')
 expected=plan['immutable_source_commit']
 if not isinstance(expected,str) or len(expected)!=40:raise RuntimeError('IMMUTABLE_SOURCE_COMMIT_REQUIRED')
 actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
 if actual!=expected or subprocess.call(['git','-C',str(root),'diff','--quiet','HEAD']):raise RuntimeError('IMMUTABLE_SOURCE_MISMATCH')
 return root
R=qualified_source_root(P);sys.path.insert(0,str(R/'runtime/listener'))
from src.core import paired_binding_guard as g
from src.core.birth_admission_journal import BirthJournal
from src.core.migration_signature_journal import MigrationJournal

def sha(p):return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def atomic(p,data,mode=0o600):
 p=pathlib.Path(p);tmp=p.with_name(p.name+'.dev004-stage');old=p.stat() if p.exists() else None
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
 try:
  assert os.write(fd,data)==len(data);os.fsync(fd)
  if old:os.fchmod(fd,old.st_mode&0o7777);os.fchown(fd,old.st_uid,old.st_gid)
 finally:os.close(fd)
 os.replace(tmp,p);fd=os.open(p.parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)
def save(p,v):atomic(p,(json.dumps(v,sort_keys=True,indent=2)+'\n').encode())
def phase(name,**kw):save(S/'authority/transition-state.json',dict(phase=name,at_ns=time.time_ns(),**kw));print(json.dumps({'phase':name,**kw}),flush=True)
def ctl(*args):
 p=subprocess.run([CTL,'-c',str(CONF),*args],capture_output=True,text=True,timeout=60)
 if p.returncode and not (args[0]=='status' and p.returncode==3):raise RuntimeError('SUPERVISOR:'+p.stdout[-1000:]+p.stderr[-1000:])
 return p.stdout.strip()
def settings(mode):
 v1=mode=='v1';pair=S/'authority'/('v1-pair.json' if v1 else 'pair.json');aux=S/'authority'/('v1-auxiliary.json' if v1 else 'auxiliary.json');e=dict(P['planned_production_environment']);e.update(DB_PATH=json.load(open(pair))['stores']['core']['path'],WT_OPS_DB_PATH=json.load(open(pair))['stores']['ops']['path'],FLEX_DB_PAIR_MANIFEST=str(pair),FLEX_DB_AUXILIARY_MANIFEST=str(aux),FLEX_DB_AUXILIARY_SHA256=sha(aux),FLEX_DB_PAIR_ROLE='runtime',PYTHONDONTWRITEBYTECODE='1');return e

def bind(mode,transfer=False):
 e=settings(mode);os.environ.update({k:str(v) for k,v in e.items()});os.environ['FLEX_DB_PAIR_ROLE']='transfer' if transfer else 'runtime';os.environ['FLEX_DB_PAIR_OWNER']='DEV004-production-owner' if transfer else ''

def owner(mode,stage):
 pair=S/'authority'/('v1-pair.json' if mode=='v1' else 'pair.json');m=json.load(open(pair));save(S/'authority/owner.json',{'version':1,'authority_id':m['authority']['id'],'origin_generation':m['authority']['origin_generation'],'generation':m['generation'],'manifest_path':str(pair),'manifest_sha256':sha(pair),'phase':stage,'owner':'runtime' if stage=='ACTIVE' else 'DEV004-production-owner','transfer_complete':stage=='ACTIVE'})

# Qualified terminal-aware recovery contract; no schema bootstrap or replay here.
TERMINAL_FLAG = 'DEV004_MIGRATION_TERMINAL_EVIDENCE_ENABLED'
TERMINAL_PAYLOAD = {'pumpfun_curve_listener.py': '15e31d294371d18302c04209775c130237a9ba220080c17aec3bda281856750c', 'migration_signature_journal.py': 'cb78113715f1a32b62f43b490e0eee427a0030adfda6045e8c791565c9a5ecd9', 'migration_terminal_evidence.py': '98979578dbe718ed24f4b7cc016a27bb493938818d5d51a67e24cae872c63cb1'}
TERMINAL_SCHEMA_CONTRACT = 'd7fe62ecec89c170def5d6cc05e0d7dfc9df585973781266ba3edcaeffa9f588'
BIRTH_JOURNAL_SHA256 = '40953361669e7a04dafcabbab8ad769a309f1d3fa68e8696d9be1fb0138b4289'
MAX_RECOVERY_CONFIG_BYTES = 1_000_000

def terminal_environment(env):
 # Called only for the listener, after every legacy override has been merged.
 env=dict(env)
 if env.get(TERMINAL_FLAG) not in (None,'1'):
  raise RuntimeError('TERMINAL_FLAG_DOWNGRADE')
 env[TERMINAL_FLAG]='1'
 for key,name in [('MIGRATION_JOURNAL_PATH','migration.jsonl'),('BIRTH_JOURNAL_PATH','birth.jsonl')]:
  if env.get(key)!=str(S/'journals'/name):raise RuntimeError('TERMINAL_STABLE_JOURNAL_REQUIRED')
 for key in ['DB_PATH','WT_OPS_DB_PATH','FLEX_DB_PAIR_MANIFEST','FLEX_DB_PAIR_AUTHORITY']:
  if not env.get(key) or os.path.realpath(env[key])!=env[key] or os.environ.get(key)!=env[key]:
   raise RuntimeError('TERMINAL_RECOVERY_BINDING_MISMATCH')
 if env.get('DEV004_PAIRED_BINDING_REQUIRED')!='1' or env.get('DEV004_SIGNATURE_FIRST_JOURNAL_ENABLED')!='1':
  raise RuntimeError('TERMINAL_PAIRED_JOURNAL_REQUIRED')
 package=pathlib.Path(P['roles']['listener']['package'])
 modules={}
 for name,expected in TERMINAL_PAYLOAD.items():
  path=package/'src/core'/name
  if not path.is_file() or path.is_symlink() or str(path.resolve())!=str(path) or sha(path)!=expected:
   raise RuntimeError('TERMINAL_UNAWARE_SOURCE_FALLBACK:'+name)
  if name!='pumpfun_curve_listener.py':
   spec=importlib.util.spec_from_file_location('terminal_recovery_'+name[:-3],path)
   module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);modules[name]=module
 terminal=modules['migration_terminal_evidence.py']
 if terminal.CONTRACT!=TERMINAL_SCHEMA_CONTRACT:raise RuntimeError('TERMINAL_SCHEMA_CONTRACT')
 generation=g.validate_pair(core_path=env['DB_PATH'],ops_path=env['WT_OPS_DB_PATH'],purpose='active')
 if not generation:raise RuntimeError('TERMINAL_GENERATION_REQUIRED')
 def validate_target(ops_path):
  if str(ops_path)!=env['WT_OPS_DB_PATH']:raise RuntimeError('TERMINAL_OPS_BINDING_MISMATCH')
  return g.validate_pair(core_path=env['DB_PATH'],ops_path=str(ops_path),purpose='active')
 journal=modules['migration_signature_journal.py'].MigrationJournal(env['MIGRATION_JOURNAL_PATH'])
 owner=terminal.TerminalEvidenceStore(env['WT_OPS_DB_PATH'],env['MIGRATION_JOURNAL_PATH'],generation=generation,validate_record=journal._validate,validate_pair=validate_target)
 # Indexed no-match lookup exercises exact owner version/schema and pair guards.
 # Existing rows, original transfer generations and quota counters stay untouched.
 owner.lookup('__TERMINAL_RECOVERY_SCHEMA_PROBE__')
 birth_flag='DEV004_BIRTH_TERMINAL_EVIDENCE_ENABLED'
 planned=P['planned_production_environment'].get(birth_flag,'0')
 if planned not in ('0','1') or env.get(birth_flag,'0')!=planned:raise RuntimeError('BIRTH_TERMINAL_FLAG_DOWNGRADE')
 if planned=='1':
  birth_path=package/'src/core/birth_admission_journal.py'
  if sha(birth_path)!=BIRTH_JOURNAL_SHA256:raise RuntimeError('BIRTH_TERMINAL_UNAWARE_SOURCE')
  spec=importlib.util.spec_from_file_location('birth_terminal_recovery_journal',birth_path)
  birth_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(birth_module)
  birth_journal=birth_module.BirthJournal(env['BIRTH_JOURNAL_PATH'])
  birth_owner=terminal.BirthTerminalEvidenceStore(env['WT_OPS_DB_PATH'],env['BIRTH_JOURNAL_PATH'],
   generation=generation,validate_record=birth_journal._validate,validate_pair=validate_target)
  birth_owner.lookup('__BIRTH_TERMINAL_SCHEMA_PROBE__')
  env[birth_flag]='1'
 else:
  with sqlite3.connect(pathlib.Path(env['WT_OPS_DB_PATH']).as_uri()+'?mode=ro',uri=True) as connection:
   if connection.execute("SELECT 1 FROM sqlite_master WHERE name='birth_terminal_owner_v1'").fetchone():raise RuntimeError('BIRTH_TERMINAL_FLAG_DOWNGRADE')
 deferred_flag='DEV004_MIGRATION_DEFERRED_EVIDENCE_ENABLED'
 planned=P['planned_production_environment'].get(deferred_flag,'0')
 if planned not in ('0','1') or env.get(deferred_flag,'0')!=planned:raise RuntimeError('DEFERRED_FLAG_DOWNGRADE')
 if planned=='1':
  deferred=modules['migration_terminal_evidence.py'].DeferredMigrationStore(
   env['WT_OPS_DB_PATH'],env['MIGRATION_JOURNAL_PATH'],generation=generation,
   validate_record=journal._validate,validate_pair=validate_target)
  deferred.lookup('__DEFERRED_RECOVERY_SCHEMA_CHECK__')
  env[deferred_flag]='1'
 else:
  with sqlite3.connect(pathlib.Path(env['WT_OPS_DB_PATH']).as_uri()+'?mode=ro',uri=True) as d:
   if d.execute("SELECT 1 FROM sqlite_master WHERE name='migration_deferred_owner_v1'").fetchone():raise RuntimeError('DEFERRED_FLAG_DOWNGRADE')
 return env

CREATE_AUDIT_FLAG = "FLEX_V2_CREATE_AUDIT_ENABLED"
CREATE_AUDIT_PAYLOAD = {'listener': {'src/ops/create_coverage.py': '220b730441f66f19922359e15335ffdea2bc5b68c8c19047978398ea871a10c0', 'src/ops/walkback_create_handoff.py': 'd88130291188c6bdfd72ad3366233dd8f52d49a8411e8eb197cca211d584afd1', 'src/core/walkback_queue.py': '3ee78cfe768ed94b9d9cac9d5e77f8f1b83a74a5afa4d1b422b26bad7167f8b7'}, 'creator_resolution': {'src/ops/create_coverage.py': '220b730441f66f19922359e15335ffdea2bc5b68c8c19047978398ea871a10c0', 'src/ops/walkback_create_handoff.py': 'd88130291188c6bdfd72ad3366233dd8f52d49a8411e8eb197cca211d584afd1', 'src/core/walkback_queue.py': '3ee78cfe768ed94b9d9cac9d5e77f8f1b83a74a5afa4d1b422b26bad7167f8b7'}, 'walkback': {'src/ops/create_coverage.py': '220b730441f66f19922359e15335ffdea2bc5b68c8c19047978398ea871a10c0', 'src/ops/walkback_create_handoff.py': 'd88130291188c6bdfd72ad3366233dd8f52d49a8411e8eb197cca211d584afd1', 'src/core/walkback_queue.py': '062c0ecc57d5b82579b24984f65b8426759c87a90c3706bbc4c9a37787d8f5b6'}}

def coverage_environment(role,env):
 env=dict(env)
 planned=P['planned_production_environment'].get(CREATE_AUDIT_FLAG,'0')
 if planned not in ('0','1') or env.get(CREATE_AUDIT_FLAG,'0')!=planned:
  raise RuntimeError('CREATE_AUDIT_FLAG_DOWNGRADE')
 path=env.get('WT_OPS_DB_PATH')
 if not path or not os.path.isabs(path) or os.path.realpath(path)!=path:raise RuntimeError('CREATE_AUDIT_OPS_REQUIRED')
 if planned=='0':
  with sqlite3.connect(pathlib.Path(path).as_uri()+'?mode=ro',uri=True) as conn:
   if conn.execute("SELECT 1 FROM sqlite_master WHERE name='create_coverage_owner_v1'").fetchone():raise RuntimeError('CREATE_AUDIT_FLAG_DOWNGRADE')
  return env
 for name in ['DB_PATH','WT_OPS_DB_PATH','FLEX_DB_PAIR_MANIFEST','FLEX_DB_PAIR_AUTHORITY']:
  if not env.get(name) or env[name]!=os.environ.get(name):raise RuntimeError('CREATE_AUDIT_RECOVERY_BINDING')
 if g.validate_pair(core_path=env['DB_PATH'],ops_path=env['WT_OPS_DB_PATH'],purpose='active') is None:raise RuntimeError('CREATE_AUDIT_GENERATION_REQUIRED')
 package=pathlib.Path(P['roles'][role]['package'])
 for relative,expected in CREATE_AUDIT_PAYLOAD[role].items():
  f=package/relative
  if f.is_symlink() or str(f.resolve())!=str(f) or sha(f)!=expected:raise RuntimeError('CREATE_AUDIT_UNAWARE_FALLBACK')
 spec=importlib.util.spec_from_file_location('recovery_create_coverage',package/'src/ops/create_coverage.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 with sqlite3.connect(pathlib.Path(path).as_uri()+'?mode=ro',uri=True) as conn:module.validate_schema(conn)
 with sqlite3.connect(pathlib.Path(env['DB_PATH']).as_uri()+'?mode=ro',uri=True) as conn:
  row=conn.execute('SELECT sql FROM sqlite_master WHERE name=?',(module.INDEX,)).fetchone()
  if not row or module.norm(row[0])!=module.norm(module.INDEX_SQL):raise RuntimeError('CREATE_AUDIT_INDEX_REQUIRED')
 env[CREATE_AUDIT_FLAG]='1'
 return env

HELIUS_GATE_FLAG='FLEX_HELIUS_DENIAL_GATE_ENABLED'
HELIUS_GATE_PAYLOAD = {'listener': {'src/analysis/pump_fun_post_migration_analyzer.py': 'efd377e4c6bea7a66d6cc19b308e348c6513a370eae03378c7861283f5171639', 'src/core/helius_denial_gate.py': '623b81d79f1160a1b1e0b52b7c3904a1489416e8617658e3cff08ad0c30bd15f', 'src/core/pool_discovery.py': '24c0a8fd245046fb096570fdc49fec65f3d356c5d275a28948431c0e604d3b1b', 'src/core/post_migration_pool_discovery.py': 'ce47fbf356b0c6bee94ddc92b2b664943fb09e4e47ad8a14d141551832225a8f', 'src/core/pumpfun_curve_listener.py': '15e31d294371d18302c04209775c130237a9ba220080c17aec3bda281856750c', 'src/core/helius_http_protection.py': '5363aabb0100dda5cb360996749b103fc528179a3d7cf3e9d0d13f4755b6db1e', 'src/core/birth_admission_journal.py': '40953361669e7a04dafcabbab8ad769a309f1d3fa68e8696d9be1fb0138b4289', 'src/core/migration_terminal_evidence.py': '98979578dbe718ed24f4b7cc016a27bb493938818d5d51a67e24cae872c63cb1'}, 'creator_resolution': {'src/analysis/pump_fun_post_migration_analyzer.py': '47b613c417f4def1e9966c37a21e94458eedff8d94bac964c8fb16033941f4b4', 'src/core/creator_resolution_queue.py': '681c7f7ce3f9515badebc4bdfa7b5fd6a6e5eca0284c155505aa2204f4587a97', 'src/core/helius_denial_gate.py': '623b81d79f1160a1b1e0b52b7c3904a1489416e8617658e3cff08ad0c30bd15f', 'src/core/helius_http_protection.py': '5363aabb0100dda5cb360996749b103fc528179a3d7cf3e9d0d13f4755b6db1e'}, 'walkback': {'src/core/helius_denial_gate.py': '623b81d79f1160a1b1e0b52b7c3904a1489416e8617658e3cff08ad0c30bd15f', 'src/core/walkback_worker.py': '75a069a5c1ff0859550b929a2f1f2ce2a719dd109bb5ffbb5dcead25e5736414', 'src/core/helius_http_protection.py': '5363aabb0100dda5cb360996749b103fc528179a3d7cf3e9d0d13f4755b6db1e', 'src/ops/anchor_reconciliation.py': '7e2cbf52b4c28a884e02728bc036559edc3fb627476b3b61a122e3be0ea33404'}}
MAX_HELIUS_RECOVERY_BYTES=67108864

def helius_module(role):
 package=pathlib.Path(P['roles'][role]['package'])
 for relative,expected in HELIUS_GATE_PAYLOAD[role].items():
  path=package/relative
  if not path.is_file() or path.is_symlink() or str(path.resolve())!=str(path) or sha(path)!=expected:raise RuntimeError('HELIUS_GATE_UNAWARE_FALLBACK:'+role+':'+relative)
 spec=importlib.util.spec_from_file_location('helius_recovery_'+role,package/'src/core/helius_denial_gate.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 return module

def helius_environment(role,env):
 env=dict(env);planned=P['planned_production_environment'].get(HELIUS_GATE_FLAG,'0')
 stable=str(S/'journals/helius-denial-v1.json')
 if planned not in ('0','1') or env.get(HELIUS_GATE_FLAG,'0')!=planned:raise RuntimeError('HELIUS_GATE_FLAG_DOWNGRADE')
 if planned=='0':
  if env.get('HELIUS_DENIAL_GATE_PATH') or pathlib.Path(stable).exists():raise RuntimeError('HELIUS_GATE_FLAG_DOWNGRADE')
  return env
 if env.get('HELIUS_DENIAL_GATE_PATH')!=stable or P['planned_production_environment'].get('HELIUS_DENIAL_GATE_PATH')!=stable or env.get('MIGRATION_JOURNAL_PATH')!=str(S/'journals/migration.jsonl'):raise RuntimeError('HELIUS_GATE_STABLE_PATH_REQUIRED')
 if os.path.realpath(stable)!=stable:raise RuntimeError('HELIUS_GATE_PHYSICAL_PATH_REQUIRED')
 for key in ('DB_PATH','WT_OPS_DB_PATH','FLEX_DB_PAIR_MANIFEST','FLEX_DB_PAIR_AUTHORITY'):
  if not env.get(key) or env[key]!=os.environ.get(key):raise RuntimeError('HELIUS_GATE_BINDING_MISMATCH')
 if env.get('DEV004_PAIRED_BINDING_REQUIRED')!='1':raise RuntimeError('HELIUS_GATE_PAIR_REQUIRED')
 generation=g.validate_pair(core_path=env['DB_PATH'],ops_path=env['WT_OPS_DB_PATH'],purpose='active')
 if not generation:raise RuntimeError('HELIUS_GATE_GENERATION_REQUIRED')
 module=helius_module(role);gate=module.Gate(stable,generation)
 with gate.locked():gate.read()  # Never initializes, restores or grants a probe.
 env[HELIUS_GATE_FLAG]='1';env['HELIUS_DENIAL_GATE_PATH']=stable
 return env

def prepare_helius_recovery(env,destination):
 # Before fencing, validate exact installed source, complete source pair and
 # frozen matched destination. No state/configuration mutation here.
 for role in ('listener','creator_resolution','walkback'):helius_environment(role,env)
 if env.get(HELIUS_GATE_FLAG,'0')!='1':return None
 source=json.load(open(env['FLEX_DB_PAIR_MANIFEST']));target=json.load(open(destination))
 if source['authority']!=target['authority'] or source['generation']==target['generation']:raise RuntimeError('HELIUS_RECOVERY_AUTHORITY')
 if set(target['stores'])!={'core','ops'}:raise RuntimeError('HELIUS_RECOVERY_PAIR')
 for role in ('core','ops'):
  if target['stores'][role]['generation']!=target['generation']:raise RuntimeError('HELIUS_RECOVERY_PAIR')
 return {'version':1,'path':env['HELIUS_DENIAL_GATE_PATH'],'source_generation':source['generation'],'authority':source['authority'],'destination_manifest':str(destination),'destination_sha256':sha(destination),'destination_generation':target['generation']}

def freeze_helius_recovery(prepared):
 # Caller has stopped all affected processes after the writer fence. Capture
 # the latest state, not an earlier in-flight snapshot. One bounded receipt.
 if prepared is None:return
 module=helius_module('listener');gate=module.Gate(prepared['path'],prepared['source_generation'])
 with gate.locked():state=gate.read()
 snapshot=dict(prepared,state=state)
 if len(json.dumps(snapshot).encode())>MAX_HELIUS_RECOVERY_BYTES:raise RuntimeError('HELIUS_RECOVERY_CAPACITY')
 if shutil.disk_usage(S).free<2067108864:raise RuntimeError('DISK_FLOOR')
 save(S/'authority/helius-denial-recovery.json',snapshot)

def finish_helius_recovery(env):
 if env.get(HELIUS_GATE_FLAG,'0')!='1':
  helius_environment('listener',env);return
 path=S/'authority/helius-denial-recovery.json'
 if not path.is_file() or path.is_symlink() or path.stat().st_size>MAX_HELIUS_RECOVERY_BYTES:raise RuntimeError('HELIUS_RECOVERY_RECEIPT_REQUIRED')
 snapshot=json.load(open(path));destination=pathlib.Path(env['FLEX_DB_PAIR_MANIFEST'])
 generation=g.validate_pair(core_path=env['DB_PATH'],ops_path=env['WT_OPS_DB_PATH'],purpose='active')
 target=json.load(open(destination))
 if snapshot['version']!=1 or str(destination)!=snapshot['destination_manifest'] or sha(destination)!=snapshot['destination_sha256'] or generation!=snapshot['destination_generation'] or target['authority']!=snapshot['authority']:raise RuntimeError('HELIUS_RECOVERY_AUTHORITY')
 if env['HELIUS_DENIAL_GATE_PATH']!=str(S/'journals/helius-denial-v1.json') or env['HELIUS_DENIAL_GATE_PATH']!=snapshot['path']:raise RuntimeError('HELIUS_RECOVERY_PATH')
 for role in ('listener','creator_resolution','walkback'):helius_module(role)
 module=helius_module('listener');old=module.Gate(snapshot['path'],snapshot['source_generation']);new=module.Gate(snapshot['path'],generation)
 expected=dict(snapshot['state']);expected['generation']=generation
 with old.locked():
  try:current=old.read()
  except module.Integrity:
   # A crash after publication but before receipt acknowledgement is idempotent.
   current=new.read()
   if current!=expected:raise RuntimeError('HELIUS_RECOVERY_STATE_CONFLICT')
  else:
   if current!=snapshot['state']:raise RuntimeError('HELIUS_RECOVERY_STATE_CHANGED')
   old.save(expected)  # Generation-only move; no allowance/counter/state reset.
 with new.locked():
  if new.read()!=expected:raise RuntimeError('HELIUS_RECOVERY_READBACK')
 for role in ('listener','creator_resolution','walkback'):helius_environment(role,env)
 snapshot['completed_generation']=generation
 save(path,snapshot)

def recovery_config_base():
 if P['planned_production_environment'].get(HELIUS_GATE_FLAG,'0')!='1':return S/'source-preimages/supervisor.conf'
 path=P.get('recovery_supervisor_preimage_path');expected=P.get('recovery_supervisor_preimage_sha256')
 if not path or not expected:raise RuntimeError('HELIUS_RECOVERY_CONFIG_AUTHORITY_REQUIRED')
 path=pathlib.Path(path)
 if not path.is_absolute() or path.is_symlink() or str(path.resolve())!=str(path) or not path.is_file() or sha(path)!=expected:raise RuntimeError('HELIUS_RECOVERY_CONFIG_PREIMAGE')
 return path

def write_config(mode):
 if mode!='v2':raise RuntimeError('TERMINAL_RECOVERY_V2_ONLY')
 from supervisor.datatypes import dict_of_key_value_pairs
 current_cfg=configparser.ConfigParser(interpolation=None);current_cfg.read(CONF);helius_transition=False
 cfg=configparser.ConfigParser(interpolation=None);cfg.read(recovery_config_base());before={k:dict(cfg[k]) for k in cfg.sections()};mapping={'watchtower_listener':'listener','creator_resolution_worker':'creator_resolution','walkback_worker':'walkback'}
 for service,role in mapping.items():
  section=cfg['program:'+service];env=dict_of_key_value_pairs(section.get('environment',''));env.update(settings(mode));env.update(P['roles'][role].get('runtime_environment_overrides',{}));env['PYTHONPATH']=P['roles'][role]['package']
  if role=='listener':
   env.update(P['roles'][role]['nonsecret_wrapper_environment'])
   env=terminal_environment(env)
  else:
   # Retryable custody is listener-owned; do not change other worker bindings.
   env.pop('DEV004_MIGRATION_DEFERRED_EVIDENCE_ENABLED',None)
  env=coverage_environment(role,env)
  env=helius_environment(role,env)
  if env.get(HELIUS_GATE_FLAG)=='1':
   previous=dict_of_key_value_pairs(current_cfg.get('program:'+service,'environment',fallback=''))
   if any(previous.get(key)!=env.get(key) for key in (HELIUS_GATE_FLAG,'HELIUS_DENIAL_GATE_PATH','DB_PATH','WT_OPS_DB_PATH','FLEX_DB_PAIR_MANIFEST')):helius_transition=True
  env['RPC_METRICS_DB']=env['DB_PATH']  # Preserve accepted V2 telemetry authority after role overrides.
  section['environment']=','.join(k+'="'+str(v).replace('\\','\\\\').replace('"','\\"')+'"' for k,v in env.items())
  if mode=='v2':section['command']=' '.join(P['roles'][role]['command'])
 for key in cfg.sections():
  if key not in {'program:'+x for x in mapping}:assert dict(cfg[key])==before[key],'UNRELATED_SUPERVISOR_CHANGE'
 for key in set(cfg.sections())|set(current_cfg.sections()):
  if key not in {'program:'+x for x in mapping}:
   if not cfg.has_section(key) or not current_cfg.has_section(key) or dict(cfg[key])!=dict(current_cfg[key]):raise RuntimeError('UNRELATED_SUPERVISOR_DRIFT')
 import io
 out=io.StringIO();cfg.write(out);payload=out.getvalue().encode()
 if len(payload)>MAX_RECOVERY_CONFIG_BYTES:raise RuntimeError('RECOVERY_CONFIG_CAPACITY')
 if helius_transition:
  for service in mapping:
   if 'STOPPED' not in ctl('status',service):raise RuntimeError('HELIUS_ACTIVATION_REQUIRES_QUIESCENCE:'+service)
 atomic(CONF,payload)
 with open(CONF,'rb') as published:
  if published.read(MAX_RECOVERY_CONFIG_BYTES+1)!=payload:raise RuntimeError('RECOVERY_CONFIG_READBACK')
 result=ctl('reread');print('SUPERVISOR_REREAD '+result,flush=True)
 for line in result.splitlines():
  if ':' in line:assert line.split(':',1)[0] in mapping,'UNEXPECTED_CHANGED_SERVICE'
 print(ctl('update',*mapping),flush=True)
 for name in mapping:
  if 'RUNNING' not in ctl('status',name):print(ctl('start',name),flush=True)
 save(S/'authority'/('supervisor-'+mode+'-identity.json'),{'configuration_sha256':sha(CONF),'services':ctl('status',*mapping)})

def install_v1():
 assert not (S/'authority/transition-state.json').exists()
 for rel,x in P['deployment_payload'].items():
  p=pathlib.Path(x['target']);assert (sha(p) if p.exists() else None)==x['preimage_sha256'];assert sha(S/'source-payload'/x['staged_name'])==x['sha256']
 assert sha(CONF)==P['original_supervisor_sha256'];assert shutil.disk_usage(S).free>2_000_000_000+256*1024*1024
 print(ctl('stop','watchtower_listener','creator_resolution_worker','walkback_worker'),flush=True);phase('OLD_SERVICES_STOPPED')
 pair=json.load(open(S/'authority/v1-pair.json'))
 holders=subprocess.run(['/usr/sbin/lsof','-nP',*[v['path'] for v in pair['stores'].values()]],capture_output=True,text=True)
 assert not holders.stdout.strip(),'UNEXPECTED_V1_CONNECTION_OWNER'
 for rel,x in P['deployment_payload'].items():
  p=pathlib.Path(x['target']);p.parent.mkdir(parents=True,exist_ok=True);atomic(p,(S/'source-payload'/x['staged_name']).read_bytes(),0o644);assert sha(p)==x['sha256']
 g.seal_manifest(str(S/'authority/v1-pair.json'));owner('v1','ACTIVE');phase('QUALIFIED_SOURCE_INSTALLED_V1_MATCHED')
 write_config('v1');phase('V1_DURABLE_JOURNAL_RUNTIME',services=ctl('status','watchtower_listener','creator_resolution_worker','walkback_worker'))

def helpers():
 scope={'os':os,'DB_PATH':settings('v1')['DB_PATH'],'_listener_ops_db_path':lambda:settings('v1')['WT_OPS_DB_PATH'],'_validate_active_pair':lambda:g.validate_pair(purpose='active')}
 tree=ast.parse((R/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text());names={'_birth_journal_canonical','_birth_journal_readback','_migration_journal_readback'}
 for n in tree.body:
  if isinstance(n,ast.FunctionDef) and n.name in names:exec(compile(ast.Module(body=[n],type_ignores=[]),'qualified-readback','exec'),scope)
 return scope

def hold_lanes(paths,publish_fence):
    # One existing owner per process; never bypass the nested-write prohibition.
    children=[]
    code="""import os,sys,uuid
sys.path.insert(0,sys.argv[1])
from src.core import database_write_service as w
lease=w.acquire_write_lease(sys.argv[2],sys.argv[3],str(uuid.uuid4()),'DEV004-authority-fence',timeout=15,_retirement_metadata_only=True)
try:
 print('READY',flush=True);sys.stdin.buffer.read(1)
finally:w.release_write_lease(lease)
"""
    try:
        for role,path in paths:
            child=subprocess.Popen([PY,'-B','-c',code,str(R/'runtime/listener'),role,path],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            children.append(child)
            ready=child.stdout.readline().strip()
            if ready!='READY':raise RuntimeError('LEASE_OWNER_NOT_READY:'+role+':'+child.stderr.read()[-500:])
        publish_fence()
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.stdin.write('x');child.stdin.flush();child.stdin.close()
            child.wait(timeout=20)
            if child.returncode:raise RuntimeError('LEASE_OWNER_EXIT:'+child.stderr.read()[-500:])

def fence():
    bind('v1');assert g.validate_pair()==P['source_generation']
    for name in ['creator_resolution_worker','walkback_worker']:
        if 'RUNNING' in ctl('status',name):print(ctl('stop',name),flush=True)
    h=helpers();witness={};prior=S/'checkpoints/pre-fence-admission-witness.json'
    if prior.exists():witness=json.load(open(prior))
    for kind,cls,verify in [('birth',BirthJournal,h['_birth_journal_readback']),('migration',MigrationJournal,h['_migration_journal_readback'])]:
        j=cls(S/'journals'/(kind+'.jsonl'),verify_completed=verify);rows=j.snapshot()
        seen={r['id']:r for r in witness.get(kind,[])}
        seen.update({r['id']:{'id':r['id'],'signature':r['signature'],'mint':r['mint'],'state':r['state'],'ack':r.get('ack')} for r in rows});witness[kind]=list(seen.values())
        save(prior,witness)
        while j.reclaim_completed():pass
        assert len(j.snapshot())<448,'ADMISSION_PRESSURE'
    hold_lanes([('core',settings('v1')['DB_PATH']),('ops',settings('v1')['WT_OPS_DB_PATH'])],lambda:owner('v1','TRANSFER'))
    bind('v1',True);assert g.validate_pair(write_path=settings('v1')['DB_PATH'],purpose='write')==P['source_generation'];phase('V1_WRITES_FENCED_NOT_RETIRED',journal_counts={k:len(cls(S/'journals'/(k+'.jsonl')).snapshot()) for k,cls in [('birth',BirthJournal),('migration',MigrationJournal)]})

if __name__=='__main__':{'install_v1':install_v1,'fence':fence}[sys.argv[1]]()
