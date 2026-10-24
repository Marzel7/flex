"""Full-seed root authority gate; no source writes/copies or speculative repair."""
import hashlib,json,os,resource,socket,sqlite3,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
REF=Path('/private/tmp/dev004-compact-creator-qualified/classification_closure')
SOURCE=Path('/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db')
FLOOR=2_000_000_000;RESERVE=64*1024*1024;FILE=8_000_000
base=Path(sys.argv[1]).resolve();assert base.is_relative_to('/private/tmp') and base.name.startswith('dev004-') and not base.exists();base.mkdir();resource.setrlimit(resource.RLIMIT_FSIZE,(FILE,FILE));sys.dont_write_bytecode=True
os.environ.clear();os.environ.update(PATH='/usr/bin:/bin',TMPDIR=str(base),SQLITE_TMPDIR=str(base),PYTHONDONTWRITEBYTECODE='1')
def gate():
 s=os.statvfs(base);free=s.f_bavail*s.f_frsize;assert free>=FLOOR+RESERVE,'DISK_FLOOR_PLUS_RESERVE';files=[p for p in base.rglob('*') if p.is_file()];assert len(files)<=16 and all(p.stat().st_size<FILE for p in files);allocated=sum(p.stat().st_blocks*512 for p in files);assert allocated<RESERVE;return {'available_bytes':free,'allocated_bytes':allocated,'files':len(files),'largest_file_bytes':max([p.stat().st_size for p in files] or [0])}
def no_network(*a,**kw):raise RuntimeError('NETWORK_FORBIDDEN')
socket.socket.connect=no_network;socket.socket.connect_ex=no_network;socket.create_connection=no_network
allowed_reads={SOURCE,REF/'ops_v2.sqlite'};target=base/'ops_fk_gate.sqlite'
def audit(event,args):
 if event=='sqlite3.connect':
  raw=str(args[0]);plain=raw[5:].split('?',1)[0] if raw.startswith('file:') else raw;p=Path(plain).resolve()
  assert p==target or p in allowed_reads and raw.startswith('file:') and 'mode=ro' in raw,'UNDECLARED_OR_WRITABLE_SOURCE'
 if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
  p=Path(os.fsdecode(args[0]));assert p.name not in ['.env','.env.production'],'CREDENTIAL_FORBIDDEN';flags=args[2] if isinstance(args[2],int) else 0
  if flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):assert p.resolve().is_relative_to(base),'PRODUCTION_WRITE_FORBIDDEN';gate()
sys.addaudithook(audit)
def save(name,value):
 data=(json.dumps(value,indent=2,sort_keys=True)+'\n').encode();assert len(data)<100000
 with (base/name).open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def ro(p):
 c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.2);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('PRAGMA cache_size=-1024');return c
def read(c,sql,args=(),cap=2049):
 plan=[x[3] for x in c.execute('EXPLAIN QUERY PLAN '+sql,args)];assert not any('SCAN ' in x for x in plan),('UNINDEXED_AUTHORITY',sql);steps=[0];start=time.monotonic()
 def progress():steps[0]+=1000;return int(steps[0]>200000 or time.monotonic()-start>1)
 c.set_progress_handler(progress,1000)
 try:rows=[dict(r) for r in c.execute(sql,args).fetchmany(cap)]
 finally:c.set_progress_handler(None,0)
 assert len(rows)<cap,'KEY_POPULATION_CAP';return rows,plan
