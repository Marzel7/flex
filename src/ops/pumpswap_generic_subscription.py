"""Fixture-qualified, disabled PumpSwap-program logs adapter.

The provider notification is signature-level only: it cannot identify separate
instructions in one transaction, so it retains no invented event index.
"""
from __future__ import annotations
from typing import Any,Mapping
from src.ops.pumpswap_boundary import PUMPSWAP_PROGRAM
from src.ops.pumpswap_raw_event_retention import FEATURE_FLAG,enabled,event_id,exists,retain_committed
SUBSCRIPTION_METHOD='logsSubscribe'; EVENT_INDEX_STATUS='EVENT_INDEX_UNRESOLVED_AT_SOURCE'
def subscription_request(request_id:int=1)->dict[str,Any]:return {'jsonrpc':'2.0','id':request_id,'method':SUBSCRIPTION_METHOD,'params':[{'mentions':[PUMPSWAP_PROGRAM]},{'commitment':'confirmed'}]}
def adapt_notification(notification:Mapping[str,Any],*,store_path:str,env:Mapping[str,str]|None=None,now:int|None=None,session=None)->dict[str,Any]:
 """One short, fail-open insert.  No RPC, semantic parsing, or retry."""
 metrics={'received':1,'accepted':0,'duplicate_or_rejected':0,'malformed':0,'rpc_calls':0,'event_index_status':EVENT_INDEX_STATUS}
 if not enabled(env):metrics['disabled']=1;return metrics
 try:
  value=notification['params']['result']['value'];signature=str(value['signature'])
  # A successful program-log notification is raw source evidence, not a swap
  # classification; logs are retained verbatim in the canonical object.
  if not signature or value.get('err') is not None:metrics['malformed']=1;return metrics
  raw={'source':'pumpswap_program_logs_subscribe','program_id':PUMPSWAP_PROGRAM,'event_index_status':EVENT_INDEX_STATUS,'notification':notification}
  ident=event_id(signature,None);duplicate=exists(store_path,ident)
  if duplicate:metrics['duplicate_or_rejected']=1;return metrics
  if session:
   admission=session.admit(signature)
   if admission!='ADMITTED':metrics['duplicate_or_rejected']=1;metrics['session_admission']=admission;return metrics
  accepted=retain_committed(store_path,raw,signature=signature,event_index=None,env=env,now=now)
  if accepted is None and session:session.release(signature)
  metrics['accepted']=int(accepted is not None);metrics['duplicate_or_rejected']=int(accepted is None);return metrics
 except Exception:
  metrics['malformed']=1;return metrics
def submission_capability():return 'NONE'
