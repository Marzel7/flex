"""Indexed direct V1 reads -> selected compact V2 INSERTs; never a database copy."""
from __future__ import annotations
import argparse,ast,hashlib,json,os,resource,socket,sqlite3,subprocess,sys,time
from pathlib import Path
from typing import Any,Optional
ROOT=Path(__file__).resolve().parents[4]
SOURCE={'core':Path('/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db'),'ops':Path('/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db')}
MINT='HXpDLpoH6Vx6MtqybpmsC6gFFoZpeb6bYTTpkrrypump'
FILE=8_000_000;TOTAL=64*1024*1024;FLOOR=2_000_000_000
class Hold(RuntimeError):pass
def require(ok,code):
 if not ok:raise Hold(code)
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
def hashfile(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(path,value):
 data=(json.dumps(value,sort_keys=True,indent=2,default=str)+'\n').encode();require(len(data)<1_000_000,'METADATA_BUDGET')
 with Path(path).open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
 fd=os.open(Path(path).parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)
def space(base):
 files=[p for p in base.rglob('*') if p.is_file()];require(len(files)<=64,'FILE_COUNT');sizes=[p.stat().st_size for p in files];alloc=sum(p.stat().st_blocks*512 for p in files);require(max(sizes or [0])<FILE and alloc<TOTAL,'STORAGE_BUDGET');free=os.statvfs(base).f_bavail*os.statvfs(base).f_frsize;require(free>=FLOOR+TOTAL,'DISK_FLOOR_PLUS_RESERVE');return {'files':len(files),'allocated_bytes':alloc,'largest_file_bytes':max(sizes or [0]),'free_bytes':free}
def isolate(base,allow_sources=True):
 os.environ.clear();os.environ.update(PATH='/usr/bin:/bin',PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(base),SQLITE_TMPDIR=str(base),DB_PATH=str(base/'core_v2.sqlite'),WT_OPS_DB_PATH=str(base/'ops_v2.sqlite'))
 resource.setrlimit(resource.RLIMIT_FSIZE,(FILE,FILE));sys.dont_write_bytecode=True
 def deny(*a,**kw):raise Hold('NETWORK_FORBIDDEN')
 socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
 def audit(event,args):
  if event=='sqlite3.connect':
   raw=str(args[0]);plain=raw[5:].split('?',1)[0] if raw.startswith('file:') else raw;p=Path(plain).resolve()
   if p in SOURCE.values():require(allow_sources and raw.startswith('file:') and 'mode=ro' in raw,'PRODUCTION_WRITE_OR_RUNTIME_SOURCE_DEPENDENCY')
   else:require(p in {base/'core_v2.sqlite',base/'ops_v2.sqlite'},'UNDECLARED_DATABASE')
  if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
   p=Path(os.fsdecode(args[0]));require(p.name not in ['.env','.env.production'],'CREDENTIAL_FILE_FORBIDDEN');flags=args[2] if isinstance(args[2],int) else 0
   if flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_APPEND|os.O_TRUNC):require(p.resolve().is_relative_to(base),'PRODUCTION_OR_OUTSIDE_WRITE');space(base)
 sys.addaudithook(audit)
def ro(path):
 c=sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True,timeout=.2);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('PRAGMA cache_size=-2048');c.execute('PRAGMA temp_store=MEMORY');return c
def bounded(c,sql,args=()):
 start=time.monotonic();steps=[0]
 def progress():steps[0]+=1000;return int(steps[0]>200000 or time.monotonic()-start>1)
 c.set_progress_handler(progress,1000)
 try:return c.execute(sql,args).fetchall()
 finally:c.set_progress_handler(None,0)
def select(c,entry):
 t=entry['table'];require(t.replace('_','').isalnum(),'TABLE_NAME');sql='SELECT * FROM "'+t+'" WHERE '+entry['predicate']+' LIMIT 129';plan=[r[3] for r in c.execute('EXPLAIN QUERY PLAN '+sql,entry['arguments'])];require(not any(x.startswith('SCAN ') for x in plan),'UNBOUNDED_SOURCE_SELECTION_'+t);rows=[dict(r) for r in bounded(c,sql,entry['arguments'])];require(len(rows)<129,'ROW_CAP_'+t);require(all(len(json.dumps(r,default=str).encode())<=65536 for r in rows),'ROW_BYTES_'+t);return rows,plan

