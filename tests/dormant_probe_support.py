import sys,pathlib,json,hashlib,importlib.util,socket,threading,sqlite3,os,traceback
R=pathlib.Path(sys.argv[1]);F=pathlib.Path(sys.argv[2]);role=sys.argv[3];sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('dormant_entry',R/'scripts/flex_v2_dormant.py');entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
blocked=[]
def forbid(*a,**kw):blocked.append({'call':str(a[0])[:100] if a else 'unknown','stack':''.join(traceback.format_stack(limit=5))});raise RuntimeError('NO_NETWORK_OR_THREAD')
socket.socket.connect=forbid;socket.socket.connect_ex=forbid;socket.socket.bind=forbid;socket.create_connection=forbid;threading.Thread.start=forbid
native=sqlite3.connect
paths=[]
def connect(db,*a,**kw):
 path=str(pathlib.Path(str(db).removeprefix('file:').split('?')[0]).resolve()) if str(db)!=':memory:' else ':memory:'
 if path!=':memory:' and not path.startswith(str(F)+'/'):blocked.append('DATABASE:'+path);raise RuntimeError('UNQUALIFIED_DATABASE')
 paths.append(path);return native(db,*a,**kw)
sqlite3.connect=connect
# Imported source must not create files anywhere except its disposable fixture.
def audit(event,args):
 if event=='open':
  p,mode,flags=args
  if isinstance(p,(str,bytes)) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC):
   path=str(pathlib.Path(os.fsdecode(p)).absolute())
   if not path.startswith(str(F)+'/') and path!='/dev/null':blocked.append('WRITE:'+path);raise RuntimeError('OUTSIDE_FIXTURE_WRITE')
sys.addaudithook(audit)
try:
 p=F/'manifest.json';m,package=entry.configure(p,hashlib.sha256(p.read_bytes()).hexdigest(),role,qualify=True);module=entry.load_component(role)
 result={'status':'PASS','role':role,'module':module.__file__,'blocked':blocked,'db_paths':sorted(set(paths))}
 if role=='api':result['routes']=len(list(module.app.url_map.iter_rules()));result['assets']=(R/'templates').is_dir() and (R/'static').is_dir()
 if len(sys.argv)>4 and sys.argv[4]=='write_guard':
  from src.utils.db_locking import db_connect
  from src.core.paired_binding_guard import validate_pair
  connection=db_connect(str(F/'core.sqlite'))
  connection.execute('CREATE TABLE IF NOT EXISTS qualification_write(id INTEGER PRIMARY KEY,value TEXT)')
  connection.execute("INSERT INTO qualification_write VALUES(1,'accepted')");connection.commit()
  (F/'core.sqlite.retired').write_text(json.dumps({'version':1,'path':str(F/'core.sqlite'),'generation':'fixture-v2','retired':True}))
  denied=False
  try:connection.execute("INSERT INTO qualification_write VALUES(2,'stale')");connection.commit()
  except Exception:denied=True
  connection.close()
  assert denied
  with native(F/'core.sqlite') as c:assert c.execute('SELECT count(*) FROM qualification_write').fetchone()[0]==1
  result['stale_writer_rejected']=True
 if len(sys.argv)>4 and sys.argv[4]=='read_fallback_guard':
  from src.utils.db_locking import db_connect
  denied=False
  try:db_connect(str(F/'legacy-not-authorized.sqlite'),read_only=True)
  except Exception:denied=True
  assert denied and not (F/'legacy-not-authorized.sqlite').exists()
  result['unknown_read_rejected']=True
 if len(sys.argv)>4 and sys.argv[4]=='api_cache':
  from src.core.rpc_cache import RPCCache
  cache=RPCCache(str(F/'core.sqlite'))
  with native(F/'core.sqlite') as c:
   c.execute('INSERT INTO rpc_response_cache(cache_key,response_json,method,cached_at,ttl_seconds,hit_count) VALUES(?,?,?,?,?,0)',('expired','{}','synthetic',0,1))
  assert cache.get('expired') is None and cache.cleanup_expired()==0
  with native(F/'core.sqlite') as c:assert c.execute("SELECT count(*) FROM rpc_response_cache WHERE cache_key='expired'").fetchone()[0]==1
  result['expired_cache_preserved']=True
 if len(sys.argv)>4 and sys.argv[4]=='api_asset':
  asset=next((R/'static').rglob('*.css'))
  response=module.app.test_client().get('/static/'+str(asset.relative_to(R/'static')))
  assert response.status_code==200 and response.data==asset.read_bytes()
  result['static_asset_response_verified']=True
 if len(sys.argv)>4 and sys.argv[4]=='checkpoint_budget':
  from src.ops import dev004_checkpoint_completion as completion
  from src.ops.dev_provider_budget import DevProviderBudget
  path=str(F/'ops.sqlite')
  candle={'timestamp':1800000000,'open':100.0,'high':110.0,'low':90.0,'close':100.0}
  with native(path) as c:
   completion.ensure_schema(c)
   refs=completion.accepted_rows(c,'synthetic-mint','15m',[candle],'synthetic-window','BIRDEYE_TERMINAL_ATH_CHUNK',1800000001)
   c.execute('INSERT INTO operation_monitor_window_completion VALUES(?,?,?,?,?,?,?,?,?)',('watchtower','synthetic-mint','synthetic-window','15m',1800000000,1800000900,json.dumps(refs),completion.digest([candle]),1800000001))
  assert completion.read_completed(path,'synthetic-mint','15m',[(1800000000,1800000900,'synthetic-window')])=={'synthetic-window':[candle]}
  budget=DevProviderBudget(F/'queue');budget.admit('synthetic-mint','test-no-dispatch',now=1800000001)
  result['budget_reserved_without_dispatch']=len(budget._read()['calls'])
 print('RESULT_JSON '+json.dumps(result))
except Exception as error:print('RESULT_JSON '+json.dumps({'status':'HOLD','error':str(error),'traceback':traceback.format_exc()[-5000:],'blocked':blocked}));sys.exit(2)
