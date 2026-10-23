"""DEV004 bounded completion metadata; canonical observations are insert-only.

One existing shared-writer transaction verifies evidence and records its window.
No raw response storage, provider calls, schema changes outside disposable DEV.
"""
from __future__ import annotations
import hashlib,json,math,os,resource,sqlite3,stat,time
from pathlib import Path
from src.core.database_write_service import database_write_service
from src.core.db_writer import DurableWriteReceipt

MAX_DB_BYTES=64*1024*1024
MAX_ROWS=4096
MAX_REFS_BYTES=65536
MAX_TOTAL_REFS=4*1024*1024
MAX_CANDLES=512
TABLE='operation_monitor_window_completion'
DDL='''CREATE TABLE IF NOT EXISTS operation_monitor_window_completion (
 operation_id TEXT NOT NULL, mint TEXT NOT NULL, chunk_id TEXT NOT NULL,
 resolution TEXT NOT NULL, window_start INTEGER NOT NULL, window_end INTEGER NOT NULL,
 evidence_refs TEXT NOT NULL CHECK(length(evidence_refs)<=65536),
 response_digest TEXT NOT NULL, created_at INTEGER NOT NULL,
 PRIMARY KEY(operation_id,mint,chunk_id), CHECK(window_end>window_start)) WITHOUT ROWID'''
OBS_COLUMNS='operation_id,mint,observation_timestamp,mc_usd,resolution,source,request_identity,provenance_digest,created_at,open_mc_usd,high_mc_usd,low_mc_usd,close_mc_usd'
OBS_INSERT='INSERT INTO operation_monitor_observations('+OBS_COLUMNS+') VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)'
SOURCES={'BIRDEYE_OHLCV','BIRDEYE_TERMINAL_ATH_CHUNK','BIRDEYE_TERMINAL_ATH_FINALIZATION'}

class CheckpointHold(ValueError):pass

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def profile(path):
 p=Path(path).absolute();relative=p.relative_to('/private/tmp') if p.is_relative_to('/private/tmp') else None
 soft,_=resource.getrlimit(resource.RLIMIT_FSIZE)
 if (os.getenv('MONITOR_RUNTIME')!='dev' or relative is None or not relative.parts[0].startswith('dev004-') or p!=p.resolve()
     or soft==resource.RLIM_INFINITY or soft>MAX_DB_BYTES):raise CheckpointHold('CHECKPOINT_DISPOSABLE_PROFILE_REQUIRED')
 info=p.stat()
 if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>MAX_DB_BYTES:raise CheckpointHold('CHECKPOINT_FILE_BUDGET')
 return str(p)

def ensure_schema(c):
 row=c.execute('SELECT sql FROM sqlite_schema WHERE name=?',(TABLE,)).fetchone()
 normalize=lambda s:''.join(s.lower().replace('if not exists','').split())
 if row and normalize(row[0])!=normalize(DDL):raise CheckpointHold('CHECKPOINT_SCHEMA_INCOMPATIBLE')
 c.execute(DDL)

def observation(c,mint,interval,stamp):
 return c.execute('SELECT '+OBS_COLUMNS+' FROM operation_monitor_observations WHERE operation_id=? AND mint=? AND observation_timestamp=? AND resolution=?',('watchtower',mint,stamp,interval)).fetchone()

def verify(row,candle):
 if row is None:raise CheckpointHold('CHECKPOINT_EVIDENCE_MISSING')
 wanted=[candle['close'],candle['open'],candle['high'],candle['low'],candle['close']]
 actual=[row[3],*row[9:13]]
 if row[5] not in SOURCES or not row[6] or not row[7]:raise CheckpointHold('CHECKPOINT_PROVENANCE_UNQUALIFIED')
 if any(v is None or not math.isfinite(float(v)) for v in actual+wanted):raise CheckpointHold('CHECKPOINT_EVIDENCE_AMBIGUOUS')
 if any(float(a)!=float(b) for a,b in zip(actual,wanted)):raise CheckpointHold('CHECKPOINT_OBSERVATION_CONFLICT')
 o,h,l,close=map(float,wanted[1:])
 if l<0 or h<max(l,o,close) or l>min(o,close):raise CheckpointHold('CHECKPOINT_OHLC_INVALID')

def accepted_rows(c,mint,interval,candles,request_id,source,created_at):
 if not candles or len(candles)>MAX_CANDLES:raise CheckpointHold('CHECKPOINT_CANDLE_BUDGET')
 refs=[];seen={}
 for candle in candles:
  stamp=int(candle['timestamp'])
  if stamp in seen:
   if seen[stamp]!=candle:raise CheckpointHold('CHECKPOINT_DUPLICATE_CONFLICT')
   continue
  seen[stamp]=candle
  row=observation(c,mint,interval,stamp)
  if row is None:
   c.execute(OBS_INSERT,('watchtower',mint,stamp,float(candle['close']),interval,source,request_id,digest(candle),created_at,float(candle['open']),float(candle['high']),float(candle['low']),float(candle['close'])))
   row=observation(c,mint,interval,stamp)
  verify(row,candle)
  refs.append([stamp,digest(list(row))])
 return refs

