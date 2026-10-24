"""One disposable DEV004 rehearsal. No production configuration is consumed.

prepare freezes small retained DEV inputs; validate is read-only; execute is
single-use and invokes crash/restart/rollback phases, never a test matrix.
"""
import argparse,asyncio,hashlib,importlib.util,json,os,resource,shutil,socket,sqlite3,subprocess,sys,time,types
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
CANDIDATE='0013ccbde314d0ad124f8ccd2963f15aff437519'
PYTHON='/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python'
FLOOR=2_000_000_000; TOTAL=384*1024*1024; FILE=16_000_000
MINT='DEV004_REHEARSAL_PRICE'; LEFT=1800000000; CHUNK='dev004-rehearsal-completed-window'
INPUTS={
 'core':('/private/tmp/dev004-compact-creator-qualified/classification_closure/core_v2.sqlite','f98cb391ec4aaea65f4dd9266b9769c298534088a5f264de7e843e50027f57e0'),
 'ops':('/private/tmp/dev004-compact-creator-qualified/classification_closure/ops_v2.sqlite','92efde2c3ae80ef1c8a897bf1485eb4d2218e6059f70492988c69469b709ec2a'),
 'history':('/private/tmp/dev004-compact-creator-qualified/history.json','f6c3d9d89a32204f116595049545ec7bcabe9391987b5a32f5e73837b05d8bcb'),
 'freshness':('/private/tmp/dev004-outage-freshness/freshness_mapping.json','87c995c633841adaaeffa3114ceacbed0441bf0c00e004036b8dd120d844026d'),
 'creator':('/private/tmp/dev004-startup-integration-final/creator.sqlite','0efa16e3d034aaf9a97021b4c6379b7a523675fcd3f6a3c26547789745ced702')}

class Hold(RuntimeError):pass

def check(ok,code):
 if not ok:raise Hold(code)
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for part in iter(lambda:f.read(65536),b''):h.update(part)
 return h.hexdigest()
def write(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);data=json.dumps(v,sort_keys=True,indent=2).encode()+b'\n';check(len(data)<1_000_000,'METADATA_LIMIT')
 with p.open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
 fd=os.open(p.parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)
def storage(base):
 files=[p for p in base.rglob('*') if p.is_file()];check(len(files)<=512,'FILE_COUNT_LIMIT')
 sizes=[p.stat().st_size for p in files];allocation=sum(p.stat().st_blocks*512 for p in files)
 check(max(sizes or [0])<FILE and allocation<TOTAL,'STORAGE_LIMIT')
 free=os.statvfs(base).f_bavail*os.statvfs(base).f_frsize
 check(free>=FLOOR+TOTAL,'DISK_FLOOR_AND_REHEARSAL_RESERVE')
 return {'files':len(files),'allocated':allocation,'logical':sum(sizes),'largest':max(sizes or [0]),'free':free}
def copy(src,dst):
 src=Path(src);dst=Path(dst);check(src.stat().st_size<FILE,'INPUT_FILE_LIMIT');check(not Path(str(src)+'-wal').exists() or Path(str(src)+'-wal').stat().st_size==0,'NONEMPTY_SOURCE_WAL')
 with src.open('rb') as f,dst.open('wb') as g:shutil.copyfileobj(f,g,65536);g.flush();os.fsync(g.fileno())
 check(sha(src)==sha(dst),'COPY_DIGEST_MISMATCH')
def logical(path):
 q=sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True);q.execute('PRAGMA query_only=ON');q.execute('PRAGMA temp_store=MEMORY');out={}
 try:
  schema=q.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name").fetchall();check(len(schema)<1500,'SCHEMA_LIMIT')
  out['_schema']=hashlib.sha256(json.dumps(schema,separators=(',',':')).encode()).hexdigest()
  tables=[x[1] for x in schema if x[0]=='table'];check(len(tables)<=128,'TABLE_LIMIT')
  for t in tables:
   cur=q.execute('SELECT * FROM "'+t.replace('"','""')+'"');digests=[]
   while True:
    rows=cur.fetchmany(128)
    if not rows:break
    for row in rows:
     encoded=json.dumps(row,separators=(',',':'),default=lambda b:{'blob':hashlib.sha256(b).hexdigest(),'bytes':len(b)}).encode();check(len(encoded)<=65536,'ROW_LIMIT');digests.append(hashlib.sha256(encoded).hexdigest())
    check(len(digests)<=50000,'ROW_POPULATION_LIMIT')
   out[t]={'rows':len(digests),'sha256':hashlib.sha256(''.join(sorted(digests)).encode()).hexdigest()}
 finally:q.close()
 return out

