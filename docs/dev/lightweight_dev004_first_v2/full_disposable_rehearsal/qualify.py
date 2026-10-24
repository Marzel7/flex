"""One direct V1 -> compact V2 import, full digests, restart and DEV rollback.
No production fencing, writable production handle, service or provider invocation.
"""
import ast,hashlib,importlib.util,json,os,resource,socket,sqlite3,subprocess,sys,time,types
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];NATIVE=sqlite3.dbapi2.connect
SOURCE=Path('/Users/kevinkeaveney/Dev/claude/flex/database');CAP=64*1024*1024;TOTAL=256*1024*1024;FLOOR=2_000_000_000

def load(path,name,omit_reaper=False):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m
 if omit_reaper:
  tree=ast.parse(path.read_text());tree.body=[n for n in tree.body if not(isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='_start_connection_reaper')];exec(compile(tree,str(path),'exec'),m.__dict__)
 else:spec.loader.exec_module(m)
 return m
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
def save(path,x):
 b=(json.dumps(x,indent=2,sort_keys=True)+'\n').encode();assert len(b)<8_000_000;tmp=path.with_suffix('.tmp')
 with tmp.open('wb') as f:f.write(b);f.flush();os.fsync(f.fileno())
 os.replace(tmp,path);fd=os.open(path.parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)
def isolate(base,source_allowed):
 os.environ.clear();os.environ.update(PATH='/usr/bin:/bin',PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(base),SQLITE_TMPDIR=str(base),DB_WRITE_SERIALIZE='1',DB_SQLITE_LIFECYCLE_DIAGNOSTICS_PATH=str(base/'sqlite-diagnostics.jsonl'))
 resource.setrlimit(resource.RLIMIT_FSIZE,(CAP,CAP));sys.dont_write_bytecode=True
 def deny(*a,**kw):raise RuntimeError('NETWORK_FORBIDDEN')
 socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
 def audit(event,args):
  if event=='sqlite3.connect':
   raw=str(args[0]);p=Path(raw[5:].split('?',1)[0] if raw.startswith('file:') else raw).resolve()
   if p.is_relative_to(base):return
   assert source_allowed and raw.startswith('file:') and 'mode=ro' in raw,'NON_DISPOSABLE_DATABASE_FORBIDDEN'
  if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
   p=Path(os.fsdecode(args[0]));assert p.name not in ['.env','.env.production'],'CREDENTIAL_READ_FORBIDDEN';flags=args[2] if isinstance(args[2],int) else 0
   if flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):assert p.resolve().is_relative_to(base),'OUTSIDE_WRITE_FORBIDDEN'
 sys.addaudithook(audit)
def storage(base):
 files=[p for p in base.rglob('*') if p.is_file()];allocated=sum(p.stat().st_blocks*512 for p in files);largest=max([p.stat().st_size for p in files] or [0]);v=os.statvfs(base);free=v.f_bavail*v.f_frsize;assert len(files)<=64 and allocated<TOTAL and largest<CAP,'STORAGE_CAP';assert free>=FLOOR+64*1024*1024,'DISK_FLOOR';return {'allocated_bytes':allocated,'largest_file_bytes':largest,'free_bytes':free,'files':len(files)}
