"""Generic durable strategy-trigger qualifications and post-commit outbox.

This module is deliberately detached from listener transactions: callers pass
already-committed evidence, then atomically write a compact qualification and
an outbox row.  Dispatch occurs after that transaction closes.
"""
from __future__ import annotations
import hashlib,json,sqlite3,time
from pathlib import Path
from typing import Any,Mapping

SCHEMA_VERSION='operation-strategy-trigger-qualification.v1'
def canon(x):return json.dumps(x,sort_keys=True,separators=(',',':'))
def identity(x):return hashlib.sha256(canon(x).encode()).hexdigest()
def connect(path):
 Path(path).parent.mkdir(parents=True,exist_ok=True); c=sqlite3.connect(path);c.row_factory=sqlite3.Row;return c
def ensure(c):
 c.executescript('''CREATE TABLE IF NOT EXISTS operation_strategy_trigger_qualifications(id TEXT PRIMARY KEY,payload TEXT NOT NULL,created_at INTEGER NOT NULL);
 CREATE TABLE IF NOT EXISTS operation_strategy_trigger_outbox(id TEXT PRIMARY KEY,qualification_id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,updated_at INTEGER NOT NULL);''')
def evaluate_byzantine_scenario_d(e:Mapping[str,Any])->dict[str,Any]:
 """Strict operation-specific evaluator over committed compact evidence."""
 required={'operation_id','mint','cluster_id','opening_positions','recurrent_cluster_completed','signature','slot','event_index','entry_state_reference'}
 if not required.issubset(e):return {'result':'TRIGGER_INSUFFICIENT_EVIDENCE'}
 if e.get('operation_id')!='byzantine' or tuple(e['opening_positions'])!=tuple(range(1,13)) or not e['recurrent_cluster_completed'] or int(e.get('position',0))!=13 or not e.get('is_cluster_member'):
  return {'result':'TRIGGER_NOT_REACHED'}
 return {'result':'TRIGGER_QUALIFIED','boundary':{'signature':e['signature'],'slot':int(e['slot']),'event_index':int(e['event_index']),'position':13},'evidence_refs':{'cluster_id':e['cluster_id'],'entry_state_reference':e['entry_state_reference']}}
def qualify(path:str,evidence:Mapping[str,Any],*,operation_version:str,strategy_version:str,trigger_name:str,trigger_version:str,evaluator=evaluate_byzantine_scenario_d,now:int|None=None)->dict[str,Any]:
 decision=evaluator(evidence)
 if decision['result']!='TRIGGER_QUALIFIED':return decision
 payload={'schema_version':SCHEMA_VERSION,'operation_id':evidence['operation_id'],'operation_version':operation_version,'strategy_definition_version':strategy_version,'trigger_name':trigger_name,'trigger_version':trigger_version,'mint':evidence['mint'],'trigger_boundary':decision['boundary'],'source_evidence_ids':decision['evidence_refs'],'evaluator_version':'byzantine-scenario-d-v1','result':'TRIGGER_QUALIFIED'}
 payload['id']=identity({k:v for k,v in payload.items() if k!='id'}); payload['provenance_digest']=identity(payload); ts=int(time.time() if now is None else now); out={'qualification_id':payload['id'],'operation_id':payload['operation_id'],'mint':payload['mint'],'strategy_definition_version':strategy_version,'trigger_boundary':payload['trigger_boundary'],'consumer':'byzantine-prospective-shadow-v1'}; out['id']=identity(out)
 with connect(path) as c:
  ensure(c);c.execute('BEGIN');c.execute('INSERT OR IGNORE INTO operation_strategy_trigger_qualifications VALUES(?,?,?)',(payload['id'],canon(payload),ts));c.execute('INSERT OR IGNORE INTO operation_strategy_trigger_outbox(id,qualification_id,payload,status,updated_at) VALUES(?,?,?,"pending",?)',(out['id'],payload['id'],canon(out),ts));c.commit()
 return {'result':'TRIGGER_QUALIFIED','qualification':payload,'outbox_id':out['id']}
def pending(path:str):
 with connect(path) as c:ensure(c);return c.execute("SELECT * FROM operation_strategy_trigger_outbox WHERE status='pending' ORDER BY id").fetchall()
def acknowledge(path:str,outbox_id:str,now:int|None=None):
 with connect(path) as c:ensure(c);c.execute("UPDATE operation_strategy_trigger_outbox SET status='dispatched',attempts=attempts+1,updated_at=? WHERE id=?",(int(time.time() if now is None else now),outbox_id));c.commit()
def submission_capability():return 'NONE'
