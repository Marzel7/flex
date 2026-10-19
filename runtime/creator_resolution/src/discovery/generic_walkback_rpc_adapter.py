"""Adapter preserving the frozen factual walkback extractor."""
from __future__ import annotations
from src.discovery.generic_wallet_walkback import find_funding_parent, HistoricalContext
from src.core import walkback_worker
from src.discovery.immutable_jsonrpc_transport import canonical
class RetainedRpcAdapter:
 def __init__(self, transport): self.transport=transport;self._attempts={};self.last_transcript=[]
 def extract(self,wallet,context=HistoricalContext(),*,depth=None):
  calls=[];self.last_transcript=[];old=walkback_worker._rpc
  def rpc(method,params):
   key=canonical([wallet,method,params]);attempt=self._attempts.get(key,0)+1;self._attempts[key]=attempt
   meta=self.transport.request(wallet=wallet,method=method,params=params,attempt=attempt,depth=depth,context={'cutoff':getattr(context,'cutoff',None),'prefer_oldest':getattr(context,'prefer_oldest',False)});calls.append(meta['request_id']);result=meta['envelope'].get('result') if meta['state']=='SUCCESS' else None;self.last_transcript.append({'wrapper_invocation_id':wallet+':'+str(depth),'ordinal':len(self.last_transcript)+1,'method':method,'params':params,'page':next((x.get('before') for x in params if isinstance(x,dict)),None),'attempt_ids':[meta['request_id']],'accepted_attempt_id':meta['request_id'] if meta['state']=='SUCCESS' else None,'transport_request_id':meta['request_id'],'transport_artifact_locator':meta['request_id']+'.json','response_digest':meta.get('response_sha256'),'decoded_result_digest':__import__('hashlib').sha256(canonical(result).encode()).hexdigest(),'state':meta['state']});return result
  try: walkback_worker._rpc=rpc;return find_funding_parent(wallet,context),calls
  finally: walkback_worker._rpc=old
PAGINATION_APPLICABILITY='PAGINATION_REQUIRED'

class TranscriptRpcReplay:
 """Strict provider-disabled `_rpc` replacement backed by one transcript."""
 def __init__(self, transcript, transport): self.transcript=transcript;self.transport=transport;self.i=0
 def __call__(self, method, params):
  if self.i >= len(self.transcript): raise RuntimeError('HOLD_P3R_GENERIC_WALKBACK_PILOT_REPLAY_MISMATCH')
  entry=self.transcript[self.i];self.i+=1
  if entry['method'] != method or canonical(entry['params']) != canonical(params): raise RuntimeError('HOLD_P3R_GENERIC_WALKBACK_PILOT_REPLAY_MISMATCH')
  locator=entry.get('transport_artifact_locator','')
  if not locator or '/' in locator or '\\' in locator or locator.startswith('.'): raise RuntimeError('HOLD_P3R_GENERIC_WALKBACK_PILOT_REPLAY_MISMATCH')
  meta=self.transport.replay(entry['transport_request_id'])
  if locator != entry['transport_request_id']+'.json' or meta.get('response_sha256') != entry.get('response_digest'): raise RuntimeError('HOLD_P3R_GENERIC_WALKBACK_PILOT_REPLAY_MISMATCH')
  result=meta.get('envelope',{}).get('result')
  if __import__('hashlib').sha256(canonical(result).encode()).hexdigest()!=entry.get('decoded_result_digest'): raise RuntimeError('HOLD_P3R_GENERIC_WALKBACK_PILOT_REPLAY_MISMATCH')
  return result
 def assert_consumed(self):
  if self.i != len(self.transcript): raise RuntimeError('HOLD_P3R_GENERIC_WALKBACK_PILOT_REPLAY_MISMATCH')
