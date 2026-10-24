"""Six exact legacy match references: authority lookup + enforced disposable import.
No match record is relabelled or manufactured as a governance identity event.
"""
import ast,hashlib,json,os,resource,socket,sqlite3,subprocess,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
OPS=Path('/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db');CORE=OPS.with_name('flex_complete_database.db');REF=Path('/private/tmp/dev004-compact-creator-qualified/classification_closure/ops_v2.sqlite')
LEDGER=ROOT/'docs/dev/lightweight_dev004_first_v2/full_operational_seed_migration/authority_gate_manifest.json'
WRITER=ROOT/'runtime/walkback/src/ops/wsol_10_sol_four_step_operation.py'
VERSION='BYZANTINE_PROVEN_CREATOR_CONTINUITY.v1';FLOOR=2_000_000_000;CAP=64*1024*1024;FILE=8_000_000
class Hold(RuntimeError):pass
def require(ok,code):
 if not ok:raise Hold(code)
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def storage(root):
 files=[p for p in root.rglob('*') if p.is_file()];require(len(files)<=32 and max([p.stat().st_size for p in files] or [0])<FILE,'STORAGE_FILES');used=sum(p.stat().st_blocks*512 for p in files);require(used<CAP,'STORAGE_ALLOCATION');free=os.statvfs(root).f_bavail*os.statvfs(root).f_frsize;require(free>=FLOOR+CAP,'DISK_FLOOR');return {'allocated':used,'free':free,'files':len(files),'largest':max([p.stat().st_size for p in files] or [0])}
def save(base,name,value):
 data=(json.dumps(value,indent=2,sort_keys=True)+'\n').encode();require(len(data)<100000,'METADATA_LIMIT')
 with (base/name).open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
def isolate(base):
 os.environ.clear();os.environ.update(PATH='/usr/bin:/bin',PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(base),SQLITE_TMPDIR=str(base));resource.setrlimit(resource.RLIMIT_FSIZE,(FILE,FILE));sys.dont_write_bytecode=True
 def deny(*a,**kw):raise Hold('NETWORK_FORBIDDEN')
 socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
 def audit(event,args):
  if event=='sqlite3.connect':
   raw=str(args[0]);plain=raw[5:].split('?',1)[0] if raw.startswith('file:') else raw;p=Path(plain).resolve()
   if p in {OPS,CORE,REF}:require(raw.startswith('file:') and 'mode=ro' in raw,'WRITABLE_SOURCE')
   else:require(p==base/'import.sqlite','UNDECLARED_DATABASE')
  if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
   p=Path(os.fsdecode(args[0]));require(p.name not in ['.env','.env.production'],'CREDENTIAL_READ');flags=args[2] if isinstance(args[2],int) else 0
   if flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):require(p.resolve().is_relative_to(base),'PRODUCTION_OR_OUTSIDE_WRITE');storage(base)
 sys.addaudithook(audit)
def ro(path):
 c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.2);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN');return c
def rows(c,sql,args,cap=65):
 plan=[r[3] for r in c.execute('EXPLAIN QUERY PLAN '+sql,args)];require(not any('SCAN ' in p for p in plan),'UNINDEXED_LOOKUP');start=time.monotonic();steps=[0]
 def progress():steps[0]+=1000;return int(steps[0]>200000 or time.monotonic()-start>1)
 c.set_progress_handler(progress,1000)
 try:result=[dict(r) for r in c.execute(sql,args).fetchmany(cap)]
 finally:c.set_progress_handler(None,0)
 require(len(result)<cap and all(len(json.dumps(r).encode())<65536 for r in result),'READ_BUDGET');return result,plan