def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def isolation(base):
 os.environ.clear();os.environ.update(PATH='/usr/bin:/bin',PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',MONITOR_RUNTIME='dev',SQLITE_TMPDIR=str(base),TMPDIR=str(base))
 resource.setrlimit(resource.RLIMIT_FSIZE,(FILE,FILE));sys.dont_write_bytecode=True
 def blocked(*a,**kw):raise Hold('NETWORK_FORBIDDEN')
 socket.socket.connect=blocked;socket.socket.connect_ex=blocked;socket.create_connection=blocked
 def audit(event,args):
  if event=='sqlite3.connect':
   raw=str(args[0]);plain=raw[5:].split('?',1)[0] if raw.startswith('file:') else raw
   check(plain!=':memory:' and Path(plain).resolve() in {base/(g+'-'+k+'.sqlite') for g in ['v1','v2','rollback'] for k in ['core','ops','creator']},'UNDECLARED_DATABASE')
   check(storage(base)['allocated']+3*FILE<TOTAL,'SQLITE_TRANSACTION_ALLOCATION_RESERVE')
  if event=='open':
   raw=args[0]
   if not isinstance(raw,(str,bytes,os.PathLike)):return
   p=Path(os.fsdecode(raw));check(p.name not in ['.env','.env.production'],'CREDENTIAL_FILE_FORBIDDEN')
   flags=args[2] if isinstance(args[2],int) else 0
   if flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):
    check(p.resolve().is_relative_to(base),'WRITE_OUTSIDE_REHEARSAL');check(storage(base)['allocated']+FILE<TOTAL,'FILE_ALLOCATION_RESERVE');check(os.statvfs(base).f_bavail*os.statvfs(base).f_frsize>=FLOOR+TOTAL,'DISK_FLOOR_AND_REHEARSAL_RESERVE')
 sys.addaudithook(audit)
 p=load('rehearsal_pair',ROOT/'tests/dev004/test_bounded_paired_transfer.py');import pytest
 patch=pytest.MonkeyPatch();env=p.support.env.__wrapped__(base,patch)
 sys.modules['src.core'].__path__=[str(ROOT/'runtime/listener/src/core')]
 for name,folder in [('src.ops','runtime/creator_resolution/src/ops'),('src.evidence','runtime/creator_resolution/src/evidence')]:
  mod=types.ModuleType(name);mod.__path__=[str(ROOT/folder)];sys.modules[name]=mod
 return p,env

def case(base,p,env):
 c=p.PairCase.__new__(p.PairCase);c.env=env;c.w,c.d=p.support.setup_writer(env);c.paths={g:{k:base/(g+'-'+k+'.sqlite') for k in ['core','ops']} for g in ['v1','v2','rollback']}
 c.birth=env.b.BirthJournal(base/'stable-birth.jsonl');c.migration=env.m.MigrationJournal(base/'stable-migration.jsonl');c.config=base/'frozen-pair.json';c.steps=[];c.calls=[];c.logs=[];c.current=None;c.scope=None;c.stale=None;c.guard=sys.modules['src.core.paired_binding_guard'];c.authority=base/'active-pair-authority.json';c.manifests={g:base/(g+'-runtime-manifest.json') for g in ['v1','v2','rollback']};c.reconciled=False
 from src.ops import dev004_checkpoint_completion as completion
 from src.evidence.queue import EvidenceIntakeQueue
 from src.ops.dev_provider_budget import DevProviderBudget
 queue=EvidenceIntakeQueue(base/'price-queue',enabled=True,max_messages=8,max_bytes=65536,max_attempts=3,clock=lambda:LEFT+30)
 return c,completion,queue,DevProviderBudget

