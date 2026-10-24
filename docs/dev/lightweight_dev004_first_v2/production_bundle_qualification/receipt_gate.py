"""Read-only receipt capacity/retirement gate. Never call maintenance.run()."""
import collections,datetime,gzip,hashlib,importlib.util,io,json,os,resource,sqlite3,sys,time,types
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];LIVE=Path('/Users/kevinkeaveney/Production/flex-listener');RECEIPTS=Path('/Users/kevinkeaveney/Dev/claude/flex/logs/pumpportal_compact_birth_receipts');DB=Path('/Users/kevinkeaveney/Dev/claude/flex/database');MAX_FILES=102400;MAX_MANIFEST=32*1024*1024

def load(path,name):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
def utc(ns):return datetime.datetime.fromtimestamp(ns/1e9,datetime.timezone.utc).isoformat()
def save(path,data):
 b=(json.dumps(data,indent=2,sort_keys=True)+'\n').encode();assert len(b)<8_000_000;path.write_bytes(b)
def log_snapshot(path):
 if not path.exists():return {'path':str(path),'exists':False}
 st=path.stat()
 with path.open('rb') as f:f.seek(max(0,st.st_size-8*1024*1024));raw=f.read(8*1024*1024)
 import re
 pat=rb'COMPACT_RECEIPT_FAILURE stage=([A-Za-z_]+) reason=([A-Z_]+)';counts=collections.Counter((a.decode(),b.decode()) for a,b in re.findall(pat,raw))
 return {'path':str(path),'exists':True,'inode':st.st_ino,'bytes':st.st_size,'tail_bytes_examined':len(raw),'failure_counts':{a+':'+b:n for (a,b),n in counts.items()},'mtime_utc':utc(st.st_mtime_ns)}
