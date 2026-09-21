"""Disabled raw-first PumpSwap event evidence source; no semantic decoding or RPC."""
from __future__ import annotations
import hashlib,json,os,sqlite3,time
from pathlib import Path
from typing import Any,Mapping
from src.ops.pumpswap_boundary import PUMPSWAP_PROGRAM
FEATURE_FLAG='PUMPSWAP_RAW_SWAP_EVENT_RETENTION_ENABLED';DEFAULT_ENABLED=False;SCHEMA_VERSION='pumpswap-raw-event-v1';MAX_EVENTS=1000;MAX_PAYLOAD_BYTES=65536
def enabled(env:Mapping[str,str]|None=None)->bool:return str((env or os.environ).get(FEATURE_FLAG,str(DEFAULT_ENABLED))).lower() in {'1','true','yes','on'}
def payload_bytes(raw:Any)->bytes:return json.dumps(raw,sort_keys=True,separators=(',',':')).encode()
def event_id(signature:str,event_index:int|None)->str:return hashlib.sha256(f'{PUMPSWAP_PROGRAM}\0{signature}\0{event_index if event_index is not None else "source-event"}\0{SCHEMA_VERSION}'.encode()).hexdigest()
def _conn(p):Path(p).parent.mkdir(parents=True,exist_ok=True);c=sqlite3.connect(p);c.row_factory=sqlite3.Row;return c
def ensure_schema(p):
 with _conn(p) as c:
  c.execute('CREATE TABLE IF NOT EXISTS pumpswap_raw_events(id TEXT PRIMARY KEY,signature TEXT NOT NULL,event_index INTEGER,payload BLOB NOT NULL,payload_sha256 TEXT NOT NULL,representation TEXT NOT NULL,created_at INTEGER NOT NULL)')
  c.execute('CREATE TABLE IF NOT EXISTS pumpswap_raw_consumer_state(consumer TEXT PRIMARY KEY,last_event_created_at INTEGER NOT NULL DEFAULT 0,last_event_id TEXT NOT NULL DEFAULT "")')
def exists(p:str,ident:str)->bool:
 ensure_schema(p)
 with _conn(p) as c:return c.execute('SELECT 1 FROM pumpswap_raw_events WHERE id=?',(ident,)).fetchone() is not None
def retain_committed(p:str,raw:Any,*,signature:str,event_index:int|None=None,env:Mapping[str,str]|None=None,now:int|None=None)->str|None:
 if not enabled(env):return None
 body=payload_bytes(raw)
 if not signature or len(body)>MAX_PAYLOAD_BYTES:return None
 ensure_schema(p);at=int(time.time()) if now is None else now;ident=event_id(signature,event_index)
 with _conn(p) as c:
  if c.execute('SELECT COUNT(*) FROM pumpswap_raw_events').fetchone()[0]>=MAX_EVENTS:return None
  c.execute('INSERT OR IGNORE INTO pumpswap_raw_events VALUES (?,?,?,?,?,?,?)',(ident,signature,event_index,body,hashlib.sha256(body).hexdigest(),'CANONICAL_PROVIDER_OBJECT',at))
 return ident
def next_committed(p:str,consumer:str):
 ensure_schema(p)
 with _conn(p) as c:
  s=c.execute('SELECT last_event_created_at,last_event_id FROM pumpswap_raw_consumer_state WHERE consumer=?',(consumer,)).fetchone();after=(s['last_event_created_at'],s['last_event_id']) if s else (0,'')
  return c.execute('SELECT * FROM pumpswap_raw_events WHERE (created_at>? OR (created_at=? AND id>?)) ORDER BY created_at,id LIMIT 1',(after[0],after[0],after[1])).fetchone()
def acknowledge(p:str,consumer:str,row):
 with _conn(p) as c:c.execute('INSERT INTO pumpswap_raw_consumer_state VALUES (?,?,?) ON CONFLICT(consumer) DO UPDATE SET last_event_created_at=excluded.last_event_created_at,last_event_id=excluded.last_event_id',(consumer,row['created_at'],row['id']))
def submission_capability():return 'NONE'
