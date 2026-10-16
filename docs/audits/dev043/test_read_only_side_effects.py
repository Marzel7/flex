"""Compact contract tests of DEV043 opt-in read-only corrections."""
import ast,os,sys,pathlib,json,sqlite3,resource,logging,logging.handlers,importlib.util,types
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT));resource.setrlimit(resource.RLIMIT_FSIZE,(8388608,8388608))
checks=[]
# Load just the actual schema helper plus its actual validation/error definitions.
# No copies of business logic: compile AST from committed source under test.
tree=ast.parse((ROOT/'src/ops/treasury_review_workspace.py').read_text())
selected=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in ['WorkspaceError','_table_exists','prepare_review_schema']]
init=[];ns={'ensure_schema':lambda c:init.append('audit'),'treasury_bank':types.SimpleNamespace(_ensure_schema_once=lambda c:init.append('treasury'))}
exec(compile(ast.Module(body=selected,type_ignores=[]),'schema-source','exec'),ns)
c=sqlite3.connect(':memory:');statements=[];c.set_trace_callback(statements.append)
for t in ['wt_treasury_review','wt_treasury_review_actions','wt_confirmed_treasuries']:c.execute('CREATE TABLE '+t+'(id INTEGER)')
statements.clear();ns['prepare_review_schema'](c,read_only=True);assert not init and not any(x.upper().startswith(('CREATE','ALTER','INSERT','UPDATE','DELETE')) for x in statements);checks.append('existing schema no DDL')
c.execute('DROP TABLE wt_treasury_review_actions')
try:ns['prepare_review_schema'](c,read_only=True)
except ns['WorkspaceError'] as e:assert e.code=='READ_ONLY_SCHEMA_UNAVAILABLE' and e.status==503
else:raise AssertionError('missing schema hidden')
checks.append('missing schema explicit503');ns['prepare_review_schema'](c);assert init==['audit','treasury'];checks.append('normal writable bootstrap preserved')
# Execute exact existing extractor initialization block against a constructor spy.
extract=ast.parse((ROOT/'src/extractors/funder_incoming_extractor.py').read_text());block=next(n for n in extract.body if isinstance(n,ast.Try) and any(isinstance(x,ast.ImportFrom) and x.module=='src.core.rpc_cache' for x in n.body));calls=[]
fake=types.ModuleType('src.core.rpc_cache');fake.RPCCache=lambda db:calls.append(db) or object()
with patch.dict(sys.modules,{'src.core.rpc_cache':fake}):
 for read_only in [True,False]:
  with patch.dict(os.environ,{'FLEX_DEV_READ_ONLY_QUALIFICATION':'1' if read_only else '0'}):
   n={'os':os,'logger':logging.getLogger('fixture'),'DB_PATH':'fixture','RPC_CACHE':None};before=len(calls);exec(compile(ast.Module(body=[block],type_ignores=[]),'extractor-source','exec'),n)
   assert (n['RPC_CACHE'] is None)==read_only and len(calls)-before==(0 if read_only else 1)
