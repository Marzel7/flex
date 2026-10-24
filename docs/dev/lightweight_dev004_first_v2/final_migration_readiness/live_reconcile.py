"""Bounded read-only operational selectors; metadata oracles, never V1 copies."""
import datetime,hashlib,json,os,resource,signal,sqlite3,time
from pathlib import Path
ROOT=Path('/Users/kevinkeaveney/Dev/claude/flex/database');OUT=Path('/private/tmp/dev004-final-live-state-20261024');OUT.mkdir(exist_ok=False)
MAX_ROWS=12000;BATCH=128;MAX_ROW=1024*1024
signal.signal(signal.SIGXFSZ,signal.SIG_IGN);resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024*1024,64*1024*1024))

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=lambda v:{'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()} if isinstance(v,bytes) else str(v)).encode()).hexdigest()
def ro(file):
 c=sqlite3.connect('file:'+str(ROOT/file)+'?mode=ro',uri=True,timeout=.2);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.set_progress_handler(lambda:1,2_000_000);return c
core=ro('flex_complete_database.db');ops=ro('wt_ops_v2.db');all_results={};sample_rows={};blockers=[]
def population(db,table,disposition,status=None,states=None,key=None,all_rows=False):
 c={'core':core,'ops':ops}[db];cols=[r[1] for r in c.execute('PRAGMA table_info("'+table+'")')]
 if not cols:raise RuntimeError('MISSING_OWNER_TABLE:'+table)
 pk=[r[1] for r in c.execute('PRAGMA table_info("'+table+'")') if r[5]];pk=pk or ['rowid'];key=key or pk[0];rows=[];plans=[];cursor=None;examined=0
 if all_rows:
  # A finite operational owner, not a historical database table. Keyset walk.
  for _ in range(1+MAX_ROWS//BATCH):
   sql='SELECT * FROM "'+table+'"'+(' WHERE "'+key+'">?' if cursor is not None else '')+' ORDER BY "'+key+'" LIMIT '+str(BATCH);args=(cursor,) if cursor is not None else ();plan=[r[3] for r in c.execute('EXPLAIN QUERY PLAN '+sql,args)];plans=plan
   batch=c.execute(sql,args).fetchall();examined+=len(batch)
   if examined>MAX_ROWS:raise RuntimeError('POPULATION_BOUND:'+table)
   rows.extend(batch)
   if len(batch)<BATCH:break
   cursor=batch[-1][key]
  else:raise RuntimeError('POPULATION_BOUND:'+table)
 else:
  assert status and states
  for state in states:
   sql='SELECT * FROM "'+table+'" WHERE "'+status+'"=? LIMIT '+str(MAX_ROWS+1);plan=[r[3] for r in c.execute('EXPLAIN QUERY PLAN '+sql,(state,))]
   if any('SCAN '+table in p or ('SCAN ' in p and 'INDEX' not in p) for p in plan):raise RuntimeError('UNINDEXED_STATE:'+table+':'+state)
   plans.append({'state':state,'plan':plan});batch=c.execute(sql,(state,)).fetchall();examined+=len(batch)
   if examined>MAX_ROWS:raise RuntimeError('POPULATION_BOUND:'+table)
   rows.extend(batch)
 bykey={};counts={};expired=[]
 for r in rows:
  v=dict(r);encoded=json.dumps(v,default=str).encode();assert len(encoded)<=MAX_ROW,'ROW_BOUND:'+table;identity=tuple(v.get(k) for k in pk);bykey[identity]={'key':list(identity),'sha256':digest(v),'disposition':disposition};s=str(v.get(status)) if status else 'retained';counts[s]=counts.get(s,0)+1
  if s.lower()=='running' and ('locked_until' in v or 'lease_expires_at' in v):
   deadline=v.get('locked_until',v.get('lease_expires_at'));expired.append({'key':list(identity),'expired':bool(deadline and deadline<time.time()),'disposition':'RETAIN_IN_V1_UNTIL_RELEASE','rule':'Frozen ownership evidence only; never activate or copy as current lease.'})
 selected=sorted(bykey.values(),key=lambda x:json.dumps(x['key'],default=str));name=db+'.'+table;all_results[name]={'count':len(selected),'examined':examined,'status_counts':counts,'pk':pk,'indexed_plans':plans,'continuation':{'batch_rows':BATCH,'last_key':cursor,'resume_rule':'Same frozen source generation, expected PK/digest; conflicts invalidate; publish cursor only after target commit; no identity dedup based solely on row position.'},'disposition':disposition,'expired_leases':expired,'rows':selected,'digest':digest(selected)};sample_rows[name]=[dict(x) for x in rows[:3]]
try:
 for db,table,disp,kw in [('ops','operators','TRANSFER',{'all_rows':True,'key':'operator_id'}),('ops','operator_launch_membership','TRANSFER',{'all_rows':True,'key':'mint'}),('core','creator_resolution_queue','TRANSFER',{'status':'status','states':['pending','retry','running','failed']}),('ops','wt_walkback_queue','TRANSFER',{'status':'status','states':['pending','retry','running','waiting']}),('core','creator_funding_queue','RETAIN_IN_V1_UNTIL_RELEASE',{'status':'status','states':['pending','retry','running']}),('core','birth_persist_queue','TRANSFER',{'status':'status','states':['PENDING','RETRY']}),('core','migration_persist_queue','TRANSFER',{'all_rows':True,'key':'rowid'}),('core','webhook_birth_queue','TRANSFER',{'all_rows':True,'key':'id'}),('ops','wt_lineage_quarantine','TRANSFER',{'all_rows':True,'key':'quarantine_id'})]:
  try:population(db,table,disp,**kw)
  except Exception as e:blockers.append({'population':db+'.'+table,'type':type(e).__name__,'detail':str(e)[:200]})
finally:core.close();ops.close()
# All current operations/memberships, not a repeated historical seed import.
result={'status':'PASS_BOUNDED_OPERATIONAL_SELECTION' if not blockers else 'HOLD','observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_mode':'read-only indexed/keyset operational reads; independent live snapshots, not final atomic generation','populations':all_results,'blockers':blockers,'dispositions':{'completed_Walkback':'PRESERVE_AS_REFERENCE through accepted seed/outcome/dedup closure; unchanged functions and prior evidence reused, not wholesale completed-history copy.','receipts':'PRESERVE_AS_REFERENCE in existing independent root with deployed1536capacity; no copy/deletion/reset.','Birdeye_checkpoint_budget':'PRESERVE_AS_REFERENCE in existing qualified checkpoint/budget authority; consumed debits retained, no fresh allowance.','frozen_creator_history_freshness':'TRANSFER exact qualified compact files to persistent bound V2auxiliary paths; no recalculation.','final_admission_watermark':'TRANSFER only after existing source writer retirement and durable journal handoff; current live counts are not represented as final generation. Existing paired transfer protocol tested in disposable fixtures.'},'production_mutations':0,'provider_calls':0,'max_source_rows_per_owner':MAX_ROWS,'no_historical_bulk':True}
(OUT/'reconciliation.json').write_text(json.dumps(result,indent=2,default=str)+'\n');(OUT/'sample_rows.json').write_text(json.dumps(sample_rows,indent=2,default=str)+'\n');print(json.dumps({'status':result['status'],'populations':{k:{x:v[x] for x in ['count','disposition','status_counts']} for k,v in all_results.items()},'blockers':blockers},indent=2))
