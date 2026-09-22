"""Passive, generic PumpSwap semantic evidence capture (disabled by default).

No RPC, signer, wallet, or execution code lives here.  Callers supply an
already-retained transaction payload only after their own commit; a bounded
research spool makes the raw payload durable before interpretation.
"""
from __future__ import annotations
import hashlib,json,os,sqlite3,time
from pathlib import Path
from typing import Any,Mapping
from src.ops.pumpswap_boundary import PUMPSWAP_PROGRAM

FEATURE_FLAG='PUMPSWAP_PROSPECTIVE_SEMANTIC_EVIDENCE_ENABLED'; DEFAULT_ENABLED=False
SCHEMA_VERSION='pumpswap-semantic-evidence-v1'; MAX_FIXTURES_PER_POOL=8; MAX_ATTEMPTS=3; WORKER_CONCURRENCY=1

def enabled(env:Mapping[str,str]|None=None)->bool:return str((env or os.environ).get(FEATURE_FLAG,str(DEFAULT_ENABLED))).lower() in {'1','true','yes','on'}
def canon(v:Any)->bytes:return json.dumps(v,sort_keys=True,separators=(',',':')).encode()
def observation_id(*,pool:str,signature:str,instruction_index:int)->str:return hashlib.sha256(f'{PUMPSWAP_PROGRAM}\0{pool}\0{signature}\0{instruction_index}\0{SCHEMA_VERSION}'.encode()).hexdigest()
def raw_digest(payload:Mapping[str,Any])->str:return hashlib.sha256(canon(payload)).hexdigest()

def direction(*,token_to_pool:int, token_from_pool:int, sol_to_pool:int, sol_from_pool:int)->str:
 if token_to_pool>0 and sol_from_pool>0 and token_from_pool==0 and sol_to_pool==0:return 'PUMPSWAP_SELL_CONFIRMED'
 if token_from_pool>0 and sol_to_pool>0 and token_to_pool==0 and sol_from_pool==0:return 'PUMPSWAP_BUY_CONFIRMED'
 return 'PUMPSWAP_DIRECTION_AMBIGUOUS'
def boundary_strength(*,direct_pre_post:bool, derivable:bool, transaction_boundary:bool)->str:
 return 'DIRECT_INSTRUCTION_BOUNDARY' if direct_pre_post else 'DETERMINISTICALLY_DERIVABLE_BOUNDARY' if derivable else 'TRANSACTION_BOUNDARY_ONLY' if transaction_boundary else 'BOUNDARY_INSUFFICIENT'
def readiness(record:Mapping[str,Any],kind:str)->str:
 required=('instruction_bytes','ordered_accounts','raw_sha256','pre_state','post_state','reserve_transition')
 complete=all(record.get(x) for x in required)
 if kind=='sell':
  complete=complete and record.get('direction')=='PUMPSWAP_SELL_CONFIRMED' and bool(record.get('token_to_pool')) and bool(record.get('sol_from_pool')) and bool(record.get('fee_evidence'))
  return 'SELL_FIXTURE_FULLY_CONSTRAINED' if complete else 'SELL_FIXTURE_PARTIALLY_CONSTRAINED' if record.get('direction')=='PUMPSWAP_SELL_CONFIRMED' else 'SELL_FIXTURE_NOT_CONSTRAINED'
 return 'MIGRATION_MAPPING_FIXTURE_FULLY_CONSTRAINED' if complete and record.get('final_pump_state') and record.get('pool_initial_state') else 'MIGRATION_MAPPING_FIXTURE_PARTIALLY_CONSTRAINED' if record.get('migration_signature') else 'MIGRATION_MAPPING_FIXTURE_NOT_CONSTRAINED'

def _conn(path:str):
 Path(path).parent.mkdir(parents=True,exist_ok=True);c=sqlite3.connect(path);c.row_factory=sqlite3.Row;return c
