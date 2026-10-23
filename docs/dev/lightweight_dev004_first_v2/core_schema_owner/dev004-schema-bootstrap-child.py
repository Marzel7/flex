from pathlib import Path
# Reuse only isolation setup, not its import/startup substitutions.
s=Path('/private/tmp/dev004-runtime-probe.py').read_text();exec(compile(s.split("result={'component'")[0],'isolation','exec'))
import hashlib,ast
os.environ['RPC_METRICS_DB']=str(out/'core.sqlite')
db_attempts=[]
def sqlite_audit(event,args):
 if event=='sqlite3.connect':
  value=str(args[0]);p=Path(value[5:].split('?')[0] if value.startswith('file:') else value).resolve()
  allowed=value==':memory:' or p in [(out/'core.sqlite').resolve(),(out/'ops.sqlite').resolve()]
  db_attempts.append({'path':str(p),'allowed':allowed,'frames':[{'file':f.filename,'line':f.lineno,'function':f.name} for f in traceback.extract_stack(limit=5)] if not allowed else []})
  if not allowed:raise RuntimeError('UNDECLARED_DATABASE_BINDING')
sys.addaudithook(sqlite_audit)


def schema(c):return [tuple(r) for r in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
def size():return {p.name:p.stat().st_size for p in out.iterdir() if p.is_file() and ('.sqlite' in p.name)}
results=[]
kind=os.environ['BOOTSTRAP_KIND']
# Sqlite ownership is explicit; all connection opens retain the isolation guard.
if kind=='core':
 import src.core.pumpfun_curve_listener as listener
 from src.core import schema_init,creator_resolution_queue
 from src.ops import migration_walkback_handoff_retry
 owners=[('listener.PumpFunCurveListener._ensure_db_with_connection',lambda c:listener.PumpFunCurveListener._ensure_db_with_connection(None,c)),('schema_init.ensure_schema',lambda c:schema_init.ensure_schema(str(out/'core.sqlite'))),('creator_resolution_queue.initialize_schema',lambda c:creator_resolution_queue.initialize_schema(str(out/'core.sqlite'))),('migration_walkback_handoff_retry.ensure_schema',migration_walkback_handoff_retry.ensure_schema)]
 path=out/'core.sqlite'
else:
 from src.core import walkback_queue,treasury_bank,deep_walkback
 from src.ops import watchtower_candidates,attribution_outcome,anchor_reconciliation,create_event_ledger,operation_fingerprint_drift,lineage_quarantine
 owners=[('walkback_queue.ensure_schema',walkback_queue.ensure_schema),('watchtower_candidates.ensure_schema',watchtower_candidates.ensure_schema),('treasury_bank.ensure_schema',treasury_bank.ensure_schema),('attribution_outcome.ensure_schema',attribution_outcome.ensure_schema),('anchor_reconciliation.ensure_schema',anchor_reconciliation.ensure_schema),('create_event_ledger.ensure_schema',create_event_ledger.ensure_schema),('operation_fingerprint_drift.ensure_schema',operation_fingerprint_drift.ensure_schema),('deep_walkback.ensure_schema',deep_walkback.ensure_schema),('lineage_quarantine.ensure_lineage_quarantine_schema',lineage_quarantine.ensure_lineage_quarantine_schema)]
 path=out/'ops.sqlite'
try:
 c=sqlite3.connect(str(path));c.row_factory=sqlite3.Row;c.execute('pragma foreign_keys=on');c.execute('pragma journal_mode=WAL');c.execute('pragma wal_autocheckpoint=0')
 peak=0
 for name,fn in owners:
  before=schema(c);started=time.monotonic()
  try:fn(c);c.commit()
  except BaseException as e:
   c.rollback();results.append({'owner':name,'status':'HOLD','error':str(e)[:400]});raise
  after=schema(c);wal=path.with_name(path.name+'-wal');peak=max(peak,wal.stat().st_size if wal.exists() else 0)
  results.append({'owner':name,'status':'PASS','created_objects':[{'type':r[0],'name':r[1]} for r in after if r not in before],'seconds':round(time.monotonic()-started,5),'wal_bytes':wal.stat().st_size if wal.exists() else 0})
 first=schema(c)
 for name,fn in owners:fn(c);c.commit()
 assert schema(c)==first,'SCHEMA_NOT_IDEMPOTENT'
 fk=[tuple(r) for r in c.execute('pragma foreign_key_check')]
 validators={}
 if kind=='ops':
  v=walkback_queue.validate_schema(c);validators['walkback']={'valid':v.valid,'code':v.code,'mismatch':v.mismatch}
  for name,module in [('treasury',treasury_bank),('outcome',attribution_outcome),('anchor',anchor_reconciliation),('ledger',create_event_ledger),('fingerprint',operation_fingerprint_drift)]:validators[name]=module.validate_schema(c)
 result={'status':'PASS' if not fk else 'HOLD_FOREIGN_KEYS','kind':kind,'owners':results,'idempotent_schema':True,'foreign_key_check':fk,'validators':validators,'objects':[{'type':r[0],'name':r[1],'table':r[2]} for r in first],'empty_user_rows':{r[1]:c.execute('SELECT COUNT(*) FROM "'+r[1]+'"').fetchone()[0] for r in first if r[0]=='table'},'peak_observed_wal_bytes':peak,'sizes_open':size(),'blocked':blocked,'database_attempts':db_attempts}
 c.close();result['sizes_closed']=size();result['database_attempts']=db_attempts
except BaseException as e:result={'status':'HOLD','kind':kind,'owners':results,'error_type':type(e).__name__,'error':str(e)[:500],'traceback':traceback.format_exc(limit=6),'blocked':blocked,'database_attempts':db_attempts}
(out/(kind+'-bootstrap.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k in ['status','kind','error','validators','idempotent_schema','peak_observed_wal_bytes','sizes_closed']}))
