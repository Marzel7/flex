import sys,os,json,importlib,socket,threading,subprocess,sqlite3,resource,time,traceback
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
root=Path(sys.argv[1]).resolve();component=sys.argv[2];out=Path(sys.argv[3]).resolve();out.mkdir(exist_ok=True)
sys.path.insert(0,str(root));os.chdir(out)
os.environ.update(DB_PATH=str(out/'core.sqlite'),WT_OPS_DB_PATH=str(out/'ops.sqlite'),DEV004_CREATOR_HISTORY_PATH='/private/tmp/dev004-compact-creator-qualified/history.json',DEV004_WALKBACK_PROJECTION_RECOVERY_ENABLED='1',BIRDEYE='synthetic-dev-only',HELIUS_RPC_URL='http://invalid.synthetic',PYTHONDONTWRITEBYTECODE='1',LISTENER_RPC_METRICS_API_START_ENABLED='0',WS_PRICE_TRACE_LOG_PATH=str(out/'logs/ws_price_trace.log'))
# No dotenv, real credentials, transport, subprocess or unmanaged background work.
import dotenv
dotenv.load_dotenv=lambda *a,**k:False
blocked=[]
boundary_traces=[]
def reject(kind):
 def denied(*a,**k):
  blocked.append(kind);boundary_traces.append({'kind':kind,'frames':[{'file':f.filename,'line':f.lineno,'function':f.name} for f in traceback.extract_stack(limit=8)]});raise RuntimeError('STARTUP_BOUNDARY:'+kind)
 return denied
socket.socket.connect=reject('network');socket.socket.connect_ex=reject('network');socket.socket.bind=reject('bind');original_start=threading.Thread.start
def start_thread(self,*a,**k):
 if self.name not in ['db-conn-reaper','db-wal-watchdog']:return reject('thread:'+self.name)()
 return original_start(self,*a,**k)
threading.Thread.start=start_thread
subprocess.Popen=reject('subprocess')
raw=sqlite3.connect

def connect(p,*a,**k):
 text=str(p);p=Path(text[5:].split('?')[0] if text.startswith('file:') else text).resolve()
 if text!=':memory:' and not p.is_relative_to(out):raise RuntimeError('DB_BINDING_OUTSIDE_FIXTURE')
 c=raw(text,*a,**k)
 if 'mode=ro' not in text:c.execute('pragma max_page_count=3840')
 return c
sqlite3.connect=connect

def audit(event,args):
 if event=='open':
  p,mode,flags=args
  if isinstance(p,(str,bytes)):
   path=Path(os.fsdecode(p)).resolve()
   if path.name=='.env' or path.name.startswith('.env.'):raise RuntimeError('CREDENTIAL_FILE_READ_BLOCKED')
   writing=(isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
   if writing and not path.is_relative_to(out):raise RuntimeError('WRITE_OUTSIDE_FIXTURE:'+str(path))
 if event in ['os.mkdir','os.remove','os.rmdir','os.rename']:
  p=args[0]
  if isinstance(p,(str,bytes)) and not Path(os.fsdecode(p)).resolve().is_relative_to(out):raise RuntimeError('MUTATION_OUTSIDE_FIXTURE:'+str(p))
sys.addaudithook(audit)
result={'component':component,'source_root':str(root),'stage':'import','blocked':blocked,'boundary_traces':boundary_traces}
try:
 m=importlib.import_module(component);result.update(status='IMPORT_PASS',module_file=m.__file__,threads=[t.name for t in threading.enumerate()],db_bindings={k:getattr(m,k) for k in ['DB_PATH','OPS_DB_PATH','LIVE_DB_PATH','CREATOR_DB_PATH'] if hasattr(m,k)})
 if component=='src.ops.attribution_outcome':
  import hashlib
  expected=json.load(open('/private/tmp/dev004-compact-creator-qualified/equivalence.json'))['cases'][0]
  c=sqlite3.connect(str(out/'core.sqlite'));c.row_factory=sqlite3.Row
  o=sqlite3.connect(str(out/'ops.sqlite'));o.row_factory=sqlite3.Row
  actual=m.evaluate_launcher_profile(o,c,expected['creator'],now=1792747000)
  assert actual==expected['v1_profile'],(actual,expected['v1_profile'])
  result['compact_profile_exact']=True
  from src.core.walkback_queue import classify_creator
  oracle=next(r for r in json.load(open('/private/tmp/dev004-compact-creator-qualified/classification_closure/result.json'))['comparisons'] if r['creator']==expected['creator'])
  # Pin only the profile's observation clock at the already frozen qualification time.
  fn=m.evaluate_launcher_profile
  m.evaluate_launcher_profile=lambda ops,core,creator,**kw:fn(ops,core,creator,now=1792747000)
  assert list(classify_creator(expected['creator'],o,c))==oracle['v1_class']
  result['compact_classification_exact']=True;c.close();o.close()
 if os.environ.get('QUALIFY_BOOTSTRAP')=='1':
  result['stage']='startup'
  if component=='src.core.creator_resolution_worker':m._STOP=True;m.run_loop(once=True);result['startup']='PASS_STOP_BEFORE_FIRST_CYCLE'
  elif component=='src.core.walkback_worker':
   # Existing schema validation is the first actual startup boundary.
   if os.environ.get('QUALIFY_SCHEMA_ONLY')=='1':
    o=sqlite3.connect(str(out/'ops.sqlite'));o.row_factory=sqlite3.Row
    from src.ops import watchtower_candidates,attribution_outcome
    watchtower_candidates.ensure_schema(o);attribution_outcome.ensure_schema(o)
    from src.core import walkback_queue,treasury_bank
    from src.ops import anchor_reconciliation,create_event_ledger,operation_fingerprint_drift
    result['metadata_validators']={'walkback':str(walkback_queue.validate_schema(o)),'treasury':treasury_bank.validate_schema(o),'outcome':attribution_outcome.validate_schema(o),'anchor':anchor_reconciliation.validate_schema(o),'ledger':create_event_ledger.validate_schema(o),'fingerprint':operation_fingerprint_drift.validate_schema(o)}
    o.close();result['startup']='HOLD_METADATA_ONLY_NOT_DAEMON'
   else:m.run_loop()
  elif component=='src.core.pumpfun_curve_listener':
   import asyncio
   asyncio.run(m.main())

except BaseException as e:
 result.update(status='HOLD',error_type=type(e).__name__,error=str(e)[:300],traceback=traceback.format_exc(limit=12))
print(json.dumps(result));(out/'probe.json').write_text(json.dumps(result,indent=2)+'\n')
