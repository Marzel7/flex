"""DEV043 qualification boundary. No application logic, providers or live queue consumers."""
import os,sys,pathlib,json,sqlite3,socket,resource,hashlib
ROOT=pathlib.Path(__file__).resolve().parents[3]
STATE=pathlib.Path(os.environ['DEV043_STATE_ROOT']).resolve()
PORT=int(os.environ.get('DEV043_PORT','8143'))
SHARED=pathlib.Path('/Users/kevinkeaveney/Dev/claude/flex')
DATABASES={SHARED/'database/wt_ops_v2.db',SHARED/'database/flex_complete_database.db',SHARED/'.dev_runtime/monitor/dev_005a/wt_ops_v2.dev.db'}
STATE.mkdir(parents=True,exist_ok=True)
os.chdir(STATE)
resource.setrlimit(resource.RLIMIT_FSIZE,(8*1024*1024,8*1024*1024))
EVENTS=[]
def inside(path):
 try:pathlib.Path(path).resolve().relative_to(STATE);return True
 except(ValueError,TypeError):return False
def reject(kind,target):
 EVENTS.append({'kind':kind,'target':str(target)[:300]})
 raise PermissionError('DEV043_ISOLATION_DENIED:'+kind)
def audit(event,args):
 if event in ['socket.connect','socket.getaddrinfo']:
  if event=='socket.getaddrinfo'and args[0]in ['127.0.0.1','localhost']:return
  reject(event,'network')
 if event=='socket.bind':
  address=args[1]
  if address!=('127.0.0.1',PORT):reject(event,address)
 if event in ['subprocess.Popen','os.system']:reject(event,'child execution')
 if event=='open'and not isinstance(args[0],int):
  name=str(args[0]);mode=args[1]or'';flags=args[2]if len(args)>2 else 0
  write=any(c in mode for c in ['w','a','x','+'])or bool(flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
  if write and name!='/dev/null'and not inside(name):reject('foreign_file_write',name)
  if pathlib.Path(name).name=='.env':reject('credential_file_read',name)
 if event in ['os.mkdir','os.remove','os.rmdir','os.rename']:
  for target in (args[:2]if event=='os.rename'else args[:1]):
   if not inside(target):reject('foreign_path_mutation',target)
sys.addaudithook(audit)
_original_connect=sqlite3.connect
def guarded_connect(database,*args,**kwargs):
 text=os.fspath(database)
 if text==':memory:'or text.startswith('file::memory:'):return _original_connect(database,*args,**kwargs)
 from urllib.parse import unquote,parse_qs
 name=unquote(text[5:].split('?',1)[0])if text.startswith('file:')else text
 path=pathlib.Path(name).resolve()
 if inside(path):return _original_connect(database,*args,**kwargs)
 params=parse_qs(text.partition('?')[2])
 if path not in DATABASES or params.get('mode')!=['ro']:reject('foreign_sqlite_write_or_authority',path)
 kwargs['uri']=True
 conn=_original_connect(path.as_uri()+'?mode=ro&immutable=1',*args,**kwargs)
 conn.execute('PRAGMA query_only=ON')
 return conn
sqlite3.connect=guarded_connect
if '--self-test'in sys.argv:
 checks=[]
 def denial(name,fn):
  try:fn()
  except PermissionError:checks.append({'name':name,'outcome':'PASS_DENIED'})
  else:raise AssertionError(name)
 denial('shared_monitor_write',lambda:guarded_connect(str(SHARED/'.dev_runtime/monitor/dev_005a/wt_ops_v2.dev.db')))
 denial('canonical_write',lambda:guarded_connect(str(SHARED/'database/wt_ops_v2.db')))
 denial('live_queue_write',lambda:open(SHARED/'.dev_runtime/monitor/dev_005a/queue/DEV043_MUST_NOT_EXIST','wb'))
 denial('production_api_connect',lambda:socket.socket().connect(('127.0.0.1',5002)))
 denial('external_provider_connect',lambda:socket.socket().connect(('1.1.1.1',443)))
 fixture=STATE/'boundary_fixture.db';c=guarded_connect(fixture);c.execute('CREATE TABLE IF NOT EXISTS boundary_check(value INTEGER)');c.commit();c.close()
 for p in DATABASES:
  c=guarded_connect(p.as_uri()+'?mode=ro',uri=True);assert c.execute('PRAGMA query_only').fetchone()[0]==1;c.close()
 print(json.dumps({'status':'PASS','checks':checks,'state':str(STATE),'external_database_policy':'allowlisted,mode=ro,immutable=1,query_only','application_imported':False,'port':PORT,'bootstrap_sha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()}));sys.exit(0)
# A passed boundary test is mandatory before any application import.
receipt=pathlib.Path(os.environ['DEV043_ISOLATION_RECEIPT']);attestation=json.loads(receipt.read_text());assert attestation['status']=='PASS' and attestation['state']==str(STATE) and attestation['port']==PORT;assert attestation['bootstrap_sha256']==hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
for name in list(os.environ):
 if any(s in name for s in ['BIRDEYE','HELIUS','ALCHEMY','API_KEY','SECRET','PASSWORD']):del os.environ[name]
os.environ.update({'FLEX_DEV_STATE_ROOT':str(STATE),'FLEX_DEV_READ_ONLY_QUALIFICATION':'1','FLEX_DEV_HOST':'127.0.0.1','FLEX_DEV_PORT':str(PORT),'FLEX_DEV_RELOAD':'0','FLEX_DEV_OPERATIONS_MODE':'MONITOR','WT_OPS_DB_PATH':str(SHARED/'database/wt_ops_v2.db'),'DB_PATH':str(SHARED/'database/flex_complete_database.db'),'FLEX_DB_PATH':str(SHARED/'database/flex_complete_database.db'),'MISSION_CONTROL_MONITOR_UI_DB_PATH':str(SHARED/'.dev_runtime/monitor/dev_005a/wt_ops_v2.dev.db'),'WATCHTOWER_MONITOR_UI_DB_PATH':str(SHARED/'.dev_runtime/monitor/dev_005a/wt_ops_v2.dev.db'),'OPERATION_MONITOR_QUEUE_PATH':str(STATE/'monitor/queue'),'WATCHTOWER_MONITOR_QUEUE_PATH':str(STATE/'monitor/queue'),'OPERATION_RESEARCH_ROOT':str(ROOT),'PYTHONDONTWRITEBYTECODE':'1'})
sys.path.insert(0,str(ROOT))
try:
 import src.dev_runtime as runtime
 print('DEV043_IMPORT_PASS',flush=True)
 runtime.main()
except BaseException:
 print('DEV043_ISOLATION_EVENTS='+json.dumps(EVENTS),flush=True)
 raise