def interfaces():
 old=ast.parse((ROOT/'docs/dev/lightweight_dev004_first_v2/qualify.py').read_text());ns={'ast':ast,'json':json,'sqlite3':sqlite3,'hashlib':hashlib,'time':time,'Any':Any,'Optional':Optional,'MINT':MINT,'operator':None,'report':{'sources':{}}}
 for name in ['functions','outputs']:
  node=next(n for n in old.body if isinstance(n,ast.FunctionDef) and n.name==name);exec(compile(ast.Module(body=[node],type_ignores=[]),'retained-interface-adapter','exec'),ns)
 return ns

def evaluations(core,ops):
 ns=interfaces();q=dict(ops.execute('SELECT * FROM wt_walkback_queue WHERE mint=?',(MINT,)).fetchone());creator,treasury,subprov=q['creator'],q['treasury'],q['subprov'];operator=ops.execute('SELECT operator_id FROM operator_launch_membership WHERE mint=?',(MINT,)).fetchone()[0];f=ns['functions']
 ns.update(creator=creator,treasury=treasury,subprov=subprov,operator=operator)
 ns['registry']=f(ROOT/'runtime/walkback/src/core/watchtower_registry_promotion.py',['_table_exists','_has_role_distinguishing_direct_edge','validate_role_normalized_queue_funding_route','is_canonical_watchtower_outcome'],{'WATCHTOWER_OPERATOR_ID':operator})
 ns['walk']=f(ROOT/'runtime/walkback/src/core/walkback_worker.py',['_is_known_treasury','_is_known_subprov'])
 ns['classification']=f(ROOT/'runtime/walkback/src/core/walkback_queue.py',['_resolve_treasury_via_op_graph','classify_creator'])
 ns['price']=f(ROOT/'runtime/creator_resolution/src/ops/watchtower_price_fact_contract.py',['reduce_watchtower_price_facts'])
 result=ns['outputs'](core,ops);return result

def source_connections():
 conns={k:ro(p) for k,p in SOURCE.items()}
 for c in conns.values():c.execute('BEGIN')
 return conns

def close(cs):
 for c in cs.values():c.close()