def extras(c):
 for k,v in {'CREATOR_DB_PATH':str(c.env.path/'v1-creator.sqlite'),'BIRTH_JOURNAL_PATH':str(c.birth.path),'MIGRATION_JOURNAL_PATH':str(c.migration.path),'DEV004_DURABLE_BIRTH_JOURNAL_ENABLED':'1','DEV004_SIGNATURE_FIRST_JOURNAL_ENABLED':'1','DEV004_CREATOR_HISTORY_PATH':str(c.env.path/'history.json'),'LIVING_V2_FRESHNESS_MAPPING_PATH':str(c.env.path/'freshness.json'),'PUMPPORTAL_COMPACT_BIRTH_RECEIPTS_PATH':str(c.env.path/'receipts'),'OPERATION_MONITOR_QUEUE_PATH':str(c.env.path/'price-queue')}.items():c.env.patch.setenv(k,v)

def artifacts(base):
 return {str(p.relative_to(base)):{'bytes':p.stat().st_size,'sha256':sha(p),'device':p.stat().st_dev,'inode':p.stat().st_ino} for p in sorted(base.rglob('*')) if p.is_file() and p.name!='manifest.json'}

def prepare(base):
 check(not base.exists(),'REHEARSAL_ROOT_ALREADY_EXISTS');base.mkdir();storage(base);p,env=isolation(base)
 for name,(source,expected) in INPUTS.items():
  check(sha(source)==expected,'INPUT_AUTHORITY_'+name)
  target=base/({'core':'v1-core.sqlite','ops':'v1-ops.sqlite','creator':'v1-creator.sqlite','history':'history.json','freshness':'freshness.json'}[name]);copy(source,target)
 for g in ['v2','rollback']:
  for kind in ['core','ops','creator']:(base/(g+'-'+kind+'.sqlite')).touch()
 c,completion,queue,budget=case(base,p,env);extras(c);c.birth.initialize();c.migration.initialize();c.prepare_manifest('v1');c.guard.seal_manifest(str(c.manifests['v1']));c.select_authority('v1','ACTIVE');c.bind_environment('v1');c.binding('v1');c.seed()
 # Exact existing schema-owner and checkpoint writer, with synthetic provenance.
 write(base/'price-queue/provider_budget.json',{'version':1,'updated_at':LEFT,'calls':[]});budget(base/'price-queue').admit(MINT,'SYNTHETIC_COMPLETED_WINDOW',now=LEFT)
 completion.commit_window(str(c.paths['v1']['ops']),MINT,'15m',LEFT,LEFT+900,CHUNK,[{'timestamp':LEFT,'open':100.0,'high':200.0,'low':80.0,'close':150.0}],LEFT)
 queue.initialize();queue.enqueue({'work_type':'WATCHTOWER_TERMINAL_ATH_FINALIZATION','mint':MINT,'logical_identity':'rehearsal-unfinished-window','completed_window':CHUNK,'next_window':[LEFT+900,LEFT+1800],'provenance':'SYNTHETIC_REHEARSAL_NOT_HISTORICAL'},message_id='rehearsal-price-job')
 audit=p.support.load('receipt_rehearsal',ROOT/'runtime/listener/src/core/pumpportal_birth_audit.py');collector=audit.PumpPortalBirthAudit(enabled=False,path=str(base/'raw-disabled'),compact_path=str(base/'receipts'))
 ident=collector.record(receive_utc_ns=1,receive_monotonic_ns=1,parser_utc_ns=1,mint='old-birth',creator='creator',signature='old-create');check(bool(ident),'RECEIPT_PREPARATION')
 check(collector.record_outcome(ident,outcome='COMMITTED',canonical=('old-birth','creator','old-create')),'RECEIPT_OUTCOME')
 # No retained source handles or WAL dependencies are carried into the manifest.
 for path in c.paths['v1'].values():
  q=sqlite3.connect(path);q.execute('PRAGMA journal_mode=DELETE');q.close()
 c.prepare_manifest('v2');origin=json.loads(c.manifests['v1'].read_text());v2=json.loads(c.manifests['v2'].read_text())
 rollback={'version':1,'authority':origin['authority'],'generation':'fixture-rollback','stores':{k:c.guard.database_identity(str(c.paths['rollback'][k]),'fixture-rollback',k) for k in ['core','ops']},'predecessor':{'generation':v2['generation'],'stores':v2['stores']}};c.publish_json(c.manifests['rollback'],rollback)
 baseline={k:logical(v) for k,v in c.paths['v1'].items()};baseline['creator']=logical(base/'v1-creator.sqlite');write(base/'baseline-logical.json',baseline)
 authority=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/evidence_completion/result.json').read_text())['ath_numerical_equivalence'];check(authority['status']=='PASS','ATH_AUTHORITY')
 manifest={'format':'DEV004_REHEARSAL_AUTHORITY_V1','scope':'DISPOSABLE_ONLY_NOT_PRODUCTION','candidate':CANDIDATE,'root':str(base),'input_authorities':{k:{'path':v[0],'sha256':v[1]} for k,v in INPUTS.items()},'assets':artifacts(base),'db_pairs':{g:{k:str(v) for k,v in stores.items()} for g,stores in c.paths.items()},'binding_manifests':{g:str(v) for g,v in c.manifests.items()},'exclusive_authority':str(c.authority),'stable_journals':[str(c.birth.path),str(c.migration.path)],'receipts':str(base/'receipts'),'checkpoint':{'owner':'src.ops.dev004_checkpoint_completion','table':completion.TABLE,'mint':MINT,'planned':[[LEFT,LEFT+900,CHUNK],[LEFT+900,LEFT+1800,'not-completed']]},'budget':{'root':str(base/'price-queue'),'ledger':'provider_budget.json','global_limit':20,'token_limit':4,'window_seconds':60,'consumed_fixture_admissions':1,'credentials':['BIRDEYE'],'legacy_fallback':False,'transport':'FORBIDDEN'},'historical_mappings':[str(base/'history.json'),str(base/'freshness.json')],'secondary':{'source':str(base/'v1-creator.sqlite'),'destination':str(base/'v2-creator.sqlite'),'rollback':str(base/'rollback-creator.sqlite'),'owner':'accepted CreatorRepository; no source schema merged into Core'},'ath':authority,'entrypoints':{'listener':'runtime/listener:src.core.pumpfun_curve_listener:run_listener_entrypoint','resolver':'runtime/creator_resolution:src.core.creator_resolution_worker:run_loop','walkback':'runtime/walkback:src.core.walkback_worker:run_loop'},'feature_flags':{k:v for k,v in os.environ.items() if k in ['DEV004_DURABLE_BIRTH_JOURNAL_ENABLED','DEV004_SIGNATURE_FIRST_JOURNAL_ENABLED','DEV004_CREATOR_HISTORY_PATH','LIVING_V2_FRESHNESS_MAPPING_PATH','MONITOR_RUNTIME','DB_WRITE_SERIALIZE','DEV004_PAIRED_BINDING_REQUIRED']},'stopped_services':['API','monitor','bridge','funding','Cascade','all production services excluded from execution'],'dependencies':{'python':PYTHON,'runtime_lock_sha256':sha(ROOT/'manifests/flex_stable_v2_runtime_python311_macos_arm64.lock')},'storage':{'single_file_limit':FILE,'allocation_limit':TOTAL,'minimum_free':FLOOR,'extra_prewrite_reserve':TOTAL,'maximum_files':512,'max_rows_per_table':50000,'hash_fetch_rows':128,'raw_retention':False,'new_unbounded_paths':0},'transition':['durable admission under V1 lease','retire both disposable V1 stores','cold bounded snapshot into closed destination inodes','byte and all-table verification','seal pair and ACTIVE publication','birth drain','migration Core commit then real process exit71 before OPS handoff','fresh-process reopen and complete handoff once','fresh authorized matched rollback snapshot of post-switch state; never unretire'], 'rollback':{'mode':'copy_and_verify_all_post_switch_state_to_fresh_pair','preserve_original_retired':True,'same_stable_journals_receipts_queue_budget_mappings':True,'secondary_copy_verified':True},'reused_evidence':['100creator','616Watchtower','26Deep','11frozen','192binding/transfer controls','Birdeye checkpoint/budget/recovery','nonP3Rreview'],'rehearsal_count':1,'production_mutations':0}
 write(base/'manifest.json',manifest);print(json.dumps({'prepared_manifest':str(base/'manifest.json'),'sha256':sha(base/'manifest.json'),'storage':storage(base)}))