def ensure_schema(path:str)->None:
 with _conn(path) as c:
  c.execute('CREATE TABLE IF NOT EXISTS pumpswap_semantic_jobs (id TEXT PRIMARY KEY,pool TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)')
  c.execute('CREATE TABLE IF NOT EXISTS pumpswap_semantic_observations (id TEXT PRIMARY KEY,raw_sha256 TEXT NOT NULL,record TEXT NOT NULL,created_at INTEGER NOT NULL)')
  columns={row['name'] for row in c.execute('PRAGMA table_info(pumpswap_semantic_jobs)')}
  if 'pool' not in columns:c.execute("ALTER TABLE pumpswap_semantic_jobs ADD COLUMN pool TEXT NOT NULL DEFAULT ''")
  c.execute('CREATE INDEX IF NOT EXISTS idx_pumpswap_semantic_jobs_pool ON pumpswap_semantic_jobs(pool,status)')
def enqueue_committed(path:str,record:Mapping[str,Any],*,committed:bool,env:Mapping[str,str]|None=None,now:int|None=None)->str|None:
 if not committed:raise RuntimeError('POST_COMMIT_REQUIRED')
 if not enabled(env):return None
 ident=observation_id(pool=str(record.get('pool','')),signature=str(record.get('signature','')),instruction_index=int(record.get('instruction_index',-1)))
 if not record.get('pool') or not record.get('signature') or int(record.get('instruction_index',-1))<0:raise ValueError('PUMPSWAP_SWAP_BOUNDARY_INSUFFICIENT')
 at=int(time.time()) if now is None else now; payload=dict(record);payload.update({'id':ident,'schema_version':SCHEMA_VERSION,'raw_sha256':raw_digest(record),'enqueued_at':at})
 ensure_schema(path)
 with _conn(path) as c:
  # The cap counts both completed and queued work, so a busy worker cannot
  # allow an unbounded burst for one pool.
  count=c.execute("SELECT COUNT(*) FROM pumpswap_semantic_jobs WHERE pool=? AND status!='dead_letter'",(str(record['pool']),)).fetchone()[0]
  if count>=MAX_FIXTURES_PER_POOL:return None
  c.execute("INSERT OR IGNORE INTO pumpswap_semantic_jobs(id,pool,payload,status,created_at,updated_at) VALUES (?,?,?,'pending',?,?)",(ident,str(record['pool']),json.dumps(payload,sort_keys=True),at,at))
 return ident
def process_one(path:str,*,now:int|None=None,crash_after_commit:bool=False)->str|None:
 at=int(time.time()) if now is None else now;ensure_schema(path)
 with _conn(path) as c:
  row=c.execute("SELECT * FROM pumpswap_semantic_jobs WHERE status IN ('pending','retry') ORDER BY created_at LIMIT 1").fetchone()
  if not row:return None
  c.execute("UPDATE pumpswap_semantic_jobs SET status='leased',attempts=attempts+1,updated_at=? WHERE id=?",(at,row['id']))
 try:
  rec=json.loads(row['payload']);rec['direction']=direction(token_to_pool=int(rec.get('token_to_pool',0)),token_from_pool=int(rec.get('token_from_pool',0)),sol_to_pool=int(rec.get('sol_to_pool',0)),sol_from_pool=int(rec.get('sol_from_pool',0)));rec['boundary_strength']=boundary_strength(direct_pre_post=bool(rec.get('direct_pre_post')),derivable=bool(rec.get('derivable')),transaction_boundary=bool(rec.get('transaction_boundary')));rec['sell_readiness']=readiness(rec,'sell');rec['migration_readiness']=readiness(rec,'migration')
  with _conn(path) as c:c.execute('INSERT OR IGNORE INTO pumpswap_semantic_observations(id,raw_sha256,record,created_at) VALUES (?,?,?,?)',(row['id'],rec['raw_sha256'],json.dumps(rec,sort_keys=True),at))
  if crash_after_commit:raise RuntimeError('SIMULATED_CRASH_AFTER_RESULT_COMMIT')
  with _conn(path) as c:c.execute("UPDATE pumpswap_semantic_jobs SET status='completed',updated_at=? WHERE id=?",(at,row['id']))
  return 'completed'
 except Exception as exc:
  with _conn(path) as c:
   attempts=c.execute('SELECT attempts FROM pumpswap_semantic_jobs WHERE id=?',(row['id'],)).fetchone()[0];status='dead_letter' if attempts>=MAX_ATTEMPTS else 'retry';c.execute('UPDATE pumpswap_semantic_jobs SET status=?,last_error=?,updated_at=? WHERE id=?',(status,str(exc)[:500],at,row['id']))
  return status
def submission_capability()->str:return 'NONE'
