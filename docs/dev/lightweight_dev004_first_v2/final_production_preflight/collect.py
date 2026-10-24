"""Bounded read-only live production preflight; no migration or service actions."""
import ast,collections,configparser,datetime,gzip,hashlib,json,os,re,resource,sqlite3,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];SOURCE=Path('/Users/kevinkeaveney/Dev/claude/flex/database');CONF=Path('/Users/kevinkeaveney/Dev/claude/flex-development/DEV090/operational-restoration/supervisord.selective-retry2.conf');REHEARSAL=Path('/private/tmp/dev004-full-direct-rehearsal-7c7304b1/dev004-import')
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
def metadata(p):
 if not p.exists():return {'path':str(p),'exists':False}
 s=p.stat();return {'path':str(p),'exists':True,'device':s.st_dev,'inode':s.st_ino,'bytes':s.st_size,'allocated_bytes':s.st_blocks*512,'mtime_ns':s.st_mtime_ns}
def ro(p):
 c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.2);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('PRAGMA cache_size=-2048');c.execute('BEGIN');return c
def save(p,data):
 b=(json.dumps(data,indent=2,sort_keys=True)+'\n').encode();assert len(b)<8_000_000;p.write_bytes(b)
def run(out):
 assert not out.exists();out.mkdir();resource.setrlimit(resource.RLIMIT_FSIZE,(8_000_000,8_000_000));started=datetime.datetime.now(datetime.timezone.utc).isoformat();v=os.statvfs(out);free_before=v.f_bavail*v.f_frsize
 def audit(event,args):
  if event=='sqlite3.connect':assert str(args[0]).startswith('file:') and 'mode=ro' in str(args[0]),'READONLY_ONLY'
  if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
   p=Path(os.fsdecode(args[0]));assert p.name not in ['.env','.env.production'],'CREDENTIAL_VALUES_FORBIDDEN';flags=args[2] if isinstance(args[2],int) else 0
   if flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):assert p.resolve().is_relative_to(out),'PRODUCTION_WRITE_FORBIDDEN'
 sys.addaudithook(audit)
 config=configparser.ConfigParser(interpolation=None);config.read(CONF);services={};safe_paths={'DB_PATH','WT_OPS_DB_PATH','FLEX_DB_PATH','OPS_V2_DB_PATH','CREATOR_DB_PATH','OPERATION_MONITOR_QUEUE_PATH','MONITOR_BRIDGE_QUEUE_PATH','MONITOR_BRIDGE_SOURCE_DB','MONITOR_BRIDGE_MONITOR_DB','MONITOR_RUNTIME_STATE_ROOT','PUMPPORTAL_CANONICAL_BIRTH_RETENTION_PATH','BIRTH_JOURNAL_PATH','MIGRATION_JOURNAL_PATH','FLEX_DB_PAIR_MANIFEST','FLEX_DB_PAIR_AUTHORITY'}
 for section in config.sections():
  if not section.startswith('program:'):continue
  d=config[section];env=dict((k,x.strip('"\'')) for k,x in re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*("[^"]*"|\'[^\']*\'|[^,]*)',d.get('environment','')))
  services[section[8:]]={'directory':d.get('directory'),'directory_exists':Path(d.get('directory','/NONEXISTENT')).exists(),'command_sha256':hashlib.sha256(d.get('command','').encode()).hexdigest(),'environment_names':sorted(env),'bindings':{k:x for k,x in env.items() if k in safe_paths or k.endswith('_ENABLED') or k in ['DB_WRITE_SERIALIZE','WALKBACK_MAX_ATTEMPTS','WALKBACK_RPC_BUDGET_BATCH','MONITOR_PROVIDER_GLOBAL_LIMIT','MONITOR_PROVIDER_TOKEN_LIMIT']},'autostart':d.get('autostart')}
 ctl=subprocess.run(['/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python','-m','supervisor.supervisorctl','-c',str(CONF),'status'],capture_output=True,text=True,timeout=10);assert ctl.returncode in [0,3] and 'RUNNING' in ctl.stdout,ctl.stderr
 for line in ctl.stdout.splitlines():
  fields=line.split();name,state=fields[:2];services[name]['state']=state;pids=re.search(r'pid (\d+)',line);services[name]['pid']=int(pids[1]) if pids else None
 prod={'listener':Path('/Users/kevinkeaveney/Production/flex-listener'),'creator_resolution':Path('/Users/kevinkeaveney/Production/flex-main-workers'),'walkback':Path('/Users/kevinkeaveney/Production/flex-walkback-dev002')};files=['src/core/db.py','src/core/database_write_service.py','src/utils/db_locking.py','src/core/paired_binding_guard.py','src/core/birth_admission_journal.py','src/core/migration_signature_journal.py','src/ops/dev004_watchtower_references.py','src/utils/birdeye_credentials.py'];composition={}
 for component,p in prod.items():
  composition[component]=[]
  for f in files:
   target=p/f;candidate=ROOT/'runtime'/component/f;entry={'file':f,'installed_exists':target.is_file(),'candidate_exists':candidate.is_file()}
   for tag,path in [('installed',target),('candidate',candidate)]:
    if path.is_file():entry[tag+'_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
   composition[component].append(entry)
 listener=prod['listener']/'src/core/pumpfun_curve_listener.py';tree=ast.parse(listener.read_text());names={n.name for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))};functions={name:name in names for name in ['_persist_durable_birth','_drain_durable_birth_batch','_schedule_journal_migration']}
 wrapper=prod['listener']/'run_listener.sh';w=wrapper.read_text();wrapper_flags={}
 for name in ['PUMPPORTAL_COMPACT_BIRTH_RECEIPTS_ENABLED','PUMPPORTAL_COMPACT_BIRTH_RECEIPTS_PATH','DEV004_DURABLE_BIRTH_JOURNAL_ENABLED','DEV004_SIGNATURE_FIRST_JOURNAL_ENABLED','DEV004_PAIRED_BINDING_REQUIRED','BIRTH_JOURNAL_PATH','MIGRATION_JOURNAL_PATH','FLEX_DB_PAIR_MANIFEST','FLEX_DB_PAIR_AUTHORITY']:
  values=re.findall(r'(?m)^\s*(?:export\s+)?'+name+'=(.*)$',w);wrapper_flags[name]=values
 # Compare the complete selected rehearsal population, not a rerun/import.
 oracle_file=ROOT/'docs/dev/lightweight_dev004_first_v2/full_disposable_rehearsal/row_oracles.json.gz';oracles=json.loads(gzip.decompress(oracle_file.read_bytes()));source={k:ro(SOURCE/n) for k,n in [('core','flex_complete_database.db'),('ops','wt_ops_v2.db')]};baseline={k:ro(REHEARSAL/(k+'.sqlite')) for k in ['core','ops']};changed=[];missing=[];checked=0;external=0;plans={};schema={};queue_states={};bounded_active={};roots={}
 def read(c,sql,args):
  plan=[r[3] for r in c.execute('EXPLAIN QUERY PLAN '+sql,args)];assert not any('SCAN ' in p for p in plan),'UNINDEXED';start=time.monotonic();c.set_progress_handler(lambda:int(time.monotonic()-start>1),1000)
  try:rows=c.execute(sql,args).fetchmany(2)
  finally:c.set_progress_handler(None,0)
  return rows,plan
 for kind,tables in oracles.items():
  for table,entry in tables.items():
   if table=='operator_membership_reference_v1':continue
   if table=='dev004_retained_reference_evidence':external+=len(entry['rows']);continue
   keys=entry['keys'];query='SELECT * FROM "'+table+'" WHERE '+' AND '.join('"'+k+'" IS ?' for k in keys)+' LIMIT 2'
   for record in entry['rows']:
    current,plan=read(source[kind],query,record['key']);plans.setdefault(kind+'.'+table,plan);checked+=1
    if len(current)!=1:missing.append({'database':kind,'table':table,'key':record['key'],'source_rows':len(current)});continue
    row=dict(current[0])
    if digest(row)!=record['sha256']:
     previous=baseline[kind].execute(query,record['key']).fetchone();before=dict(previous) if previous else {};fields=sorted(k for k in set(before)|set(row) if before.get(k)!=row.get(k));safe={k:{'before':before.get(k),'now':row.get(k)} for k in fields if k in ['status','state','outcome_type','confidence','attempts','locked_until','claimed_by','lease_expires_at','next_retry_at','review_state']};changed.append({'database':kind,'table':table,'key':record['key'],'fields':fields,'before_sha256':record['sha256'],'now_sha256':digest(row),'state_differences':safe})
    if table in ['creator_resolution_queue','creator_funding_queue','birth_persist_queue','migration_walkback_handoff','wt_walkback_queue','monitor_admission_outbox']:
     queue_states.setdefault(kind+'.'+table,collections.Counter())[str(row.get('status',row.get('state','NO_STATUS_COLUMN')))]+=1
  schema[kind]={'schema_version':source[kind].execute('PRAGMA schema_version').fetchone()[0],'journal_mode':source[kind].execute('PRAGMA journal_mode').fetchone()[0]}
 # Current known-operation root delta, bounded by the existing ten identities.
 for record in oracles['ops']['operators']['rows']:
  op=record['key'][0];q='SELECT mint,event_id,operator_id FROM operator_launch_membership WHERE operator_id=? LIMIT 2049';plan=[x[3] for x in source['ops'].execute('EXPLAIN QUERY PLAN '+q,(op,))];assert not any('SCAN ' in p for p in plan);current=source['ops'].execute(q,(op,)).fetchmany(2049);assert len(current)<2049;old={x['key'][0] for x in oracles['ops']['operator_launch_membership']['rows'] if baseline['ops'].execute('SELECT operator_id FROM operator_launch_membership WHERE mint=?',(x['key'][0],)).fetchone()[0]==op};now={x['mint'] for x in current};roots[op]={'current_memberships':len(now),'rehearsal_memberships':len(old),'new_memberships':sorted(now-old),'missing_memberships':sorted(old-now),'plan':plan}
 # Indexed bounded current active-state samples, irrespective of seed membership.
 for kind,table in [('core','creator_resolution_queue'),('core','creator_funding_queue'),('core','birth_persist_queue'),('core','migration_walkback_handoff'),('ops','wt_walkback_queue')]:
  columns=[r[1] for r in source[kind].execute('PRAGMA table_info("'+table+'")')];pk=[r[1] for r in source[kind].execute('PRAGMA table_info("'+table+'")') if r[5]];status='status' if 'status' in columns else 'state' if 'state' in columns else None
  if not status:bounded_active[kind+'.'+table]={'qualification':'NO_GENERIC_STATUS_CONTRACT'};continue
  entries={}
  for state in ['pending','retry','running','processing','quarantined','PENDING','RETRY','RUNNING','QUARANTINED']:
   cols=list(dict.fromkeys(pk+[status]+[k for k in ['attempts','locked_until','claimed_by','lease_expires_at','next_retry_at'] if k in columns]));q='SELECT '+','.join('"'+k+'"' for k in cols)+' FROM "'+table+'" WHERE "'+status+'"=? LIMIT 129';plan=[r[3] for r in source[kind].execute('EXPLAIN QUERY PLAN '+q,(state,))]
   if any('SCAN ' in p for p in plan):entries[state]={'not_executed':'UNINDEXED_STATUS_SELECTOR'};continue
   source[kind].set_progress_handler(lambda:1,200000)
   try:data=[dict(r) for r in source[kind].execute(q,(state,)).fetchmany(129)];entries[state]={'rows':data[:128],'truncated':len(data)==129,'plan':plan}
   except sqlite3.OperationalError:entries[state]={'not_qualified':'READ_BUDGET_EXCEEDED'}
   finally:source[kind].set_progress_handler(None,0)
  bounded_active[kind+'.'+table]=entries
 for c in list(source.values())+list(baseline.values()):c.close()
 # Targeted existing receipt authority only; no general file inventory.
 receipt=Path('/Users/kevinkeaveney/Dev/claude/flex/logs/pumpportal_compact_birth_receipts');stats={'path':str(receipt),'files':0,'allocated_bytes':0,'logical_bytes':0,'largest_file_bytes':0,'legacy_files':0,'bucket_files':{},'complete':True}
 if receipt.exists():
  for directory,dirs,files in os.walk(receipt):
   for name in files:
    if not name.endswith('.json'):continue
    stats['files']+=1
    if stats['files']>105000:stats['complete']=False;break
    path=Path(directory)/name;s=path.stat();stats['allocated_bytes']+=s.st_blocks*512;stats['logical_bytes']+=s.st_size;stats['largest_file_bytes']=max(stats['largest_file_bytes'],s.st_size)
    if Path(directory)==receipt:stats['legacy_files']+=1
    else:stats['bucket_files'][Path(directory).name]=stats['bucket_files'].get(Path(directory).name,0)+1
   if not stats['complete']:break
 stats['full_buckets']=sum(n>=384 for n in stats['bucket_files'].values());stats['remaining_shard_slots']=sum(max(0,384-stats['bucket_files'].get(f'{n:02x}',0)) for n in range(256))
 budget=Path('/Users/kevinkeaveney/Dev/claude/flex/.dev_runtime/monitor/dev_005a/queue/provider_budget.json');budget_state=metadata(budget)
 if budget.exists() and budget.stat().st_size<65536:
  raw=budget.read_bytes();ledger=json.loads(raw);budget_state.update(sha256=hashlib.sha256(raw).hexdigest(),version=ledger.get('version'),entry_count=len(ledger.get('calls',ledger.get('entries',[]))));budget_state['allowance_interpretation']='NOT_RESET_OR_ASSUMED_CURRENT'
 journals={str(Path(str(SOURCE/'flex_complete_database.db')+suffix)):metadata(Path(str(SOURCE/'flex_complete_database.db')+suffix)) for suffix in ['.migration_fallback.jsonl','.migration_terminal_durability_risk.jsonl','.birth_fallback.jsonl']}
 files={kind:{suffix or 'main':metadata(Path(str(SOURCE/name)+suffix)) for suffix in ['', '-wal','-shm','.retired','.pair-bound','.write-owner.json']} for kind,name in [('core','flex_complete_database.db'),('ops','wt_ops_v2.db')]};creator=metadata(Path('/Users/kevinkeaveney/Dev/claude/flex/pumpswap_tokens.db'))
 current=datetime.datetime.now(datetime.timezone.utc).isoformat();v=os.statvfs(out);summary={'status':'HOLD','observed_start_utc':started,'observed_end_utc':current,'source_candidate':'bf0da567634a040c96cc955c9afe165466d94beb','supervisor_config':str(CONF),'supervisor_config_sha256':hashlib.sha256(CONF.read_bytes()).hexdigest(),'services':services,'installed_critical_composition':composition,'installed_listener_durable_functions':functions,'listener_wrapper_sha256':hashlib.sha256(wrapper.read_bytes()).hexdigest(),'listener_wrapper_safe_flags':wrapper_flags,'database_files':files,'secondary_creator_repository':creator,'source_schema':schema,'selected_V1_rows_compared':checked,'external_retained_records_reused':external,'selected_row_changes':len(changed),'selected_row_missing':len(missing),'known_operation_root_delta':roots,'selected_queue_states':{k:dict(v) for k,v in queue_states.items()},'active_state_probes':bounded_active,'receipt_capacity':stats,'price_budget_state':budget_state,'legacy_DB_relative_journals':journals,'delta_scope':'Exact selected-row PK/digest delta plus bounded known-operation roots. Global new birth/migration delta cannot be reconstructed exactly: rehearsal retained no whole-source admission watermark or atomic source generation. No global source-table scan attempted.','free_before':free_before,'free_after':v.f_bavail*v.f_frsize,'production_mutations':0,'provider_calls':0,'imports_or_rehearsals_run':0}
 save(out/'live_evidence.json',summary);save(out/'selected_delta.json',{'changed':changed,'missing':missing});print(json.dumps({'status':'HOLD','selected_rows_compared':checked,'changed':len(changed),'missing':len(missing),'memberships_added':sum(len(x['new_memberships']) for x in roots.values()),'memberships_missing':sum(len(x['missing_memberships']) for x in roots.values()),'receipt_files':stats['files'],'full_buckets':stats['full_buckets'],'remaining_slots':stats['remaining_shard_slots'],'free':summary['free_after']},indent=2))
if __name__=='__main__':run(Path(sys.argv[1]))
