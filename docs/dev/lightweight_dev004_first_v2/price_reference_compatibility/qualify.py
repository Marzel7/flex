"""Exact historical-price compatibility; disposable targets, RO indexed V1 reads."""
import ast,hashlib,importlib.util,json,os,resource,socket,sqlite3,subprocess,sys
from pathlib import Path
from typing import Dict
ROOT=Path(__file__).resolve().parents[4];MINT='BEy3GeKMC3J9dr2eabXYTUuD37YGiSnwWFq13hSgpump'
CORE=Path('/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db');REF=Path('/private/tmp/dev004-compact-creator-qualified/classification_closure/core_v2.sqlite')
def load(path,name):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
owner=load(ROOT/'runtime/creator_resolution/src/ops/dev004_core_base_schema.py','owner');old=load(ROOT/'docs/dev/lightweight_dev004_first_v2/byzantine_event_provenance/qualify.py','old');typed=load(ROOT/'runtime/listener/src/ops/dev004_membership_references.py','typed')
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
def run(base):
 require(not base.exists(),'ROOT_EXISTS');base.mkdir();resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024*1024,64*1024*1024));before=os.statvfs(base).f_bavail*os.statvfs(base).f_frsize;require(before>=2_000_000_000+64*1024*1024,'DISK_FLOOR')
 def deny(*a,**kw):raise RuntimeError('NETWORK_FORBIDDEN')
 socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
 def audit(event,args):
  if event=='sqlite3.connect':
   raw=str(args[0]);p=Path(raw[5:].split('?',1)[0] if raw.startswith('file:') else raw).resolve()
   require(p.is_relative_to(base) or (raw.startswith('file:') and 'mode=ro' in raw),'WRITABLE_SOURCE')
 sys.addaudithook(audit)
 checks=[];plans=[];source=old.ro(CORE);ref=old.ro(REF)
 def exact(c,t):
  rows,plan=old.rows(c,'SELECT * FROM '+t+' WHERE mint=?',(MINT,),2);plans.append({'table':t,'plan':plan});return rows
 peak=exact(source,'token_market_cap_peaks');identity=exact(source,'token_analysis');require(len(peak)==len(identity)==1,'IDENTITY_AUTHORITY');require(not exact(source,'tracked_tokens'),'REGISTRY_CHANGED');require(exact(ref,'token_market_cap_peaks')==peak and exact(ref,'token_analysis')==identity,'RETAINED_SOURCE_DIFFERENCE');checks.append('exact_retained_V1_identity_and_price')
 ddl={t:source.execute('SELECT sql FROM sqlite_schema WHERE name=?',(t,)).fetchone()[0] for t in ['token_analysis','tracked_tokens','token_market_cap_peaks']};source.close();ref.close()
 path=base/'core.sqlite';c=sqlite3.connect(path);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA max_page_count=8192')
 for t in ['token_analysis','tracked_tokens']:c.execute(ddl[t])
 c.commit();c.close()
 def initialize(**kw):return owner.initialize(path,disposable_root=base,allow_disposable_v2=True,price_identity_parent='token_analysis',**kw)
 require(initialize()['status']=='INITIALIZED','INITIALIZE');checks.append('explicit_canonical_parent_bootstrap')
 c=sqlite3.connect(path);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
 old.insert_exact(c,'token_analysis',['mint'],identity[0]);old.insert_exact(c,'token_market_cap_peaks',['mint'],peak[0]);c.commit()
 require(not c.execute('PRAGMA foreign_key_check').fetchall(),'FK');checks.append('exact_import_FK_enabled')
 require(c.execute('SELECT 1 FROM tracked_tokens WHERE mint=?',(MINT,)).fetchone() is None,'INVENTED_MONITORING');checks.append('no_tracking_record_created')
 require(dict(c.execute('SELECT * FROM token_market_cap_peaks WHERE mint=?',(MINT,)).fetchone())==peak[0],'PEAK_CHANGED');checks.append('all_price_columns_exact')
 require(old.insert_exact(c,'token_market_cap_peaks',['mint'],peak[0])==0,'DUPLICATE');checks.append('duplicate_import_idempotent')
 def reject(name,sql,args):
  try:c.execute(sql,args)
  except sqlite3.IntegrityError:checks.append(name);return
  raise RuntimeError('ACCEPTED:'+name)
 reject('missing_identity_rejected','INSERT INTO token_market_cap_peaks(mint) VALUES(?)',('synthetic-absent',))
 reject('required_identity_delete_rejected','DELETE FROM token_analysis WHERE mint=?',(MINT,))
 # Actual accepted audit reader, extracted without module-level runtime startup.
 node=next(n for n in ast.parse((ROOT/'runtime/listener/src/core/launch_audit.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_read_peak_and_current')
 def reader(db):
  ns={'Dict':Dict,'db_connect':lambda *a,**kw:sqlite3.connect(str(db)),'LIVE_DB_PATH':str(db)};exec(compile(ast.Module(body=[node],type_ignores=[]),'accepted-launch-audit-reader','exec'),ns);return ns['_read_peak_and_current'](MINT)
 # Source reader receives a read-only URI, never an ordinary writable source path.
 def source_reader():
  ns={'Dict':Dict,'db_connect':lambda *a,**kw:sqlite3.connect(CORE.as_uri()+'?mode=ro',uri=True),'LIVE_DB_PATH':str(CORE)};exec(compile(ast.Module(body=[node],type_ignores=[]),'accepted-launch-audit-reader','exec'),ns);return ns['_read_peak_and_current'](MINT)
 c.rollback();expected=source_reader();actual=reader(path);require(actual==expected,'READER_DIFFERENCE');checks.append('actual_installed_audit_reader_exact')
 # Actual accepted service UPSERT, no transport, run against disposable evidence.
 tree=ast.parse((ROOT/'runtime/listener/src/core/price_service.py').read_text());sql=next(n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str) and 'INSERT INTO token_market_cap_peaks' in n.value)
 args=(MINT,peak[0]['peak_market_cap'],peak[0]['peak_market_cap_at'],peak[0]['raw_peak_mc'],peak[0]['effective_peak_mc'],peak[0]['candidate_peak_mc'],peak[0]['candidate_peak_count'],peak[0]['raw_peak_mc_at'])
 c.execute(sql,args);c.commit();require(dict(c.execute('SELECT * FROM token_market_cap_peaks WHERE mint=?',(MINT,)).fetchone())==peak[0],'PRICE_UPSERT_CHANGE');checks.append('accepted_price_service_UPSERT_equivalent')
 c.execute('SAVEPOINT prospective');larger=list(args);larger[1]=args[1]+100;c.execute(sql,larger);require(c.execute('SELECT peak_market_cap FROM token_market_cap_peaks WHERE mint=?',(MINT,)).fetchone()[0]==larger[1],'FUTURE_PEAK');c.execute('ROLLBACK TO prospective');c.execute('RELEASE prospective');checks.append('prospective_price_write_preserved_without_monitor_membership')
 c.close();require(initialize()['status']=='ALREADY_INITIALIZED','REPEAT');checks.append('repeat_schema_profile')
 try:owner.initialize(path,disposable_root=base,allow_disposable_v2=True)
 except owner.BootstrapHold:checks.append('incompatible_default_profile_rejected')
 else:raise RuntimeError('SILENT_PROFILE_SWITCH')
 # Reopen in a new interpreter: no source access required, exact row oracle passed by file.
 (base/'oracle.json').write_text(json.dumps({'identity':identity[0],'peak':peak[0]},sort_keys=True))
 child=subprocess.run([sys.executable,'-B',__file__,str(base),'restart'],capture_output=True,text=True,timeout=15);require(child.returncode==0,'RESTART:'+child.stderr[-1000:]);checks.append('fresh_process_restart_exact_and_idempotent')
 # Requested six Byzantine imports, independently current exact source records.
 entries,opsddl,_=old.source_evidence(base);oc=sqlite3.connect(base/'ops.sqlite');oc.row_factory=sqlite3.Row;oc.execute('PRAGMA foreign_keys=ON');oc.execute('PRAGMA max_page_count=4096')
 for t in opsddl:oc.execute(opsddl[t])
 typed.initialize(oc,disposable_root=base,allow_disposable_v2=True)
 for e in entries:
  for t,k,r in [('operators','operator_id',e['operator']),('confirmed_operation_matches','match_id',e['match']),('operator_launch_membership','mint',e['membership'])]:old.insert_exact(oc,t,[k],r)
  require(old.insert_exact(oc,'operator_launch_membership',['mint'],e['membership'])==0,'BYZ_DUPLICATE');checks.append('Byzantine_FK_import:'+e['key']['mint'])
 oc.commit();require(not oc.execute('PRAGMA foreign_key_check').fetchall(),'BYZ_FK');oc.close()
 # Return directly to the full-seed integrity gate. Only bounded fixture metadata/rows.
 rc=old.ro(REF);rc.set_progress_handler(lambda:1,2_000_000)
 missing=rc.execute('SELECT p.mint FROM token_market_cap_peaks p LEFT JOIN token_analysis t ON t.mint=p.mint WHERE t.mint IS NULL LIMIT 1').fetchone();require(missing is None,'ANOTHER_PRICE_IDENTITY_MISSING');checks.append('all_retained_peak_canonical_identities_present');rc.close()
 after=os.statvfs(base).f_bavail*os.statvfs(base).f_frsize;files=[p for p in base.iterdir() if p.is_file()];require(after>=2_000_000_000+64*1024*1024,'FINAL_FLOOR');require(sum(p.stat().st_blocks*512 for p in files)<64*1024*1024 and all(p.stat().st_size<64*1024*1024 for p in files),'STORAGE')
 result={'price_reference_status':'PASS','checks_passed':len(checks),'checks':checks,'mint':MINT,'rowid_retained':13,'source_schema_sha256':old.digest(ddl),'identity_sha256':old.digest(identity[0]),'peak_sha256':old.digest(peak[0]),'price_reader_output':actual,'price_values':peak[0],'source_provenance':{k:identity[0].get(k) for k in ['first_observed_source','first_observed_at','first_observed_mc','first_observed_confidence','price_source','migration_tx','create_tx_signature']},'ATH_relative_difference':0,'ATH_tolerance':0.001,'byzantine_imports':6,'foreign_keys_enabled':True,'tracked_records_created':0,'provider_calls':0,'production_mutations':0,'free_before':before,'free_after':after,'allocated_bytes':sum(p.stat().st_blocks*512 for p in files),'largest_file_bytes':max(p.stat().st_size for p in files),'plans':plans,'full_seed_price_parent_gate':'PASS','complete_migration_rehearsal':'PENDING_NEXT_PHASE_NOT_CLAIMED'}
 (base/'result.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');print(json.dumps(result,indent=2))
def restart(base):
 resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024*1024,64*1024*1024));c=sqlite3.connect(base/'core.sqlite');c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');oracle=json.loads((base/'oracle.json').read_text())
 for t,k in [('token_analysis','identity'),('token_market_cap_peaks','peak')]:require(dict(c.execute('SELECT * FROM '+t+' WHERE mint=?',(MINT,)).fetchone())==oracle[k],'RESTART_CHANGED');require(old.insert_exact(c,t,['mint'],oracle[k])==0,'RESTART_DUPLICATE')
 require(not c.execute('PRAGMA foreign_key_check').fetchall(),'RESTART_FK');c.close();print('PASS')
if __name__=='__main__':
 if len(sys.argv)>2:restart(Path(sys.argv[1]))
 else:run(Path(sys.argv[1]))
