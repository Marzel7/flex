"""Immutable JSON-RPC envelope capture for generic walkback acquisition."""
from __future__ import annotations
import hashlib,json,time,urllib.request
from pathlib import Path

def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'))
def redacted_endpoint(url): return url.split('?',1)[0]

class ImmutableJsonRpcTransport:
 def __init__(self, root, run_id, endpoint, opener=urllib.request.urlopen, clock=time.time): self.root=Path(root)/run_id;self.endpoint=endpoint;self.opener=opener;self.clock=clock
 def request(self, *, wallet, method, params, attempt=1, timeout=8):
  self.root.mkdir(parents=True,exist_ok=True); rid=hashlib.sha256(canonical([wallet,method,params,attempt]).encode()).hexdigest(); base=self.root/(rid+'.json')
  if base.exists(): raise RuntimeError('immutable attempt collision')
  meta={'request_id':rid,'wallet':wallet,'method':method,'params':params,'attempt':attempt,'endpoint_identity':redacted_endpoint(self.endpoint),'timestamp':self.clock()}
  try:
   req=urllib.request.Request(self.endpoint,data=canonical({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json','User-Agent':'walkback-worker/1.0'})
   response=self.opener(req,timeout=timeout); body=response.read(); status=getattr(response,'status',200)
   envelope=json.loads(body); state='JSON_RPC_ERROR' if envelope.get('error') else 'SUCCESS'
   meta.update({'http_status':status,'response_sha256':hashlib.sha256(body).hexdigest(),'body':body.decode('utf-8'),'envelope':envelope,'state':state})
  except json.JSONDecodeError as exc: meta.update({'state':'JSON_PARSE_FAILURE','error':type(exc).__name__})
  except TimeoutError as exc: meta.update({'state':'TIMEOUT','error':type(exc).__name__})
  except Exception as exc: meta.update({'state':'HTTP_TRANSPORT_FAILURE','error':type(exc).__name__})
  base.write_text(canonical(meta)+'\n'); return meta
 def replay(self, request_id):
  m=json.loads((self.root/(request_id+'.json')).read_text())
  if m.get('body') is not None and hashlib.sha256(m['body'].encode()).hexdigest()!=m.get('response_sha256'): raise RuntimeError('RESPONSE_INTEGRITY_MISMATCH')
  return m
