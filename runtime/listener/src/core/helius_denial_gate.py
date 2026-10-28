"""Opt-in, bounded shared Helius denial state; no providers or implicit init.
Reservations precede dispatch; pre-existing in-flight requests remain ambiguous.
Ordinary 429 is rate limiting, never asserted to prove credit exhaustion.
"""
import contextlib,fcntl,hashlib,json,os,stat,time,uuid,threading,sys
from pathlib import Path
from urllib.parse import urlsplit
FLAG='FLEX_HELIUS_DENIAL_GATE_ENABLED'
MAX_BYTES=4096
_PUBLICATION_CONTEXT=threading.local()
class Deferred(RuntimeError):pass
class Integrity(Deferred):pass
class BudgetDeferred(Deferred):pass

def enabled():
 v=os.environ.get(FLAG,'0')
 if v not in ('0','1'):raise Integrity('HELIUS_GATE_FLAG')
 journal=os.environ.get('MIGRATION_JOURNAL_PATH')
 stable=Path(journal).parent/'helius-denial-v1.json' if journal and os.path.isabs(journal) else None
 if v=='0' and (os.environ.get('HELIUS_DENIAL_GATE_PATH') or (stable is not None and stable.exists())):raise Integrity('HELIUS_GATE_DOWNGRADE')
 return v=='1'
def helius(url):
 host=(urlsplit(url).hostname or '').encode('idna').decode('ascii').lower().rstrip('.')
 return host=='helius-rpc.com' or host.endswith('.helius-rpc.com') or host=='helius.xyz' or host.endswith('.helius.xyz')
def authority():
 from src.core.paired_binding_guard import validate_pair
 return validate_pair(core_path=os.environ['DB_PATH'],ops_path=os.environ['WT_OPS_DB_PATH'],purpose='active')
