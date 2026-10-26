"""Process-wide HTTP enforcement for the accepted Python transports.

No network or state initialization on import. Explicit installation precedes
worker acquisition. Unknown Helius TLS clients are refused. No keys, bodies or
URLs are persisted: request identity is a digest, distinct from provenance.
"""
import contextlib,contextvars,functools,hashlib,inspect,json,ssl,threading
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from src.core import helius_denial_gate as gate

_scope=contextvars.ContextVar('helius_scope',default=())
_transport=contextvars.ContextVar('helius_covered_transport',default=False)
_role=None
_installed=False
_originals=[]
_install_lock=threading.RLock()

def _hash(value):return hashlib.sha256(value.encode()).hexdigest()

@contextlib.contextmanager
def investigation(identity):
 if not isinstance(identity,str) or not identity or len(identity)>512:raise gate.Integrity('HELIUS_INVESTIGATION_REQUIRED')
 alias=_hash(identity);scopes=_scope.get();g=gate.current() if gate.enabled() else None
 if g and g.budget_version()==2:
  if scopes:
   root=g.investigation_owner(scopes[0])
   alias=_hash(identity) if identity.startswith('mint:') else _hash('CHILD_SCOPE_V2:'+root+':'+identity)
   g.bind_child(scopes[0],alias,_hash('CHILD_SCOPE_V2:'+scopes[0]+':'+identity))
  else:g.investigation_owner(alias)  # Never creates a root from a caller ID.
  scopes=(alias,)
 else:
  scopes=tuple(sorted(set(scopes)|{alias}))
  if len(scopes)>4:raise gate.Integrity('HELIUS_SCOPE_DEPTH')
 token=_scope.set(scopes)
 try:yield
 finally:_scope.reset(token)


def core_identity_proof(mint):
 import os,sqlite3
 from pathlib import Path
 path=os.environ.get('DB_PATH')
 if not path or not Path(path).is_absolute():raise gate.BudgetDeferred('HELIUS_ORIGIN_AUTHORITY_MISSING')
 # current() validates the active matched pair before this indexed lookup.
 try:
  with contextlib.closing(sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True,timeout=1)) as conn:
   conn.execute('PRAGMA query_only=ON')
   row=conn.execute('SELECT mint FROM token_analysis WHERE mint=? LIMIT 1',(mint,)).fetchone()
 except sqlite3.Error:raise gate.BudgetDeferred('HELIUS_ORIGIN_AUTHORITY_UNAVAILABLE') from None
 if row is None:raise gate.BudgetDeferred('HELIUS_ORIGIN_UNKNOWN')
 return _hash('CORE_TOKEN_MINT_V1:solana-mainnet:'+mint)


@contextlib.contextmanager
def owned_context(identity):
 g=gate.current()
 if g and g.budget_version()==2 and not _scope.get():
  if identity.startswith('mint:'):
   mint=identity[5:];proof=core_identity_proof(mint);g.register_origin(identity,proof)
  elif identity.startswith('migration:'):
   import os
   from src.core.migration_signature_journal import MigrationJournal
   signature=identity[len('migration:'):]
   path=os.environ.get('MIGRATION_JOURNAL_PATH')
   if not path:raise gate.BudgetDeferred('HELIUS_ORIGIN_AUTHORITY_MISSING')
   row=next((row for row in MigrationJournal(path).snapshot() if row['signature']==signature),None)
   mint=(row.get('mint') or row.get('notification_mint')) if row else None
   if not mint:raise gate.BudgetDeferred('HELIUS_ORIGIN_UNKNOWN')
   proof=core_identity_proof(mint);parent='mint:'+mint;g.register_origin(parent,proof)
   # This is budget attribution to an admitted subject, never CREATE or
   # migration confirmation and never a fabricated mint for signature-only work.
   g.bind_child(_hash(parent),_hash(identity),_hash('ADMITTED_NOTIFICATION_V1:'+row['id']))
 with investigation(identity):yield

def scoped(role,parameter,prefix='mint'):
 def decorate(fn):
  signature=inspect.signature(fn)
  def identity(args,kwargs):
   value=signature.bind(*args,**kwargs).arguments[parameter]
   if parameter=='row':value=value['mint']
   return prefix+':'+str(value)
  def enter(args,kwargs):
   context=owned_context(identity(args,kwargs))
   try:context.__enter__()
   except gate.BudgetDeferred:return None
   return context
  if inspect.iscoroutinefunction(fn):
   @functools.wraps(fn)
   async def wrapped(*args,**kwargs):
    if not gate.enabled():return await fn(*args,**kwargs)
    install(role);context=enter(args,kwargs)
    try:return await fn(*args,**kwargs)
    finally:
     if context is not None:context.__exit__(None,None,None)
  else:
   @functools.wraps(fn)
   def wrapped(*args,**kwargs):
    if not gate.enabled():return fn(*args,**kwargs)
    install(role);context=enter(args,kwargs)
    try:return fn(*args,**kwargs)
    finally:
     if context is not None:context.__exit__(None,None,None)
  return wrapped
 return decorate

