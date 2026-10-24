import os,sqlite3,json,hashlib,resource,signal,sys
sys.path.insert(0,"/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex/runtime/listener")
from src.ops import dev004_operational_continuation as continuation
from src.ops.dev004_quarantine_transfer import capacity
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,499000000));signal.signal(signal.SIGXFSZ,signal.SIG_IGN)
BASE=Path('/Users/kevinkeaveney/Dev/claude/flex/.runtime/dev004-v2/databases');SRC=Path('/Users/kevinkeaveney/Dev/claude/flex/database');MAX=128
CHECKPOINTS=BASE.parent/"checkpoints"/os.environ["DEV004_RECONCILIATION_EPOCH"];CHECKPOINTS.mkdir(exist_ok=True)
sources={};targets={};counts={};digests={};selected={};total=0;source_absences=set()
for kind,name in [('core','flex_complete_database.db'),('ops','wt_ops_v2.db')]:
 s=sqlite3.connect((SRC/name).as_uri()+'?mode=ro',uri=True,timeout=1);s.row_factory=sqlite3.Row;s.execute('pragma query_only=on');sources[kind]=s
 t=sqlite3.connect(BASE/(kind+'.sqlite'));t.row_factory=sqlite3.Row;t.execute('pragma foreign_keys=on');t.execute('pragma max_page_count=117187');targets[kind]=t

def commit_pages():
 for t in targets.values():t.commit()
 for path in BASE.glob('*.sqlite'):capacity(path)
 for t in targets.values():t.execute('begin');t.execute('pragma defer_foreign_keys=on')

def rows(kind,table,where='',args=()):
 c=sources[kind]
 if where in ('mint=?','migration_tx=?'):
  continuation.bound_query(c)
  found=[dict(r) for r in c.execute('select * from "'+table+'" where '+where+' limit 2',args)]
  if len(found)>1:raise RuntimeError('AMBIGUOUS_IDENTITY:'+table)
  return found
 def stream():
  key=hashlib.sha256(json.dumps([kind,table,where,args]).encode()).hexdigest()[:24]
  accepted=(lambda row: row.get('status')==args[0]) if where=='status=?' else (lambda row:True)
  if where and where!='status=?':raise RuntimeError('UNSUPPORTED_PREDICATE')
  yield from continuation.pages(c,SRC/('flex_complete_database.db' if kind=='core' else 'wt_ops_v2.db'),CHECKPOINTS/(key+'.json'),table,commit=commit_pages,accept=accepted)
 return stream()

def put(kind,table,row):
 global total
 t=targets[kind];info=list(t.execute('pragma table_xinfo("'+table+'")'))
 if not info:
  ddl=sources[kind].execute("select sql from sqlite_master where type='table' and name=?",(table,)).fetchone();assert ddl,'NO_ACCEPTED_SCHEMA:'+table;t.execute(ddl[0]);info=list(t.execute('pragma table_xinfo("'+table+'")'))
 keys=[r[1] for r in sorted(info,key=lambda r:r[5]) if r[5]]
 if not keys:
  if table=='birth_retry_quarantine' and not row:return
  raise RuntimeError('NO_PRIMARY_IDENTITY:'+table)
 writable={r[1] for r in info if not r[6]};cols=[x for x in row if x in writable];pk=tuple(row[x] for x in keys)
 name=kind+'.'+table
 total+=1
 encoded=json.dumps(row,sort_keys=True,default=str).encode();assert len(encoded)<1048576
 old=t.execute('select * from "'+table+'" where '+' and '.join('"'+k+'" is ?' for k in keys),pk).fetchone()
 if old is not None and dict(old)==row:return
 if old is not None:
  t.execute('update "'+table+'" set '+','.join('"'+x+'"=?' for x in cols if x not in keys)+' where '+' and '.join('"'+k+'" is ?' for k in keys),tuple(row[x] for x in cols if x not in keys)+pk)
 else:t.execute('insert into "'+table+'"('+','.join('"'+x+'"' for x in cols)+') values('+','.join('?' for _ in cols)+')',tuple(row[x] for x in cols))
 actual=t.execute('select * from "'+table+'" where '+' and '.join('"'+k+'" is ?' for k in keys),pk).fetchone()
 if dict(actual)!=row:raise RuntimeError('EXACT_IMPORTED_STATE_MISMATCH:'+name)
 counts[name]=counts.get(name,0)+1
 digests.setdefault(name,hashlib.sha256()).update(hashlib.sha256(encoded).digest())

def token(mint):
 if not mint:return
 r=rows('core','token_analysis','mint=?',(mint,))
 if not r:
  source_absences.add(mint);return
 if len(r)!=1:raise RuntimeError('AMBIGUOUS_CANONICAL_TOKEN:'+mint)
 put('core','token_analysis',r[0])

