"""Explicit launcher for the retained dormant API, Monitor and bridge.

No implicit databases, no provider acquisition in qualification, no service
registration. Normal launch requires separate operational authorization.
"""
from __future__ import annotations
import argparse,hashlib,importlib,json,os,pathlib,resource,runpy,signal,subprocess,sys

ROOT=pathlib.Path(__file__).resolve().parents[1]
MAX_MANIFEST=131072
MAX_FILE=499000000
PACKAGES={'api':'.','monitor':'integration_sources/monitor_worker','bridge':'integration_sources/monitor_bridge'}
MODULES={'api':'src.core.main','monitor':'src.ops.operation_monitor_service','bridge':'src.ops.operation_monitor_bridge_service'}
REQUIRED=('DB_PATH','WT_OPS_DB_PATH','FLEX_DB_PAIR_MANIFEST','FLEX_DB_PAIR_AUTHORITY','TMPDIR','SQLITE_TMPDIR','RPC_METRICS_DB')

class ConfigurationError(RuntimeError):pass

def physical(value):
 if not isinstance(value,str) or not os.path.isabs(value) or str(pathlib.Path(value).resolve())!=value:raise ConfigurationError('EXPLICIT_PHYSICAL_PATH_REQUIRED')
 return pathlib.Path(value)

