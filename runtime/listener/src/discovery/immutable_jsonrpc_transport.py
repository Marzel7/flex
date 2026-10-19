"""Run-local immutable JSON-RPC attempt capture for generic walkback."""
from __future__ import annotations
import hashlib, json, time, urllib.request
from pathlib import Path

def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':'))
def sha(value): return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()
def redacted_endpoint(url): return url.split('?', 1)[0]
class RequestBoundExceeded(RuntimeError): pass

class ImmutableJsonRpcTransport:
 def __init__(self, root, run_id, endpoint, opener=urllib.request.urlopen, clock=time.time, max_requests=5000): self.root=Path(root)/run_id;self.run_id=run_id;self.endpoint=endpoint;self.opener=opener;self.clock=clock;self.max_requests=max_requests
 def _attempts(self): return sorted(self.root.glob('*.json')) if self.root.exists() else []
 def request(self, *, wallet, method, params, attempt=1, timeout=8, depth=None, page=None, context=None):
  self.root.mkdir(parents=True,exist_ok=True);sequence=len(self._attempts())+1
  request={'run_id':self.run_id,'wallet':wallet,'method':method,'params':params,'attempt':attempt,'depth':depth,'page':page,'context':context or {},'endpoint_identity':redacted_endpoint(self.endpoint)};request_sha256=sha(request)
  if sequence>self.max_requests:
   hold={'state':'HOLD_P3R_GENERIC_WALKBACK_PILOT_REQUEST_BOUND_EXCEEDED','sequence':sequence,'request':request,'request_sha256':request_sha256,'timestamp':self.clock()};(self.root/('HOLD-%06d.json'%sequence)).write_text(canonical(hold)+'\n');raise RequestBoundExceeded(hold['state'])
  rid=hashlib.sha256((request_sha256+':'+str(sequence)).encode()).hexdigest();path=self.root/(rid+'.json');meta={'request_id':rid,'sequence':sequence,'request':request,'request_sha256':request_sha256,'timestamp':self.clock(),'state':'STARTED'};path.write_text(canonical(meta)+'\n')
  try:
   req=urllib.request.Request(self.endpoint,data=canonical({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json','User-Agent':'walkback-worker/1.0'});response=self.opener(req,timeout=timeout);body=response.read();status=getattr(response,'status',200);envelope=json.loads(body);state='JSON_RPC_ERROR' if envelope.get('error') else 'SUCCESS';meta.update({'http_status':status,'raw_response_body':body.decode('utf-8'),'body':body.decode('utf-8'),'raw_response_sha256':hashlib.sha256(body).hexdigest(),'envelope':envelope,'provider_error':envelope.get('error'),'state':state})
  except json.JSONDecodeError as exc: meta.update({'state':'JSON_PARSE_FAILURE','error':type(exc).__name__})
  except TimeoutError as exc: meta.update({'state':'TIMEOUT','error':type(exc).__name__})
  except Exception as exc: meta.update({'state':'HTTP_TRANSPORT_FAILURE','error':type(exc).__name__})
  meta['response_sha256']=sha({'request_sha256':request_sha256,'http_status':meta.get('http_status'),'raw_response_body':meta.get('raw_response_body'),'envelope':meta.get('envelope'),'provider_error':meta.get('provider_error'),'state':meta['state']});path.write_text(canonical(meta)+'\n');return meta
 def records(self): return [json.loads(p.read_text()) for p in self._attempts()]
 def replay(self,request_id):
  m=json.loads((self.root/(request_id+'.json')).read_text())
  if sha(m['request'])!=m.get('request_sha256'):raise RuntimeError('HOLD_P3R_GENERIC_WALKBACK_PILOT_REPLAY_MISMATCH')
  body=m.get('raw_response_body'); legacy=m.get('body',body)
  if body is not None and (legacy != body or hashlib.sha256(body.encode()).hexdigest()!=m.get('raw_response_sha256')):raise RuntimeError('HOLD_P3R_GENERIC_WALKBACK_PILOT_REPLAY_MISMATCH')
  if m.get('response_sha256')!=sha({'request_sha256':m['request_sha256'],'http_status':m.get('http_status'),'raw_response_body':body,'envelope':m.get('envelope'),'provider_error':m.get('provider_error'),'state':m['state']}):raise RuntimeError('HOLD_P3R_GENERIC_WALKBACK_PILOT_REPLAY_MISMATCH')
  return m