def run(out):
 assert not out.exists();out.mkdir();resource.setrlimit(resource.RLIMIT_FSIZE,(MAX_MANIFEST,MAX_MANIFEST));sys.dont_write_bytecode=True;start_ns=time.time_ns();cutoff=start_ns-7*86400*1_000_000_000;v=os.statvfs(out);assert v.f_bavail*v.f_frsize>2_000_000_000+64*1024*1024
 def audit(event,args):
  if event=='sqlite3.connect':assert str(args[0]).startswith('file:') and 'mode=ro' in str(args[0]),'SOURCE_SQLITE_READONLY_REQUIRED'
  if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
   p=Path(os.fsdecode(args[0]));assert p.name not in ['.env','.env.production'],'CREDENTIAL_READ_FORBIDDEN';flags=args[2] if isinstance(args[2],int) else 0
   if flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):assert p.resolve().is_relative_to(out),'PRODUCTION_WRITE_FORBIDDEN'
 sys.addaudithook(audit)
 for name,path in [('src',LIVE/'src'),('src.core',LIVE/'src/core')]:
  pkg=types.ModuleType(name);pkg.__path__=[str(path)];sys.modules[name]=pkg
 receipt=load(LIVE/'src/core/pumpportal_birth_audit.py','src.core.pumpportal_birth_audit');maintenance=load(LIVE/'src/core/pumpportal_receipt_maintenance.py','src.core.pumpportal_receipt_maintenance');checker=maintenance.ReceiptDependencyChecker(DB/'flex_complete_database.db',DB/'wt_ops_v2.db');owner=maintenance.CompactReceiptMaintenance(RECEIPTS,checker=checker);reader=receipt.PumpPortalBirthAudit(enabled=False,path=str(out/'unused'),compact_path=str(RECEIPTS))
 logs=[Path('/Users/kevinkeaveney/Dev/claude/flex/logs/supervisor')/n for n in ['listener.log','listener_err.log']];before_logs=[log_snapshot(p) for p in logs]
 primary=collections.Counter();secondary=collections.Counter();outcomes=collections.Counter();types_count=collections.Counter();oldest=None;newest=None;age_eligible=0;dependency_checks=0;protected=[];seen=set();unknown=[];stable=False;initial_counts=None;read_bytes=0
 def list_active():
  dirs=[RECEIPTS]+[RECEIPTS/f'{i:02x}' for i in range(256)];paths=[];counts={};legacy=0
  for directory in dirs:
   if not directory.exists():continue
   assert not directory.is_symlink(),'RECEIPT_DIRECTORY_SYMLINK'
   entries=list(os.scandir(directory));assert len(entries)<=4370 if directory==RECEIPTS else len(entries)<=386
   json_files=[Path(e.path) for e in entries if e.name.endswith('.json') and e.is_file(follow_symlinks=False)]
   if directory==RECEIPTS:legacy=len(json_files)
   else:counts[directory.name]={'all_entries':len(entries),'json_files':len(json_files)}
   paths.extend(json_files)
  assert len(paths)<=MAX_FILES
  return sorted(paths),counts,legacy
 for round_no in range(3):
  paths,counts,legacy=list_active()
  if initial_counts is None:initial_counts=counts
  todo=[p for p in paths if str(p.relative_to(RECEIPTS)) not in seen]
  if not todo:stable=True;break
  for path in todo:
   relative=str(path.relative_to(RECEIPTS));seen.add(relative)
   try:
    assert not path.is_symlink()
    with path.open('rb') as f:raw=f.read(4097)
    assert len(raw)<=4096;read_bytes+=len(raw);sha=hashlib.sha256(raw).hexdigest()
    class Cached:
     def is_symlink(self):return False
     def open(self,*a,**k):return io.BytesIO(raw)
    row=reader._read_receipt(Cached());t=row.get('receipt_utc_ns',row.get('handoff_utc_ns'));types_count[str(row.get('kind'))]+=1;outcomes[str(row.get('persistence_outcome',row.get('outcome')))]+=1
    if isinstance(t,int):oldest=t if oldest is None else min(oldest,t);newest=t if newest is None else max(newest,t)
    if isinstance(t,int) and t<cutoff:
     age_eligible+=1
     if dependency_checks<128:
      for c in checker._connections:c.set_progress_handler(lambda:1,200000)
      try:decision=owner._eligibility(row,start_ns)
      finally:
       for c in checker._connections:c.set_progress_handler(None,0)
      dependency_checks+=1
     else:decision=maintenance.DependencyResult('UNKNOWN','DEPENDENCY_CHECK_BUDGET')
    else:decision=owner._eligibility(row,start_ns)
    if row.get('kind')=='MIGRATION_HANDOFF':secondary['DURABLE_HANDOFF_RETAINED']+=1
    if row.get('canonical_conflict'):secondary['PROTECTED_AUDIT_CONFLICT']+=1
    if row.get('kind')=='PUMPPORTAL_BIRTH' and row.get('persistence_outcome')!='COMMITTED':secondary['UNRESOLVED_RECEIPT']+=1
    primary[decision.status+':'+decision.reason]+=1;protected.append([relative,sha,decision.status,decision.reason])
   except (OSError,ValueError,TypeError,AssertionError,sqlite3.Error) as e:primary['UNKNOWN:UNREADABLE_OR_BUDGET']+=1;unknown.append({'receipt':relative,'reason':type(e).__name__});protected.append([relative,None,'UNKNOWN','UNREADABLE_OR_BUDGET'])
   assert len(seen)<=MAX_FILES
 checker_info=[]
 for c in checker._connections:
  tables={x[0] for x in c.execute("SELECT name FROM sqlite_schema WHERE type='table'")};info={'database':c.execute('PRAGMA database_list').fetchone()[2]}
  if 'migration_persist_queue' in tables:info['migration_index']={r[1]:[x[2] for x in c.execute('PRAGMA index_info('+r[1]+')')] for r in c.execute('PRAGMA index_list(migration_persist_queue)')}
  if 'wb_create_control' in tables:info['handoff_control_present']=c.execute('SELECT 1 FROM wb_create_control WHERE id=1').fetchone() is not None
  checker_info.append(info)
 checker.close();final_paths,final_counts,legacy=list_active();after_logs=[log_snapshot(p) for p in logs];interval=[]
 import re
 for b,a in zip(before_logs,after_logs):
  entry={'path':a['path'],'same_file':b.get('inode')==a.get('inode'),'new_capacity_receipt_failures':None}
  if b.get('exists') and a.get('exists') and entry['same_file'] and a['bytes']>=b['bytes'] and a['bytes']-b['bytes']<1024*1024:
   with Path(a['path']).open('rb') as f:f.seek(b['bytes']);data=f.read(1024*1024)
   counts=collections.Counter((x.decode(),y.decode()) for x,y in re.findall(rb'COMPACT_RECEIPT_FAILURE stage=([A-Za-z_]+) reason=([A-Z_]+)',data));entry.update(new_bytes=len(data),new_failures={x+':'+y:n for (x,y),n in counts.items()},new_capacity_receipt_failures=sum(n for (x,y),n in counts.items() if y=='RECEIPT_CAPACITY'))
  interval.append(entry)
 archive=RECEIPTS/'.retired';archive_info={'exists':archive.exists(),'eligible_archive_write_tested':False,'reason':'No mkdir,rename,archive-capacity mutation or maintenance command executed.'}
 data=(json.dumps(protected,separators=(',',':'))+'\n').encode();assert len(data)<MAX_MANIFEST;compressed=gzip.compress(data,mtime=0);assert len(compressed)<MAX_MANIFEST;(out/'receipt_identity_manifest.json.gz').write_bytes(compressed)
 final_ns=time.time_ns();space=os.statvfs(out);assert space.f_bavail*space.f_frsize>2_000_000_000
 result={'status':'HOLD_P0_RECEIPT_CAPACITY','observed_start_utc':utc(start_ns),'observed_end_utc':utc(final_ns),'retention_cutoff_utc':utc(cutoff),'oldest_receipt_utc':utc(oldest) if oldest else None,'newest_receipt_utc':utc(newest) if newest else None,'oldest_first_retirement_eligible_utc':utc(oldest+7*86400*1_000_000_000+1) if oldest else None,'current_active_json_files':len(final_paths),'legacy_files':legacy,'full_buckets':sum(x['all_entries']>=384 for x in final_counts.values()),'remaining_slots':sum(max(0,384-final_counts.get(f'{i:02x}',{}).get('all_entries',0)) for i in range(256)),'bucket_counts':final_counts,'bodies_read':len(seen),'body_read_bytes':read_bytes,'age_eligible_receipts':age_eligible,'primary_decisions':dict(primary),'secondary_protection_flags':dict(secondary),'receipt_kinds':dict(types_count),'outcome_counts':dict(outcomes),'unreadable':unknown,'bounded_manifest_covers_all_current_paths':len(final_paths)==len(seen) and all(str(p.relative_to(RECEIPTS)) in seen for p in final_paths),'dependency_checks_executed':dependency_checks,'dependency_schema_metadata':checker_info,'archive_state':archive_info,'log_before':before_logs,'log_after':after_logs,'natural_observation_seconds':(final_ns-start_ns)/1e9,'new_log_failures_during_scan':interval,'maintenance_run_executed':False,'maintenance_is_dry_run_capable':False,'retirement_reclaims_physical_space':False,'retirement_semantics':'same-filesystem reversible rename into .retired; no deletion or canonicalDBwrite; recovers active bucket slots only','protected_identity_archive_sha256':hashlib.sha256(compressed).hexdigest(),'protected_manifest_uncompressed_sha256':hashlib.sha256(data).hexdigest(),'protected_manifest_uncompressed_bytes':len(data),'protected_manifest_archive_bytes':len(compressed),'installed_source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [LIVE/'src/core/pumpportal_birth_audit.py',LIVE/'src/core/pumpportal_receipt_maintenance.py',LIVE/'scripts/maintain_pumpportal_compact_receipts.py']},'free_bytes_at_end':space.f_bavail*space.f_frsize,'production_mutations':0,'receipt_moves_or_deletions':0,'provider_calls':0,'service_actions':0,'canonical_birth_loss_count':'NOT_ESTABLISHED; receipt/provenance capacity failures do not alone prove canonicalCorebirthloss.'}
 save(out/'receipt_gate_result.json',result);print(json.dumps({k:v for k,v in result.items() if k not in ['bucket_counts','log_before','log_after','outcome_counts','installed_source_sha256']},indent=2))
if __name__=='__main__':run(Path(sys.argv[1]))