def transaction(path,label,fn):
 path=profile(path);selector='live:'+os.path.realpath(path)
 database_write_service.register_database(selector,path)
 return database_write_service.submit(selector,label,fn)

def commit_window(path,mint,interval,left,right,chunk_id,candles,created_at):
 if not left<right:raise CheckpointHold('CHECKPOINT_WINDOW_CONFLICT')
 response=digest(candles)
 def commit(c):
  ensure_schema(c)
  old=c.execute('SELECT resolution,window_start,window_end,evidence_refs,response_digest FROM '+TABLE+' WHERE operation_id=? AND mint=? AND chunk_id=?',('watchtower',mint,chunk_id)).fetchone()
  refs=accepted_rows(c,mint,interval,candles,chunk_id,'BIRDEYE_TERMINAL_ATH_CHUNK',created_at)
  encoded=json.dumps(refs,separators=(',',':'))
  if len(encoded.encode())>MAX_REFS_BYTES:raise CheckpointHold('CHECKPOINT_REFERENCE_BUDGET')
  if old:
   if tuple(old)!=(interval,left,right,encoded,response):raise CheckpointHold('CHECKPOINT_COMPLETION_CONFLICT')
   return
  sizes=c.execute('SELECT length(evidence_refs) FROM '+TABLE+' LIMIT 4097').fetchall()
  if len(sizes)>=MAX_ROWS or sum(x[0] for x in sizes)+len(encoded)>MAX_TOTAL_REFS:raise CheckpointHold('CHECKPOINT_CAPACITY')
  c.execute('INSERT INTO '+TABLE+' VALUES(?,?,?,?,?,?,?,?,?)',('watchtower',mint,chunk_id,interval,left,right,encoded,response,created_at))
 transaction(path,'watchtower-terminal-ath-chunk',commit)
 return DurableWriteReceipt(True,time.time(),len(candles)+1)

def read_completed(path,mint,interval,planned):
 path=profile(path);complete={};steps={'1m':60,'5m':300,'15m':900};step=steps[interval]
 with sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True) as c:
  present=c.execute('SELECT 1 FROM sqlite_schema WHERE name=?',(TABLE,)).fetchone()
  for left,right,chunk_id in planned:
   row=c.execute('SELECT resolution,window_start,window_end,evidence_refs FROM '+TABLE+' WHERE operation_id=? AND mint=? AND chunk_id=?',('watchtower',mint,chunk_id)).fetchone() if present else None
   if row:
    if tuple(row[:3])!=(interval,left,right) or len(row[3].encode())>MAX_REFS_BYTES:raise CheckpointHold('CHECKPOINT_COMPLETION_CONFLICT')
    refs=json.loads(row[3]);candles=[]
    if not refs or len(refs)>MAX_CANDLES:raise CheckpointHold('CHECKPOINT_REFERENCE_BUDGET')
    for stamp,fingerprint in refs:
     evidence=observation(c,mint,interval,int(stamp))
     if evidence is None or digest(list(evidence))!=fingerprint:raise CheckpointHold('CHECKPOINT_EVIDENCE_CHANGED')
     candle={'timestamp':int(stamp),'open':evidence[9],'high':evidence[10],'low':evidence[11],'close':evidence[12]}
     verify(evidence,candle);candles.append(candle)
    complete[chunk_id]=candles
   else:
    # Preserve independently verified legacy full-window checkpoint evidence.
    rows=c.execute('SELECT '+OBS_COLUMNS+' FROM operation_monitor_observations WHERE operation_id=? AND mint=? AND resolution=? AND source=? AND request_identity=? ORDER BY observation_timestamp LIMIT 513',('watchtower',mint,interval,'BIRDEYE_TERMINAL_ATH_CHUNK',chunk_id)).fetchall()
    if rows:
     candles=[{'timestamp':x[2],'open':x[9],'high':x[10],'low':x[11],'close':x[12]} for x in rows]
     if len(rows)>MAX_CANDLES or rows[0][2]>left or rows[-1][2]+step<right or any(b[2]-a[2]>step for a,b in zip(rows,rows[1:])):raise CheckpointHold('CHECKPOINT_LEGACY_COVERAGE_UNQUALIFIED')
     for evidence,candle in zip(rows,candles):verify(evidence,candle)
     complete[chunk_id]=candles
 return complete

def commit_finalization(path,mint,interval,candles,request_id,created_at,update,params):
 def commit(c):
  accepted_rows(c,mint,interval,candles,request_id,'BIRDEYE_TERMINAL_ATH_FINALIZATION',created_at)
  fact=c.execute("SELECT ath_finalization_request_id,final_proven_ath_mc FROM operation_monitor_facts WHERE operation_id='watchtower' AND mint=?",(mint,)).fetchone()
  if not fact:raise CheckpointHold('ATH_FACT_MISSING')
  if fact[0] is not None and fact[1] is not None:
   if fact[0]!=request_id:raise CheckpointHold('ATH_FINALIZATION_CONFLICT')
   return
  if c.execute(update,params).rowcount!=1:raise CheckpointHold('ATH_FINALIZATION_UNCOMMITTED')
 transaction(path,'watchtower-terminal-ath-finalizer',commit)
 return DurableWriteReceipt(True,time.time(),len(candles)+1)
