from pathlib import Path
import sqlite3,json,time,hashlib,shutil,resource,collections,socket
resource.setrlimit(resource.RLIMIT_FSIZE,(48000000,48000000))
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
R=Path('/Users/kevinkeaveney/Dev/claude/flex');O=Path('/private/tmp/dev004-operational-seed-qualified');O.mkdir(exist_ok=False)
seed=Path('/private/tmp/dev004-price-provenance-final');src={};dst={};report={'tables':{},'holds':[],'rows':[],'source_authority':{},'provider_calls':0,'production_writes':0};start=time.monotonic();total=0;logical=0
for db,name in [('core','flex_complete_database.db'),('ops','wt_ops_v2.db')]:
 p=R/'database'/name;src[db]=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True,timeout=1);src[db].row_factory=sqlite3.Row;src[db].execute('pragma query_only=on');src[db].execute('pragma cache_size=-2048');src[db].execute('begin');report['source_authority'][db]={'path':str(p),'inode':p.stat().st_ino,'bytes':p.stat().st_size}
 shutil.copyfile(seed/(db+'_v2.sqlite'),O/(db+'_v2.sqlite'));dst[db]=sqlite3.connect(O/(db+'_v2.sqlite'));dst[db].execute('pragma max_page_count=10000');dst[db].execute('pragma cache_size=-2048')
def query(db,sql,args=()):
 if time.monotonic()-start>90:raise RuntimeError('TOTAL_TIME_BUDGET')
 c=src[db];deadline=time.monotonic()+.4;c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
 try:return c.execute(sql,args).fetchall()
 finally:c.set_progress_handler(None,0)
def schema(db,t):
 row=src[db].execute("select sql from sqlite_master where type='table' and name=?",(t,)).fetchone()
 if not row:return False
 if not dst[db].execute('select 1 from sqlite_master where name=?',(t,)).fetchone():
  dst[db].execute(row[0])
  for x in src[db].execute("select sql from sqlite_master where type='index' and tbl_name=? and sql is not null",(t,)):dst[db].execute(x[0])
 return True
def add(db,t,row,reason):
 global total,logical
 values=dict(row);blob=json.dumps(values,sort_keys=True,default=str,separators=(',',':')).encode();size=len(blob)
 if size>262144 or total>=24000 or logical+size>24000000:report['holds'].append({'table':db+'.'+t,'reason':'ROW_OR_TOTAL_BUDGET','bytes':size});return
 writable={r[1] for r in src[db].execute('pragma table_xinfo("'+t+'")') if r[6]==0};cols=[x for x in values if x in writable];q='insert or ignore into "'+t+'"('+','.join('"'+x+'"' for x in cols)+') values('+','.join('?'*len(cols))+')';before=dst[db].total_changes;dst[db].execute(q,tuple(values[x] for x in cols));added=dst[db].total_changes-before
 key={r[1]:values[r[1]] for r in src[db].execute('pragma table_info("'+t+'")') if r[5]};digest=hashlib.sha256(blob).hexdigest();report['rows'].append({'database':db,'table':t,'key':key,'sha256':digest,'encoded_bytes':size,'reason':reason,'inserted':bool(added)})
 stat=report['tables'].setdefault(db+'.'+t,{'selected':0,'added':0,'encoded_bytes':0});stat['selected']+=1;stat['added']+=added;stat['encoded_bytes']+=size;total+=1;logical+=size
# Existing row-level selection authority, not a renewed schema inventory.
for name in ['row_dependency_manifest','supplemental_manifest']:
 m=json.load(open(R/'docs/audits/dev004_minimum_seed'/ (name+'.json')));report['holds']+=m.get('holds',[])
 for ref in m['rows']:
  db,t,key=ref['database'],ref['table'],ref['key']
  if not key or not schema(db,t):continue
  where=' and '.join('"'+k+'"=?' for k in key)
  try:
   plan=[r[3] for r in src[db].execute('explain query plan select * from "'+t+'" where '+where+' limit 2',tuple(key.values()))]
   if any(x.startswith('SCAN ') for x in plan):raise RuntimeError('UNINDEXED_KEY')
   rows=query(db,'select * from "'+t+'" where '+where+' limit 2',tuple(key.values()))
   if len(rows)!=1:raise RuntimeError('MISSING_OR_NONUNIQUE_KEY')
   add(db,t,rows[0],name)
  except (sqlite3.Error,RuntimeError) as e:report['holds'].append({'table':db+'.'+t,'key':key,'reason':str(e)})
# Compact authoritative candidate ledgers and review state: bounded small populations.
for t in ['potential_operation_identity','potential_operation_evidence_association','potential_operation_assessment_version','potential_operation_current','potential_operation_assessment_lineage','potential_operation_assessment_association_binding','potential_operation_workflows','potential_operation_child_candidates']:
 if not schema('ops',t):report['holds'].append({'table':'ops.'+t,'reason':'SOURCE_ABSENT'});continue
 try:
  rows=query('ops','select * from "'+t+'" limit 2001')
  if len(rows)==2001:raise RuntimeError('POPULATION_CAP')
  for row in rows:add('ops',t,row,'complete bounded candidate/review ledger')
 except (sqlite3.Error,RuntimeError) as e:report['holds'].append({'table':'ops.'+t,'reason':str(e)})
for db,c in dst.items():c.commit();c.close()
for c in src.values():c.close()
report['selected_rows']=total;report['selected_encoded_bytes']=logical;report['elapsed_seconds']=time.monotonic()-start
report['fixture_bytes']={db:{'bytes':(O/(db+'_v2.sqlite')).stat().st_size,'allocated':(O/(db+'_v2.sqlite')).stat().st_blocks*512} for db in dst}
(O/'seed_manifest.json').write_text(json.dumps(report,indent=2,default=str)+'\n');print(json.dumps({k:report[k] for k in ['selected_rows','selected_encoded_bytes','elapsed_seconds','fixture_bytes']},indent=2));print('HOLDS',len(report['holds']))