def validate(base,source_commit,expected_hash):
 m=json.loads((base/'manifest.json').read_text());check(sha(base/'manifest.json')==expected_hash,'MANIFEST_HASH');check(m['candidate']==CANDIDATE and m['source_commit']==source_commit,'SOURCE_BINDING')
 for name,digest in m['source_files'].items():check(sha(ROOT/name)==digest,'SOURCE_FILE_IDENTITY_'+name)
 check(sha(PYTHON)==m['dependencies']['python_sha256'],'PYTHON_IDENTITY')
 for name,a in m['assets'].items():
  p=base/name;check(p.exists() and sha(p)==a['sha256'] and p.stat().st_ino==a['inode'] and p.stat().st_dev==a['device'],'ASSET_AUTHORITY_'+name)
 check(len(m['db_pairs'])==3 and len(m['assets'])>15 and len(m['entrypoints'])==3,'MANIFEST_INCOMPLETE')
 for g,pair in m['db_pairs'].items():
  check(set(pair)=={'core','ops'} and len(set(pair.values()))==2,'PAIR_SHAPE')
 for k,loc in m['secondary'].items():
  if k!='owner':check(Path(loc).resolve().is_relative_to(base),'SECONDARY_BINDING')
 check(m['budget']['consumed_fixture_admissions']==1 and m['ath']['maximum_relative_difference']=='0.001','ACCEPTED_CONTRACT')
 check(set(m['supplemental_state'])=={'secondary','journals','receipts','historical_mappings','price_queue_budget_checkpoint'},'STATE_COVERAGE_REQUIRED')
 storage(base);return m