def ro(path):
 c=NATIVE(path.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');return c

def pair_modules():
 for name in ['src','src.core','src.utils']:
  m=types.ModuleType(name);m.__path__=[];sys.modules[name]=m
 guard=load(ROOT/'runtime/listener/src/core/paired_binding_guard.py','src.core.paired_binding_guard')
 return guard

def set_pair(manifest,authority,role='runtime'):
 v=json.loads(manifest.read_text());os.environ.update(DB_PATH=v['stores']['core']['path'],WT_OPS_DB_PATH=v['stores']['ops']['path'],DEV004_PAIRED_BINDING_REQUIRED='1',FLEX_DB_PAIR_MANIFEST=str(manifest),FLEX_DB_PAIR_AUTHORITY=str(authority),FLEX_DB_PAIR_ROLE=role,FLEX_DB_PAIR_OWNER='rehearsal-owner' if role=='transfer' else '')
def publish_authority(path,manifest,phase='ACTIVE'):
 m=json.loads(manifest.read_text());save(path,{'version':1,'authority_id':m['authority']['id'],'origin_generation':m['authority']['origin_generation'],'generation':m['generation'],'manifest_path':str(manifest),'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),'phase':phase,'owner':'runtime' if phase=='ACTIVE' else 'rehearsal-owner','transfer_complete':phase=='ACTIVE'})

def verify_pair(base,manifest):
 guard=pair_modules();authority=base/'pair-authority.json';set_pair(manifest,authority);generation=guard.validate_pair();m=json.loads(manifest.read_text());oracles=json.loads((base/'row-oracles.json').read_text());checked=0
 for component in ['listener','creator_resolution','walkback']:
  db=load(ROOT/f'runtime/{component}/src/core/db.py','qualified_db_'+component);assert db.DB_PATH==m['stores']['core']['path'] and (db.resolve_ops_db_path() if hasattr(db,'resolve_ops_db_path') else db.OPS_DB_PATH)==m['stores']['ops']['path']
 for kind in ['core','ops']:
  c=ro(Path(m['stores'][kind]['path']));assert c.execute('PRAGMA quick_check').fetchall()[0][0]=='ok';assert not c.execute('PRAGMA foreign_key_check').fetchall()
  for table,entry in oracles[kind].items():
   keys=entry['keys'];actual=c.execute('SELECT * FROM "'+table+'" ORDER BY '+','.join('"'+k+'"' for k in keys)+' LIMIT 30001').fetchall();assert len(actual)==len(entry['rows']),'ROW_COUNT:'+kind+'.'+table
   for row,expected in zip(actual,entry['rows']):assert [row[k] for k in keys]==expected['key'] and digest(dict(row))==expected['sha256'],'ROW_CHANGED:'+kind+'.'+table;checked+=1
  c.close()
 readers=load(ROOT/'docs/dev/lightweight_dev004_first_v2/compact_direct_migration/qualify.py','accepted_readers');core=ro(Path(m['stores']['core']['path']));ops=ro(Path(m['stores']['ops']['path']));values=readers.evaluations(core,ops);expected=json.loads((base/'reader-oracles.json').read_text());assert {k:digest(v) for k,v in values.items()}==expected;core.close();ops.close()
 return {'status':'PASS','generation':generation,'rows_verified':checked,'reader_comparisons':len(values),'all_source_database_access_forbidden':True,'quick_check':'ok','foreign_key_violations':0,'three_component_database_bindings':'PASS'}

def run(base):
 assert not base.exists();base.mkdir();isolate(base,True);before=storage(base);assert before['free_bytes']>=FLOOR+TOTAL
 importer=load(ROOT/'docs/dev/lightweight_dev004_first_v2/price_reference_compatibility/compact_rehearsal.py','accepted_compact_importer');readers=load(ROOT/'docs/dev/lightweight_dev004_first_v2/compact_direct_migration/qualify.py','accepted_readers');captured={'core':{},'ops':{}};source_connections={};frozen_readers={};native_ro=importer.old.ro;original_insert=importer.import_row;samples=[]
 def source_hook(path):
  c=native_ro(path)
  if path==SOURCE/'flex_complete_database.db':source_connections['core']=c
  elif path==SOURCE/'wt_ops_v2.db':source_connections['ops']=c
  if len(source_connections)==2 and not frozen_readers:
   values=readers.evaluations(source_connections['core'],source_connections['ops']);frozen_readers.update({k:digest(v) for k,v in values.items()})
  return c
 def capture(c,table,keys,row):
  result=original_insert(c,table,keys,row);path=next(r[2] for r in c.execute('PRAGMA database_list') if r[1]=='main');kind=Path(path).stem;assert kind in captured
  data=captured[kind].setdefault(table,{'keys':keys,'rows':{}});pk=tuple(row[k] for k in keys);sha=digest(row)
  if pk in data['rows']:assert data['rows'][pk]==sha
  else:data['rows'][pk]=sha
  if sum(len(t['rows']) for ts in captured.values() for t in ts.values())%128==0:samples.append(storage(base))
  return result
 importer.old.ro=source_hook;importer.import_row=capture
 # Existing run requires a fresh root; the wrapper's own authority root is parent.
 target=base/'dev004-import';importer.run(target)
 result=json.loads((target/'result.json').read_text())
 if result['status']!='FULL_REFERENCE_IMPORT_PASS':raise RuntimeError('IMPORT_HOLD:'+json.dumps(result['blocker']))
 # Freeze oracles from source records, plus exactly the derived typed sidecar.
 original_rows=sum(len(t['rows']) for ts in captured.values() for t in ts.values());assert original_rows==28462
 c=ro(target/'ops.sqlite');data={'keys':['mint'],'rows':{}}
 for row in c.execute('SELECT * FROM operator_membership_reference_v1 ORDER BY mint LIMIT 50001'):data['rows'][(row['mint'],)]=digest(dict(row))
 assert len(data['rows'])==1171;captured['ops']['operator_membership_reference_v1']=data;c.close()
 oracles={k:{t:{'keys':entry['keys'],'rows':[{'key':list(pk),'sha256':sha} for pk,sha in sorted(entry['rows'].items())]} for t,entry in ts.items()} for k,ts in captured.items()}
 save(base/'row-oracles.json',oracles);save(base/'reader-oracles.json',frozen_readers);samples.append(storage(base))
 save(base/'execution-context.json',{'before':before,'original_rows':original_rows,'samples':samples})
 finish(base)

def finish(base):
 target=base/'dev004-import';importer=load(ROOT/'docs/dev/lightweight_dev004_first_v2/price_reference_compatibility/compact_rehearsal.py','accepted_compact_importer');result=json.loads((target/'result.json').read_text());assert result['status']=='FULL_REFERENCE_IMPORT_PASS'
 if (base/'execution-context.json').exists():
  context=json.loads((base/'execution-context.json').read_text());before=context['before'];original_rows=context['original_rows'];samples=context['samples']
 else:
  before={'free_bytes':result['free_before'],'allocated_bytes':0,'largest_file_bytes':0,'files':0};original_rows=sum(len(e['rows']) for kind,tables in json.loads((base/'row-oracles.json').read_text()).items() for table,e in tables.items() if table!='operator_membership_reference_v1');samples=[storage(base),{'allocated_bytes':result['peak_allocated_sampled'],'free_bytes':result['free_after'],'largest_file_bytes':0,'files':0}]
 assert original_rows==28462
 guard=pair_modules();authority=base/'pair-authority.json';paths={k:target/(k+'.sqlite') for k in ['core','ops']};manifest=base/'v2-runtime-manifest.json';config={'version':1,'authority':{'id':'dev004-full-disposable-rehearsal','path':str(authority),'origin_generation':'disposable-imported-v2'},'generation':'disposable-imported-v2','stores':{k:guard.database_identity(str(p),'disposable-imported-v2',k) for k,p in paths.items()},'predecessor':None};save(manifest,config);guard.seal_manifest(str(manifest));publish_authority(authority,manifest);set_pair(manifest,authority)
 child=subprocess.run([sys.executable,'-B',__file__,'verify',str(base),str(manifest)],capture_output=True,text=True,timeout=45,env=dict(os.environ));save(base/'restart-process.json',{'exit':child.returncode,'stdout':child.stdout[-2000:],'stderr':child.stderr[-3000:]});assert child.returncode==0,child.stderr;restart=json.loads(child.stdout);save(base/'restart-result.json',restart)
 # True managed rollback of staged writes, never altering retained target evidence.
 writer=load(ROOT/'runtime/listener/src/core/database_write_service.py','src.core.database_write_service');locking=load(ROOT/'runtime/listener/src/utils/db_locking.py','src.utils.db_locking',True);sys.modules['src.utils'].db_locking=locking
 def tracked(p):
  c=NATIVE(p,factory=locking.TrackedConnection);c._db_path=str(p);c._db_caller='full-disposable-rehearsal';c.row_factory=sqlite3.Row;return c
 with tracked(paths['core']) as c:
  old=c.execute('SELECT first_observed_mc FROM token_analysis WHERE mint=?',(importer.price.MINT,)).fetchone()[0];c.execute('UPDATE token_analysis SET first_observed_mc=? WHERE mint=?',(old+1,importer.price.MINT));c.rollback();assert c.execute('SELECT first_observed_mc FROM token_analysis WHERE mint=?',(importer.price.MINT,)).fetchone()[0]==old
 stale=tracked(paths['core']);writer.retire_database('core',paths['core'],generation='disposable-imported-v2',timeout=1);writer.retire_database('ops',paths['ops'],generation='disposable-imported-v2',timeout=1)
 # Matched rollback stores are copies of compact V2 only, never production V1.
 rollback={k:target/('rollback-'+k+'.sqlite') for k in ['core','ops']}
 for kind in ['core','ops']:
  src=ro(paths[kind]);dst=NATIVE(rollback[kind]);dst.execute('PRAGMA max_page_count=8192');src.backup(dst,pages=128,progress=lambda *args:samples.append(storage(base)));dst.close();src.close()
 rollback_manifest=base/'rollback-runtime-manifest.json';rv={'version':1,'authority':config['authority'],'generation':'disposable-rollback','stores':{k:guard.database_identity(str(p),'disposable-rollback',k) for k,p in rollback.items()},'predecessor':{'generation':config['generation'],'stores':config['stores']}};save(rollback_manifest,rv);guard.seal_manifest(str(rollback_manifest));publish_authority(authority,rollback_manifest);set_pair(rollback_manifest,authority)
 try:stale.execute("INSERT INTO token_analysis(mint) VALUES('FORBIDDEN_STALE_WRITER')")
 except writer.DatabaseRetiredError:pass
 else:raise RuntimeError('STALE_WRITER_NOT_REJECTED')
 stale.close()
 child=subprocess.run([sys.executable,'-B',__file__,'verify',str(base),str(rollback_manifest)],capture_output=True,text=True,timeout=45,env=dict(os.environ));save(base/'rollback-process.json',{'exit':child.returncode,'stdout':child.stdout[-2000:],'stderr':child.stderr[-3000:]});assert child.returncode==0,child.stderr;rollback_result=json.loads(child.stdout);save(base/'rollback-result.json',rollback_result)
 samples.append(storage(base));result={'status':'PASS','source_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'direct_import_executions':1,'selected_source_rows':original_rows,'derived_typed_rows':1171,'total_target_rows':original_rows+1171,'source_and_target_row_digests_exact':True,'restart':restart,'rollback':rollback_result,'managed_transaction_rollback':'PASS;stagedpricechangereverted','retired_dev_writer_rejected':True,'source_V1_retired_or_rebound':False,'production_mutations':0,'provider_calls':0,'full_V1_copies':0,'compact_V2_rollback_pair_copy':True,'before':before,'after':samples[-1],'sampled_peak_allocated_bytes':max(s['allocated_bytes'] for s in samples),'smallest_observed_free_bytes':min(s['free_bytes'] for s in samples),'largest_observed_file_bytes':max(s['largest_file_bytes'] for s in samples),'storage_samples':len(samples),'runtime_deployment_status':'QUALIFIED_NOT_DEPLOYED','limitations':['This is one full selected operational-seed import, not a production cutover.','Source Core/OPS reads are independent read-only snapshots, not a quiesced atomic production generation.','Rollback is an authorized fresh matched compact DEV pair; real V1 remains authoritative and unchanged.','Prior journals/provider recovery qualifications reused; no live intake or provider execution.']};save(base/'qualification-result.json',result);print(json.dumps(result,indent=2))

if __name__=='__main__':
 if sys.argv[1]=='verify':
  base=Path(sys.argv[2]);isolate(base,False);print(json.dumps(verify_pair(base,Path(sys.argv[3]))))
 else:
  base=Path(sys.argv[2])
  try:
   if sys.argv[1]=='resume-verification':isolate(base,False);finish(base)
   else:run(base)
  except BaseException as e:
   if base.exists():save(base/'first-blocker.json',{'status':'HOLD','type':type(e).__name__,'detail':str(e)[:3000]})
   raise
