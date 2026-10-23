from pathlib import Path
s=Path('/private/tmp/dev004-schema-bootstrap-child.py').read_text();exec(compile(s.split('results=[]')[0],'isolated-schema-owner-setup','exec'))
import ast,hashlib
import src.core.pumpfun_curve_listener as listener
from src.core import price_service,schema_init,creator_resolution_queue
from src.ops import migration_walkback_handoff_retry
owners=[('PumpFunCurveListener._ensure_db_with_connection',lambda c:listener.PumpFunCurveListener._ensure_db_with_connection(None,c)),('TokenPriceService._ensure_tables_with_connection',lambda c:price_service.TokenPriceService._ensure_tables_with_connection(None,c)),('schema_init.ensure_schema',lambda c:schema_init.ensure_schema(str(out/'core.sqlite'))),('creator_resolution_queue.initialize_schema',lambda c:creator_resolution_queue.initialize_schema(str(out/'core.sqlite'))),('migration_walkback_handoff_retry.ensure_schema',migration_walkback_handoff_retry.ensure_schema)]
def shape(c):return [tuple(r) for r in c.execute("select type,name,tbl_name,sql from sqlite_schema where name not like 'sqlite_%' order by type,name")]
result={'status':'HOLD_FRESH_BASE_CONTRACT','owners':[],'network_provider_calls':0}
try:
 c=sqlite3.connect(str(out/'core.sqlite'));c.row_factory=sqlite3.Row;c.execute('pragma foreign_keys=on');c.execute('pragma journal_mode=WAL');c.execute('pragma wal_autocheckpoint=0');peaks=[]
 for phase in [1,2]:
  for name,fn in owners:
   before=shape(c);fn(c);c.commit();after=shape(c);wal=out/'core.sqlite-wal';peaks.append(wal.stat().st_size if wal.exists() else 0)
   result['owners'].append({'pass':phase,'owner':name,'created_objects':[{'type':r[0],'name':r[1]} for r in after if r not in before]})
  if phase==1:first=shape(c)
  else:assert first==shape(c),'SCHEMA_CHANGED_ON_REPEAT'
 result['repeat_safe_schema']=True
 result['columns']={t:[{'name':r[1],'type':r[2]} for r in c.execute('pragma table_info("'+t+'")')] for t in ['token_analysis','token_market_cap_peaks','creator_resolution_queue','creator_funding_queue','birth_persist_queue','migration_persist_queue','migration_walkback_handoff','circuit_breaker_state','token_pool_accounts']}
 result['creator_queue_ready']=creator_resolution_queue.schema_ready(str(out/'core.sqlite'))
 result['foreign_key_check']=[tuple(r) for r in c.execute('pragma foreign_key_check')]
 result['missing_contracts']=['token_analysis.first_observed_at','token_market_cap_peaks base table']
 result['objects']=[{'type':r[0],'name':r[1],'table':r[2]} for r in shape(c)];result['user_rows']={r[1]:c.execute('select count(*) from "'+r[1]+'"').fetchone()[0] for r in shape(c) if r[0]=='table'}
 result['peak_sampled_wal_bytes']=max(peaks)
 c.close();result['core_bytes']=(out/'core.sqlite').stat().st_size
except BaseException as e:result.update(status='HOLD_OWNER_EXECUTION',error=str(e)[:400],traceback=traceback.format_exc(limit=5))
result['database_attempts']=db_attempts;result['blocked']=blocked
(out/'owner_result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k in ['status','repeat_safe_schema','creator_queue_ready','missing_contracts','peak_sampled_wal_bytes','core_bytes','error']}))
