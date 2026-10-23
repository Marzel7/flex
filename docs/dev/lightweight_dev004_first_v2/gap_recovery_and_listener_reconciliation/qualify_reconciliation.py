from pathlib import Path
exec(compile(Path('/private/tmp/dev004-runtime-probe.py').read_text().split("result={'component'")[0],'isolation','exec'))
sys.dont_write_bytecode=True
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1';os.environ['RPC_METRICS_DB']=str(out/'core.sqlite')
sys.path.insert(0,str(out))
opens=[]
def db_audit(event,args):
 if event=='sqlite3.connect':
  v=str(args[0]);p=Path(v[5:].split('?')[0] if v.startswith('file:') else v).resolve();allowed=v==':memory:' or p.is_relative_to(out);opens.append({'path':str(p),'allowed':allowed})
  if not allowed:raise RuntimeError('DATABASE_OUTSIDE_DISPOSABLE')
sys.addaudithook(db_audit)
def baseline_output(args,**kw):
 if isinstance(args,list) and 'show' in args:
  obj=args[-1]
  if obj=='a766d7e9c3efadc7bf270c62fca0b6d01fdb361c:runtime/listener/src/core/pumpfun_curve_listener.py':p=out/'numeric-baseline.py.txt'
  elif obj=='ce98ec0af892768b1420213716b659e3fe1b6653:runtime/listener/src/core/pumpportal_birth_audit.py':p=out/'receipt-baseline.py.txt'
  else:raise RuntimeError('UNEXPECTED_BASELINE_REQUEST')
  return p.read_text() if kw.get('text') else p.read_bytes()
 raise RuntimeError('SUBPROCESS_FORBIDDEN')
subprocess.check_output=baseline_output
import tempfile
tempfile.tempdir=str(out)
def test_threads(self,*a,**kw):
 target=getattr(self,'_target',None)
 if self.name.startswith('asyncio_') or (getattr(target,'__name__','')=='_do_shutdown' and getattr(target,'__module__','')=='asyncio.base_events'):
  return original_start(self,*a,**kw)
 return start_thread(self,*a,**kw)
threading.Thread.start=test_threads
import pytest
class Qualifier:
 def __init__(self):self.results=[];self.deselected=[]
 def pytest_collection_modifyitems(self,config,items):
  retained=[]
  for item in items:
   mod=item.module
   if hasattr(mod,'ROOT'):mod.ROOT=root.parents[1]
   if hasattr(mod,'SERVICE'):mod.SERVICE=root
   if hasattr(mod,'WRITER'):mod.WRITER=out/'composition/src/core/pumpportal_birth_audit.py'
   if hasattr(mod,'LISTENER'):mod.LISTENER=out/'composition/src/core/pumpfun_curve_listener.py'
   if hasattr(mod,'SOURCE'):mod.SOURCE=out/'composition/src/core/pumpfun_curve_listener.py'
   if item.name=='test_only_requested_handler_changed':self.deselected.append(item.nodeid)
   else:retained.append(item)
  config.hook.pytest_deselected(items=[i for i in items if i not in retained]);items[:]=retained
 def pytest_runtest_logreport(self,report):
  if report.when=='call' or (report.when=='setup' and report.failed):self.results.append({'test':report.nodeid,'outcome':report.outcome,'error':str(report.longrepr)[:1500] if report.failed else None})
q=Qualifier();code=pytest.main(['-q','--log-file='+str(out/'pytest-log.txt'),'-p','no:cacheprovider','-k','actual_readback_wrapper','--basetemp='+str(out/'pytest-readback'),str(out/'tests/test_receipt_failure_accounting.py'),str(out/'tests/test_pumpportal_numeric_parser.py')],plugins=[q])
# Independently verify the intended multi-correction composition boundary.
import ast,hashlib
text=(out/'composition/src/core/pumpfun_curve_listener.py').read_text();dev=(root/'src/core/pumpfun_curve_listener.py').read_text();installed=Path('/Users/kevinkeaveney/Production/flex-listener/src/core/pumpfun_curve_listener.py').read_text()
def definitions(s):
 return {n.name:ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(s)) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
a,b,c=definitions(text),definitions(dev),definitions(installed)
boundary={'startup_exact_dev004':a['_ensure_webhook_birth_queue_schema']==b['_ensure_webhook_birth_queue_schema'],'all_other_functions_exact_installed':all(a.get(k)==v for k,v in c.items() if k!='_ensure_webhook_birth_queue_schema')}
result={'pytest_exit':code,'results':q.results,'deselected':q.deselected,'composition_boundary':boundary,'blocked':blocked,'database_opens':opens}
(out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'pytest_exit':code,'passed':sum(x['outcome']=='passed' for x in q.results),'failed':[x for x in q.results if x['outcome']!='passed'],'composition_boundary':boundary,'blocked':blocked},indent=2))