def freeze(base):
 require(not base.exists(),'ROOT_ALREADY_EXISTS');base.mkdir();isolate(base);before=space(base);old=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/result.json').read_text());cs=source_connections();schemas={};identity={};selectors=[];unique=set();selectedbytes=0
 try:
  for db,p in SOURCE.items():
   st=p.stat()
   with p.open('rb') as f:header=f.read(100)
   identity[db]={'path':str(p),'device':st.st_dev,'inode':st.st_ino,'observed_file_bytes':st.st_size,'header_observation_sha256':hashlib.sha256(header).hexdigest(),'schema_version':cs[db].execute('PRAGMA schema_version').fetchone()[0],'user_version':cs[db].execute('PRAGMA user_version').fetchone()[0],'full_file_hash':'NOT_READ: source logical authority is schemas plus selected-row hashes; live V1 is not a frozen whole-database generation'}
  for name in old['rows']:
   db,t=name.split('.',1);ddl=cs[db].execute("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?",(t,)).fetchone();require(ddl is not None,'MISSING_REQUIRED_SCHEMA_'+name);indexes=[r[0] for r in cs[db].execute("SELECT sql FROM sqlite_schema WHERE type='index' AND tbl_name=? AND sql IS NOT NULL ORDER BY name",(t,))];schemas[name]={'ddl':ddl[0],'indexes':indexes}
  for entry in old['selectors']:
   item={k:entry[k] for k in ['database','table','predicate','arguments']};rows,plan=select(cs[item['database']],item);hashes=sorted(digest(r) for r in rows);item.update(row_hashes=hashes,rows=len(rows),plan=plan);selectors.append(item)
   for row,h in zip(rows,[digest(r) for r in rows]):
    key=(item['database'],item['table'],h)
    if key not in unique:selectedbytes+=len(json.dumps(row,sort_keys=True,default=str,separators=(',',':')).encode());unique.add(key)
   require(len(unique)<=2048 and selectedbytes<=2_000_000,'TOTAL_SELECTION_BUDGET')
  outputs=evaluations(cs['core'],cs['ops']);require(outputs['price_fact']['ath_finalization_request_id'],'FINALIZATION_AUTHORITY_MISSING')
 finally:close(cs)
 targets={}
 for db in ['core','ops']:
  p=base/(db+'_v2.sqlite');p.touch();st=p.stat();targets[db]={'path':str(p),'device':st.st_dev,'inode':st.st_ino,'initial_sha256':hashfile(p)}
 sources=['docs/dev/lightweight_dev004_first_v2/qualify.py','runtime/walkback/src/core/watchtower_registry_promotion.py','runtime/walkback/src/core/walkback_worker.py','runtime/walkback/src/core/walkback_queue.py','runtime/creator_resolution/src/ops/watchtower_price_fact_contract.py','runtime/creator_resolution/src/ops/watchtower_terminal_ath_finalizer.py','docs/dev/lightweight_dev004_first_v2/compact_direct_migration/qualify.py']
 ath=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/evidence_completion/result.json').read_text())
 m={'format':'DEV004_DIRECT_COMPACT_SELECTION_V1','source_commit':None,'runtime_candidate':'0013ccbde314d0ad124f8ccd2963f15aff437519','root':str(base),'mint':MINT,'sources':identity,'targets':targets,'schemas':schemas,'schema_sha256':digest(schemas),'selectors':selectors,'unique_selected_rows':len(unique),'selected_encoded_bytes':selectedbytes,'output_digests':{k:digest(v) for k,v in outputs.items()},'source_files':{s:hashfile(ROOT/s) for s in sources},'canonical_price_authority':{k:outputs['price_fact'][k] for k in ['entry_method','entry_mc_usd','entry_timestamp','entry_exactness','final_proven_ath_mc','final_ath_evidence','final_ath_bucket_start','final_ath_bucket_end','ath_finalized_at','ath_finalization_request_id','ath_finalization_provenance_digest']},'ATH_numerical_tolerance_relative':.001,'accepted_numerical_oracle':ath['ath_numerical_equivalence'],'checkpoint':{'source':'completed Walkback state and price finalization identity; no invented unfinished checkpoint','progress':str(base/'migration-progress.json'),'commit_rule':'update cursor only after target batch commit; restart rechecks source digest and exact existing PK row','source_views':'independent bounded read-only transactions; representative historical-case closure, not global cross-database cutover generation'},'provenance':'Every selected field retained exactly. Whole V1 tables, raw source rows in intermediate files and raw provider bodies are not copied. Source schema definitions and row hashes are metadata-only authority.','rollback':{'mode':'ABORT_BEFORE_ACTIVATION','core_v1':identity['core'],'ops_v1':identity['ops'],'procedure':'Never activate these representative targets. On failure leave both V1 authorities unchanged and retain partial target plus progress for diagnosis. No source restore/copy/unretire required.'},'provider':{'credential_name':'BIRDEYE','forbidden_fallback':'BIRDEYE_KK','global_limit':20,'per_token_limit':4,'window_seconds':60,'new_provider_requests':0,'price_state':'accepted finalized historical record remains final; no consumed-budget ledger is reset'},'storage':{'single_file_max':FILE,'sqlite_max_pages':1024,'page_size':4096,'total_allocation_max':TOTAL,'floor':FLOOR,'required_free_reserve':TOTAL,'max_unique_rows':2048,'rows_per_selector':128,'row_encoded_max':65536,'selected_encoded_max':2000000,'raw_retention':False,'new_unbounded_paths':0},'before':before,'reused_evidence':['100creator','616Watchtower','26Deep','existing journal/paired guard/queue recovery','Birdeye acquisition and budget continuity'],'scope':'One direct production-read-only compact selection; no rebind, journal relocation, full V1 clone, backup API, VACUUM, source snapshot or production activation'}
 save(base/'manifest.json',m);print(json.dumps({'manifest':str(base/'manifest.json'),'rows':len(unique),'selected_encoded_bytes':selectedbytes,'sources_gib':sum(x['observed_file_bytes'] for x in identity.values())/(1024**3),'source_reads':'RO bounded indexed selectors only','manifest_sha256':hashfile(base/'manifest.json')},indent=2))

def validate(base,expected):
 m=json.loads((base/'manifest.json').read_text());require(hashfile(base/'manifest.json')==expected,'MANIFEST_DIGEST');require(m['source_commit'],'SOURCE_COMMIT_REQUIRED');require(digest(m['schemas'])==m['schema_sha256'],'SCHEMA_DIGEST')
 for n,h in m['source_files'].items():require(hashfile(ROOT/n)==h,'SOURCE_CODE_CHANGED_'+n)
 for k,p in SOURCE.items():st=p.stat();require((st.st_dev,st.st_ino)==(m['sources'][k]['device'],m['sources'][k]['inode']),'SOURCE_IDENTITY_CHANGED')
 for v in m['targets'].values():p=Path(v['path']);st=p.stat();require((st.st_dev,st.st_ino)==(v['device'],v['inode']),'TARGET_IDENTITY_CHANGED')
 space(base);return m

