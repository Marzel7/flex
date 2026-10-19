"""Generic, disabled-by-default Pump.fun opening-action evidence store.

Network acquisition is deliberately absent.  A listener may retain an exact
transaction payload here after it has committed its own source event; a
separate worker can later submit an already-retained block or exact boundary
state.  This keeps provider work and operation interpretation outside writes.
"""
from __future__ import annotations
import hashlib, json, os, sqlite3, time
from pathlib import Path
from typing import Any, Mapping
from src.ops.birth_anchored_opening_acquisition import actions_from_transaction, actions_from_block

SCHEMA_VERSION='pumpfun-opening-action-evidence.v1'
FEATURE_FLAG='PUMPFUN_OPENING_ACTION_EVIDENCE_ENABLED'
ORDER_PARTIAL='ORDER_PARTIAL'; ORDER_COMPLETE='ORDER_COMPLETE'; ORDER_UNRESOLVED='ORDER_UNRESOLVED'
BOUNDARY_UNAVAILABLE='BOUNDARY_STATE_UNAVAILABLE'; BOUNDARY_READY='BOUNDARY_STATE_READY'

def _canon(x): return json.dumps(x,sort_keys=True,separators=(',',':'))
def _id(x): return hashlib.sha256(_canon(x).encode()).hexdigest()
def enabled(env=None): return (env or os.environ).get(FEATURE_FLAG,'0')=='1'
def connect(path): Path(path).parent.mkdir(parents=True,exist_ok=True);c=sqlite3.connect(path);c.row_factory=sqlite3.Row;return c
def ensure(c):
 c.executescript('''CREATE TABLE IF NOT EXISTS pumpfun_opening_raw_events(id TEXT PRIMARY KEY,signature TEXT NOT NULL,slot INTEGER NOT NULL,payload BLOB NOT NULL,payload_sha256 TEXT NOT NULL,source TEXT NOT NULL,commitment TEXT NOT NULL,created_at INTEGER NOT NULL);
 CREATE TABLE IF NOT EXISTS pumpfun_opening_actions(id TEXT PRIMARY KEY,raw_id TEXT NOT NULL,mint TEXT NOT NULL,signature TEXT NOT NULL,slot INTEGER NOT NULL,outer_instruction_index INTEGER,action_index INTEGER,actor TEXT,action_type TEXT NOT NULL,payload TEXT NOT NULL,order_status TEXT NOT NULL,boundary_status TEXT NOT NULL,boundary_ref TEXT,created_at INTEGER NOT NULL,UNIQUE(raw_id,mint,action_index));
 CREATE TABLE IF NOT EXISTS pumpfun_opening_block_artifacts(id TEXT PRIMARY KEY,slot INTEGER NOT NULL UNIQUE,payload BLOB NOT NULL,payload_sha256 TEXT NOT NULL,source TEXT NOT NULL,created_at INTEGER NOT NULL);
 CREATE TABLE IF NOT EXISTS pumpfun_boundary_state_artifacts(id TEXT PRIMARY KEY,mint TEXT NOT NULL,signature TEXT NOT NULL,slot INTEGER NOT NULL,boundary TEXT NOT NULL,payload BLOB NOT NULL,payload_sha256 TEXT NOT NULL,parser_version TEXT NOT NULL,provenance TEXT NOT NULL,created_at INTEGER NOT NULL,UNIQUE(mint,signature,boundary,payload_sha256));
 CREATE TABLE IF NOT EXISTS pumpfun_opening_cursor(consumer TEXT PRIMARY KEY,last_created_at INTEGER NOT NULL DEFAULT 0,last_id TEXT NOT NULL DEFAULT '');''')

def retain_raw(path,tx:Mapping[str,Any],*,signature:str,slot:int,source:str='listener_getTransaction',commitment:str='confirmed',env=None,now=None):
 if not enabled(env): return None
 body=_canon(tx).encode(); ident=_id({'v':SCHEMA_VERSION,'signature':signature,'slot':slot,'sha':hashlib.sha256(body).hexdigest()});at=int(now or time.time())
 with connect(path) as c: ensure(c);c.execute('INSERT OR IGNORE INTO pumpfun_opening_raw_events VALUES(?,?,?,?,?,?,?,?)',(ident,signature,slot,body,hashlib.sha256(body).hexdigest(),source,commitment,at));c.commit()
 return ident