try:
 for t in targets.values():t.execute('begin');t.execute('pragma defer_foreign_keys=on')
 # Finite operation owners, not historical provider populations.
 for table in ['operators','operator_identity_events','confirmed_operation_matches','monitor_admission_outbox']:
  for r in rows('ops',table):put('ops',table,r)
 memberships=rows('ops','operator_launch_membership')
 for r in memberships:
  mint=r['mint'];token(mint)
  for table in ['wt_attribution_outcomes','wt_walkback_queue']:
   for dep in rows('ops',table,'mint=?',(mint,)):put('ops',table,dep)
  put('ops','operator_launch_membership',r)
 for kind,table,states in [('core','creator_resolution_queue',['pending','retry','running','failed']),('core','creator_funding_queue',['pending','retry','running']),('core','birth_persist_queue',['PENDING','RETRY','QUARANTINED','FAILED','RETRY_EXHAUSTED']),('ops','wt_walkback_queue',['pending','retry','running','waiting'])]:
  for state in states:
   for r in rows(kind,table,'status=?',(state,)):
    if table in ['creator_resolution_queue','creator_funding_queue','wt_walkback_queue']:token(r.get('mint'))
    put(kind,table,r)
 for kind,table in [('core','migration_persist_queue'),('core','webhook_birth_queue'),('core','birth_retry_quarantine'),('core','wb_create_control'),('core','wb_create_delivery'),('core','migration_walkback_handoff')]:
  if sources[kind].execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone():
   for r in rows(kind,table):
    if table!='migration_walkback_handoff' or r.get('state') not in ('COMPLETE','TERMINAL'):token(r.get('mint'))
    if table=='migration_walkback_handoff' and r.get('state') not in ('COMPLETE','TERMINAL'):
     for dep in rows('ops','wt_walkback_queue','mint=?',(r['mint'],)):put('ops','wt_walkback_queue',dep)
    put(kind,table,r)
 # Preserve every canonical admission since journal deployment, including verified completions reclaimed before the fence.
 state_root=BASE.parent
 import time
 boundary=CHECKPOINTS/'boundary.json'
 if boundary.exists():low,high=json.load(open(boundary))
 else:
  low=str(int(state_root.stat().st_birthtime)-10);high=str(int(time.time())+10);continuation.q.write_checkpoint(boundary,[low,high])
 admission_count=0
 for r in continuation.pages(sources['core'],SRC/'flex_complete_database.db',CHECKPOINTS/'canonical-admissions-desc.json','token_analysis',commit=commit_pages,where='created_at>=? AND created_at<=?',args=(low,high),order=('-created_at','rowid')):
  admission_count+=1;put('core','token_analysis',r)
 witness=json.load(open(state_root/'checkpoints/pre-fence-admission-witness.json'))
 for kind,records in witness.items():
  for record in records:
   mint=record.get('mint')
   if not mint and kind=='migration':
    found=rows('core','token_analysis','migration_tx=?',(record['signature'],));assert len(found)<=1
    if found:mint=found[0]['mint']
   if mint:
    token(mint)
    if record['state']=='ACKED' and mint in source_absences:raise RuntimeError('ACK_CANONICAL_SOURCE_MISSING')
    if kind=='migration':
     for r in rows('core','migration_walkback_handoff','mint=?',(mint,)):put('core','migration_walkback_handoff',r)
     for r in rows('ops','wt_walkback_queue','mint=?',(mint,)):put('ops','wt_walkback_queue',r)
 # Old owners are stopped; copied running claims become recoverable, never active leases.
 released={}
 for kind,table,expiry in [('core','creator_resolution_queue','locked_until'),('core','creator_funding_queue','locked_until'),('ops','wt_walkback_queue','lease_expires_at')]:
  t=targets[kind];info=list(t.execute('pragma table_info("'+table+'")'));cols={r[1]:r for r in info};pk=[r[1] for r in info if r[5]]
  while True:
   old=t.execute('select * from "'+table+'" where status="running" limit 1').fetchone()
   if old is None:break
   changes={'status':'retry'}
   if expiry in cols:changes[expiry]=0
   for key in ['lease_owner','locked_by','claimed_by']:
    if key in cols:changes[key]='' if cols[key][3] else None
   t.execute('update "'+table+'" set '+','.join('"'+k+'"=?' for k in changes)+' where '+' and '.join('"'+k+'"=?' for k in pk),tuple(changes.values())+tuple(old[k] for k in pk))
   name=kind+'.'+table;released[name]=released.get(name,0)+1
   commit_pages()
 # Exact existing V1 index required for duplicate signature-only recovery.
 sql=sources['core'].execute("select sql from sqlite_master where name='idx_psi0a_token_analysis_migration_tx' and type='index'").fetchone()[0]
 if not targets['core'].execute('select 1 from sqlite_master where name=?',('idx_psi0a_token_analysis_migration_tx',)).fetchone():targets['core'].execute(sql)
 for kind,t in targets.items():
  problem=t.execute('select * from pragma_foreign_key_check limit 1').fetchone()
  if problem:raise RuntimeError('FK:'+kind+':'+str(tuple(problem)))
 for t in targets.values():t.commit()
 result={'status':'PASS_PRELIMINARY_OPERATIONAL_STAGE','final_fence':True,'old_lease_releases':released,'admission_window_rows':admission_count,'reconciled_changes':counts,'selected_rows':total,'digest':{k:v.hexdigest() for k,v in digests.items()},'schema_index_from_V1':sql,'free_bytes':os.statvfs(BASE).f_bavail*os.statvfs(BASE).f_frsize,'database_sizes':{k:(BASE/(k+'.sqlite')).stat().st_size for k in targets},'production_mutations':0,'preserved_source_absence_count':len(source_absences)}
except Exception as e:
 for t in targets.values():t.rollback()
 import traceback
 result={'status':'HOLD','error':str(e),'traceback':traceback.format_exc(),'production_mutations':0}
finally:
 for c in list(sources.values())+list(targets.values()):c.close()
(BASE/'operational-stage.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2));sys.exit(0 if result['status'].startswith('PASS') else 1)