def configure(manifest_path,digest,component,*,qualify=False):
 if component not in PACKAGES:raise ConfigurationError('COMPONENT_REQUIRED')
 path=physical(str(manifest_path))
 with path.open('rb') as f:raw=f.read(MAX_MANIFEST+1)
 if len(raw)>MAX_MANIFEST or hashlib.sha256(raw).hexdigest()!=digest:raise ConfigurationError('MANIFEST_AUTHORITY')
 m=json.loads(raw)
 if set(m)!={'version','source_commit','generation','environment','queue_path','budget_path','budget_contract_sha256','selection_path','selection_sha256','credential_file'} or m['version']!=1:raise ConfigurationError('MANIFEST_SCHEMA')
 if not isinstance(m['source_commit'],str) or len(m['source_commit'])!=40:raise ConfigurationError('SOURCE_COMMIT_REQUIRED')
 actual=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
 if actual!=m['source_commit']:raise ConfigurationError('SOURCE_COMMIT_MISMATCH')
 if not qualify and subprocess.call(['git','-C',str(ROOT),'diff','--quiet','HEAD']):raise ConfigurationError('SOURCE_DIRTY')
 if not qualify:
  for flags in [[],['--ignored']]:
   unknown=subprocess.check_output(['git','-C',str(ROOT),'ls-files','--others','--exclude-standard',*flags,'--','src','integration_sources','scripts','templates','static'],text=True).splitlines()
   if any(path.endswith(('.py','.pyc','.pyo','.html','.js','.css')) for path in unknown):raise ConfigurationError('UNTRACKED_SOURCE')
 package=physical(str(ROOT/PACKAGES[component]))
 if not package.is_dir() or not (package/'src').is_dir():raise ConfigurationError('PACKAGE_MISSING')
 env=m['environment']
 if not isinstance(env,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in env.items()):raise ConfigurationError('ENVIRONMENT_SCHEMA')
 allowed=set(REQUIRED)|{'FLEX_DB_PREDECESSOR_RETIREMENT_MANIFEST','FLEX_DB_PREDECESSOR_RETIREMENT_SHA256','DEV004_PAIRED_BINDING_REQUIRED','FLEX_DB_PAIR_ROLE','CREATOR_DB_PATH','FLEX_DB_AUXILIARY_MANIFEST','FLEX_DB_AUXILIARY_SHA256','DEV004_MIGRATION_TERMINAL_EVIDENCE_ENABLED','DEV004_SIGNATURE_FIRST_JOURNAL_ENABLED','BIRTH_JOURNAL_PATH','MIGRATION_JOURNAL_PATH','SQLITE_LIFECYCLE_LOG','DB_CONNECTION_LIFECYCLE_LOG','DB_WRITE_DIAGNOSTICS_PATH','WS_SNAPSHOT_LOG_PATH','WS_PRICE_TRACE_LOG_PATH','LAUNCH_PRICE_LOG_PATH','MONITOR_BRIDGE_HEALTH_PATH','MONITOR_BRIDGE_CADENCE_SECONDS','OPERATION_MONITOR_IDLE_SECONDS','OPERATION_MONITOR_FAIR_SCHEDULER','MONITOR_CLAIM_AUTHORITY_DB_PATH'}
 if set(env)-allowed:raise ConfigurationError('UNQUALIFIED_ENVIRONMENT_KEY')
 for key in REQUIRED:physical(env.get(key))
 if env.get('DEV004_PAIRED_BINDING_REQUIRED')!='1' or env.get('FLEX_DB_PAIR_ROLE')!='runtime':raise ConfigurationError('EXPLICIT_PAIR_REQUIRED')
 if env['RPC_METRICS_DB']!=env['DB_PATH']:raise ConfigurationError('TELEMETRY_BINDING_MISMATCH')
 if env['DB_PATH']==env['WT_OPS_DB_PATH']:raise ConfigurationError('DISTINCT_PAIR_REQUIRED')
 for database in [env['DB_PATH'],env['WT_OPS_DB_PATH']]:
  space=os.statvfs(physical(database).parent)
  if space.f_bavail*space.f_frsize<2_000_000_000+64*1024*1024:raise ConfigurationError('DORMANT_DISK_FLOOR')
 for key in ['DB_PATH','WT_OPS_DB_PATH','FLEX_DB_PAIR_MANIFEST','FLEX_DB_PAIR_AUTHORITY']:
  if not physical(env[key]).is_file():raise ConfigurationError('BOUND_AUTHORITY_MISSING')
 for key in ['TMPDIR','SQLITE_TMPDIR']:
  if not physical(env[key]).is_dir():raise ConfigurationError('TEMP_ROOT_MISSING')
 queue=physical(m['queue_path']);selection=physical(m['selection_path'])
 if not queue.is_dir() or not selection.is_file():raise ConfigurationError('MONITOR_RETAINED_STATE_MISSING')
 with selection.open('rb') as stream:selected=stream.read(65537)
 if len(selected)>65536 or hashlib.sha256(selected).hexdigest()!=m['selection_sha256']:raise ConfigurationError('SELECTION_AUTHORITY_MISMATCH')
 selection_value=json.loads(selected)
 if selection_value.get('mode')!='DEV_005_ISOLATED_SOAK' or not isinstance(selection_value.get('allowlist'),list):raise ConfigurationError('SELECTION_CONTRACT')
 if any(not isinstance(v,str) or not v for v in selection_value['allowlist']):raise ConfigurationError('SELECTION_CONTRACT')
 if m['credential_file'] is not None:
  credentials=physical(m['credential_file'])
  if not credentials.is_file() or credentials.stat().st_size>65536:raise ConfigurationError('CREDENTIAL_AUTHORITY_MISSING')
 elif not qualify and component in {'api','monitor'}:raise ConfigurationError('CREDENTIAL_AUTHORITY_REQUIRED')
 budget=physical(m['budget_path'])
 if budget!=queue/'provider_budget.json':raise ConfigurationError('BUDGET_QUEUE_AUTHORITY')
 if not budget.is_file():raise ConfigurationError('BUDGET_AUTHORITY_MISSING')
 # Read-only preparation authority; budget mutations remain with existing owner.
 budget_owner=package/'src/ops/dev_provider_budget.py'
 if hashlib.sha256(budget_owner.read_bytes()).hexdigest()!=m['budget_contract_sha256']:raise ConfigurationError('BUDGET_CONTRACT_MISMATCH')
 if not qualify and m['credential_file'] is not None:
  from dotenv import dotenv_values
  secret_values=dotenv_values(m['credential_file'],interpolate=False)
  for key in ['BIRDEYE','HELIUS_RPC_URL']:
   value=secret_values.get(key)
   if value:os.environ[key]=value
  if component in {'api','monitor'} and (not os.environ.get('BIRDEYE') or not os.environ.get('HELIUS_RPC_URL')):raise ConfigurationError('PROVIDER_CREDENTIAL_AUTHORITY_REQUIRED')
 for obsolete in ['BIRDEYE_KK','BIRDEYE_RILEY']:
  os.environ.pop(obsolete,None)
 os.environ.update(env)
 os.environ.update(PYTHONDONTWRITEBYTECODE='1',FLEX_V2_DORMANT_RUNTIME='1',FLEX_DORMANT_QUALIFICATION='1' if qualify else '0',FLEX_ENABLE_FLASK_BACKGROUND_WORKERS='0',FLEX_UI_RECOVERY_MODE='1',FLEX_DEV_READ_ONLY_QUALIFICATION='1' if qualify else '0',FLEX_WS_DISABLED='1',ENABLE_CREATE_INTERCEPTOR='false',BIRDEYE_CREDENTIAL_LABEL='BIRDEYE',MONITOR_RUNTIME='dev',MONITOR_PROVIDER_GLOBAL_LIMIT='20',MONITOR_PROVIDER_TOKEN_LIMIT='4',OPERATION_MONITOR_PRICE_MODE='HISTORICAL',OPERATION_MONITOR_QUEUE_PATH=str(queue),DEV_005_SOAK_SELECTION_PATH=str(selection),OPERATION_MONITOR_CANONICAL_MIGRATION_DB_PATH=env['DB_PATH'],OPERATION_MONITOR_CANONICAL_BIRTH_DB_PATH=env['WT_OPS_DB_PATH'],MONITOR_BRIDGE_SOURCE_DB=env['WT_OPS_DB_PATH'],MONITOR_BRIDGE_MONITOR_DB=env['WT_OPS_DB_PATH'],MONITOR_BRIDGE_QUEUE_PATH=str(queue),MONITOR_BRIDGE_SELECTION_PATH=str(selection),MONITOR_CLAIM_AUTHORITY_DB_PATH=env['WT_OPS_DB_PATH'],MISSION_CONTROL_MONITOR_UI_DB_PATH=env['WT_OPS_DB_PATH'],WATCHTOWER_MONITOR_QUEUE_PATH=str(queue),OPERATIONS_MODE='MONITOR' if component!='api' else 'OFF',MONITOR_PREFLIGHT_NO_TRANSPORT='1' if qualify else '0')
 limit=resource.getrlimit(resource.RLIMIT_FSIZE)[1];limit=MAX_FILE if limit==resource.RLIM_INFINITY else min(limit,MAX_FILE)
 signal.signal(signal.SIGXFSZ,signal.SIG_IGN);resource.setrlimit(resource.RLIMIT_FSIZE,(limit,limit));sys.dont_write_bytecode=True
 # A fresh process is mandatory; no cached package may redirect imports.
 if any(k=='src' or k.startswith('src.') for k in sys.modules):raise ConfigurationError('FRESH_PROCESS_REQUIRED')
 sys.path.insert(0,str(package))
 # Qualification never acquires a port, starts a service thread, or opens an
 # undeclared database. urllib3's optional IPv6 bind probe is disabled as well.
 if qualify:
  import socket,threading,sqlite3
  def deny(*args,**kwargs):raise ConfigurationError('QUALIFICATION_SIDE_EFFECT_FORBIDDEN')
  socket.has_ipv6=False
  socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.socket.bind=deny
  socket.create_connection=deny;threading.Thread.start=deny
  native=sqlite3.connect
  allowed_databases={env['DB_PATH'],env['WT_OPS_DB_PATH']}
  if env.get('CREATOR_DB_PATH'):allowed_databases.add(str(physical(env['CREATOR_DB_PATH'])))
  def bound_connect(database,*args,**kwargs):
   text=os.fsdecode(database) if isinstance(database,(str,bytes,os.PathLike)) else str(database)
   candidate=text.removeprefix('file:').split('?')[0]
   if text!=':memory:' and str(pathlib.Path(candidate).resolve()) not in allowed_databases:
    raise ConfigurationError('QUALIFICATION_DATABASE_NOT_AUTHORIZED')
   return native(database,*args,**kwargs)
  sqlite3.connect=bound_connect
 from src.core.paired_binding_guard import validate_pair
 generation=validate_pair(purpose='active')
 if generation!=m['generation']:raise ConfigurationError('GENERATION_MISMATCH')
 if env.get('FLEX_DB_PREDECESSOR_RETIREMENT_MANIFEST'):
  os.environ.update(FLEX_DB_PATH=env['DB_PATH'],OPS_V2_DB_PATH=env['WT_OPS_DB_PATH'])
 for bound in [env['DB_PATH'],env['WT_OPS_DB_PATH']]:validate_pair(write_path=bound,purpose='write')
 from src.ops.dev_provider_budget import DevProviderBudget
 DevProviderBudget(queue)._read()  # Validate retained allowance; never initialize/reset/debit.
 guard=queue/'provider_budget.lock'
 if guard.is_symlink():raise ConfigurationError('BUDGET_RECOVERY_REQUIRED')
 if guard.exists():
  with guard.open('rb') as stream:marker=stream.read(65)
  if marker not in (b'',b'READY_V1\n'):raise ConfigurationError('BUDGET_RECOVERY_REQUIRED')
 if (queue/'.provider_budget.json.tmp').exists() or (queue/'.provider_budget.json.tmp').is_symlink():raise ConfigurationError('BUDGET_RECOVERY_REQUIRED')
 os.environ['FLEX_DORMANT_EXPECTED_GENERATION']=m['generation']
 return m,package

