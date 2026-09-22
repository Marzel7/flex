"""Single-concurrency post-commit enrichment worker for generic Pump.fun evidence."""
from __future__ import annotations
import json, sqlite3, time
from typing import Any, Callable, Mapping
from src.ops import pumpfun_opening_action_evidence as evidence

VERSION='pumpfun-opening-action-worker.v1'; MAX_ATTEMPTS=3
def ensure(c):
 evidence.ensure(c);c.executescript('''CREATE TABLE IF NOT EXISTS pumpfun_opening_enrichment_jobs(id TEXT PRIMARY KEY,raw_id TEXT UNIQUE NOT NULL,signature TEXT NOT NULL,slot INTEGER NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL);
 CREATE TABLE IF NOT EXISTS pumpfun_opening_enrichment_metrics(name TEXT PRIMARY KEY,value INTEGER NOT NULL DEFAULT 0);''')
def _inc(c,n): c.execute("INSERT INTO pumpfun_opening_enrichment_metrics VALUES(?,1) ON CONFLICT(name) DO UPDATE SET value=value+1",(n,))
def enqueue(path,*,raw_id,signature,slot,now=None):
 at=int(now or time.time());ident=evidence._id({'schema':VERSION,'raw':raw_id})
 with evidence.connect(path) as c:
  ensure(c);before=c.total_changes;c.execute('INSERT OR IGNORE INTO pumpfun_opening_enrichment_jobs VALUES(?,?,?,?,?,?,?,?,?)',(ident,raw_id,signature,slot,'pending',0,None,at,at));created=c.total_changes>before
  if created:_inc(c,'jobs_enqueued')
  else:_inc(c,'duplicates')
  c.commit()
 return {'id':ident,'created':created}
def bind_committed_transaction(path,tx:Mapping[str,Any],*,signature:str,slot:int,source='PumpFunCurveListener.handle_birth',env=None,now=None):
 """Bounded listener handoff: atomically commit raw source and spool job.

 The caller invokes this only after its own canonical source transaction has
 committed.  No resolver/network work occurs here; OFF is a strict no-op.
 """
 if not evidence.enabled(env): return None
 body=evidence._canon(tx).encode();raw_id=evidence._id({'v':evidence.SCHEMA_VERSION,'signature':signature,'slot':slot,'sha':__import__('hashlib').sha256(body).hexdigest()});at=int(now or time.time());job_id=evidence._id({'schema':VERSION,'raw':raw_id})
 with evidence.connect(path) as c:
  ensure(c);c.execute('BEGIN');c.execute('INSERT OR IGNORE INTO pumpfun_opening_raw_events VALUES(?,?,?,?,?,?,?,?)',(raw_id,signature,slot,body,__import__('hashlib').sha256(body).hexdigest(),source,'confirmed',at));before=c.total_changes;c.execute('INSERT OR IGNORE INTO pumpfun_opening_enrichment_jobs VALUES(?,?,?,?,?,?,?,?,?)',(job_id,raw_id,signature,slot,'pending',0,None,at,at));_inc(c,'jobs_enqueued' if c.total_changes>before else 'duplicates');c.commit()
 return {'raw_id':raw_id,'job_id':job_id}
def event_post_state_resolver(action:Mapping[str,Any]):
 """Event-carried reserves are explicitly post-transition but not full curve state."""
 p=json.loads(action['payload']); state=p.get('event_post_state') or {}
 if set(state) != {'post_virtual_sol_reserves','post_virtual_token_reserves','post_real_sol_reserves','post_real_token_reserves'}: return None
 return {'boundary':'POST_ACTION_STATE','raw_state':evidence._canon(state).encode(),'decoded':state,'parser_version':'pumpfun-trade-event-prefix.v1','provenance':'EVENT_EMITTED_POST_ACTION_RESERVES_PARTIAL'}
def _job(path):
 with evidence.connect(path) as c:
  ensure(c);return c.execute("SELECT * FROM pumpfun_opening_enrichment_jobs WHERE status='pending' ORDER BY created_at,id LIMIT 1").fetchone()
def process_one(path,*,block_resolver:Callable[[int],Mapping[str,Any]|None],state_resolver:Callable[[Mapping[str,Any]],Mapping[str,Any]|None]|None=None,now=None):
 """Resolvers run after read connection closes and before any write transaction."""
 row=_job(path)
 if not row:return {'result':'IDLE'}
 raw_id,slot=row['raw_id'],int(row['slot'])
 try:
  materialized=evidence.materialize(path,raw_id,now=now)
  # Check cache/artifact before provider resolver. The resolver itself may use a
  # shared cache; this local check guarantees one committed block per slot.
  with evidence.connect(path) as c: ensure(c);cached=c.execute('SELECT 1 FROM pumpfun_opening_block_artifacts WHERE slot=?',(slot,)).fetchone()
  if cached: block_result={'cache':'local'}
  else:
   block=block_resolver(slot) # no SQLite transaction is open here
   if block is None: raise RuntimeError('BLOCK_UNAVAILABLE')
   block_result=evidence.retain_block_and_enrich(path,block,slot=slot)
  with evidence.connect(path) as c:
   ensure(c);actions=[dict(x) for x in c.execute('SELECT * FROM pumpfun_opening_actions WHERE raw_id=?',(raw_id,)).fetchall()]
  for action in actions:
   if action['order_status'] != evidence.ORDER_COMPLETE: continue
   state=state_resolver(action) if state_resolver else None # never called in a DB tx
   if state and state.get('boundary') in {'PRE_ACTION_STATE','POST_ACTION_STATE'}:
    evidence.retain_boundary_state(path,mint=action['mint'],signature=action['signature'],slot=action['slot'],boundary=state['boundary'],raw_state=state['raw_state'],decoded=state['decoded'],parser_version=state['parser_version'],provenance=state['provenance'],now=now)
  with evidence.connect(path) as c:
   ensure(c);c.execute("UPDATE pumpfun_opening_enrichment_jobs SET status='complete',updated_at=? WHERE id=?",(int(now or time.time()),row['id']));_inc(c,'jobs_processed');_inc(c,'block_cache_hits' if cached else 'block_fetches');c.commit()
  return {'result':'COMPLETE','materialized':materialized,'block':block_result}
 except Exception as exc:
  with evidence.connect(path) as c:
   ensure(c);attempts=int(row['attempts'])+1;status='dead_letter' if attempts>=MAX_ATTEMPTS else 'pending';c.execute('UPDATE pumpfun_opening_enrichment_jobs SET attempts=?,status=?,last_error=?,updated_at=? WHERE id=?',(attempts,status,str(exc)[:500],int(now or time.time()),row['id']));_inc(c,'dead_letters' if status=='dead_letter' else 'retries');c.commit()
  return {'result':status.upper(),'reason':str(exc)}
def metrics(path):
 with evidence.connect(path) as c:ensure(c);return {r['name']:r['value'] for r in c.execute('SELECT * FROM pumpfun_opening_enrichment_metrics')}
def submission_capability():return 'NONE'