def apply(c,table,rows):
 info=c.execute('PRAGMA table_info("'+table+'")').fetchall();pk=[x[1] for x in sorted(info,key=lambda r:r[5]) if x[5]];require(bool(pk) or not rows,'STABLE_KEY_REQUIRED_'+table);inserted=0
 for row in rows:
  existing=c.execute('SELECT * FROM "'+table+'" WHERE '+' AND '.join('"'+k+'" IS ?' for k in pk),tuple(row[k] for k in pk)).fetchone()
  if existing:require(dict(existing)==row,'TARGET_CONFLICT_'+table)
  else:
   cols=list(row);c.execute('INSERT INTO "'+table+'"('+','.join('"'+x+'"' for x in cols)+') VALUES('+','.join('?' for x in cols)+')',tuple(row.values()));inserted+=1
 c.commit();return inserted

def migrate(base,expected,crash):
 isolate(base);m=validate(base,expected);progress=base/'migration-progress.json';state=json.loads(progress.read_text()) if progress.exists() else {'manifest_sha256':expected,'next_selector':0,'inserted':0};require(state['manifest_sha256']==expected,'CURSOR_AUTHORITY');cs=source_connections();dest={}
 try:
  for db in ['core','ops']:
   c=sqlite3.connect(base/(db+'_v2.sqlite'));c.row_factory=sqlite3.Row;c.execute('PRAGMA page_size=4096');c.execute('PRAGMA max_page_count=1024');c.execute('PRAGMA temp_store=MEMORY');dest[db]=c
   if not progress.exists():
    for name,schema in m['schemas'].items():
     if name.startswith(db+'.'):
      present=c.execute("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?",(name.split('.',1)[1],)).fetchone()
      if present:require(present[0]==schema['ddl'],'SCHEMA_RESTART_MISMATCH_'+name)
      else:c.execute(schema['ddl'])
      for sql in schema['indexes']:
       # Reuse the exact accepted definition, adding only SQLite repeat guard.
       c.execute(sql.replace('CREATE UNIQUE INDEX ','CREATE UNIQUE INDEX IF NOT EXISTS ',1) if sql.startswith('CREATE UNIQUE INDEX ') else sql.replace('CREATE INDEX ','CREATE INDEX IF NOT EXISTS ',1))
    c.commit()
  for i in range(state['next_selector'],len(m['selectors'])):
   entry=m['selectors'][i];rows,_=select(cs[entry['database']],entry);require(sorted(digest(r) for r in rows)==entry['row_hashes'],'SOURCE_ROW_CHANGE_'+entry['table']);n=apply(dest[entry['database']],entry['table'],rows)
   if crash and rows:
    save(base/'crash-evidence.json',{'point':'after compact row commit before progress cursor','selector':i,'inserted':n,'source_readonly':True});os._exit(71)
   state.update(next_selector=i+1,inserted=state['inserted']+n);save(progress,state);space(base)
  # Every selected row, including NULL provenance/event links, must match.
  for entry in m['selectors']:
   rows,_=select(dest[entry['database']],entry);require(sorted(digest(r) for r in rows)==entry['row_hashes'],'ALL_SELECTED_FIELDS_'+entry['table'])
  # Final equality is measured against the frozen current V1 reader outputs.
  result=evaluations(dest['core'],dest['ops']);equal={k:digest(v)==m['output_digests'][k] for k,v in result.items()};require(all(equal.values()),'V1_V2_READER_EQUIVALENCE')
  canonical=m['canonical_price_authority'];require(all(result['price_fact'][k]==v for k,v in canonical.items()),'CANONICAL_PRICE_PROVENANCE_CHANGED');ath=abs(result['price_fact']['final_proven_ath_mc']-canonical['final_proven_ath_mc'])/abs(canonical['final_proven_ath_mc']);require(ath<=.001,'ATH_NUMERICAL_EQUIVALENCE')
  sample=m['selectors'][0];rows,_=select(cs[sample['database']],sample);require(apply(dest[sample['database']],sample['table'],rows)==0,'DUPLICATE_INSERT')
  for db,p in SOURCE.items():st=p.stat();require((st.st_dev,st.st_ino)==(m['sources'][db]['device'],m['sources'][db]['inode']),'SOURCE_REBOUND')
  save(base/'migration-result.json',{'status':'PASS','comparisons':equal,'canonical_price_fields_exact':True,'ATH_relative_difference':ath,'all_selected_fields_and_null_event_links_exact':True,'selected_unique_rows':m['unique_selected_rows'],'cursor_complete':state['next_selector']==len(m['selectors']),'source_database_copy_bytes':0,'intermediate_row_snapshot_bytes':0,'source_mutations':0,'idempotent_existing_row_insertions':0,'frozen_legacy_missing_original_candles':'No new original-candle reconstruction; accepted numerical evidence reused separately'})
 finally:close(cs);close(dest)

