"""Opt-in, bounded shared Helius denial state; no providers or implicit init.
Reservations precede dispatch; pre-existing in-flight requests remain ambiguous.
Ordinary 429 is rate limiting, never asserted to prove credit exhaustion.
"""
import contextlib,fcntl,hashlib,json,os,stat,time,uuid
from pathlib import Path
from urllib.parse import urlsplit
FLAG='FLEX_HELIUS_DENIAL_GATE_ENABLED'
MAX_BYTES=4096
class Deferred(RuntimeError):pass
class Integrity(Deferred):pass

def enabled():
 v=os.environ.get(FLAG,'0')
 if v not in ('0','1'):raise Integrity('HELIUS_GATE_FLAG')
 journal=os.environ.get('MIGRATION_JOURNAL_PATH')
 stable=Path(journal).parent/'helius-denial-v1.json' if journal and os.path.isabs(journal) else None
 if v=='0' and (os.environ.get('HELIUS_DENIAL_GATE_PATH') or (stable is not None and stable.exists())):raise Integrity('HELIUS_GATE_DOWNGRADE')
 return v=='1'
def helius(url):
 host=(urlsplit(url).hostname or '').lower()
 return host=='helius-rpc.com' or host.endswith('.helius-rpc.com') or host=='helius.xyz' or host.endswith('.helius.xyz')
def authority():
 from src.core.paired_binding_guard import validate_pair
 return validate_pair(core_path=os.environ['DB_PATH'],ops_path=os.environ['WT_OPS_DB_PATH'],purpose='active')
class Gate:
 def __init__(self,path,generation,clock=time.time):
  self.path=Path(path);self.generation=generation;self.clock=clock
  if not self.path.is_absolute() or str(self.path.resolve())!=str(self.path) or not generation:raise Integrity('HELIUS_GATE_AUTHORITY')
 @contextlib.contextmanager
 def locked(self):
  fd=os.open(str(self.path)+'.lock',os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK)
  try:
   info=os.fstat(fd)
   if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>128:raise Integrity('HELIUS_GATE_LOCK')
   try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:raise Deferred('HELIUS_GATE_BUSY') from None
   yield
  finally:os.close(fd)
 def read(self):
  lock=os.open(str(self.path)+'.lock',os.O_RDONLY|os.O_NOFOLLOW)
  try:
   published=os.read(lock,129)
   if not published.startswith(b'READY_V1 '):raise Integrity('HELIUS_GATE_PUBLICATION_AMBIGUOUS')
  finally:os.close(lock)
  fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
  try:
   i=os.fstat(fd)
   if not stat.S_ISREG(i.st_mode) or i.st_nlink!=1 or i.st_size>MAX_BYTES:raise Integrity('HELIUS_GATE_STATE')
   raw=os.read(fd,MAX_BYTES+1)
   if published!=b'READY_V1 '+hashlib.sha256(raw).hexdigest().encode()+b'\n':raise Integrity('HELIUS_GATE_PUBLICATION_AMBIGUOUS')
   def unique(items):
    out={}
    for key,value in items:
     if key in out:raise Integrity('HELIUS_GATE_STATE')
     out[key]=value
    return out
   s=json.loads(raw,object_pairs_hook=unique)
  except (ValueError,OSError):raise Integrity('HELIUS_GATE_STATE') from None
  finally:os.close(fd)
  if not isinstance(s,dict):raise Integrity('HELIUS_GATE_STATE')
  if s.get('version')!=1 or s.get('generation')!=self.generation or s.get('state') not in ('OPEN','RATE_LIMITED','OUTAGE','PAYMENT_DENIED','AUTH_DENIED'):raise Integrity('HELIUS_GATE_STATE')
  for k in ('reservations','outcomes','failures','probe_until','next_at','recover_until'):
   if type(s.get(k)) is not int or not 0<=s[k]<2**63:raise Integrity('HELIUS_GATE_STATE')
  if not isinstance(s.get('methods'),dict) or len(s['methods'])>8 or any(type(v) is not int or v<0 for v in s['methods'].values()):raise Integrity('HELIUS_GATE_COUNTERS')
  if not isinstance(s.get('statuses'),dict) or len(s['statuses'])>8 or any(type(v) is not int or v<0 for v in s['statuses'].values()):raise Integrity('HELIUS_GATE_COUNTERS')
  return s
 def save(self,s):
  # Persist ambiguity before publishing; interrupted fsync/rename cannot be
  # mistaken for fresh allowance after restart.
  lock=os.open(str(self.path)+'.lock',os.O_WRONLY|os.O_NOFOLLOW)
  def marker(value):
   os.lseek(lock,0,0)
   if os.write(lock,value)!=len(value):raise Integrity('HELIUS_GATE_SHORT_WRITE')
   os.ftruncate(lock,len(value));os.fsync(lock)
  try:marker(b'PENDING_V1\n')
  finally:os.close(lock)
  if os.statvfs(self.path.parent).f_bavail*os.statvfs(self.path.parent).f_frsize<2067108864:raise Integrity('DISK_FLOOR')
  b=(json.dumps(s,sort_keys=True)+'\n').encode()
  if len(b)>MAX_BYTES:raise Integrity('HELIUS_GATE_CAPACITY')
  temp=self.path.with_name(self.path.name+'.stage')
  fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  try:
   n=0
   while n<len(b):
    w=os.write(fd,b[n:])
    if w<=0:raise Integrity('HELIUS_GATE_SHORT_WRITE')
    n+=w
   os.fsync(fd)
  finally:os.close(fd)
  os.replace(temp,self.path);fd=os.open(self.path.parent,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)
  lock=os.open(str(self.path)+'.lock',os.O_WRONLY|os.O_NOFOLLOW)
  try:marker(b'READY_V1 '+hashlib.sha256(b).hexdigest().encode()+b'\n')
  finally:os.close(lock)
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
   s=self.read()
   if hashlib.sha256(self.path.read_bytes()).hexdigest()!=expected_sha256:raise Integrity('HELIUS_RESTORE_AUTHORITY_CHANGED')
   s.update(state='OUTAGE',probe_until=0,next_at=int(self.clock()),recover_until=int(self.clock())+300)
   self.save(s)
 def blocked(self):
  with self.locked():
   s=self.read();now=int(self.clock())
   return s['state'] in ('PAYMENT_DENIED','AUTH_DENIED') or now<s['next_at'] or now<s['probe_until']
 def reserve(self,method="other"):
  with self.locked():
   s=self.read();now=int(self.clock())
   if s['state'] in ('PAYMENT_DENIED','AUTH_DENIED'):raise Deferred('HELIUS_ACQUISITION_DEFERRED')
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
   s=self.read();now=int(self.clock());s['outcomes']+=1
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
 return Gate(path,authority())
def blocked():
 try:g=current()
 except Integrity:return True
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
 url=request.full_url;g=current() if enabled() and helius(url) else None;probe=reserve_sync(g,json.loads(request.data).get('method','other')) if g else False
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
  async with connect(url,**kwargs) as ws:
   yield _Socket(ws,g,probe) if g else ws
 except Exception as exc:
  status=getattr(exc,'status_code',None) or getattr(getattr(exc,'response',None),'status_code',None)
  if status in (401,402,403,429) or (type(status) is int and status>=500):record(g,status,probe)
  elif isinstance(exc,(OSError,TimeoutError)) or type(exc).__name__ in ('ConnectionClosed','ConnectionClosedError','ConnectionClosedOK','InvalidHandshake','InvalidMessage'):record(g,0,probe,transport_failure=True)
  raise

def confirm_websocket(ws):
 if isinstance(ws,_Socket):ws.confirm()