def provenance(key,member,match):
 return (member['mint']==key['mint'] and member['operator_id']==key['operator_id'] and member['event_id']==key['event_id']
 and match['match_id']==member['event_id'] and match['operator_id']==member['operator_id'] and match['mint']==member['mint']
 and match['state']=='CONFIRMED_MATCH' and match['detector_version']==VERSION
 and member['source_population_id']=='BYZANTINE_PROVEN_CREATOR_CONTINUITY'
 and match['detected_at']==member['assigned_at']
 and str(uuid.uuid5(uuid.NAMESPACE_URL,VERSION+':'+key['mint']))==member['event_id'])
def insert_exact(c,table,keycols,row):
 old=c.execute('SELECT * FROM '+table+' WHERE '+' AND '.join('"'+k+'"=?' for k in keycols),tuple(row[k] for k in keycols)).fetchone()
 if old:require(dict(old)==row,'DUPLICATE_CONTENT_CONFLICT');return 0
 columns=list(row);c.execute('INSERT INTO '+table+'('+','.join('"'+k+'"' for k in columns)+') VALUES('+','.join('?' for k in columns)+')',tuple(row.values()));return 1

def source_evidence(base):
 src=ro(OPS);core=ro(CORE);ref=ro(REF);entries=[];plans=[]
 try:
  for key in json.loads(LEDGER.read_text())['missing_event_dependencies']:
   member,p=rows(src,'SELECT * FROM operator_launch_membership WHERE mint=?',(key['mint'],),2);plans.append({'owner':'membership','plan':p});require(len(member)==1 and digest(member[0])==key['membership_sha256'],'ASSIGNMENT_CHANGED');member=member[0]
   matches,p=rows(src,'SELECT * FROM confirmed_operation_matches WHERE match_id=?',(key['event_id'],),2);plans.append({'owner':'confirmed_match','plan':p});require(len(matches)==1 and provenance(key,member,matches[0]),'MATCH_AUTHORITY_UNQUALIFIED');match=matches[0]
   event,p=rows(src,'SELECT * FROM operator_identity_events WHERE event_id=?',(key['event_id'],),2);plans.append({'owner':'identity_event','plan':p})
   other={}
   for label,c in [('CoreV1',core),('accepted_reference_OPS',ref)]:
    exists=c.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='operator_identity_events'").fetchone()
    if exists:found,p=rows(c,'SELECT * FROM operator_identity_events WHERE event_id=?',(key['event_id'],),2);other[label]={'identity_events':len(found),'plan':p}
    else:other[label]={'identity_event_table':False}
   evidence=json.loads(match['evidence_json']);proof=evidence['creator_proof'];anchor,p=rows(core,'SELECT mint,pf_ws_creator,create_tx_signature,migration_tx FROM token_analysis WHERE mint=?',(key['mint'],),2);plans.append({'owner':'canonical_creator_CREATE_migration','plan':p});require(len(anchor)==1,'CORE_ANCHOR_MISSING');a=anchor[0]
   require((a['pf_ws_creator'],a['create_tx_signature'],a['migration_tx'])==(proof['creator'],proof['create_tx_signature'],proof['migration_tx']),'ANCHOR_PROVENANCE_CONFLICT')
   historical,p=rows(src,'SELECT * FROM operator_launch_membership WHERE mint=?',(proof['proof_mint'],),2);plans.append({'owner':'proof_membership','plan':p});require(len(historical)==1 and historical[0]['operator_id']==key['operator_id'],'CANONICAL_CREATOR_PROOF_MISSING')
   atomic,p=rows(src,'SELECT * FROM wt_walkback_atomic_flows WHERE evidence_key=?',(proof['atomic_evidence_key'],),2);plans.append({'owner':'atomic_proof','plan':p});require(len(atomic)==1 and atomic[0]['mint']==proof['proof_mint'] and atomic[0]['signature']==proof['signature'],'ATOMIC_PROOF_MISSING')
   operator,p=rows(src,'SELECT * FROM operators WHERE operator_id=?',(key['operator_id'],),2);require(len(operator)==1,'OPERATOR_MISSING')
   entries.append({'key':key,'membership':member,'match':match,'operator':operator[0],'identity_event_rows':event,'other_identity_authorities':other,'canonical_core_digest':digest(a),'proof_membership_digest':digest(historical[0]),'atomic_digest':digest(atomic[0]),'provenance_verified':True})
  ddl={t:src.execute("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?",(t,)).fetchone()[0] for t in ['operators','operator_identity_events','confirmed_operation_matches','operator_launch_membership']}
  return entries,ddl,plans
 finally:src.close();core.close();ref.close()

