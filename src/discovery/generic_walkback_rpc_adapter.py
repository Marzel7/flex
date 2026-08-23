"""Real-response-shaped adapter for the frozen factual walkback wrapper."""
from __future__ import annotations
from src.discovery.generic_wallet_walkback import find_funding_parent, HistoricalContext
from src.core import walkback_worker

class RetainedRpcAdapter:
 def __init__(self, transport): self.transport=transport
 def extract(self, wallet, context=HistoricalContext()):
  calls=[]
  old=walkback_worker._rpc
  def rpc(method, params):
   # Retained fixtures/replay provide envelopes keyed by exact historical call.
   meta=self.transport.request(wallet=wallet,method=method,params=params,attempt=1)
   if meta['state']!='SUCCESS': return None
   calls.append(meta['request_id']); return meta['envelope'].get('result')
  try:
   walkback_worker._rpc=rpc
   result=find_funding_parent(wallet,context)
   return result,calls
  finally: walkback_worker._rpc=old

# The historical lookup is paginated: getSignaturesForAddress uses `before`
# cursor, up to SIG_PAGE_COUNT pages, followed by bounded getTransaction and
# getAccountInfo requests.  Each call is a distinct immutable transport attempt.
PAGINATION_APPLICABILITY='PAGINATION_REQUIRED'
