import os,sqlite3,json,hashlib,resource,signal
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,499000000));signal.signal(signal.SIGXFSZ,signal.SIG_IGN)
BASE=Path('/Users/kevinkeaveney/Dev/claude/flex/.runtime/dev004-v2/databases');SRC=Path('/Users/kevinkeaveney/Dev/claude/flex/database');MAX=12000
sources={};targets={};counts={};digests={};selected={};total=0;source_absences=set()
for kind,name in [('core','flex_complete_database.db'),('ops','wt_ops_v2.db')]:
 s=sqlite3.connect((SRC/name).as_uri()+'?mode=ro',uri=True,timeout=1);s.row_factory=sqlite3.Row;s.execute('pragma query_only=on');s.set_progress_handler(lambda:1,2000000);sources[kind]=s
 t=sqlite3.connect(BASE/(kind+'.sqlite'));t.row_factory=sqlite3.Row;t.execute('pragma foreign_keys=on');t.execute('pragma max_page_count=117187');targets[kind]=t

def rows(kind,table,where='',args=()):
 c=sources[kind];sql='select * from "'+table+'"'+(' where '+where if where else '')+' limit '+str(MAX+1)
 if where:
  plan=[r[3] for r in c.execute('explain query plan '+sql,args)]
  if any(x.startswith('SCAN ') and 'INDEX' not in x for x in plan):raise RuntimeError('UNINDEXED_SELECTION:'+table)
 found=[dict(r) for r in c.execute(sql,args)]
 if len(found)>MAX:raise RuntimeError('ROW_LIMIT:'+table)
 return found

def put(kind,table,row):
 global total
 t=targets[kind];info=list(t.execute('pragma table_xinfo("'+table+'")'))
 if not info:
  ddl=sources[kind].execute("select sql from sqlite_master where type='table' and name=?",(table,)).fetchone();assert ddl,'NO_ACCEPTED_SCHEMA:'+table;t.execute(ddl[0]);info=list(t.execute('pragma table_xinfo("'+table+'")'))
 keys=[r[1] for r in sorted(info,key=lambda r:r[5]) if r[5]]
 if not keys:raise RuntimeError('NO_PRIMARY_IDENTITY:'+table)
 writable={r[1] for r in info if not r[6]};cols=[x for x in row if x in writable];pk=tuple(row[x] for x in keys)
 name=kind+'.'+table
 if pk in selected.setdefault(name,set()):return
 selected[name].add(pk);total+=1
 if total>30000:raise RuntimeError('TOTAL_ROW_BOUND')
 encoded=json.dumps(row,sort_keys=True,default=str).encode();assert len(encoded)<1048576
 old=t.execute('select * from "'+table+'" where '+' and '.join('"'+k+'" is ?' for k in keys),pk).fetchone()
 if old is not None and dict(old)==row:return
 if old is not None:
  t.execute('update "'+table+'" set '+','.join('"'+x+'"=?' for x in cols if x not in keys)+' where '+' and '.join('"'+k+'" is ?' for k in keys),tuple(row[x] for x in cols if x not in keys)+pk)
 else:t.execute('insert into "'+table+'"('+','.join('"'+x+'"' for x in cols)+') values('+','.join('?' for _ in cols)+')',tuple(row[x] for x in cols))
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
 for kind,table in [('core','migration_persist_queue'),('core','webhook_birth_queue'),('core','birth_retry_quarantine')]:
  if sources[kind].execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone():
   for r in rows(kind,table):put(kind,table,r)
 # Preserve every canonical admission since journal deployment, including verified completions reclaimed before the fence.
 state_root=BASE.parent
 import time
 low=str(int(state_root.stat().st_birthtime)-10);high=str(int(time.time())+10)
 admission_sql='SELECT * FROM token_analysis WHERE created_at>=? AND created_at<=? ORDER BY created_at LIMIT 12001'
 plan=[r[3] for r in sources['core'].execute('EXPLAIN QUERY PLAN '+admission_sql,(low,high))]
 assert all('SEARCH' in x and 'INDEX' in x for x in plan),'ADMISSION_WINDOW_INDEX_REQUIRED'
 admitted=[dict(r) for r in sources['core'].execute(admission_sql,(low,high))];assert len(admitted)<=MAX,'ADMISSION_WINDOW_BOUND'
 for r in admitted:put('core','token_analysis',r)
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
 released=[]
 for kind,table,expiry in [('core','creator_resolution_queue','locked_until'),('core','creator_funding_queue','locked_until'),('ops','wt_walkback_queue','lease_expires_at')]:
  t=targets[kind];info=list(t.execute('pragma table_info("'+table+'")'));cols={r[1]:r for r in info};pk=[r[1] for r in info if r[5]]
  for old in t.execute('select * from "'+table+'" where status="running" limit 12001').fetchall():
   assert len(released)<=MAX
   changes={'status':'retry'}
   if expiry in cols:changes[expiry]=0
   for key in ['lease_owner','locked_by','claimed_by']:
    if key in cols:changes[key]='' if cols[key][3] else None
   t.execute('update "'+table+'" set '+','.join('"'+k+'"=?' for k in changes)+' where '+' and '.join('"'+k+'"=?' for k in pk),tuple(changes.values())+tuple(old[k] for k in pk))
   released.append({'table':kind+'.'+table,'identity':[old[k] for k in pk],'source_state_digest':hashlib.sha256(json.dumps(dict(old),sort_keys=True,default=str).encode()).hexdigest(),'retained_attempts':dict(old).get('attempts'),'action':'retry without old ownership; original backoff/ceilings retained'})
 # Exact existing V1 index required for duplicate signature-only recovery.
 sql=sources['core'].execute("select sql from sqlite_master where name='idx_psi0a_token_analysis_migration_tx' and type='index'").fetchone()[0]
 if not targets['core'].execute('select 1 from sqlite_master where name=?',('idx_psi0a_token_analysis_migration_tx',)).fetchone():targets['core'].execute(sql)
 for kind,t in targets.items():
  problem=t.execute('select * from pragma_foreign_key_check limit 1').fetchone()
  if problem:raise RuntimeError('FK:'+kind+':'+str(tuple(problem)))
 for t in targets.values():t.commit()
 result={'status':'PASS_PRELIMINARY_OPERATIONAL_STAGE','final_fence':True,'old_lease_releases':released,'admission_window_rows':len(admitted),'reconciled_changes':counts,'selected_rows':total,'digest':{k:v.hexdigest() for k,v in digests.items()},'schema_index_from_V1':sql,'free_bytes':os.statvfs(BASE).f_bavail*os.statvfs(BASE).f_frsize,'database_sizes':{k:(BASE/(k+'.sqlite')).stat().st_size for k in targets},'production_mutations':0,'preserved_source_absence_count':len(source_absences)}
except Exception as e:
 for t in targets.values():t.rollback()
 result={'status':'HOLD','error':str(e),'production_mutations':0}
finally:
 for c in list(sources.values())+list(targets.values()):c.close()
(BASE/'operational-stage.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
