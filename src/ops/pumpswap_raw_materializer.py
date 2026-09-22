"""Committed-raw-event-only PumpSwap materializer; resolver is injected."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from pathlib import Path
from typing import Any,Callable,Mapping
from src.ops.pumpswap_boundary import PUMPSWAP_PROGRAM
from src.ops.pumpswap_raw_event_retention import acknowledge,next_committed
CONSUMER='pumpswap-transaction-materializer-v1';MAX_PROVIDER_FETCHES=10;VERSION='v1'
def _c(p):Path(p).parent.mkdir(parents=True,exist_ok=True);c=sqlite3.connect(p);c.row_factory=sqlite3.Row;return c
def ensure(p):
 with _c(p) as c:c.execute('CREATE TABLE IF NOT EXISTS pumpswap_materializations(id TEXT PRIMARY KEY,raw_id TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,created_at INTEGER NOT NULL)')
def _keys(tx):
 msg=(tx.get('transaction')or{}).get('message')or{};base=msg.get('accountKeys')or[];keys=[x.get('pubkey') if isinstance(x,dict) else x for x in base]
 loaded=(tx.get('meta')or{}).get('loadedAddresses')or{}
 return keys+list(loaded.get('writable')or[])+list(loaded.get('readonly')or[])
def instructions(tx):
 msg=(tx.get('transaction')or{}).get('message')or{};keys=_keys(tx);result=[]
 for i,x in enumerate(msg.get('instructions')or[]):
  pid=x.get('programId')
  if pid is None and isinstance(x.get('programIdIndex'),int) and x['programIdIndex']<len(keys):pid=keys[x['programIdIndex']]
  if pid!=PUMPSWAP_PROGRAM:continue
  raw_accounts=x.get('accounts',[]);accounts=[keys[a] if isinstance(a,int) and 0<=a<len(keys) else a for a in raw_accounts]
  result.append({'outer_instruction_index':i,'program_id':pid,'instruction_data':x.get('data'),'account_indexes':raw_accounts,'accounts':accounts})
 return result
def materialize_one(raw_db:str,mat_db:str,resolver:Callable[[str],Mapping[str,Any]|None],*,now:int|None=None,crash_after_commit=False):
 row=next_committed(raw_db,CONSUMER)
 if row is None:return None
 raw=json.loads(row['payload']);sig=raw['notification']['params']['result']['value']['signature'];out=resolver(sig) or {};tx=out.get('transaction') if 'status' in out else out;at=int(time.time()) if now is None else now
 ins=instructions(tx) if isinstance(tx,Mapping) else [];status='PUMPSWAP_INSTRUCTIONS_MATERIALIZED' if ins else ('TRANSACTION_UNAVAILABLE' if not tx else 'NO_PUMPSWAP_INSTRUCTION_FOUND')
 payload={'raw_event_id':row['id'],'signature':sig,'source_event_index':'EVENT_INDEX_UNRESOLVED_AT_SOURCE','status':status,'transaction_provenance':out.get('status'),'transaction_evidence_id':out.get('transaction_evidence_id'),'transaction_sha256':out.get('response_sha256'),'slot':out.get('slot') or (tx or {}).get('slot'),'instructions':ins,'version':VERSION};ident=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest();ensure(mat_db)
 with _c(mat_db) as c:c.execute('INSERT OR IGNORE INTO pumpswap_materializations VALUES (?,?,?,?,?)',(ident,row['id'],status,json.dumps(payload,sort_keys=True),at))
 if crash_after_commit:raise RuntimeError('CRASH_AFTER_COMMIT')
 acknowledge(raw_db,CONSUMER,row);return payload
def submission_capability():return 'NONE'
