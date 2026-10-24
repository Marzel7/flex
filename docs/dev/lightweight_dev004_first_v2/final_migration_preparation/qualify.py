"""Bounded exact-six typed import and mandatory full-reference integrity gate."""
import hashlib,importlib.util,json,os,resource,socket,sqlite3,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
def load(path,name):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
old=load(ROOT/'docs/dev/lightweight_dev004_first_v2/byzantine_event_provenance/qualify.py','authority')
mod=load(ROOT/'runtime/listener/src/ops/dev004_membership_references.py','typed')
def require(ok,message):
 if not ok:raise RuntimeError(message)
def run(base):
 require(not base.exists(),'ROOT_EXISTS');base.mkdir();resource.setrlimit(resource.RLIMIT_FSIZE,(8_000_000,8_000_000))
 before=os.statvfs(base).f_bavail*os.statvfs(base).f_frsize;require(before>=2_000_000_000+64*1024*1024,'DISK_FLOOR')
 def deny(*a,**kw):raise RuntimeError('NETWORK_FORBIDDEN')
 socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
 def audit(event,args):
  if event=='sqlite3.connect':
   raw=str(args[0]);path=Path(raw[5:].split('?',1)[0] if raw.startswith('file:') else raw).resolve()
   require(path.is_relative_to(base) or (raw.startswith('file:') and 'mode=ro' in raw),'WRITABLE_SOURCE_FORBIDDEN')
 sys.addaudithook(audit)
 entries,ddl,plans=old.source_evidence(base);checks=[]
 def passed(name):checks.append(name)
 c=sqlite3.connect(base/'import.sqlite');c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA max_page_count=1024')
 for t in ddl:c.execute(ddl[t])
 mod.initialize(c,disposable_root=base,allow_disposable_v2=True);passed('empty_legacy_bootstrap')
 for e in entries:
  old.insert_exact(c,'operators',['operator_id'],e['operator']);old.insert_exact(c,'confirmed_operation_matches',['match_id'],e['match'])
  require(old.insert_exact(c,'operator_launch_membership',['mint'],e['membership'])==1,'EXACT_IMPORT_FAILED')
  require(dict(c.execute('SELECT * FROM operator_launch_membership WHERE mint=?',(e['key']['mint'],)).fetchone())==e['membership'],'CANONICAL_CHANGED')
  r=dict(c.execute('SELECT * FROM operator_membership_reference_v1 WHERE mint=?',(e['key']['mint'],)).fetchone())
  require(r['reference_kind']=='CONFIRMED_MATCH' and r['confirmed_match_id']==e['key']['event_id'] and r['identity_event_id'] is None,'WRONG_NAMESPACE')
  passed('exact_FK_import:'+e['key']['mint'])
 c.commit();require(not c.execute('PRAGMA foreign_key_check').fetchall(),'FK_FAILED');passed('all_foreign_keys')
 require(c.execute('SELECT count(*) FROM operator_identity_events').fetchone()[0]==0,'MANUFACTURED_EVENTS');passed('no_manufactured_identity_events')
 for e in entries:require(old.insert_exact(c,'operator_launch_membership',['mint'],e['membership'])==0,'DUPLICATE')
 c.commit();passed('six_idempotent_replays')
 mod.initialize(c,disposable_root=base,allow_disposable_v2=True);passed('repeat_initialization')
 def reject(name,sql,args=()):
  c.execute('SAVEPOINT probe')
  try:
   c.execute(sql,args);raise RuntimeError('UNEXPECTED_ACCEPTANCE:'+name)
  except sqlite3.IntegrityError:passed(name)
  finally:c.execute('ROLLBACK TO probe');c.execute('RELEASE probe')
 e=entries[0];v=e['membership'];op=v['operator_id'];mint=v['mint'];event=v['event_id']
 reject('unknown_parent',"INSERT INTO operator_launch_membership VALUES(?,?,?,?,?)",('unknown',op,None,1,'absent'))
 reject('wrong_match_mint',"INSERT INTO operator_launch_membership VALUES(?,?,?,?,?)",('different',op,None,1,event))
 reject('wrong_operator',"UPDATE operator_launch_membership SET operator_id='absent' WHERE mint=?",(mint,))
 reject('required_match_parent_deletion','DELETE FROM confirmed_operation_matches WHERE match_id=?',(event,))
 reject('direct_reference_deletion','DELETE FROM operator_membership_reference_v1 WHERE mint=?',(mint,))
 reject('reference_rewrite',"UPDATE operator_membership_reference_v1 SET reference_kind='NONE',legacy_event_id=NULL,confirmed_match_id=NULL WHERE mint=?",(mint,))
 reject('immutable_membership_identity',"UPDATE operator_launch_membership SET mint='changed' WHERE mint=?",(mint,))
 c.execute("INSERT INTO operator_launch_membership VALUES('synthetic-null',?,NULL,1,NULL)",(op,));require(c.execute("SELECT reference_kind FROM operator_membership_reference_v1 WHERE mint='synthetic-null'").fetchone()[0]=='NONE','NULL_COMPATIBILITY');passed('legacy_null_compatibility')
 c.execute("DELETE FROM operator_launch_membership WHERE mint='synthetic-null'");require(not c.execute("SELECT 1 FROM operator_membership_reference_v1 WHERE mint='synthetic-null'").fetchone(),'CASCADE');passed('membership_delete_cascade')
 # Both accepted parent namespaces remain usable; ambiguous namespaces fail closed.
 columns=[r[1] for r in c.execute('PRAGMA table_info(operator_identity_events)')]
 prototype={k:None for k in columns}
 for r in c.execute('PRAGMA table_info(operator_identity_events)'):
  if r[3]:prototype[r[1]]=1 if 'INT' in r[2].upper() else 'synthetic-control'
 prototype['event_id']='synthetic-identity';prototype['operator_id']=op
 old.insert_exact(c,'operator_identity_events',['event_id'],prototype)
 c.execute("INSERT INTO operator_launch_membership VALUES('identity-control',?,NULL,1,'synthetic-identity')",(op,))
 require(c.execute("SELECT reference_kind FROM operator_membership_reference_v1 WHERE mint='identity-control'").fetchone()[0]=='IDENTITY_EVENT','IDENTITY_NAMESPACE');passed('identity_event_namespace')
 c.execute("DELETE FROM operator_launch_membership WHERE mint='identity-control'");c.execute("DELETE FROM operator_identity_events WHERE event_id='synthetic-identity'")
 prototype['event_id']=event;old.insert_exact(c,'operator_identity_events',['event_id'],prototype)
 reject('ambiguous_namespace',"UPDATE operator_launch_membership SET assigned_at=assigned_at WHERE mint=?",(e['membership']['mint'],))
 c.execute('DELETE FROM operator_identity_events WHERE event_id=?',(event,));c.commit()
 try:mod.initialize(c,disposable_root=base,allow_disposable_v2=False);raise RuntimeError('IMPLICIT_INITIALIZATION')
 except mod.ReferenceContractError:passed('explicit_disposable_authorization_required')
 c.close()
 child=subprocess.run([sys.executable,'-B',__file__,str(base),'restart'],capture_output=True,text=True,timeout=15)
 require(child.returncode==0,'RESTART:'+child.stderr[-1000:]);passed('fresh_process_restart_and_duplicate_suppression')
 # Three packages contain byte-identical explicit disposable owners.
 package_hashes={p:hashlib.sha256((ROOT/f'runtime/{p}/src/ops/dev004_membership_references.py').read_bytes()).hexdigest() for p in ['listener','creator_resolution','walkback']}
 require(len(set(package_hashes.values()))==1,'PACKAGE_COMPOSITION');passed('three_package_composition')
 # Mandatory full reference gate: first exact retained violation only, bounded lookup.
 core=old.ro(Path('/private/tmp/dev004-compact-creator-qualified/classification_closure/core_v2.sqlite'))
 violation=core.execute('SELECT * FROM pragma_foreign_key_check LIMIT 1').fetchone();require(violation is not None,'REFERENCE_GATE_CHANGED')
 table,rowid,parent,fkid=tuple(violation);require(table=='token_market_cap_peaks' and parent=='tracked_tokens','UNEXPECTED_REFERENCE_GATE')
 peak=core.execute('SELECT * FROM token_market_cap_peaks WHERE rowid=?',(rowid,)).fetchone();mint=peak['mint'];core.close()
 src=old.ro(old.CORE);parent_rows,parent_plan=old.rows(src,'SELECT * FROM tracked_tokens WHERE mint=?',(mint,),2);src.close();require(not parent_rows,'PARENT_AUTHORITY_CHANGED')
 blocker={'table':table,'retained_rowid':rowid,'mint':mint,'missing_parent':parent,'source_parent_count':0,'indexed_source_plan':parent_plan,'action':'HOLD; no parent synthesized, peak deleted or FK bypassed'}
 after=os.statvfs(base).f_bavail*os.statvfs(base).f_frsize
 files=[p for p in base.iterdir() if p.is_file()];require(all(p.stat().st_size<8_000_000 for p in files),'FILE_CAP');require(after>=2_000_000_000+64*1024*1024,'FINAL_DISK_FLOOR')
 result={'status':'HOLD','six_imports':'PASS','controls_passed':len(checks),'controls':checks,'package_sha256':package_hashes,'cases':[{'mint':e['key']['mint'],'event_id':e['key']['event_id'],'membership_sha256':old.digest(e['membership']),'match_sha256':old.digest(e['match']),'reference_kind':'CONFIRMED_MATCH'} for e in entries],'source_schema_sha256':old.digest(ddl),'source_paths':{'CoreV1':str(old.CORE),'OPSV1':str(old.OPS)},'foreign_keys_enabled':True,'new_identity_events':0,'blocker':blocker,'full_rehearsal':'NOT_RUN_MANDATORY_REFERENCE_INTEGRITY_GATE_FAILED','production_mutations':0,'provider_calls':0,'disk_free_before':before,'disk_free_after':after,'largest_file':max(p.stat().st_size for p in files),'allocated_bytes':sum(p.stat().st_blocks*512 for p in files),'runtime_deployment_required':False,'runtime_deployment_reason':'Explicit disposable schema qualification only; no production activation authorized.'}
 (base/'result.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');print(json.dumps(result,indent=2))
def restart(base):
 resource.setrlimit(resource.RLIMIT_FSIZE,(8_000_000,8_000_000));c=sqlite3.connect(base/'import.sqlite');c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');mod.initialize(c,disposable_root=base,allow_disposable_v2=True)
 require(c.execute('SELECT count(*) FROM operator_launch_membership').fetchone()[0]==6,'RESTART_CARDINALITY')
 require(c.execute("SELECT count(*) FROM operator_membership_reference_v1 WHERE reference_kind='CONFIRMED_MATCH'").fetchone()[0]==6,'RESTART_REFERENCE')
 require(not c.execute('PRAGMA foreign_key_check').fetchall(),'RESTART_FK');c.close();print('PASS')
if __name__=='__main__':
 if len(sys.argv)>2:restart(Path(sys.argv[1]))
 else:run(Path(sys.argv[1]))