def resume_case(base,g):
 p,env=isolation(base);c,completion,queue,budget=case(base,p,env);
 for key,value in json.loads((base/'manifest.json').read_text())['feature_flags'].items():env.patch.setenv(key,str(value))
 extras(c);c.bind_environment(g);c.binding(g);env.patch.setenv('CREATOR_DB_PATH',str(base/(g+'-creator.sqlite')));return p,c,completion,queue,budget

def freeze_copy(c,source,target):
 for k in ['core','ops']:
  # All callers/managed connections closed before bytes are copied. Never rename.
  copy(c.paths[source][k],c.paths[target][k]);check(logical(c.paths[source][k])==logical(c.paths[target][k]),'ALL_TABLE_EQUIVALENCE_'+k)
 copy(c.env.path/(source+'-creator.sqlite'),c.env.path/(target+'-creator.sqlite'))

def activate(c,g):
 c.guard.seal_manifest(str(c.manifests[g]));c.select_authority(g,'ACTIVE');c.bind_environment(g);c.binding(g);c.env.patch.setenv('CREATOR_DB_PATH',str(c.env.path/(g+'-creator.sqlite')))

def stage(base,phase):
 if phase=='migrate':
  p,c,completion,queue,budget=resume_case(base,'v1');c.admit_during_lease();check(len(c.birth.pending())==1,'BIRTH_ADMISSION');check(len(c.migration.pending())==1,'MIGRATION_ADMISSION');claimed=queue.claim(1);check(len(claimed)==1,'PRICE_PENDING_CLAIM')
  c.retire();c.select_authority('v2','TRANSFER');c.bind_environment('v2',True);freeze_copy(c,'v1','v2');write(base/'snapshot-equivalence.json',{'core':logical(c.paths['v2']['core']),'ops':logical(c.paths['v2']['ops']),'creator':logical(base/'v2-creator.sqlite')});activate(c,'v2');c.drain_births()
  check(not c.birth.pending(),'BIRTH_DRAIN')
  try:c.finish_migration(True)
  except p.Crash:
   write(base/'crash-state.json',{'phase':'after_Core_commit_before_OPS_handoff','pending_migrations':len(c.migration.pending()),'pending_births':len(c.birth.pending()),'budget_sha256':sha(base/'price-queue/provider_budget.json')});os._exit(71)
  raise Hold('CRASH_BOUNDARY_NOT_REACHED')
 p,c,completion,queue,budget=resume_case(base,'v2');m=json.loads((base/'manifest.json').read_text());before_budget=sha(base/'price-queue/provider_budget.json');check(before_budget==m['assets']['price-queue/provider_budget.json']['sha256'],'BUDGET_NOT_MANIFEST_AUTHORITY');mapping={Path(x).name:sha(x) for x in m['historical_mappings']}
 if phase=='recover':
  c.finish_migration();check(not c.migration.pending(),'MIGRATION_DRAIN');check(queue.recover()==1,'PRICE_RESTART_RECOVERY');done=completion.read_completed(str(c.paths['v2']['ops']),MINT,'15m',m['checkpoint']['planned']);check(set(done)=={CHUNK},'CHECKPOINT_CONTINUITY')
  before={k:logical(v) for k,v in c.paths['v2'].items()};check(c.birth.admit_birth(p.support.payload(sig='pending-create',mint='new-launch',time=3))['state']=='ACKED','BIRTH_DUPLICATE');check(c.migration.admit('pending-migration','PUMPSWAP_MIGRATION_ACCOUNT',3)['state']=='ACKED','MIGRATION_DUPLICATE');c.drain_births();check(before=={k:logical(v) for k,v in c.paths['v2'].items()},'IDEMPOTENCY')
  for kind in ['core','ops']:
   q=c.conn(c.paths['v1'][kind])
   try:
    try:q.execute('CREATE TABLE forbidden_stale_writer(x)');raise Hold('STALE_WRITER_ACCEPTED')
    except c.w.DatabaseRetiredError:pass
   finally:q.close()
  check(sha(base/'price-queue/provider_budget.json')==before_budget,'BUDGET_RESET');check(all(sha(x)==mapping[Path(x).name] for x in m['historical_mappings']),'MAPPING_CHANGED')
  write(base/'restart-result.json',{'status':'PASS','birth_ACKs':[r['state'] for r in c.birth.snapshot()],'migration_ACKs':[r['state'] for r in c.migration.snapshot()],'completed_windows':list(done),'budget_sha256':before_budget,'mapping_hashes':mapping,'duplicate_changes':False,'stale_writes_rejected':2,'price_queue_pending':queue.depth()['pending']})
 elif phase=='rollback':
  before={k:logical(v) for k,v in c.paths['v2'].items()};before['creator']=logical(base/'v2-creator.sqlite')
  c.w.retire_database('core',c.paths['v2']['core'],generation='fixture-v2',timeout=.2);c.w.retire_database('ops',c.paths['v2']['ops'],generation='fixture-v2',timeout=.2);c.select_authority('rollback','TRANSFER');c.bind_environment('rollback',True);freeze_copy(c,'v2','rollback');activate(c,'rollback')
  after={k:logical(v) for k,v in c.paths['rollback'].items()};after['creator']=logical(base/'rollback-creator.sqlite');check(before==after,'ROLLBACK_RESTORATION')
  check(sha(base/'price-queue/provider_budget.json')==before_budget and all(sha(x)==mapping[Path(x).name] for x in m['historical_mappings']),'ROLLBACK_STATE_CHANGED')
  write(base/'rollback-result.json',{'status':'PASS','all_tables_and_secondary_exact':True,'target_generation':c.guard.validate_pair(),'stable_state_preserved':True,'originals_remain_retired':all(Path(str(c.paths[g][k])+'.retired').exists() for g in ['v1','v2'] for k in ['core','ops'])})
 else:raise Hold('UNKNOWN_PHASE')
 storage(base)

