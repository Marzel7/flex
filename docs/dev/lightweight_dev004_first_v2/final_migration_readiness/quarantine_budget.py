"""Bounded quarantine payload accounting, keys/digests only, no source copy."""
import gzip,hashlib,json,os,resource,signal,sqlite3,time
from pathlib import Path
signal.signal(signal.SIGXFSZ,signal.SIG_IGN);resource.setrlimit(resource.RLIMIT_FSIZE,(128*1024*1024,128*1024*1024))
OUT=Path('/private/tmp/dev004-final-quarantine-budget-qualified-20261024');OUT.mkdir(exist_ok=False);DB=Path('/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db');c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True,timeout=.2);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');fuel=[0]
def progress():
 fuel[0]+=1000;return int(fuel[0]>=200000)
c.set_progress_handler(progress,1000);limit=256*1024*1024;cursor=None;rows=encoded=0;maxrow=0;started=time.monotonic();status='HOLD';reason=None
upper=c.execute('SELECT quarantine_id FROM wt_lineage_quarantine ORDER BY quarantine_id DESC LIMIT 1').fetchone()[0]
with gzip.open(OUT/'row_oracles.jsonl.gz','wt') as f:
 for step in range(3000):
  free=os.statvfs(OUT).f_bavail*os.statvfs(OUT).f_frsize
  if free<2_000_000_000+64*1024*1024:reason='DISK_FLOOR_RESERVE';break
  sql='SELECT * FROM wt_lineage_quarantine WHERE quarantine_id<=?'+(' AND quarantine_id>?' if cursor else '')+' ORDER BY quarantine_id LIMIT 128';args=(upper,cursor) if cursor else (upper,);fuel[0]=0;batch=c.execute(sql,args).fetchall()
  for r in batch:
   b=json.dumps(dict(r),sort_keys=True,separators=(',',':')).encode();encoded+=len(b);rows+=1;maxrow=max(maxrow,len(b));assert len(b)<1024*1024
   line=json.dumps({'key':r['quarantine_id'],'sha256':hashlib.sha256(b).hexdigest()})+'\n';assert len(line.encode())<=512 and f.tell()+len(line.encode())<128*1024*1024;f.write(line);cursor=r['quarantine_id']
  if encoded>limit:reason='PROTECTED_QUARANTINE_ENCODED_BUDGET';break
  if len(batch)<128:status='PASS_COMPLETE_BOUNDED_ACCOUNTING';break
 else:reason='ROW_STEP_BUDGET'
c.close();result={'status':status,'reason':reason,'rows_examined':rows,'encoded_bytes':encoded,'encoded_budget':limit,'maximum_row_bytes':maxrow,'cursor':cursor,'captured_upper_identity':upper,'seconds':time.monotonic()-started,'source_reads':'Individual128row indexed queries, no long source snapshot, no writer lock. Current observation requires final fenced revalidation; no atomic crossDBclaim.','protection':'All rows retained in V1; no rows copied or excluded. Only key/hash metadata captured.','production_mutations':0};(OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