checks+=['read-only cache None/no initialization','normal cache constructor unchanged']
# Import the real trace module; deny all actual file effects with spies.
log=logging.getLogger('ws_price_tracer');saved=log.handlers[:];log.handlers=[]
try:
 with patch.dict(os.environ,{'FLEX_DEV_READ_ONLY_QUALIFICATION':'1'}),patch('os.makedirs',side_effect=AssertionError('mkdir')),patch.object(logging.handlers,'RotatingFileHandler',side_effect=AssertionError('file handler')):
  spec=importlib.util.spec_from_file_location('fixture_trace',ROOT/'src/core/ws_price_tracer.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.trace('STAGE','mint');assert not log.handlers
 checks.append('read-only trace import/call zero file sink')
 with patch.dict(os.environ,{'FLEX_DEV_READ_ONLY_QUALIFICATION':'0'}),patch('os.makedirs') as mkdir,patch.object(logging.handlers,'RotatingFileHandler') as handler:
  h=logging.NullHandler();handler.return_value=h;spec.loader.exec_module(m);assert mkdir.call_count==1 and handler.call_count==1;assert handler.call_args.kwargs['maxBytes']==50*1024*1024 and handler.call_args.kwargs['backupCount']==2
 checks.append('normal trace rotation configuration unchanged')
finally:log.handlers=saved
# API missing-schema error is surfaced by the existing unavailable UI state.
ui=(ROOT/'templates/treasury_review.html').read_text();assert 'if(!d.ok)throw new Error' in ui and 'list.textContent=' in ui;checks.append('unavailable response displayed not empty success')
# The exact runtime-budget import block never imports a listener in read-only mode.
budget=ast.parse((ROOT/'src/core/operation_dashboard_routes.py').read_text())
fn=next(n for n in budget.body if isinstance(n,ast.FunctionDef) and n.name=='api_intel_runtime_budget')
block=next(n for n in fn.body if isinstance(n,ast.Try) and any(isinstance(x,ast.Import) and any(a.name=='importlib' for a in x.names) for x in n.body))
import threading
listener=types.SimpleNamespace(_BUDGET_COUNTERS_LOCK=threading.Lock(),_BUDGET_COUNTERS={'validation_timeout':7})
with patch.dict(sys.modules,{'src.core.pumpfun_curve_listener':None}):
 with patch.dict(os.environ,{'FLEX_DEV_READ_ONLY_QUALIFICATION':'1'}),patch('importlib.import_module',side_effect=AssertionError('listener import')):
  ns={'os':os,'runtime':{}};exec(compile(ast.Module(body=[block],type_ignores=[]),'budget-source','exec'),ns);assert ns['runtime']['listener_budget_counters_status']=='UNAVAILABLE' and ns['runtime']['validation_timeout'] is None
with patch.dict(os.environ,{'FLEX_DEV_READ_ONLY_QUALIFICATION':'0'}),patch('importlib.import_module',return_value=listener) as importer:
 ns={'os':os,'runtime':{}};exec(compile(ast.Module(body=[block],type_ignores=[]),'budget-source','exec'),ns);assert importer.call_count==1 and ns['runtime']['validation_timeout']==7
checks+=['read-only budget no listener import; explicit unavailable','normal listener budget import unchanged']
main=ast.parse((ROOT/'src/core/main.py').read_text())
branch=next(n for n in ast.walk(main) if isinstance(n,ast.If) and isinstance(n.test,ast.UnaryOp) and '_watchtower_alignment' in ast.unparse(n.test) and any(isinstance(x,ast.Raise) for x in ast.walk(n)))
for qualification in [True,False]:
 calls=[]
 def reconcile(db):calls.append(db);return {'audit':{'healthy':True}}
 with patch.dict(os.environ,{'FLEX_DEV_READ_ONLY_QUALIFICATION':'1' if qualification else '0'}):
  ns={'os':os,'_watchtower_alignment':{'audit':{'healthy':False}},'_ops_db_path':'fixture','initialize_and_reconcile':reconcile}
  try:exec(compile(ast.Module(body=[branch],type_ignores=[]),'startup-source','exec'),ns)
  except RuntimeError as e:assert qualification and 'READ_ONLY_CANONICAL_ALIGNMENT_UNAVAILABLE' in str(e)
  else:assert not qualification
  assert len(calls)==(0 if qualification else 1)
checks+=['read-only alignment explicit gap without repair','normal canonical reconciliation unchanged']
fn=next(n for n in ast.parse((ROOT/'src/core/operation_dashboard_routes.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='api_intel_runtime_budget')
start=next(i for i,n in enumerate(fn.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_critical_failures' for t in n.targets))
end=next(i for i,n in enumerate(fn.body[start:],start) if isinstance(n,ast.If) and '_cascade_ok' in ast.unparse(n.test))
for unknown in [True,False]:
 runtime={k:0 for k in ['treasury_timeout','create_fetch_timeout','creator_resolution_skipped','pool_discovery_deferred','validation_timeout','catchup_timeout','db_lock_errors_5m','db_p99_wait_ms']};runtime['cascade_state']='LIVE'
 if unknown:runtime['validation_timeout']=None
 ns={'runtime':runtime};exec(compile(ast.Module(body=fn.body[start:end+1],type_ignores=[]),'budget-health-source','exec'),ns);assert ns['health']==('UNKNOWN' if unknown else 'GREEN')
checks+=['missing budget total remains UNKNOWN without null arithmetic','normal budget health unchanged']
print(json.dumps({'status' :'PASS','checks':checks,'provider_calls':0,'canonical_writes':0}))