def execute(base,source_commit,expected_hash):
 validate(base,source_commit,expected_hash);check(not (base/'execution-started.json').exists(),'SINGLE_EXECUTION_ONLY');write(base/'execution-started.json',{'source':source_commit,'manifest_sha256':expected_hash,'migration_count':1});before=storage(base);runs=[]
 for phase,expected in [('migrate',71),('recover',0),('rollback',0)]:
  result=subprocess.run([PYTHON,'-B',str(Path(__file__).resolve()),'phase',str(base),'--phase',phase],capture_output=True,text=True,timeout=90,env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'})
  write(base/(phase+'-process.json'),{'phase':phase,'exit':result.returncode,'stdout':result.stdout[-8000:],'stderr':result.stderr[-8000:]});runs.append({'phase':phase,'exit':result.returncode});check(result.returncode==expected,'PHASE_FAILED_'+phase);check(sha(base/'manifest.json')==expected_hash,'MANIFEST_CHANGED');storage(base)
 m=json.loads((base/'manifest.json').read_text());snapshot=json.loads((base/'snapshot-equivalence.json').read_text());base_log=json.loads((base/'baseline-logical.json').read_text());ath=m['ath'];check(float(ath['relative_difference'])<=.001,'ATH_TOLERANCE')
 # Canonical historical table populations are unchanged except explicit synthetic
 # admission surfaces. Exact V1 snapshot equality was verified before replay.
 expected_changes={'core':{'token_analysis','creator_resolution_queue','migration_walkback_handoff','sqlite_sequence'},'ops':{'wt_walkback_queue'}}
 rollback={k:logical(base/('rollback-'+k+'.sqlite')) for k in ['core','ops']}
 for kind in ['core','ops']:
  for table,old in snapshot[kind].items():
   if table not in expected_changes[kind]:check(rollback[kind][table]==old,'PROTECTED_EVIDENCE_CHANGED_'+kind+'_'+table)
 result={'status':'PASS','source_commit':source_commit,'candidate':CANDIDATE,'manifest_sha256':expected_hash,'migration_executions':1,'phases':runs,'authority_bindings_complete':True,'equivalence':{'all_tables_at_transfer':'EXACT','protected_historical_rows_and_schema':'EXACT','history_and_freshness':'EXACT','ATH_numerical_relative_difference':ath['relative_difference'],'ATH_tolerance':'.001','canonical_ATH_provenance_unchanged':True,'original_candles':'independently unqualified; not required by accepted numerical contract'},'restart_idempotency':'PASS','rollback':'PASS_FRESH_MATCHED_PAIR','disk_before':before,'disk_after':storage(base),'production_mutations':0,'provider_calls':0,'runtime_deployment_required':False,'scope':'One provider-free migration of all retained compact DEV tables plus bounded operational fixture; existing worker/classifier/acquisition acceptance reused, not full live daemon deployment'};write(base/'result.json',result);print(json.dumps(result,indent=2))

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('mode',choices=['prepare','validate','execute','phase']);a.add_argument('root');a.add_argument('--source-commit',default='');a.add_argument('--manifest-sha256',default='');a.add_argument('--phase',default='');args=a.parse_args();base=Path(args.root).resolve();check(base.is_relative_to('/private/tmp') and base.name.startswith('dev004-'),'DISPOSABLE_ROOT_REQUIRED')
 try:
  if args.mode=='prepare':prepare(base)
  elif args.mode=='validate':validate(base,args.source_commit,args.manifest_sha256);print('AUTHORITY_BINDINGS_COMPLETE')
  elif args.mode=='phase':stage(base,args.phase)
  else:execute(base,args.source_commit,args.manifest_sha256)
 except BaseException as e:
  if isinstance(e,(KeyboardInterrupt,SystemExit)):raise
  if base.exists():write(base/'first-blocker.json',{'status':'HOLD','mode':args.mode,'phase':args.phase,'error':type(e).__name__,'detail':str(e)[:1000]})
  raise