def _target(url):
 p=urlsplit(str(url))
 if p.scheme not in ('http','https') or not p.hostname:raise gate.Integrity('HELIUS_HTTP_TARGET')
 return p

def _admit(url,verb,body):
 p=_target(url)
 if not gate.helius(url):return None
 g=gate.current()
 if g is None:raise gate.Integrity('HELIUS_GATE_REQUIRED')
 if _role not in gate.ROLES or not _scope.get():raise gate.BudgetDeferred('HELIUS_INVESTIGATION_REQUIRED')
 if body is None:body=b''
 if isinstance(body,str):body=body.encode()
 if not isinstance(body,bytes) or len(body)>65536:raise gate.BudgetDeferred('HELIUS_UNBOUNDED_HTTP_BODY')
 try:data=json.loads(body) if body else None
 except (ValueError,UnicodeError):raise gate.BudgetDeferred('HELIUS_UNKNOWN_REQUEST_COST') from None
 query=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in ('api-key','apikey','api_key','token','authorization')]
 safe=urlunsplit((p.scheme,p.netloc.split('@')[-1],p.path,urlencode(sorted(query)),''))
 page=any(k.lower() in ('before','after','cursor','page','paginationtoken') for k,v in query)
 if isinstance(data,dict) and verb.upper()=='POST' and isinstance(data.get('method'),str):
  selector='rpc:'+data['method']
  # Dynamic-cost methods/options require their own verified selector policy.
  if data['method']=='getTransactionsForAddress':
   params=data.get('params',[])
   options=params[1] if len(params)>1 else {}
   if not isinstance(options,dict) or options.get('transactionDetails','signatures') not in ('signatures','full') or type(options.get('limit',1000)) is not int or not 1<=options.get('limit',1000)<=1000:raise gate.BudgetDeferred('HELIUS_UNKNOWN_REQUEST_COST')
   details=options.get('transactionDetails','signatures')
   selector+=':'+details
   if details=='full':selector+=':band'+str((options.get('limit',1000)+99)//100)
  if data['method']=='getSignatureStatuses':selector+=':history' if any(isinstance(v,dict) and v.get('searchTransactionHistory') for v in data.get('params',[])) else ':recent'
  payload={'method':data['method'],'params':data.get('params',[])}
  page=page or data['method']=='getSignaturesForAddress' or any(isinstance(v,dict) and any(k in v for k in ('before','after','cursor','page','paginationToken')) for v in payload['params'])
 elif isinstance(data,dict) and verb.upper()=='POST' and p.path.rstrip('/')=='/v0/transactions':
  transactions=data.get('transactions')
  if not isinstance(transactions,list) or not transactions or any(not isinstance(v,str) or not 0<len(v)<=128 for v in transactions):raise gate.BudgetDeferred('HELIUS_UNKNOWN_REQUEST_COST')
  # The policy must qualify this entire batch, not assume a flat per-item or
  # per-request billing rule. Unknown sizes refuse before any HTTP bytes.
  selector='rest:transaction-batch:'+str(len(transactions))
  payload=data
 elif data is None and verb.upper()=='GET':
  segments=p.path.strip('/').split('/')
  if len(segments)==4 and segments[:2]==['v0','addresses'] and segments[3]=='transactions':selector='rest:address-transactions';page=True
  else:raise gate.BudgetDeferred('HELIUS_UNKNOWN_REQUEST_COST')
  payload=None
 else:raise gate.BudgetDeferred('HELIUS_UNKNOWN_REQUEST_COST')
 key=_hash(json.dumps([verb.upper(),safe,payload],sort_keys=True,separators=(',',':')))
 origin=_hash(p.scheme+'://'+p.netloc.split('@')[-1])
 ticket=g.reserve_http(role=_role,scopes=_scope.get(),key=key,selector=selector,origin=origin,page=page)
 return (g,ticket)

def _notice(reservation,status,headers):
 if reservation:
  value=str(headers.get('Retry-After','60'))
  retry=int(value) if len(value)<=10 and value.isdigit() else 60
  reservation[0].notice_http(reservation[1],status,retry_after=retry)

def _finish(reservation,status,transport_failure=False):
 if reservation:reservation[0].finish_http(reservation[1],status,transport_failure=transport_failure)

def _patch(obj,name,value):
 _originals.append((obj,name,getattr(obj,name)));setattr(obj,name,value)

def _install(role):
 global _installed,_role
 if role not in gate.ROLES:raise gate.Integrity('HELIUS_WORKER_AUTHORITY')
 if _installed:
  if role!=_role:raise gate.Integrity('HELIUS_WORKER_AUTHORITY')
  return
 _role=role
 import http.client,requests.sessions,aiohttp.client,aiohttp.client_reqrep
 # urllib, urllib3/requests and stdlib clients converge before the first
 # HTTP request byte at endheaders. Connection retries re-enter this boundary.
 original_put=http.client.HTTPConnection.putrequest
 def putrequest(self,method,url,*a,**kw):
  self._helius_method=method;self._helius_target=url
  return original_put(self,method,url,*a,**kw)
 _patch(http.client.HTTPConnection,'putrequest',putrequest)
 original_end=http.client.HTTPConnection.endheaders
 def endheaders(self,message_body=None,*,encode_chunked=False):
  target=getattr(self,'_helius_target','/')
  host=getattr(self,'_tunnel_host',None) or self.host
  url=target if str(target).startswith(('http://','https://')) else ('https://' if isinstance(self,http.client.HTTPSConnection) or getattr(self,'is_verified',False) else 'http://')+host+target
  body=message_body if message_body is not None else getattr(self,'_helius_prepared_body',None)
  reservation=_admit(url,getattr(self,'_helius_method',''),body)
  self._helius_reservation=reservation
  token=_transport.set(True)
  try:return original_end(self,message_body,encode_chunked=encode_chunked)
  except BaseException:
   _finish(reservation,0,True);raise
  finally:_transport.reset(token)
 _patch(http.client.HTTPConnection,'endheaders',endheaders)
 import urllib3.connection
 urllib_request=urllib3.connection.HTTPConnection.request
 def pool_request(self,method,url,body=None,headers=None,**kw):
  self._helius_prepared_body=body
  try:return urllib_request(self,method,url,body=body,headers=headers,**kw)
  finally:self._helius_prepared_body=None
 _patch(urllib3.connection.HTTPConnection,'request',pool_request)
 original_response=http.client.HTTPConnection.getresponse
 def getresponse(self):
  reservation=getattr(self,'_helius_reservation',None)
  try:response=original_response(self)
  except BaseException:
   _finish(reservation,0,True);raise
  if reservation:
   _notice(reservation,response.status,response.headers)
   done=[False];status=response.status;read=response.read;close=response.close
   def finish():
    if not done[0]:_finish(reservation,status);done[0]=True
   def read_body(*a,**kw):
    try:result=read(*a,**kw)
    except BaseException:
     if not done[0]:_finish(reservation,0,True);done[0]=True
     raise
    if response.isclosed():finish()
    return result
   def close_body():
    try:return close()
    finally:finish()
   response.read=read_body;response.close=close_body
  return response
 _patch(http.client.HTTPConnection,'getresponse',getresponse)
 # requests connects via urllib3 before endheaders; mark the known transport
 # for TLS only. Actual credit authority remains at endheaders, never here.
 send=requests.sessions.Session.send
 def request_send(self,*a,**kw):
  token=_transport.set(True)
  try:return send(self,*a,**kw)
  finally:_transport.reset(token)
 _patch(requests.sessions.Session,'send',request_send)
 # aiohttp redirects and automatic retries create a new ClientRequest.send,
 # so each wire attempt gets a fresh shared reservation.
 aio_request=aiohttp.client.ClientSession._request
 async def request(self,*a,**kw):
  token=_transport.set(True)
  try:return await aio_request(self,*a,**kw)
  finally:_transport.reset(token)
 _patch(aiohttp.client.ClientSession,'_request',request)
 aio_send=aiohttp.client_reqrep.ClientRequest.send
 async def async_send(self,conn):
  body=self.body
  if body is not None:body=getattr(body,'_value',None)
  reservation=_admit(str(self.url),self.method,body)
  try:response=await aio_send(self,conn)
  except BaseException:
   _finish(reservation,0,True);raise
  if reservation:
   done=[False]
   def finish(*,terminal_error=False):
    if not done[0] and (response.status is not None or terminal_error):
     _finish(reservation,0 if terminal_error else response.status,terminal_error);done[0]=True
   for name in ('release','close','_release_connection'):
    original=getattr(response,name)
    def closing(original=original):
     try:return original()
     finally:finish()
    setattr(response,name,closing)
  if reservation:response._helius_finish=finish;response._helius_reservation=reservation
  return response
 _patch(aiohttp.client_reqrep.ClientRequest,'send',async_send)
 response_start=aiohttp.client_reqrep.ClientResponse.start
 async def start(self,*a,**kw):
  try:result=await response_start(self,*a,**kw)
  except BaseException:
   if getattr(self,'_helius_reservation',None):self._helius_finish(terminal_error=True)
   raise
  reservation=getattr(self,'_helius_reservation',None)
  if reservation:
   _notice(reservation,self.status,self.headers)
   if self.closed:self._helius_finish()
  return result
 _patch(aiohttp.client_reqrep.ClientResponse,'start',start)
 try:
  import httpx
 except ImportError:httpx=None
 if httpx is not None:
  sync_handle=httpx.HTTPTransport.handle_request
  def handle(self,request):
   reservation=_admit(str(request.url),request.method,request.content if gate.helius(str(request.url)) else None)
   token=_transport.set(True)
   try:response=sync_handle(self,request)
   except BaseException:
    _finish(reservation,0,True);raise
   finally:_transport.reset(token)
   _notice(reservation,response.status_code,response.headers)
   if reservation and response.is_closed:
    _finish(reservation,response.status_code)
   elif reservation:
    stream=response.stream
    class ChargedStream(httpx.SyncByteStream):
     def __iter__(self):
      try:yield from stream
      except BaseException:_finish(reservation,0,True);raise
      else:_finish(reservation,response.status_code)
     def close(self):
      try:stream.close()
      finally:_finish(reservation,response.status_code)
    response.stream=ChargedStream()
   return response
  _patch(httpx.HTTPTransport,'handle_request',handle)
  async_handle=httpx.AsyncHTTPTransport.handle_async_request
  async def handle_async(self,request):
   reservation=_admit(str(request.url),request.method,request.content if gate.helius(str(request.url)) else None)
   token=_transport.set(True)
   try:response=await async_handle(self,request)
   except BaseException:
    _finish(reservation,0,True);raise
   finally:_transport.reset(token)
   _notice(reservation,response.status_code,response.headers)
   if reservation and response.is_closed:
    _finish(reservation,response.status_code)
   elif reservation:
    stream=response.stream
    class ChargedAsyncStream(httpx.AsyncByteStream):
     async def __aiter__(self):
      try:
       async for item in stream:yield item
      except BaseException:_finish(reservation,0,True);raise
      else:_finish(reservation,response.status_code)
     async def aclose(self):
      try:await stream.aclose()
      finally:_finish(reservation,response.status_code)
    response.stream=ChargedAsyncStream()
   return response
  _patch(httpx.AsyncHTTPTransport,'handle_async_request',handle_async)
 # Refuse unknown TLS stacks targeting Helius. This is a safety backstop,
 # not a claim to control unrelated processes or arbitrary raw-IP clients.
 for name in ('wrap_socket','wrap_bio'):
  original=getattr(ssl.SSLContext,name)
  @functools.wraps(original)
  def wrap(self,*a,_original=original,**kw):
   bound=inspect.signature(_original).bind_partial(self,*a,**kw)
   host=bound.arguments.get('server_hostname')
   if host and gate.helius('https://'+str(host)) and not _transport.get():raise gate.Deferred('HELIUS_UNQUALIFIED_TRANSPORT')
   return _original(self,*a,**kw)
  _patch(ssl.SSLContext,name,wrap)
 _installed=True

def uninstall_for_tests():
 global _installed,_role
 for obj,name,original in reversed(_originals):setattr(obj,name,original)
 _originals.clear();_installed=False;_role=None


def require_installed():
 if not _installed:raise gate.Integrity("HELIUS_HTTP_PROTECTION_REQUIRED")


def install(role):
 with _install_lock:_install(role)


@contextlib.asynccontextmanager
async def websocket_connection(manager):
 # Called only after the existing Gate.websocket reservation. Permit the
 # accepted TLS handshake, then reset before yielding to application code.
 require_installed()
 token=_transport.set(True)
 try:ws=await manager.__aenter__()
 finally:_transport.reset(token)
 try:yield ws
 except BaseException:
  import sys
  if not await manager.__aexit__(*sys.exc_info()):raise
 else:await manager.__aexit__(None,None,None)


@contextlib.contextmanager
def resume_investigation(alias):
 # Durable child-job handoff: the alias must already name an immutable owner.
 g=gate.current()
 if g is None:raise gate.Integrity('HELIUS_GATE_REQUIRED')
 g.investigation_owner(alias)
 token=_scope.set((alias,))
 try:yield
 finally:_scope.reset(token)