before=gate();ref=ro(REF/'ops_v2.sqlite');src=ro(SOURCE);src.execute('BEGIN');ref.execute('BEGIN')
try:
 oldids=[r[0] for r in ref.execute('SELECT operator_id FROM operators LIMIT 65')];assert len(oldids)<65
 inventories=[];new=[];plans=[]
 for op in oldids:
  sql='SELECT * FROM operator_launch_membership WHERE operator_id=? LIMIT 2049';current,plan=read(src,sql,(op,));plans.append({'sql':sql,'plan':plan});old={r[0] for r in ref.execute('SELECT mint FROM operator_launch_membership WHERE operator_id=?',(op,))};current_mints={r['mint'] for r in current}
  inventories.append({'operator_id':op,'retained_memberships':len(old),'current_memberships':len(current),'new_mints':sorted(current_mints-old),'missing_from_source':sorted(old-current_mints)})
  new += [r for r in current if r['mint'] not in old]
 assert len(new)<=64,'NEW_ROOT_BUDGET'
 missing=[]
 for row in new:
  if not row['event_id']:continue
  events,plan=read(src,'SELECT * FROM operator_identity_events WHERE event_id=?',(row['event_id'],),2);plans.append({'sql':'SELECT * FROM operator_identity_events WHERE event_id=?','plan':plan})
  if not events:missing.append(row)
 assert missing,'NO_REPRODUCED_BLOCKER: stop and qualify full selection separately'
 tables=['operators','operator_identity_events','operator_launch_membership'];schemas={t:src.execute("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?",(t,)).fetchone()[0] for t in tables};fk=[dict(r) for r in src.execute('PRAGMA foreign_key_list(operator_launch_membership)')];assert any(r['table']=='operator_identity_events' and r['from']=='event_id' and r['to']=='event_id' for r in fk)
 operators={}
 for row in missing:
  values,_=read(src,'SELECT * FROM operators WHERE operator_id=?',(row['operator_id'],),2);assert len(values)==1;operators[row['operator_id']]=values[0]
 st=SOURCE.stat();manifest={'status':'AUTHORITY_INCOMPLETE','scope':'Mandatory current-membership foreign-key gate before full operational seed migration','source':{'path':str(SOURCE),'device':st.st_dev,'inode':st.st_ino,'observed_bytes':st.st_size,'schema_version':src.execute('PRAGMA schema_version').fetchone()[0]},'reference_file':{'path':str(REF/'ops_v2.sqlite'),'sha256':hashlib.sha256((REF/'ops_v2.sqlite').read_bytes()).hexdigest()},'inventories':inventories,'source_schemas':schemas,'membership_foreign_keys':fk,'missing_event_dependencies':[{'mint':r['mint'],'operator_id':r['operator_id'],'event_id':r['event_id'],'membership_sha256':digest(r)} for r in missing],'plans':plans,'source_records_retained_in_memory_only':True,'storage':{'floor':FLOOR,'reserve':RESERVE,'file_max':FILE,'sqlite_page_max':256,'production_mutations':0,'provider_calls':0},'rollback':'Target diagnostic never activated; V1 unchanged; no source rollback needed'}
 save('authority_gate_manifest.json',manifest)
 target_db=sqlite3.connect(target);target_db.execute('PRAGMA foreign_keys=ON');assert target_db.execute('PRAGMA foreign_keys').fetchone()[0]==1;target_db.execute('PRAGMA page_size=4096');target_db.execute('PRAGMA max_page_count=256')
 for t in tables:target_db.execute(schemas[t])
 for row in operators.values():cols=list(row);target_db.execute('INSERT INTO operators('+','.join('"'+k+'"' for k in cols)+') VALUES('+','.join('?' for k in cols)+')',tuple(row.values()))
 target_db.commit();tests=[]
 for row in missing:
  target_db.execute('SAVEPOINT missing_event_probe');cols=list(row)
  try:
   target_db.execute('INSERT INTO operator_launch_membership('+','.join('"'+k+'"' for k in cols)+') VALUES('+','.join('?' for k in cols)+')',tuple(row.values()));raise AssertionError('MISSING_EVENT_MEMBERSHIP_IMPORTED_WITH_FK_ENFORCED')
  except sqlite3.IntegrityError as e:assert str(e)=='FOREIGN KEY constraint failed';tests.append({'mint':row['mint'],'event_id':row['event_id'],'result':'REJECTED_FOREIGN_KEY_CONSTRAINT','expected_safety_result':True})
  finally:target_db.execute('ROLLBACK TO missing_event_probe');target_db.execute('RELEASE missing_event_probe')
 assert target_db.execute('SELECT COUNT(*) FROM operator_launch_membership').fetchone()[0]==0;assert not target_db.execute('PRAGMA foreign_key_check').fetchall();target_db.close()
 result={'status':'HOLD_MISSING_CANONICAL_EVENT_DEPENDENCIES','source_case':'Six new current Byzantine memberships not present in accepted retained seed; each non-null event_id has no source identity event','source_operator_membership_counts':inventories,'missing_dependencies':manifest['missing_event_dependencies'],'diagnostic_tests':tests,'safe_rejections':len(tests),'schema_changed_or_weakened':False,'event_ids_cleared_or_invented':False,'full_seed_migration_started':False,'full_seed_size':'NOT_QUALIFIED: mandatory dependency gate blocks import','full_seed_peak_storage':'NOT_QUALIFIED: only bounded authority diagnostic executed','diagnostic_database_bytes':target.stat().st_size,'diagnostic_before':before,'diagnostic_after':gate(),'production_mutations':0,'source_snapshot_or_full_copy_bytes':0,'provider_calls':0,'next_action':'Establish independently accepted provenance/event authority for these6memberships in DEV, or explicitly qualify a preserved unresolved-state contract without altering canonical source. Do not disable FKs, delete/omit memberships, invent events, clear event IDs or repair production.'}
 save('result.json',result);print(json.dumps({k:v for k,v in result.items() if k not in ['source_operator_membership_counts','missing_dependencies','diagnostic_tests']},indent=2))
finally:src.close();ref.close()
