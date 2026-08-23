"""Durable, read-only timing diagnosis for the frozen S2B population query."""
import argparse,hashlib,json,os,sqlite3,time
from pathlib import Path

SNAPSHOT=Path('docs/audits/ops_discovery_p3r_s2b_runs/s2b-source-snapshot-20260822T221819272628000Z-16a03978948f3881020440ceeb080e8a/snapshot.sqlite')
AUDIT=Path('docs/audits/ops_discovery_p3r_s2b_population_query_diagnostic.json')
QUERY="""SELECT ta.mint,ta.pf_ws_creator FROM token_analysis AS ta INDEXED BY idx_ta_pf_ws_creator WHERE ta.pf_ws_creator IS NOT NULL AND EXISTS (SELECT 1 FROM pumpfun_migration_verification AS pmv WHERE pmv.mint=ta.mint) ORDER BY ta.mint ASC,ta.pf_ws_creator ASC"""
def save(d):
 t=AUDIT.with_suffix('.tmp');t.write_text(json.dumps(d,indent=2,sort_keys=True)+'\n');os.replace(t,AUDIT)
def main():
 global AUDIT
 ap=argparse.ArgumentParser();ap.add_argument('--snapshot',default=str(SNAPSHOT));ap.add_argument('--audit',default=str(AUDIT));ap.add_argument('--wall-seconds',type=float,default=120);args=ap.parse_args()
 AUDIT=Path(args.audit); snapshot=Path(args.snapshot);start=time.monotonic();deadline=start+args.wall_seconds
 d={'milestone':'OPS-DISCOVERY-P3R-S2B-POPULATION-QUERY-PERFORMANCE-DIAGNOSIS','status':'STARTED','snapshot':str(snapshot),'query_sha256':hashlib.sha256(QUERY.encode()).hexdigest(),'provider_calls':0,'production_writes':0,'bounds':{'wall_seconds':args.wall_seconds,'deadline_monotonic':deadline,'replay':False},'active_stage':'STARTED','last_completed_stage':None};save(d)
 def guard():
  if time.monotonic()>=deadline: raise TimeoutError('BOUND_EXCEEDED')
 try:
  c=sqlite3.connect('file:'+str(snapshot.resolve())+'?mode=ro',uri=True);c.execute('pragma query_only=on');c.set_progress_handler(lambda:int(time.monotonic()>=deadline),1000);d['active_stage']='QUERY_PLAN';d['plan']=list(c.execute('EXPLAIN QUERY PLAN '+QUERY));d['last_completed_stage']='QUERY_PLAN';save(d);guard()
  d['active_stage']='TIME_TO_FIRST_ROW';save(d);t=time.monotonic();cur=c.execute(QUERY);first=next(cur);d['time_to_first_row_seconds']=time.monotonic()-t;d['first_row_observed']=True;d['last_completed_stage']='TIME_TO_FIRST_ROW';save(d);guard()
  d['active_stage']='SQL_COUNT';save(d);t=time.monotonic();n=c.execute('SELECT COUNT(*) FROM ('+QUERY+')').fetchone()[0];d['sql_count']=n;d['sql_count_seconds']=time.monotonic()-t;d['last_completed_stage']='SQL_COUNT';save(d)
  d['active_stage']='MANIFEST_SERIALIZATION_DIGEST';save(d);t=time.monotonic();h=hashlib.sha256();count=0
  for mint,creator in c.execute(QUERY):
   guard()
   b=(json.dumps({'population_ordinal':count+1,'mint':mint,'create_creator':creator,'source_table':'token_analysis','migration_verification_table':'pumpfun_migration_verification'},sort_keys=True,separators=(',',':'))+'\n').encode();h.update(b);count+=1
  d['manifest_count']=count;d['manifest_sha256']=h.hexdigest();d['manifest_serialization_digest_seconds']=time.monotonic()-t;d['one_pass_projected_seconds']=time.monotonic()-start;d['two_pass_projected_seconds']=d['one_pass_projected_seconds']+d['manifest_serialization_digest_seconds'];d['last_completed_stage']='MANIFEST_SERIALIZATION_DIGEST';d['active_stage']='COMPLETE';d['status']='COMPLETE';save(d);return 0
 except Exception as e:
  d.update(status='HOLD_BOUND_EXCEEDED' if isinstance(e,(TimeoutError,sqlite3.OperationalError)) and ('BOUND' in str(e) or 'interrupted' in str(e)) else 'HOLD_ERROR',exception_type=type(e).__name__,exception=str(e),elapsed_seconds=time.monotonic()-start,timeout_stage=d.get('active_stage'));save(d);return 3
if __name__=='__main__':raise SystemExit(main())