class Gate:
 def __init__(self,path,generation,clock=time.time):
  self.path=Path(path);self.generation=generation;self.clock=clock;self._publication_thread=_PUBLICATION_CONTEXT
  if not self.path.is_absolute() or str(self.path.resolve())!=str(self.path) or not generation:raise Integrity('HELIUS_GATE_AUTHORITY')
 def _publication_owned(self):
  return (os.getpid(),str(self.path)) in getattr(self._publication_thread,'paths',())
 @contextlib.contextmanager
 def locked(self,*,blocking=False):
  fd=os.open(str(self.path)+'.lock',os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK)
  try:
   info=os.fstat(fd)
   if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>128:raise Integrity('HELIUS_GATE_LOCK')
   try:fcntl.flock(fd,fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB))
   except BlockingIOError:raise BudgetDeferred('HELIUS_GATE_BUSY') from None
   previous=getattr(self._publication_thread,'paths',());self._publication_thread.paths=previous+((os.getpid(),str(self.path)),)
   try:yield
   finally:self._publication_thread.paths=previous
  finally:os.close(fd)
 def _marker(self):
  fd=os.open(str(self.path)+'.lock',os.O_RDONLY|os.O_NOFOLLOW)
  try:
   st=os.fstat(fd)
   if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_size>128:raise Integrity('HELIUS_GATE_LOCK')
   return os.read(fd,129)
  finally:os.close(fd)
 def _raw(self):
  fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
  try:
   st=os.fstat(fd)
   if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_size>16777216:raise Integrity('HELIUS_GATE_STATE')
   return os.read(fd,16777217)
  finally:os.close(fd)
 def _decode(self,raw):
  def unique(items):
   result={}
   for key,value in items:
    if key in result:raise Integrity('HELIUS_GATE_STATE')
    result[key]=value
   return result
  try:s=json.loads(raw,object_pairs_hook=unique)
  except (ValueError,TypeError,UnicodeError):raise Integrity('HELIUS_GATE_STATE') from None
  if not isinstance(s,dict):raise Integrity('HELIUS_GATE_STATE')
  if s.get('version')!=1 or s.get('generation')!=self.generation or s.get('state') not in ('OPEN','RATE_LIMITED','OUTAGE','PAYMENT_DENIED','AUTH_DENIED'):raise Integrity('HELIUS_GATE_STATE')
  for k in ('reservations','outcomes','failures','probe_until','next_at','recover_until'):
   if type(s.get(k)) is not int or not 0<=s[k]<2**63:raise Integrity('HELIUS_GATE_STATE')
  if not isinstance(s.get('methods'),dict) or len(s['methods'])>16 or any(type(v) is not int or v<0 for v in s['methods'].values()):raise Integrity('HELIUS_GATE_COUNTERS')
  if not isinstance(s.get('statuses'),dict) or len(s['statuses'])>8 or any(type(v) is not int or v<0 for v in s['statuses'].values()):raise Integrity('HELIUS_GATE_COUNTERS')
  if 'rpc_budget' in s:
   try:_budget(self,s)
   except (KeyError,TypeError,ValueError,AttributeError):raise Integrity('HELIUS_BUDGET_STATE') from None
  elif len(raw)>MAX_BYTES:raise Integrity('HELIUS_GATE_STATE')
  return s
 def _write_marker(self,value):
  if len(value)>128:raise Integrity('HELIUS_GATE_LOCK')
  fd=os.open(str(self.path)+'.lock',os.O_WRONLY|os.O_NOFOLLOW)
  try:
   if os.write(fd,value)!=len(value):raise Integrity('HELIUS_GATE_SHORT_WRITE')
   os.ftruncate(fd,len(value));os.fsync(fd)
  finally:os.close(fd)
 def _sync_directory(self):
  fd=os.open(self.path.parent,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)
 def _storage(self):
  st=os.statvfs(self.path.parent)
  if st.f_bavail*st.f_frsize<2067108864:raise Integrity('DISK_FLOOR')
 def read(self):
  # Strictly read-only, including prospective interrupted publications.
  marker=self._marker();raw=self._raw()
  if marker!=b'READY_V1 '+hashlib.sha256(raw).hexdigest().encode()+b'\n':raise Integrity('HELIUS_GATE_PUBLICATION_AMBIGUOUS')
  return self._decode(raw)
 def finish_publication(self,*,expected_state=None):
  # Writer-only finalization of a durable target identified independently by
  # the versioned intent marker. Never infer legacy intent or release a slot.
  if not self._publication_owned():raise Integrity('HELIUS_PUBLICATION_LOCK_REQUIRED')
  marker=self._marker();parts=marker.split()
  if len(parts)!=4 or parts[0]!=b'PENDING_V2' or len(parts[1])!=64 or any(c not in b'0123456789abcdef' for c in parts[1]) or not parts[2].isdigit() or not 1<int(parts[2])<2**32 or parts[3] not in self._publication_operations() or marker!=b' '.join(parts)+b'\n':raise Integrity('HELIUS_GATE_PUBLICATION_AMBIGUOUS')
  temp=self.path.with_name(self.path.name+'.stage')
  if os.path.lexists(temp):raise Integrity('HELIUS_PUBLICATION_STAGE_UNCERTAIN')
  raw=self._raw()
  if hashlib.sha256(raw).hexdigest().encode()!=parts[1]:raise Integrity('HELIUS_PUBLICATION_TARGET_MISMATCH')
  state=self._decode(raw)
  if expected_state is not None and state!=expected_state:raise Integrity('HELIUS_PUBLICATION_EXPECTED_STATE_MISMATCH')
  self._storage()
  fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW)
  try:
   if os.read(fd,16777217)!=raw:raise Integrity('HELIUS_PUBLICATION_STATE_CHANGED')
   os.fsync(fd)
  finally:os.close(fd)
  self._sync_directory()
  if self._marker()!=marker:raise Integrity('HELIUS_PUBLICATION_STATE_CHANGED')
  self._write_marker(b'READY_V1 '+parts[1]+b'\n')
  return self.read()
 @staticmethod
 def _publication_operations():
  return {b'state',b'initialize',b'reserve_http',b'finish_http',b'notice_http',b'register_origin',b'bind_child',b'configure_budget',b'authorize_probe',b'reserve_ws',b'record_ws',b'clock_update',b'reconcile',b'generation_move'}
 def _writer_read(self):
  try:return self.read()
  except Integrity as exc:
   if str(exc)!='HELIUS_GATE_PUBLICATION_AMBIGUOUS':raise
   return self.finish_publication()
 def save(self,state):
  names={'initialize':'initialize','_reserve_http':'reserve_http','_reserve2_locked':'reserve_http','_finish_http':'finish_http','_notice_http':'notice_http','_register_origin':'register_origin','_bind_child':'bind_child','_configure_budget':'configure_budget','_configure2':'configure_budget','authorize_probe':'authorize_probe','reserve':'reserve_ws','result':'record_ws','record':'record_ws','finish_helius_recovery':'generation_move','recover_clock':'clock_update','reconcile_forward_clock':'clock_update','_reconcile_terminated_http':'reconcile'}
  operation=names.get(sys._getframe(1).f_code.co_name,'state')
  if self._publication_owned():return self._save_publication(state,operation)
  with self.locked():return self._save_publication(state,operation)
 def _save_publication(self,state,operation):
  prepared=(json.dumps(state,sort_keys=True)+'\n').encode()
  limit=16777216 if state.get('rpc_budget',{}).get('version')==2 else 262144 if 'rpc_budget' in state else MAX_BYTES
  if len(prepared)>limit:
   if state.get('rpc_budget',{}).get('version')==2:raise BudgetDeferred('HELIUS_INVESTIGATION_STATE_CAPACITY')
   raise Integrity('HELIUS_GATE_CAPACITY')
  target=self if state.get('generation')==self.generation else Gate(self.path,state.get('generation'))
  target._decode(prepared)
  prior=None;marker=self._marker()
  if self.path.exists():
   raw=self._raw();prior=self._decode(raw)
   if marker!=b'READY_V1 '+hashlib.sha256(raw).hexdigest().encode()+b'\n':
    # Preserve the accepted explicit conservative legacy recovery, never an
    # OPEN allowance or a changed accounting/ownership body over ambiguity.
    if marker==b'PENDING_V1\n' and state['state'] in ('PAYMENT_DENIED','AUTH_DENIED') and {k:v for k,v in state.items() if k!='state'}=={k:v for k,v in prior.items() if k!='state'}:pass
    else:raise Integrity('HELIUS_GATE_PUBLICATION_AMBIGUOUS')
  elif marker:raise Integrity('HELIUS_GATE_PUBLICATION_AMBIGUOUS')
  if target is not self:
   # Retain the accepted renderer's generation-only fresh-pair move. The new
   # active matched pair must independently authorize the target generation.
   try:active_generation=authority()
   except Exception:raise Integrity('HELIUS_GENERATION_MOVE_AUTHORITY') from None
   if prior is None or active_generation!=state['generation'] or state!=dict(prior,generation=state['generation']):raise Integrity('HELIUS_GENERATION_MOVE_AUTHORITY')
  digest=hashlib.sha256(prepared).hexdigest().encode()
  intent=b'PENDING_V2 '+digest+b' '+str(os.getpid()).encode()+b' '+operation.encode()+b'\n'
  restrictive=prior is not None and state['state']!=prior['state'] and state['state'] in ('PAYMENT_DENIED','AUTH_DENIED','RATE_LIMITED','OUTAGE')
  # A newly observed provider denial must remain fail-closed even if there is
  # insufficient storage to persist its full state. Normal refusal must not
  # poison the previous valid publication or authorize an undispatched RPC.
  if restrictive:self._write_marker(intent)
  self._storage()
  temp=self.path.with_name(self.path.name+'.stage');created=False
  try:
   fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);created=True
   try:
    done=0
    while done<len(prepared):
     n=os.write(fd,prepared[done:])
     if n<=0:raise Integrity('HELIUS_GATE_SHORT_WRITE')
     done+=n
    os.fsync(fd)
   finally:os.close(fd)
   if not restrictive:self._write_marker(intent)
   os.replace(temp,self.path);self._sync_directory()
   self._write_marker(b'READY_V1 '+digest+b'\n')
  except BaseException:
   # Remove only this call's unpublished temporary file, with independently
   # verified old READY authority. Never discard an uncertain staged intent.
   if created and temp.exists() and marker.startswith(b'READY_V1 ') and self._marker()==marker and prior is not None and self._raw()==raw:
    os.unlink(temp);self._sync_directory()
   raise
 def initialize(self,initial):
  # Explicit operator authority; never auto-repair or recreate missing state.
  if initial not in ('OPEN','PAYMENT_DENIED'):raise Integrity('HELIUS_GATE_INITIAL_AUTHORITY')
  if self.path.exists() or Path(str(self.path)+'.lock').exists():raise Integrity('HELIUS_GATE_ALREADY_INITIALIZED')
  fd=os.open(str(self.path)+'.lock',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.fsync(fd);os.close(fd)
  self.save(dict(version=1,generation=self.generation,state=initial,reservations=0,outcomes=0,failures=0,probe_until=0,next_at=0,recover_until=0,methods={},statuses={}))
 def authorize_probe(self,expected_sha256):
  # Explicit restoration authority, not a timer or automatic reset. Counts and
  # prior denial remain retained; only one bounded probe can be reserved.
  with self.locked():
   s=self._writer_read()
   if hashlib.sha256(self.path.read_bytes()).hexdigest()!=expected_sha256:raise Integrity('HELIUS_RESTORE_AUTHORITY_CHANGED')
   s.update(state='OUTAGE',probe_until=0,next_at=int(self.clock()),recover_until=int(self.clock())+300)
   self.save(s)
 def blocked(self):
  with self.locked():
   s=self.read();now=int(self.clock())
   return s['state'] in ('PAYMENT_DENIED','AUTH_DENIED') or now<s['next_at'] or now<s['probe_until']
 def reserve(self,method="other"):
  with self.locked():
   s=self._writer_read();now=int(self.clock())
   if s['state'] in ('PAYMENT_DENIED','AUTH_DENIED'):raise Deferred('HELIUS_ACQUISITION_DEFERRED')
   if 'rpc_budget' in s and method!='websocket_connect':raise Integrity('HELIUS_HTTP_RESERVATION_REQUIRES_WIRE_GATE')
   if now<s['next_at'] or now<s['probe_until']:
    raise Deferred('HELIUS_GATE_PACING' if s['state']=='OPEN' else 'HELIUS_ACQUISITION_DEFERRED')
   probe=s['state']!='OPEN'
   if probe:s['probe_until']=now+120;s['next_at']=now+300;s['recover_until']=now+300
   if s['state']=='OPEN' and now<s['recover_until']:s['next_at']=now+2
   s['reservations']+=1
   method=method if method in ('getTransaction','getSignaturesForAddress','getAccountInfo','getMultipleAccounts','getTokenLargestAccounts','getTokenAccountsByOwner','websocket_connect') else 'other'
   s['methods'][method]=s['methods'].get(method,0)+1
   self.save(s)
   return probe
 def result(self,status,*,probe=False,retry_after=60,transport_failure=False):
  with self.locked():
   s=self._writer_read();now=int(self.clock());s['outcomes']+=1
   label='transport_error' if transport_failure else str(status) if status in (200,401,402,403,429) else '5xx' if status>=500 else 'other'
   s['statuses'][label]=s['statuses'].get(label,0)+1
   if s['state'] in ('PAYMENT_DENIED','AUTH_DENIED'):self.save(s);return
   if status==402:s['state']='PAYMENT_DENIED';s['probe_until']=0
   elif status in (401,403):s['state']='AUTH_DENIED';s['probe_until']=0
   elif status==429:
    s['state']='RATE_LIMITED';s['next_at']=max(s['next_at'],now+min(900,max(60,int(retry_after))));s['probe_until']=0
   elif transport_failure or status>=500:
    s['failures']+=1
    if s['failures']>=3 or probe:s['state']='OUTAGE';s['next_at']=max(s['next_at'],now+300);s['probe_until']=0
   elif status==200 and probe:s.update(state='OPEN',failures=0,probe_until=0,next_at=now+2)
   elif status==200 and s['state']=='OPEN':s['failures']=0
   self.save(s)

def current():
 if not enabled():return None
 path=os.environ.get('HELIUS_DENIAL_GATE_PATH')
 journal=os.environ.get('MIGRATION_JOURNAL_PATH')
 if not path or not journal or not os.path.isabs(journal) or path!=str(Path(journal).parent/'helius-denial-v1.json'):raise Integrity('HELIUS_GATE_STABLE_PATH_REQUIRED')
 g=Gate(path,authority())
 try:
  with g.locked():state=g._writer_read()
 except OSError:raise Integrity('HELIUS_GATE_STATE') from None
 if 'rpc_budget' in state and state['rpc_budget'].get('version')!=2:raise Integrity('HELIUS_INVESTIGATION_POLICY_V2_REQUIRED')
 if 'rpc_budget' not in state and state['state'] not in ('PAYMENT_DENIED','AUTH_DENIED'):raise BudgetDeferred('HELIUS_INVESTIGATION_POLICY_V2_REQUIRED')
 return g
def blocked():
 try:g=current()
 except (Deferred,Integrity,OSError):return True
 if not g:return False
 try:return g.blocked()
 except (Deferred,Integrity,OSError):return True  # Paid channel closed; admission remains live.

def record(g,status,probe,retry_after=60,transport_failure=False):
 if g:
  try:g.result(status,probe=probe,retry_after=retry_after,transport_failure=transport_failure)
  except OSError:raise Integrity('HELIUS_GATE_PUBLICATION_FAILED') from None

async def reserve_async(g,method):
 import asyncio
 deadline=time.monotonic()+6
 while True:
  try:return g.reserve(method)
  except OSError:raise Integrity('HELIUS_GATE_PUBLICATION_FAILED') from None
  except Deferred as exc:
   if str(exc) not in ('HELIUS_GATE_PACING','HELIUS_GATE_BUSY') or time.monotonic()>=deadline:raise
   await asyncio.sleep(2 if str(exc)=='HELIUS_GATE_PACING' else 0.05)

def reserve_sync(g,method):
 deadline=time.monotonic()+6
 while True:
  try:return g.reserve(method)
  except OSError:raise Integrity('HELIUS_GATE_PUBLICATION_FAILED') from None
  except Deferred as exc:
   if str(exc) not in ('HELIUS_GATE_PACING','HELIUS_GATE_BUSY') or time.monotonic()>=deadline:raise
   time.sleep(2 if str(exc)=='HELIUS_GATE_PACING' else 0.05)

@contextlib.asynccontextmanager
async def post(session,url,**kwargs):
 g=current() if enabled() and helius(url) else None
 if g and g.has_budget():
  from src.core.helius_http_protection import require_installed
  require_installed()
  import aiohttp
  try:
   async with session.post(url,**kwargs) as response:
    if response.status in (401,402,403,429) or response.status>=500:raise Deferred('HELIUS_PROVIDER_DEFERRED')
    yield response
  except (TimeoutError,ConnectionError,aiohttp.ClientError):raise Deferred('HELIUS_TRANSPORT_UNAVAILABLE') from None
  return
 probe=await reserve_async(g,(kwargs.get('json') or {}).get('method','other')) if g else False
 try:
  import aiohttp
  supplied=kwargs.get('timeout',getattr(session,'timeout',None))
  total=getattr(supplied,'total',supplied if isinstance(supplied,(int,float)) else None)
  if g and (total is None or total>60):kwargs['timeout']=aiohttp.ClientTimeout(total=60)
  async with session.post(url,**kwargs) as response:
   if g:
    value=response.headers.get('Retry-After','60')
    retry=int(value) if len(str(value))<=10 and str(value).isdigit() else 60
    record(g,response.status,probe,retry)
    if response.status in (401,402,403,429) or response.status>=500:raise Deferred('HELIUS_PROVIDER_DEFERRED')
   yield response
 except Deferred:raise
 except Exception as exc:
  import aiohttp
  if isinstance(exc,(TimeoutError,ConnectionError,aiohttp.ClientError)):
   record(g,0,probe,transport_failure=True)
   if g:raise Deferred('HELIUS_TRANSPORT_UNAVAILABLE') from None
  raise

@contextlib.contextmanager
def urlopen(request,*,timeout):
 import urllib.request,urllib.error
 url=request.full_url;g=current() if enabled() and helius(url) else None
 if g and g.has_budget():
  from src.core.helius_http_protection import require_installed
  require_installed()
  try:
   with urllib.request.urlopen(request,timeout=timeout) as response:yield response
  except urllib.error.HTTPError:raise Deferred('HELIUS_PROVIDER_DEFERRED') from None
  except urllib.error.URLError:raise Deferred('HELIUS_TRANSPORT_UNAVAILABLE') from None
  return
 probe=reserve_sync(g,json.loads(request.data).get('method','other')) if g else False
 try:
  with urllib.request.urlopen(request,timeout=timeout) as response:
   record(g,response.status,probe);yield response
 except urllib.error.HTTPError as e:
  record(g,e.code,probe)
  if g:raise Deferred('HELIUS_PROVIDER_DEFERRED') from None
  raise
 except urllib.error.URLError:
  record(g,0,probe,transport_failure=True)
  if g:raise Deferred('HELIUS_TRANSPORT_UNAVAILABLE') from None
  raise


class _Socket:
 def __init__(self,ws,g,probe):self.ws=ws;self.g=g;self.probe=probe
 def __getattr__(self,name):return getattr(self.ws,name)
 def confirm(self):record(self.g,200,self.probe)

@contextlib.asynccontextmanager
async def websocket(connect,url,**kwargs):
 g=current() if enabled() and helius(url) else None
 probe=await reserve_async(g,'websocket_connect') if g else False
 try:
  manager=connect(url,**kwargs)
  if g and g.has_budget():
   from src.core.helius_http_protection import websocket_connection
   manager=websocket_connection(manager)
  async with manager as ws:
   yield _Socket(ws,g,probe) if g else ws
 except Exception as exc:
  status=getattr(exc,'status_code',None) or getattr(getattr(exc,'response',None),'status_code',None)
  if status in (401,402,403,429) or (type(status) is int and status>=500):record(g,status,probe)
  elif isinstance(exc,(OSError,TimeoutError)) or type(exc).__name__ in ('ConnectionClosed','ConnectionClosedError','ConnectionClosedOK','InvalidHandshake','InvalidMessage'):record(g,0,probe,transport_failure=True)
  raise

def confirm_websocket(ws):
 if isinstance(ws,_Socket):ws.confirm()

# Shared credit reservation extension. Policy is explicitly provisioned while
# denied; no process restart, timer, or missing state creates an allowance.
BUDGET_MAX_BYTES = 262144
BUDGET_MAX_SCOPES = 128
BUDGET_MAX_KEYS = 512
ROLES = ('listener', 'creator_resolution', 'walkback')

def _digest(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _policy(p):
 if isinstance(p,dict) and p.get('version')==2:return _policy2(p)
 if not isinstance(p,dict) or p.get('version')!=1:raise Integrity('HELIUS_BUDGET_POLICY')
 required=('minute','hour','day','inflight','scope_credits','scope_requests','scope_retries','scope_pages','scope_fallbacks')
 if any(type(p.get(k)) is not int or not 0<p[k]<=10**9 for k in required):raise Integrity('HELIUS_BUDGET_POLICY')
 if not p['minute']<=p['hour']<=p['day'] or p['inflight']>32:raise Integrity('HELIUS_BUDGET_POLICY')
 if set(p.get('workers',{}))!=set(ROLES) or any(type(v) is not int or not 0<v<=p['day'] for v in p['workers'].values()):raise Integrity('HELIUS_BUDGET_WORKERS')
 if sum(p['workers'].values())>p['day']:raise Integrity('HELIUS_BUDGET_WORKERS')
 # Costs must be exact selector entries bound to operator-verified billing
 # evidence. No telemetry estimate, wildcard price, or unknown default is used.
 costs=p.get('costs')
 if not isinstance(costs,dict) or not costs or len(costs)>64:raise Integrity('HELIUS_COST_AUTHORITY')
 for k,v in costs.items():
  if not isinstance(k,str) or len(k)>128 or not isinstance(v,dict):raise Integrity('HELIUS_COST_AUTHORITY')
  if set(v)!=set(('credits','freshness_seconds','evidence_sha256')):raise Integrity('HELIUS_COST_AUTHORITY')
  if type(v['credits']) is not int or not 0<v['credits']<=p['scope_credits']:raise Integrity('HELIUS_COST_AUTHORITY')
  if type(v['freshness_seconds']) is not int or not 0<=v['freshness_seconds']<=86400:raise Integrity('HELIUS_COST_AUTHORITY')
  if not isinstance(v['evidence_sha256'],str) or len(v['evidence_sha256'])!=64 or any(x not in '0123456789abcdef' for x in v['evidence_sha256']):raise Integrity('HELIUS_COST_AUTHORITY')
 if len(json.dumps(p))>16384:raise Integrity('HELIUS_BUDGET_POLICY')
 return p

def _budget(self,s):
 b=s.get('rpc_budget')
 if isinstance(b,dict) and b.get('version')==2:return _budget2(self,s)
 if not isinstance(b,dict) or b.get('version')!=1:raise Integrity('HELIUS_RPC_BUDGET_REQUIRED')
 _policy(b['policy'])
 if b['policy_sha256']!=_digest(b['policy']):raise Integrity('HELIUS_BUDGET_POLICY_HASH')
 if len(b['scopes'])>BUDGET_MAX_SCOPES or len(b['keys'])>BUDGET_MAX_KEYS or len(b['inflight'])>32:raise Integrity('HELIUS_BUDGET_STATE')
 # Corrupt checksummed state is still not accepted as accounting authority.
 for name in ('wall','mono'):
  if type(b.get(name)) is not int or b[name]<0:raise Integrity('HELIUS_BUDGET_CLOCK')
 if set(b['windows'])!=set(('minute','hour','day')):raise Integrity('HELIUS_BUDGET_STATE')
 for w in b['windows'].values():
  if set(w)!=set(('start','credits','workers','previous','previous_workers')) or type(w['start']) is not int or type(w['credits']) is not int or w['credits']<0:raise Integrity('HELIUS_BUDGET_STATE')
  if set(w['workers'])!=set(ROLES) or any(type(v) is not int or v<0 for v in w['workers'].values()) or sum(w['workers'].values())!=w['credits']:raise Integrity('HELIUS_BUDGET_STATE')
  if type(w.get('previous')) is not int or w['previous']<0 or set(w.get('previous_workers',{}))!=set(ROLES) or any(type(v) is not int or v<0 for v in w['previous_workers'].values()) or sum(w['previous_workers'].values())!=w['previous']:raise Integrity('HELIUS_BUDGET_STATE')
 for q in b['scopes'].values():
  if set(q)!=set(('credits','requests','retries','pages','fallbacks','origin')):raise Integrity('HELIUS_BUDGET_STATE')
  if any(type(q[n]) is not int or q[n]<0 for n in ('credits','requests','retries','pages','fallbacks')):raise Integrity('HELIUS_BUDGET_STATE')
 for key,v in b['keys'].items():
  if len(key)!=64 or v.get('status') not in ('INFLIGHT','HTTP_COMPLETED','DETERMINISTIC_FAILURE','RETRYABLE'):raise Integrity('HELIUS_BUDGET_STATE')
  if type(v.get('attempts')) is not int or v['attempts']<1 or type(v.get('at')) is not int:raise Integrity('HELIUS_BUDGET_STATE')
 for ticket,v in b['inflight'].items():
  if len(ticket)!=32 or v.get('key') not in b['keys'] or b['keys'][v['key']]['status']!='INFLIGHT':raise Integrity('HELIUS_BUDGET_STATE')
 return b

def _tick(self,b):
 now=int(self.clock());mono=int(self.monotonic())
 if now<b['wall'] or mono<b['mono'] or abs((now-b['wall'])-(mono-b['mono']))>5:raise Integrity('HELIUS_BUDGET_CLOCK_AMBIGUOUS')
 b['wall']=now;b['mono']=mono
 return now

def _configure_budget(self,policy,expected_sha256):
 if isinstance(policy,dict) and policy.get('version')==2:return _configure2(self,policy,expected_sha256)
 _policy(policy)
 with self.locked():
  s=self._writer_read()
  if s['state']!='PAYMENT_DENIED' or 'rpc_budget' in s or hashlib.sha256(self.path.read_bytes()).hexdigest()!=expected_sha256:raise Integrity('HELIUS_BUDGET_CONFIGURATION_AUTHORITY')
  now=int(self.clock())
  s['rpc_budget']={'version':1,'policy':policy,'policy_sha256':_digest(policy),'wall':now,'mono':int(self.monotonic()),'windows':{k:{'start':now,'credits':0,'workers':{r:0 for r in ROLES},'previous':0,'previous_workers':{r:0 for r in ROLES}} for k in ('minute','hour','day')},'scopes':{},'keys':{},'inflight':{}}
  self.save(s)

def _reserve_http(self,*,role,scopes,key,selector,origin,page=False):
 with self.locked():
  s=self._writer_read()
  if s.get('rpc_budget',{}).get('version')==2:return _reserve2_locked(self,s,role=role,scopes=scopes,key=key,selector=selector,origin=origin,page=page)
  if s['state'] in ('PAYMENT_DENIED','AUTH_DENIED'):raise BudgetDeferred('HELIUS_ACQUISITION_DEFERRED')
  b=_budget(self,s);now=_tick(self,b);p=b['policy']
  if role not in ROLES or not scopes or len(scopes)>4 or any(not isinstance(x,str) or len(x)!=64 for x in scopes):raise Integrity('HELIUS_INVESTIGATION_REQUIRED')
  if len(key)!=64 or len(origin)!=64:raise Integrity('HELIUS_REQUEST_IDENTITY')
  if selector not in p['costs']:raise BudgetDeferred('HELIUS_UNKNOWN_REQUEST_COST')
  if now<s['next_at'] or now<s['probe_until']:raise BudgetDeferred('HELIUS_ACQUISITION_DEFERRED')
  cost=p['costs'][selector]['credits'];prior=b['keys'].get(key)
  if prior:
   if prior['status']=='INFLIGHT':raise BudgetDeferred('HELIUS_DUPLICATE_INFLIGHT')
   if prior['status']=='DETERMINISTIC_FAILURE':raise BudgetDeferred('HELIUS_DETERMINISTIC_FAILURE')
   ttl=p['costs'][selector]['freshness_seconds']
   if prior['status']=='HTTP_COMPLETED' and now-prior['at']<max(60,ttl):raise BudgetDeferred('HELIUS_REQUEST_COOLDOWN')
  if len(b['inflight'])>=p['inflight']:raise BudgetDeferred('HELIUS_INFLIGHT_LIMIT')
  if key not in b['keys'] and len(b['keys'])>=BUDGET_MAX_KEYS:raise BudgetDeferred('HELIUS_REQUEST_IDENTITY_CAPACITY')
  if len(set(scopes)-set(b['scopes']))+len(b['scopes'])>BUDGET_MAX_SCOPES:raise BudgetDeferred('HELIUS_INVESTIGATION_CAPACITY')
  for name,duration in [('minute',60),('hour',3600),('day',86400)]:
   w=b['windows'][name]
   elapsed=now-w['start']
   if elapsed>=duration:
    previous=w['credits'] if elapsed<2*duration else 0
    workers=dict(w['workers']) if elapsed<2*duration else {r:0 for r in ROLES}
    w.update(start=w['start']+(elapsed//duration)*duration,credits=0,workers={r:0 for r in ROLES},previous=previous,previous_workers=workers)
   if w['credits']+w['previous']+cost>p[name]:raise BudgetDeferred('HELIUS_GLOBAL_'+name.upper()+'_LIMIT')
   if name=='day' and w['workers'][role]+w['previous_workers'][role]+cost>p['workers'][role]:raise BudgetDeferred('HELIUS_WORKER_LIMIT')
  retry=int(prior is not None and prior['status'] in ('RETRYABLE','HTTP_COMPLETED'))
  for scope in set(scopes):
   q=b['scopes'].setdefault(scope,dict(credits=0,requests=0,retries=0,pages=0,fallbacks=0,origin=origin))
   increments={'credits':cost,'requests':1,'retries':retry,'pages':int(page),'fallbacks':int(origin!=q['origin'])}
   for n,add in increments.items():
    if q[n]+add>p['scope_'+n]:raise BudgetDeferred('HELIUS_INVESTIGATION_'+n.upper()+'_LIMIT')
   for n,add in increments.items():q[n]+=add
  for w in b['windows'].values():w['credits']+=cost;w['workers'][role]+=cost
  ticket=uuid.uuid4().hex;probe=s['state']!='OPEN'
  if probe:s.update(probe_until=now+120,next_at=now+300,recover_until=now+300)
  elif now<s['recover_until']:s['next_at']=now+2
  b['keys'][key]={'attempts':(prior['attempts'] if prior else 0)+1,'status':'INFLIGHT','at':now,'selector':selector,'role':role,'pid':os.getpid(),'credits':cost+(prior.get('credits',0) if prior else 0)}
  b['inflight'][ticket]={'key':key,'role':role,'credits':cost,'probe':probe}
  s['reservations']+=1;s['methods']['http_budget']=s['methods'].get('http_budget',0)+1
  self.save(s)  # No network dispatch until state AND directory fsync return.
  return ticket

def _finish_http(self,ticket,status,*,transport_failure=False):
 # Outcomes do not refund credits. A crashed/ambiguous reservation is never
 # timed out or released automatically; it remains charged and in-flight.
 with self.locked(blocking=True):
  s=self._writer_read();b=_budget(self,s);now=_tick(self,b)
  v=b['inflight'].get(ticket)
  if v is None:return  # duplicate local close/read completion
  key=b['keys'][v['key']];key['status']='HTTP_COMPLETED' if status==200 else 'DETERMINISTIC_FAILURE' if status in (400,422) else 'RETRYABLE';key['at']=now
  del b['inflight'][ticket];s['outcomes']+=1
  label=str(status) if status in (200,400,401,402,403,404,422,429) else 'other'
  # Keep legacy bounded status buckets.
  label=label if label in ('200','401','402','403','429') else 'other'
  s['statuses'][label]=s['statuses'].get(label,0)+1
  if s['state'] not in ('PAYMENT_DENIED','AUTH_DENIED'):
   if status==402:s.update(state='PAYMENT_DENIED',probe_until=0)
   elif status in (401,403):s.update(state='AUTH_DENIED',probe_until=0)
   elif status==429:s.update(state='RATE_LIMITED',next_at=max(s['next_at'],now+60),probe_until=0)
   elif transport_failure or status>=500:
    s['failures']+=1
    if s['failures']>=3 or v['probe']:s.update(state='OUTAGE',next_at=max(s['next_at'],now+300),probe_until=0)
   elif status==200 and v['probe']:s.update(state='OPEN',failures=0,probe_until=0,next_at=now+2)
  self.save(s)

Gate.monotonic=staticmethod(time.monotonic)
Gate.configure_budget=_configure_budget
Gate.reserve_http=_reserve_http
Gate.finish_http=_finish_http
Gate.has_budget=lambda self:'rpc_budget' in self.read()


def _notice_http(self,ticket,status,*,retry_after=60):
 # A denial is authoritative as soon as response headers arrive. Do not wait
 # for a body, and do not release the in-flight slot before local completion.
 if status not in (401,402,403,429):return
 with self.locked(blocking=True):
  s=self._writer_read();b=_budget(self,s);now=_tick(self,b)
  if ticket not in b['inflight'] or s['state'] in ('PAYMENT_DENIED','AUTH_DENIED'):return
  if status==402:s.update(state='PAYMENT_DENIED',probe_until=0)
  elif status in (401,403):s.update(state='AUTH_DENIED',probe_until=0)
  else:s.update(state='RATE_LIMITED',next_at=max(s['next_at'],now+min(900,max(60,int(retry_after)))),probe_until=0)
  self.save(s)

Gate.notice_http=_notice_http

# Version 2: originating investigations, not aggregate hourly/daily spending.
INVESTIGATION_STATE_BYTES = 16 * 1024 * 1024
INVESTIGATION_MAX_ROOTS = 8192
INVESTIGATION_MAX_IDENTITIES = 32768
INVESTIGATION_MAX_REQUESTS = 32768

def _policy2(p):
 expected={'version','lifetime_credits','warning_credits','requests_per_minute','inflight','costs'}
 if set(p)!=expected or p.get('version')!=2:raise Integrity('HELIUS_INVESTIGATION_POLICY')
 if p['lifetime_credits']!=100000 or p['warning_credits']!=50000:raise Integrity('HELIUS_INVESTIGATION_ALLOWANCE')
 if type(p['requests_per_minute']) is not int or not 1<=p['requests_per_minute']<=6000:raise Integrity('HELIUS_REQUEST_RATE_POLICY')
 if type(p['inflight']) is not int or not 1<=p['inflight']<=32:raise Integrity('HELIUS_INFLIGHT_POLICY')
 # Reuse the exact cost-entry validation; no price wildcard or guessed default.
 legacy=dict(version=1,minute=100000,hour=100000,day=300000,inflight=p['inflight'],scope_credits=100000,scope_requests=100000,scope_retries=100000,scope_pages=100000,scope_fallbacks=100000,workers={r:100000 for r in ROLES},costs=p['costs'])
 _policy(legacy)
 return p

def _budget2(self,s):
 b=s['rpc_budget'];_policy2(b['policy'])
 if b.get('version')!=2 or b.get('policy_sha256')!=_digest(b['policy']):raise Integrity('HELIUS_INVESTIGATION_STATE')
 for field,cap in [('scopes',INVESTIGATION_MAX_ROOTS),('owners',INVESTIGATION_MAX_IDENTITIES),('keys',INVESTIGATION_MAX_REQUESTS),('inflight',32)]:
  if not isinstance(b.get(field),dict) or len(b[field])>cap:raise Integrity('HELIUS_INVESTIGATION_STATE')
 for field in ('wall','mono'):
  if type(b.get(field)) is not int or b[field]<0:raise Integrity('HELIUS_BUDGET_CLOCK')
 if set(b['rate'])!={'start','count','previous'} or any(type(v) is not int or v<0 for v in b['rate'].values()):raise Integrity('HELIUS_RATE_STATE')
 for root,q in b['scopes'].items():
  if len(root)!=64 or set(q)!={'credits','requests','retries','pages','fallbacks','warned','workers','origin'}:raise Integrity('HELIUS_ROOT_STATE')
  if any(type(q[n]) is not int or q[n]<0 for n in ('credits','requests','retries','pages','fallbacks')):raise Integrity('HELIUS_ROOT_STATE')
  if q['credits']>100000 or type(q['warned']) is not bool or q['warned']!=(q['credits']>=50000):raise Integrity('HELIUS_ROOT_STATE')
  if set(q['workers'])!=set(ROLES) or any(type(v) is not int or v<0 for v in q['workers'].values()) or sum(q['workers'].values())!=q['credits']:raise Integrity('HELIUS_ROOT_STATE')
  if root not in b['owners'] or b['owners'][root]['root']!=root:raise Integrity('HELIUS_ROOT_AUTHORITY')
 for alias,owner in b['owners'].items():
  if len(alias)!=64 or set(owner)!={'root','parent','proof'} or owner['root'] not in b['scopes'] or owner['parent'] not in b['owners'] or len(owner['proof'])!=64:raise Integrity('HELIUS_OWNER_STATE')
  if b['owners'][owner['parent']]['root']!=owner['root']:raise Integrity('HELIUS_OWNER_STATE')
 for key,v in b['keys'].items():
  if len(key)!=64 or v.get('status') not in ('INFLIGHT','HTTP_COMPLETED','DETERMINISTIC_FAILURE','RETRYABLE','OWNER_EXIT_UNCERTAIN') or type(v.get('attempts')) is not int or v['attempts']<1 or type(v.get('at')) is not int:raise Integrity('HELIUS_REQUEST_STATE')
  if v.get('status')=='OWNER_EXIT_UNCERTAIN':
   receipt=v.get('owner_exit')
   if not isinstance(receipt,dict) or set(receipt)!={'ticket','root','pid','original_at','retired_at','reason'} or len(receipt.get('ticket',''))!=32 or receipt.get('root') not in b['scopes'] or receipt.get('pid')!=v.get('pid') or receipt.get('original_at')!=v['at'] or type(receipt.get('retired_at')) is not int or receipt['retired_at']<v['at'] or receipt.get('reason')!='PROCESS_GONE_OUTCOME_UNKNOWN':raise Integrity('HELIUS_OWNER_EXIT_EVIDENCE')
 for ticket,v in b['inflight'].items():
  if len(ticket)!=32 or v.get('key') not in b['keys'] or b['keys'][v['key']]['status']!='INFLIGHT' or v.get('root') not in b['scopes']:raise Integrity('HELIUS_INFLIGHT_STATE')
 return b

def _configure2(self,policy,expected_sha256):
 _policy2(policy)
 with self.locked():
  s=self._writer_read()
  if s['state']!='PAYMENT_DENIED' or 'rpc_budget' in s or hashlib.sha256(self.path.read_bytes()).hexdigest()!=expected_sha256:raise Integrity('HELIUS_BUDGET_CONFIGURATION_AUTHORITY')
  now=int(self.clock());s['rpc_budget']=dict(version=2,policy=policy,policy_sha256=_digest(policy),wall=now,mono=int(self.monotonic()),rate=dict(start=now,count=0,previous=0),scopes={},owners={},keys={},inflight={})
  self.save(s)

def _register_origin(self,identity,proof):
 # Trusted identity-owner callback only. HTTP callers cannot create roots by
 # supplying an investigation ID: they must resolve a pre-existing binding.
 if not isinstance(identity,str) or not identity.startswith('mint:') or not 5<len(identity)<=133 or not isinstance(proof,str) or len(proof)!=64:raise Integrity('HELIUS_ORIGIN_PROOF')
 alias=hashlib.sha256(identity.encode()).hexdigest()
 with self.locked():
  s=self._writer_read();b=_budget2(self,s)
  if alias in b['owners']:return b['owners'][alias]['root']
  if len(b['scopes'])>=INVESTIGATION_MAX_ROOTS or len(b['owners'])>=INVESTIGATION_MAX_IDENTITIES:raise BudgetDeferred('HELIUS_ORIGIN_CAPACITY')
  b['owners'][alias]=dict(root=alias,parent=alias,proof=proof)
  b['scopes'][alias]=dict(credits=0,requests=0,retries=0,pages=0,fallbacks=0,warned=False,workers={r:0 for r in ROLES},origin='')
  self.save(s);return alias

def _bind_child(self,parent,child,proof):
 if any(not isinstance(v,str) or len(v)!=64 for v in (parent,child,proof)):raise Integrity('HELIUS_CHILD_PROOF')
 with self.locked():
  s=self._writer_read();b=_budget2(self,s)
  if parent not in b['owners']:raise BudgetDeferred('HELIUS_UNATTRIBUTED_PARENT')
  root=b['owners'][parent]['root']
  if child in b['owners']:
   if b['owners'][child]['root']!=root:raise Integrity('HELIUS_OWNER_REBIND_REFUSED')
   return root  # Keep the original parent/proof; never rewrite history.
  if len(b['owners'])>=INVESTIGATION_MAX_IDENTITIES:raise BudgetDeferred('HELIUS_CHILD_IDENTITY_CAPACITY')
  b['owners'][child]=dict(root=root,parent=parent,proof=proof);self.save(s);return root

def _owner(self,alias):
 with self.locked():
  b=_budget2(self,self.read())
  if alias not in b['owners']:raise BudgetDeferred('HELIUS_UNATTRIBUTED_INVESTIGATION')
  return b['owners'][alias]['root']

def _reserve2_locked(self,s,*,role,scopes,key,selector,origin,page=False):
 if s['state'] in ('PAYMENT_DENIED','AUTH_DENIED'):raise BudgetDeferred('HELIUS_ACQUISITION_DEFERRED')
 b=_budget2(self,s);now=_tick(self,b);p=b['policy']
 if role not in ROLES or not scopes or len(scopes)>4:raise BudgetDeferred('HELIUS_UNATTRIBUTED_INVESTIGATION')
 if any(alias not in b['owners'] for alias in scopes):raise BudgetDeferred('HELIUS_UNATTRIBUTED_INVESTIGATION')
 roots={b['owners'][alias]['root'] for alias in scopes}
 if len(roots)!=1:raise Integrity('HELIUS_AMBIGUOUS_ORIGIN')
 root=next(iter(roots));q=b['scopes'][root]
 if len(key)!=64 or len(origin)!=64:raise Integrity('HELIUS_REQUEST_IDENTITY')
 if selector not in p['costs']:raise BudgetDeferred('HELIUS_UNKNOWN_REQUEST_COST')
 if now<s['next_at'] or now<s['probe_until']:raise BudgetDeferred('HELIUS_ACQUISITION_DEFERRED')
 cost=p['costs'][selector]['credits'];prior=b['keys'].get(key)
 if q['credits']+cost>100000:raise BudgetDeferred('HELIUS_INVESTIGATION_CREDIT_LIMIT')
 if prior:
  if prior['status']=='INFLIGHT':raise BudgetDeferred('HELIUS_DUPLICATE_INFLIGHT')
  if prior['status']=='OWNER_EXIT_UNCERTAIN':raise BudgetDeferred('HELIUS_UNCERTAIN_REQUEST_REQUIRES_REVIEW')
  if prior['status']=='DETERMINISTIC_FAILURE':raise BudgetDeferred('HELIUS_DETERMINISTIC_FAILURE')
  if prior['status']=='HTTP_COMPLETED' and now-prior['at']<max(60,p['costs'][selector]['freshness_seconds']):raise BudgetDeferred('HELIUS_REQUEST_COOLDOWN')
 if len(b['inflight'])>=p['inflight']:raise BudgetDeferred('HELIUS_INFLIGHT_LIMIT')
 if key not in b['keys'] and len(b['keys'])>=INVESTIGATION_MAX_REQUESTS:raise BudgetDeferred('HELIUS_REQUEST_IDENTITY_CAPACITY')
 rate=b['rate'];elapsed=now-rate['start']
 if elapsed>=60:rate.update(start=rate['start']+(elapsed//60)*60,count=0,previous=rate['count'] if elapsed<120 else 0)
 if rate['count']+rate['previous']+1>p['requests_per_minute']:raise BudgetDeferred('HELIUS_REQUEST_RATE_LIMIT')
 rate['count']+=1
 warn=not q['warned'] and q['credits']+cost>=50000
 q['credits']+=cost;q['requests']+=1;q['workers'][role]+=cost;q['pages']+=int(page)
 q['retries']+=int(prior is not None);q['fallbacks']+=int(bool(q['origin']) and q['origin']!=origin);q['origin']=q['origin'] or origin;q['warned']=q['credits']>=50000
 ticket=uuid.uuid4().hex;probe=s['state']!='OPEN'
 if probe:s.update(probe_until=now+120,next_at=now+300,recover_until=now+300)
 elif now<s['recover_until']:s['next_at']=now+2
 b['keys'][key]=dict(attempts=(prior['attempts'] if prior else 0)+1,status='INFLIGHT',at=now,selector=selector,role=role,pid=os.getpid(),credits=cost+(prior.get('credits',0) if prior else 0))
 b['inflight'][ticket]=dict(key=key,root=root,role=role,credits=cost,probe=probe)
 s['reservations']+=1;s['methods']['http_budget']=s['methods'].get('http_budget',0)+1
 self.save(s)
 if warn:
  import logging
  try:logging.getLogger(__name__).warning('HELIUS_INVESTIGATION_WARNING root=%s reserved_credits=%s ceiling=100000',root,q['credits'])
  except Exception:pass  # Durable warned state remains queryable after restart.
 return ticket

Gate.register_origin=_register_origin
Gate.bind_child=_bind_child
Gate.investigation_owner=_owner


# Keep the v1 reader for recovery/reconciliation of retained prototype state,
# but do not permit its hourly/daily policy to be newly provisioned.
_configure_legacy_budget=_configure_budget

def _configure_current_budget(self,policy,expected_sha256):
 if not isinstance(policy,dict) or policy.get('version')!=2:raise Integrity('HELIUS_INVESTIGATION_POLICY_V2_REQUIRED')
 return _configure2(self,policy,expected_sha256)

Gate.configure_budget=_configure_current_budget


def _budget_version(self):
 with self.locked():return self.read().get('rpc_budget',{}).get('version',0)

Gate.budget_version=_budget_version
Gate.has_budget=lambda self:bool(self.budget_version())



def _reconcile_terminated_http(self,ticket,*,expected_sha256,expected_pid):
 # Explicit offline/operator action, never invoked by a timer, dispatch, or
 # startup. Process absence is necessary; age/timeout alone grants nothing.
 if not isinstance(ticket,str) or len(ticket)!=32 or type(expected_pid) is not int or expected_pid<=1 or not isinstance(expected_sha256,str) or len(expected_sha256)!=64:raise Integrity('HELIUS_RETIREMENT_AUTHORITY')
 with self.locked():
  state=self._writer_read()
  if hashlib.sha256(self.path.read_bytes()).hexdigest()!=expected_sha256:raise Integrity('HELIUS_RETIREMENT_STATE_CHANGED')
  if state.get('rpc_budget',{}).get('version')!=2:raise Integrity('HELIUS_RETIREMENT_VERSION')
  budget=_budget2(self,state);entry=budget['inflight'].get(ticket)
  if entry is None:
   prior=next((value for value in budget['keys'].values() if value.get('status')=='OWNER_EXIT_UNCERTAIN' and value.get('owner_exit',{}).get('ticket')==ticket),None)
   if prior and prior['pid']==expected_pid:return 'ALREADY_RECONCILED'
   raise Integrity('HELIUS_RETIREMENT_TICKET_UNKNOWN')
  key=budget['keys'][entry['key']]
  if key.get('pid')!=expected_pid or key['status']!='INFLIGHT':raise Integrity('HELIUS_RETIREMENT_OWNER_MISMATCH')
  try:os.kill(expected_pid,0)
  except ProcessLookupError:pass
  except OSError:raise Integrity('HELIUS_RETIREMENT_OWNER_UNVERIFIED') from None
  else:raise Integrity('HELIUS_RETIREMENT_OWNER_STILL_PRESENT')
  now=_tick(self,budget)
  key['status']='OWNER_EXIT_UNCERTAIN'
  key['owner_exit']=dict(ticket=ticket,root=entry['root'],pid=expected_pid,original_at=key['at'],retired_at=now,reason='PROCESS_GONE_OUTCOME_UNKNOWN')
  del budget['inflight'][ticket]
  # No credit refund, retry reset, HTTP outcome, success, canonical ACK or
  # allowance reset is inferred. The original request remains paid-blocked.
  self.save(state)
  return 'RECONCILED_UNCERTAIN_NO_REFUND'

Gate.reconcile_terminated_http=_reconcile_terminated_http