def verify_reopened(base,expected):
 isolate(base,False);m=validate(base,expected);cs={k:ro(base/(k+'_v2.sqlite')) for k in ['core','ops']}
 try:
  out=evaluations(cs['core'],cs['ops']);require(all(digest(v)==m['output_digests'][k] for k,v in out.items()),'REOPEN_EQUIVALENCE')
  ns=interfaces();f=ns['functions'];finalizer=f(ROOT/'runtime/creator_resolution/src/ops/watchtower_terminal_ath_finalizer.py',['_digest','_candles','WatchtowerTerminalAthFinalizer'],{'Path':Path,'json':json,'hashlib':hashlib,'TERMINAL_STATE':'PRICE_MONITOR_COMPLETE_COLLAPSED','FINALIZER_VERSION':'watchtower-terminal-ath.v1'});class_type=finalizer['WatchtowerTerminalAthFinalizer']
  result=class_type(str(base/'ops_v2.sqlite'),binding=object()).finalize(MINT);require(result['state']=='ALREADY_FINALIZED' and result['provider_calls']==0,'FINALIZATION_REPLAY')
  save(base/'reopen-result.json',{'status':'PASS','runtime_V1_database_opens_allowed':False,'all_reader_outputs_exact':True,'finalization':result['state'],'provider_calls':0,'restoration':'abort-before-activation; original V1 remains authoritative and never written','storage':space(base)})
 finally:close(cs)

def run(base,expected):
 m=validate(base,expected);require(not (base/'execution-started.json').exists(),'ONE_QUALIFICATION_ONLY');save(base/'execution-started.json',{'manifest_sha256':expected,'count':1});before=space(base);phases=[]
 for mode,code in [('migrate-crash',71),('migrate-resume',0),('verify-reopened',0)]:
  r=subprocess.run([sys.executable,'-B',__file__,mode,str(base),'--manifest-sha256',expected],capture_output=True,text=True,timeout=45,env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'});save(base/(mode+'-process.json'),{'mode':mode,'exit':r.returncode,'stdout':r.stdout[-3000:],'stderr':r.stderr[-6000:]});phases.append({'mode':mode,'exit':r.returncode});require(r.returncode==code,'PHASE_'+mode);space(base)
 result={'status':'PASS','source_commit':m['source_commit'],'manifest_sha256':expected,'qualification_count':1,'method':'DIRECT_INDEXED_V1_READ_TO_COMPACT_V2_INSERT','source_copy_bytes':0,'intermediate_source_snapshot_bytes':0,'source_reads':'52 bounded selectors, second process retries only uncheckpointed selector','selected_unique_rows':m['unique_selected_rows'],'selected_encoded_bytes':m['selected_encoded_bytes'],'phases':phases,'disk_before':before,'disk_after':space(base),'destination_files':{k:{'bytes':(base/(k+'_v2.sqlite')).stat().st_size,'allocated':(base/(k+'_v2.sqlite')).stat().st_blocks*512,'sha256':hashfile(base/(k+'_v2.sqlite'))} for k in ['core','ops']},'production_mutations':0,'provider_calls':0,'wider_migration':'NOT_AUTHORIZED; representative historical closure only; current full seed/delta eligibility and final cutover authority remain separate'};save(base/'result.json',result);print(json.dumps(result,indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['freeze','validate','run','migrate-crash','migrate-resume','verify-reopened']);p.add_argument('root');p.add_argument('--manifest-sha256',default='');a=p.parse_args();base=Path(a.root).resolve();require(base.is_relative_to('/private/tmp') and base.name.startswith('dev004-'),'DISPOSABLE_ROOT')
 try:
  if a.mode=='freeze':freeze(base)
  elif a.mode=='validate':validate(base,a.manifest_sha256);print('AUTHORITY_VALID')
  elif a.mode=='run':run(base,a.manifest_sha256)
  elif a.mode=='verify-reopened':verify_reopened(base,a.manifest_sha256)
  else:migrate(base,a.manifest_sha256,a.mode=='migrate-crash')
 except Exception as e:
  if base.exists():save(base/'first-blocker.json',{'status':'HOLD','phase':a.mode,'error':type(e).__name__,'detail':str(e)[:1000]})
  raise
