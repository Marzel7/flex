"""Exact Helius credit reservations for observation-attributed provider attempts."""
from __future__ import annotations
import sqlite3,time,hashlib
from src.apis.rpc_metrics_config import CREDIT_SCHEDULE

SCHEDULE='helius-rpc-metrics-config.v1'; METHODS={'getSignaturesForAddress','getTransaction','getAccountInfo'}
def cost(method):
 if method not in METHODS: raise ValueError('unmapped Walkback request type')
 value=CREDIT_SCHEDULE[method]
 if not isinstance(value,int): raise ValueError('parameter-dependent cost unsupported')
 return value
def ensure_schema(c):
 c.executescript('''CREATE TABLE IF NOT EXISTS operation_observation_credit_budget(observation_id TEXT PRIMARY KEY,ceiling INTEGER NOT NULL,stop_reason TEXT);CREATE TABLE IF NOT EXISTS operation_observation_provider_credit_ledger(ledger_id TEXT PRIMARY KEY,observation_id TEXT NOT NULL,request_id TEXT NOT NULL,mint TEXT NOT NULL,job_id TEXT NOT NULL,provider TEXT NOT NULL,request_type TEXT NOT NULL,attempt_ordinal INTEGER NOT NULL,reserved_credits INTEGER NOT NULL,state TEXT NOT NULL,created_at INTEGER NOT NULL,UNIQUE(observation_id,job_id,request_type,attempt_ordinal));''')
def identity(obs,job,method,ordinal): return hashlib.sha256(f'{obs}|{job}|{method}|{ordinal}'.encode()).hexdigest()
def reserve(c,obs,request_id,mint,job,method,ordinal,now=None):
 ensure_schema(c);n=int(now or time.time());amount=cost(method);lid=identity(obs,job,method,ordinal);c.execute('BEGIN IMMEDIATE')
 try:
  old=c.execute('SELECT state FROM operation_observation_provider_credit_ledger WHERE ledger_id=?',(lid,)).fetchone()
  if old: c.commit();return old[0],lid
  budget=c.execute('SELECT ceiling,COALESCE((SELECT SUM(reserved_credits) FROM operation_observation_provider_credit_ledger WHERE observation_id=? AND state IN ("RESERVED","CONSUMED","AMBIGUOUS")),0) FROM operation_observation_credit_budget WHERE observation_id=?',(obs,obs)).fetchone()
  if not budget or budget[1]+amount>budget[0]: c.execute("UPDATE operation_observation_credit_budget SET stop_reason='RPC_CEILING' WHERE observation_id=?",(obs,));c.commit();return 'DENIED',lid
  c.execute('INSERT INTO operation_observation_provider_credit_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?)',(lid,obs,request_id,mint,job,'helius',method,ordinal,amount,'RESERVED',n));c.commit();return 'RESERVED',lid
 except Exception:c.rollback();raise
def dispatching(c,lid): c.execute("UPDATE operation_observation_provider_credit_ledger SET state='AMBIGUOUS' WHERE ledger_id=?",(lid,));c.commit()
def consume(c,lid): c.execute("UPDATE operation_observation_provider_credit_ledger SET state='CONSUMED' WHERE ledger_id=?",(lid,));c.commit()
def release(c,lid): c.execute("UPDATE operation_observation_provider_credit_ledger SET state='RELEASED' WHERE ledger_id=? AND state='RESERVED'",(lid,));c.commit()
def total(c,obs): return c.execute('SELECT COALESCE(SUM(reserved_credits),0) FROM operation_observation_provider_credit_ledger WHERE observation_id=? AND state IN ("RESERVED","CONSUMED","AMBIGUOUS")',(obs,)).fetchone()[0]