def materialize(path,raw_id,*,now=None):
 with connect(path) as c:
  ensure(c);r=c.execute('SELECT * FROM pumpfun_opening_raw_events WHERE id=?',(raw_id,)).fetchone()
  if not r:return {'result':'RAW_NOT_FOUND'}
  tx=json.loads(r['payload']); acts=[]
  # Decode every mint observed in this exact tx; callers can pass a persisted
  # mint list later, but target TradeEvents provide mint themselves.
  logs=(tx.get('meta') or {}).get('logMessages') or []
  import base64
  from src.ops.birth_anchored_opening_acquisition import decode_trade_event
  for i,line in enumerate(logs):
   if isinstance(line,str) and line.startswith('Program data: '):
    try: event=decode_trade_event(base64.b64decode(line.split(': ',1)[1]))
    except Exception: event=None
    if event:
     event.update({'slot':r['slot'],'signature':r['signature'],'transaction_index':None,'action_index':i});acts.append(event)
  for a in acts:
   ident=_id({'raw':raw_id,'mint':a['mint'],'event':a['action_index']}); payload={'schema_version':SCHEMA_VERSION,'logical_fact_id':ident,'raw_artifact_id':raw_id,'parser_version':'pumpfun-trade-event-prefix.v1','program_id':'6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P','signature':r['signature'],'slot':r['slot'],'action_index':a['action_index'],'actor':a.get('buyer'),'action_type':a['action_type'],'related_evidence_refs':[],'finality':r['commitment'],'event_post_state':{k:a[k] for k in ('post_virtual_sol_reserves','post_virtual_token_reserves','post_real_sol_reserves','post_real_token_reserves') if k in a}}
   c.execute('INSERT OR IGNORE INTO pumpfun_opening_actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(ident,raw_id,a['mint'],r['signature'],r['slot'],None,a['action_index'],a.get('buyer'),a['action_type'],_canon(payload),ORDER_PARTIAL,BOUNDARY_UNAVAILABLE,None,int(now or time.time())))
  c.commit();return {'result':'MATERIALIZED','count':len(acts)}

def retain_block_and_enrich(path,block:Mapping[str,Any],*,slot:int,source='retained_block',now=None):
 body=_canon(block).encode();bid=_id({'slot':slot,'sha':hashlib.sha256(body).hexdigest()});at=int(now or time.time())
 with connect(path) as c:
  ensure(c);c.execute('INSERT OR IGNORE INTO pumpfun_opening_block_artifacts VALUES(?,?,?,?,?,?)',(bid,slot,body,hashlib.sha256(body).hexdigest(),source,at))
  rows=c.execute('SELECT id,mint,signature,action_index,payload FROM pumpfun_opening_actions WHERE slot=?',(slot,)).fetchall(); all_actions=[]
  for row in rows: all_actions.extend(actions_from_block(block,mint=row['mint'],slot=slot))
  by_key={(a.get('signature'),a.get('action_index')):a for a in all_actions}
  for row in rows:
   a=by_key.get((row['signature'],row['action_index']))
   if not a or a.get('transaction_index') is None: status=ORDER_UNRESOLVED; ordinal=None
   else: status=ORDER_COMPLETE;ordinal=int(a['transaction_index'])
   p=json.loads(row['payload']);p.update({'transaction_ordinal':ordinal,'ordering_key':['slot','transaction_ordinal','action_index','signature'],'ordering_artifact_id':bid,'ordering_schema':'slot-tx-ordinal-log-index.v1'})
   c.execute('UPDATE pumpfun_opening_actions SET outer_instruction_index=?,order_status=?,payload=? WHERE id=?',(ordinal,status,_canon(p),row['id']))
  c.commit();return {'block_artifact_id':bid,'enriched':len(rows)}

def retain_boundary_state(path,*,mint,signature,slot,boundary,raw_state:bytes,decoded:Mapping[str,Any],parser_version,provenance,now=None):
 if boundary not in {'PRE_ACTION_STATE','POST_ACTION_STATE'}: raise ValueError('BOUNDARY_IDENTITY_REQUIRED')
 body=_canon({'decoded':dict(decoded),'raw_state_hex':raw_state.hex()}).encode();ident=_id({'mint':mint,'signature':signature,'boundary':boundary,'sha':hashlib.sha256(body).hexdigest()})
 with connect(path) as c:
  ensure(c);c.execute('INSERT OR IGNORE INTO pumpfun_boundary_state_artifacts VALUES(?,?,?,?,?,?,?,?,?,?)',(ident,mint,signature,slot,boundary,body,hashlib.sha256(body).hexdigest(),parser_version,provenance,int(now or time.time())))
  c.execute('UPDATE pumpfun_opening_actions SET boundary_status=?,boundary_ref=? WHERE mint=? AND signature=?',(BOUNDARY_READY,ident,mint,signature));c.commit()
 return ident

def ready_actions(path):
 with connect(path) as c: ensure(c);return c.execute('SELECT * FROM pumpfun_opening_actions WHERE order_status=? AND boundary_status=? ORDER BY slot,outer_instruction_index,action_index,signature',(ORDER_COMPLETE,BOUNDARY_READY)).fetchall()
def submission_capability(): return 'NONE'