def load_component(component):
 module=importlib.import_module(MODULES[component])
 expected=ROOT/PACKAGES[component]/(MODULES[component].replace('.','/')+'.py')
 if pathlib.Path(module.__file__).resolve()!=expected.resolve():raise ConfigurationError('MODULE_IDENTITY_MISMATCH')
 if component=='api':
  if module.app.config.get('DATABASE')!=os.environ['DB_PATH']:raise ConfigurationError('API_BINDING_MISMATCH')
  if not (ROOT/'templates').is_dir() or not (ROOT/'static').is_dir():raise ConfigurationError('API_ASSETS_MISSING')
 elif component=='monitor':
  module.validate_monitor_schema(os.environ['WT_OPS_DB_PATH'])
  from src.ops.dev004_checkpoint_completion import profile
  profile(os.environ['WT_OPS_DB_PATH'])
 else:
  module.config()
 from src.core.paired_binding_guard import validate_pair
 if validate_pair(purpose='active')!=os.environ['FLEX_DORMANT_EXPECTED_GENERATION']:raise ConfigurationError('GENERATION_CHANGED_DURING_IMPORT')
 return module

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--manifest',required=True);parser.add_argument('--sha256',required=True);parser.add_argument('--component',choices=PACKAGES,required=True);parser.add_argument('--qualify',action='store_true');parser.add_argument('--plan',action='store_true');a=parser.parse_args()
 if a.qualify and a.plan:parser.error('--qualify and --plan are mutually exclusive')
 manifest,package=configure(a.manifest,a.sha256,a.component,qualify=a.qualify)
 if a.plan:
  print(json.dumps({'status':'VALIDATED_NOT_ACTIVATED','component':a.component,'generation':manifest['generation']}));return
 if a.qualify:
  load_component(a.component);print(json.dumps({'status':'QUALIFIED_NOT_ACTIVATED','component':a.component,'generation':manifest['generation']}));return
 if a.component=='api':
  os.execv(sys.executable,[sys.executable,'-m','gunicorn','--chdir',str(package),'--config',str(ROOT/'config/gunicorn.conf.py'),'--access-logfile','-','--error-logfile','-','src.core.main:app'])
 load_component(a.component).main()
if __name__=='__main__':main()