def run(base):
 require(not base.exists(),'ROOT_EXISTS');base.mkdir();isolate(base);before=storage(base);entries,ddl,plans=source_evidence(base)
 require(all(not e['identity_event_rows'] for e in entries),'IDENTITY_EVENT_AUTHORITY_CHANGED')
 # This exact candidate writer records match_id in membership.event_id and
 # never creates identity events. Do not execute the producer during an audit.
 tree=ast.parse(WRITER.read_text());project=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='project_completed_walkback');sql=[n.value for n in ast.walk(project) if isinstance(n,ast.Constant) and isinstance(n.value,str) and 'INSERT' in n.value];require(any('confirmed_operation_matches' in s for s in sql) and any('operator_launch_membership' in s and 'event_id' in s for s in sql) and all('operator_identity_events' not in s for s in sql),'WRITER_AUTHORITY_DIFFERENT')
 c=sqlite3.connect(base/'import.sqlite');c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');require(c.execute('PRAGMA foreign_keys').fetchone()[0]==1,'FOREIGN_KEYS_DISABLED');c.execute('PRAGMA max_page_count=256')
 for t in ddl:c.execute(ddl[t])
 positive=[];negative=[]
 for e in entries:
  insert_exact(c,'operators',['operator_id'],e['operator']);require(insert_exact(c,'confirmed_operation_matches',['match_id'],e['match'])==1,'MATCH_DUPLICATE_INITIAL');c.commit();require(insert_exact(c,'confirmed_operation_matches',['match_id'],e['match'])==0,'MATCH_DUPLICATE_REPLAY')
  wrong=dict(e['match']);wrong['mint']='UNRELATED_IDENTITY';require(not provenance(e['key'],e['membership'],wrong),'WRONG_MINT_ACCEPTED')
  positive.append({'mint':e['key']['mint'],'event_id':e['key']['event_id'],'match_sha256':digest(e['match']),'evidence_sha256':digest(json.loads(e['match']['evidence_json'])),'match_import_exact':True,'duplicate_insertions':0,'wrong_identity_rejected':True,'operator_status':e['operator']['status'],'operator_confidence':e['operator']['confidence']})
  c.execute('SAVEPOINT unresolved')
  try:insert_exact(c,'operator_launch_membership',['mint'],e['membership']);raise Hold('UNEXPECTED_MEMBERSHIP_ACCEPTANCE')
  except sqlite3.IntegrityError as error:require(str(error)=='FOREIGN KEY constraint failed','UNEXPECTED_CONSTRAINT');negative.append({'mint':e['key']['mint'],'event_id':e['key']['event_id'],'result':'HOLD_NO_ACCEPTED_IDENTITY_EVENT_MAPPING','fk_rejected_even_with_match_present':True})
  finally:c.execute('ROLLBACK TO unresolved');c.execute('RELEASE unresolved')
 require(c.execute('SELECT COUNT(*) FROM confirmed_operation_matches').fetchone()[0]==6,'MATCH_CARDINALITY');require(c.execute('SELECT COUNT(*) FROM operator_launch_membership').fetchone()[0]==0,'INVALID_ASSIGNMENT_PRESENT');require(not c.execute('PRAGMA foreign_key_check').fetchall(),'TARGET_FK_FAILURE');c.close()
 # Reopen in a new interpreter for idempotent exact match-record import.
 save(base,'source-record-digests.json',[{'key':e['key'],'match_sha256':digest(e['match']),'membership_sha256':digest(e['membership'])} for e in entries]);result=subprocess.run([sys.executable,'-B',__file__,str(base),'restart'],capture_output=True,text=True,timeout=15,env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'});save(base,'restart-process.json',{'exit':result.returncode,'stdout':result.stdout[-2000:],'stderr':result.stderr[-4000:]});require(result.returncode==0,'RESTART_VERIFICATION_FAILED')
 manifest={'baseline':'2d825bbcf27f802bd0bfcef90cedeba43cc968eb','candidate':'0013ccbde314d0ad124f8ccd2963f15aff437519','prior_evidence':'dab00bbf931e70684228a6195e20268d6557cf5f','writer':str(WRITER),'writer_sha256':hashlib.sha256(WRITER.read_bytes()).hexdigest(),'schema_sha256':digest(ddl),'foreign_key_contract':ddl['operator_launch_membership'],'lookups':plans,'cases':[{'key':e['key'],'membership_sha256':digest(e['membership']),'confirmed_match_sha256':digest(e['match']),'identity_event_count':len(e['identity_event_rows']),'other_authorities':e['other_identity_authorities'],'creator_CREATE_migration_sha256':e['canonical_core_digest'],'original_strict_proof_membership_sha256':e['proof_membership_digest'],'atomic_proof_sha256':e['atomic_digest']} for e in entries]};save(base,'authority_manifest.json',manifest)
 report={'status':'HOLD_NO_ACCEPTED_MATCH_TO_IDENTITY_EVENT_COMPATIBILITY','provenance_source_resolved':6,'FK_compatible_assignments_resolved':0,'original_writer':'project_completed_walkback stores uuid5(detector_version:mint) confirmed-match identity in membership.event_id','cause':'V1 writer/schema namespace inconsistency, not a missing confirmed-match import dependency','authoritative_existing_records':'confirmed_operation_matches; not operator_identity_events','positive_match_imports':positive,'unresolved_assignments':negative,'restart_and_match_idempotency':'PASS;6existingmatchrowsbyte/field-exact,0duplicates','identity_events_manufactured':0,'event_ids_changed_or_cleared':0,'foreign_keys_enforced':True,'source_mutations':0,'provider_calls':0,'diagnostic_before':before,'diagnostic_after':storage(base),'next_action':'Qualify an explicitly authorized typed legacy confirmed-match reference compatibility contract before full seed import. No identity-event synthesis,FKbypass,assignment omission orproductionrepair.'};save(base,'result.json',report);print(json.dumps({k:v for k,v in report.items() if k not in ['positive_match_imports','unresolved_assignments']},indent=2))

def restart(base):
 isolate(base);src=ro(OPS);c=sqlite3.connect(base/'import.sqlite');c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
 try:
  for item in json.loads((base/'source-record-digests.json').read_text()):
   key=item['key'];match,_=rows(src,'SELECT * FROM confirmed_operation_matches WHERE match_id=?',(key['event_id'],),2);require(len(match)==1 and digest(match[0])==item['match_sha256'],'SOURCE_MATCH_CHANGED');require(insert_exact(c,'confirmed_operation_matches',['match_id'],match[0])==0,'RESTART_DUPLICATED_MATCH')
  require(c.execute('SELECT COUNT(*) FROM confirmed_operation_matches').fetchone()[0]==6 and c.execute('SELECT COUNT(*) FROM operator_launch_membership').fetchone()[0]==0,'RESTART_CARDINALITY');require(not c.execute('PRAGMA foreign_key_check').fetchall(),'RESTART_FK_FAILED');c.commit();print('RESTART_MATCH_RECORDS_EXACT_NO_DUPLICATES')
 finally:c.close();src.close()
if __name__=='__main__':
 base=Path(sys.argv[1]).resolve();require(base.is_relative_to('/private/tmp') and base.name.startswith('dev004-'),'DISPOSABLE_ROOT')
 if len(sys.argv)>2:restart(base)
 else:run(base)
