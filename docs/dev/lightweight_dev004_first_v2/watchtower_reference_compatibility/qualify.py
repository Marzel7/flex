"""Synthetic-only FK/transaction controls plus RO preservation of all118 sources.
No selected V1 row is imported into V2; no migration runs.
"""
import ast,hashlib,importlib.util,json,os,resource,socket,sqlite3,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
def load(p,n):
 s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
owner=load(ROOT/'runtime/listener/src/ops/dev004_watchtower_references.py','owner')
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def writer():
 tree=ast.parse((ROOT/'runtime/walkback/src/ops/monitor_live_admission.py').read_text());ns={'hashlib':hashlib,'json':json,'sqlite3':sqlite3,'VERSION':'monitor-live-admission.v1','MAX_EVENT_BYTES':2048,'MAX_PENDING':500,'MAX_LOGICAL_RECORDS':12000,'NORMAL_BUDGET_BYTES':32*1024*1024,'HARD_STOP_BYTES':48*1024*1024,'ImmutableConflict':RuntimeError}
 for name in ['_id','record_member','commit_membership_and_outbox']:
  node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name);exec(compile(ast.Module(body=[node],type_ignores=[]),'accepted-monitor-admission-interface','exec'),ns)
 return ns

def run(base):
 assert not base.exists();base.mkdir();resource.setrlimit(resource.RLIMIT_FSIZE,(8_000_000,8_000_000));before=os.statvfs(base).f_bavail*os.statvfs(base).f_frsize;assert before>=2_000_000_000+64*1024*1024
 def deny(*a,**kw):raise RuntimeError('NETWORK_FORBIDDEN')
 socket.socket.connect=deny;socket.create_connection=deny
 c=sqlite3.connect(base/'synthetic.sqlite');c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA max_page_count=512')
 for ddl in ['CREATE TABLE operators(operator_id TEXT PRIMARY KEY)', 'CREATE TABLE operator_identity_events(event_id TEXT PRIMARY KEY,operator_id TEXT)', 'CREATE TABLE confirmed_operation_matches(match_id TEXT PRIMARY KEY,operator_id TEXT,mint TEXT,state TEXT)', 'CREATE TABLE monitor_admission_outbox(event_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL,mint TEXT NOT NULL,membership_id TEXT NOT NULL UNIQUE,committed_at INTEGER NOT NULL,semantic_version TEXT NOT NULL,opening_id TEXT,state TEXT NOT NULL DEFAULT \'PENDING\',claimed_at INTEGER,delivered_at INTEGER)', 'CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY,funder_sig TEXT,status TEXT,intelligence_outcome TEXT)', 'CREATE TABLE wt_attribution_outcomes(mint TEXT PRIMARY KEY,operator_id TEXT,outcome_type TEXT,confidence TEXT,evidence_json TEXT)']:c.execute(ddl)
 c.execute('INSERT INTO operators VALUES(?)',(owner.WATCHTOWER,));owner.initialize(c,disposable_root=base,allow_disposable_v2=True);c.commit();checks=[]
 def passed(n):checks.append(n)
 passed('fresh_extended_schema_FK_enabled')
 def member(mint,event,pop=None,t=1):return (mint,owner.WATCHTOWER,pop,t,event)
 def add(row):c.execute('INSERT INTO operator_launch_membership VALUES(?,?,?,?,?)',row)
 def outbox(mint,t=1,state='PENDING',outcome='UNKNOWN_INFRASTRUCTURE'):
  mid=f'WATCHTOWER_CONFIRMED:{mint}:{t}';data=['monitor-live-admission.v1',owner.WATCHTOWER,mint,mid,t,'WATCHTOWER_CONFIRMED_V1',None];oid=hashlib.sha256(json.dumps(data,separators=(',',':')).encode()).hexdigest()
  c.execute('INSERT INTO monitor_admission_outbox(event_id,operation_id,mint,membership_id,committed_at,semantic_version,state) VALUES(?,?,?,?,?,?,?)',(oid,owner.WATCHTOWER,mint,mid,t,'WATCHTOWER_CONFIRMED_V1',state));c.execute('INSERT INTO wt_attribution_outcomes VALUES(?,?,?,?,?)',(mint,owner.WATCHTOWER if outcome=='CANONICAL_OPERATOR_REACHED' else None,outcome,'MEDIUM','{"counterevidence":"retain"}'));return member(mint,mid,owner.OUTBOX_POP,t)
 def reject(name,sql,args=()):
  c.execute('SAVEPOINT reject')
  try:c.execute(sql,args)
  except sqlite3.IntegrityError:passed(name)
  else:raise AssertionError('UNEXPECTED_ACCEPTANCE:'+name)
  finally:c.execute('ROLLBACK TO reject');c.execute('RELEASE reject')
 # Existing supported branches are unchanged.
 add(member('SYNTHETIC_NONE',None));c.execute('INSERT INTO operator_identity_events VALUES(?,?)',('SYNTHETIC_EVENT',owner.WATCHTOWER));add(member('SYNTHETIC_IDENTITY','SYNTHETIC_EVENT'))
 c.execute('INSERT INTO confirmed_operation_matches VALUES(?,?,?,?)',('SYNTHETIC_MATCH',owner.WATCHTOWER,'SYNTHETIC_MATCH_MINT','CONFIRMED_MATCH'));add(member('SYNTHETIC_MATCH_MINT','SYNTHETIC_MATCH'))
 for mint,expected in [('SYNTHETIC_NONE','NONE'),('SYNTHETIC_IDENTITY','IDENTITY_EVENT'),('SYNTHETIC_MATCH_MINT','CONFIRMED_MATCH')]:assert c.execute('SELECT reference_kind FROM operator_membership_reference_v1 WHERE mint=?',(mint,)).fetchone()[0]==expected;passed('existing_branch:'+expected)
 a=outbox('SYNTHETIC_CONFLICT');before_outcome=dict(c.execute('SELECT * FROM wt_attribution_outcomes WHERE mint=?',(a[0],)).fetchone());add(a);assert dict(c.execute('SELECT * FROM wt_attribution_outcomes WHERE mint=?',(a[0],)).fetchone())==before_outcome;assert c.execute('SELECT reference_kind FROM operator_membership_reference_v1 WHERE mint=?',(a[0],)).fetchone()[0]=='MEMBERSHIP_COMMIT';passed('unknown_infrastructure_not_promoted')
 assert c.execute('SELECT state FROM monitor_admission_outbox WHERE mint=?',(a[0],)).fetchone()[0]=='PENDING';passed('pending_admission_unchanged')
 b=outbox('SYNTHETIC_DELIVERED',state='DELIVERED');add(b);passed('delivered_reference_supported')
 c.execute('INSERT INTO wt_walkback_queue VALUES(?,?,?,?)',('SYNTHETIC_FUNDING','SYNTHETIC_SIGNATURE','complete','LINEAGE_GAP'));c.execute('INSERT INTO wt_attribution_outcomes VALUES(?,?,?,?,?)',('SYNTHETIC_FUNDING',owner.WATCHTOWER,'CANONICAL_OPERATOR_REACHED','HIGH','{}'));add(member('SYNTHETIC_FUNDING','SYNTHETIC_SIGNATURE',owner.FUNDING_POP));assert c.execute("SELECT intelligence_outcome FROM wt_walkback_queue WHERE mint='SYNTHETIC_FUNDING'").fetchone()[0]=='LINEAGE_GAP';passed('funding_signature_and_lineage_gap_unchanged')
 reject('unknown_reference','INSERT INTO operator_launch_membership VALUES(?,?,?,?,?)',member('SYNTHETIC_UNKNOWN','SYNTHETIC_ABSENT'))
 reject('wrong_source_population','INSERT INTO operator_launch_membership VALUES(?,?,?,?,?)',member(a[0]+'wrong',a[4],owner.FUNDING_POP))
 reject('wrong_commit_timestamp','UPDATE operator_launch_membership SET assigned_at=2 WHERE mint=?',(a[0],))
 reject('outbox_parent_delete','DELETE FROM monitor_admission_outbox WHERE mint=?',(a[0],))
 reject('outcome_counterevidence_delete','DELETE FROM wt_attribution_outcomes WHERE mint=?',(a[0],))
 reject('funding_signature_rewrite',"UPDATE wt_walkback_queue SET funder_sig='CHANGED' WHERE mint='SYNTHETIC_FUNDING'")
 reject('sidecar_type_rewrite',"UPDATE operator_membership_reference_v1 SET reference_kind='CONFIRMED_MATCH' WHERE mint=?",(a[0],))
 # Outbox delivery remains a permitted separate recovery action; this test rolls it back.
 c.execute('SAVEPOINT delivery');c.execute("UPDATE monitor_admission_outbox SET state='DELIVERED',delivered_at=9 WHERE mint=?",(a[0],));assert owner.validate(c);c.execute('ROLLBACK TO delivery');c.execute('RELEASE delivery');passed('delivery_recovery_not_fenced_by_reference_identity')
 c.commit();owner.initialize(c,disposable_root=base,allow_disposable_v2=True);passed('idempotent_extended_bootstrap')
 before_rows=c.execute('SELECT COUNT(*) FROM operator_launch_membership').fetchone()[0]
 for row in [a,b]:c.execute('INSERT OR IGNORE INTO operator_launch_membership VALUES(?,?,?,?,?)',row)
 assert c.execute('SELECT COUNT(*) FROM operator_launch_membership').fetchone()[0]==before_rows;passed('duplicate_memberships_not_added')
 assert not c.execute('PRAGMA foreign_key_check').fetchall();passed('all_synthetic_foreign_keys')
 # Actual accepted writer interface: no module startup or providers.
 ns=writer()
 mint='SYNTHETIC_NEW_COMMIT';t=5;mid=f'WATCHTOWER_CONFIRMED:{mint}:{t}';c.execute('INSERT INTO wt_attribution_outcomes VALUES(?,?,?,?,?)',(mint,owner.WATCHTOWER,'CANONICAL_OPERATOR_REACHED','HIGH','{}'));c.commit()
 ns['commit_membership_and_outbox'](c,operation_id=owner.WATCHTOWER,mint=mint,source_population_id=owner.OUTBOX_POP,membership_id=mid,committed_at=t,semantic_version='WATCHTOWER_CONFIRMED_V1');assert c.in_transaction;probe=sqlite3.connect(base/'synthetic.sqlite');assert not probe.execute('SELECT 1 FROM operator_launch_membership WHERE mint=?',(mint,)).fetchone();probe.close();c.commit();passed('actual_membership_and_outbox_writer_compatible_no_implicit_commit')
 # A failed child insert must not leave a newly admitted outbox parent behind.
 failed='SYNTHETIC_FAILED_CHILD';mid=f'WATCHTOWER_CONFIRMED:{failed}:7'
 try:ns['commit_membership_and_outbox'](c,operation_id=owner.WATCHTOWER,mint=failed,source_population_id=owner.OUTBOX_POP,membership_id=mid,committed_at=7,semantic_version='WATCHTOWER_CONFIRMED_V1')
 except sqlite3.IntegrityError:pass
 else:raise AssertionError('MISSING_OUTCOME_ACCEPTED')
 assert not c.execute('SELECT 1 FROM monitor_admission_outbox WHERE mint=?',(failed,)).fetchone();c.rollback();passed('failed_child_rolls_back_outbox_savepoint')
 c.execute('SAVEPOINT ambiguity');c.execute('INSERT INTO operator_identity_events VALUES(?,?)',(a[4],owner.WATCHTOWER));reject('ambiguous_parent_namespace','UPDATE operator_launch_membership SET assigned_at=assigned_at WHERE mint=?',(a[0],));c.execute('ROLLBACK TO ambiguity');c.execute('RELEASE ambiguity')
 c.execute('SAVEPOINT nullable_ambiguity');c.execute('INSERT INTO operator_identity_events VALUES(?,?)',('SYNTHETIC_SIGNATURE',owner.WATCHTOWER));c.execute('INSERT INTO confirmed_operation_matches VALUES(?,?,?,?)',('SYNTHETIC_SIGNATURE',owner.WATCHTOWER,'SYNTHETIC_FUNDING','CONFIRMED_MATCH'));reject('nullable_population_cannot_bypass_exclusive_namespace',"UPDATE operator_launch_membership SET source_population_id=NULL WHERE mint='SYNTHETIC_FUNDING'");c.execute('ROLLBACK TO nullable_ambiguity');c.execute('RELEASE nullable_ambiguity')
 c.commit();before_state=digest([dict(x) for x in c.execute('SELECT * FROM monitor_admission_outbox ORDER BY event_id')]);before_outcomes=digest([dict(x) for x in c.execute('SELECT * FROM wt_attribution_outcomes ORDER BY mint')]);c.close()
 child=subprocess.run([sys.executable,'-B',__file__,str(base),'restart'],capture_output=True,text=True,timeout=15);assert child.returncode==0,child.stderr;passed('fresh_process_restart_and_duplicate_suppression')
 child=subprocess.run([sys.executable,'-B',__file__,str(base),'crash'],capture_output=True,text=True,timeout=15);assert child.returncode==71,child.stderr
 c=sqlite3.connect(base/'synthetic.sqlite');c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');assert not c.execute("SELECT 1 FROM operator_launch_membership WHERE mint='SYNTHETIC_CRASH'").fetchone();assert not c.execute("SELECT 1 FROM monitor_admission_outbox WHERE mint='SYNTHETIC_CRASH'").fetchone();assert digest([dict(x) for x in c.execute('SELECT * FROM monitor_admission_outbox ORDER BY event_id')])==before_state
 assert digest([dict(x) for x in c.execute("SELECT * FROM wt_attribution_outcomes WHERE mint!='SYNTHETIC_CRASH' ORDER BY mint")])==before_outcomes;assert owner.validate(c);passed('process_crash_before_caller_commit_rolls_back_both_writes');c.close()
 c=sqlite3.connect(base/'flagoff.sqlite');c.execute('PRAGMA foreign_keys=ON')
 for ddl in ['CREATE TABLE operator_launch_membership(mint TEXT PRIMARY KEY,operator_id TEXT,source_population_id TEXT,assigned_at INTEGER,event_id TEXT)', 'CREATE TABLE monitor_admission_outbox(event_id TEXT PRIMARY KEY,operation_id TEXT,mint TEXT,membership_id TEXT UNIQUE,committed_at INTEGER,semantic_version TEXT,opening_id TEXT,state TEXT DEFAULT \'PENDING\',claimed_at INTEGER,delivered_at INTEGER)']:c.execute(ddl)
 trace=[];c.set_trace_callback(trace.append);ns['commit_membership_and_outbox'](c,operation_id=owner.WATCHTOWER,mint='SYNTHETIC_FLAGOFF',source_population_id='UNCHANGED',membership_id='SYNTHETIC_LEGACY_ID',committed_at=1);assert next(i for i,x in enumerate(trace) if x.startswith('INSERT INTO operator_launch_membership'))<next(i for i,x in enumerate(trace) if x.startswith('INSERT INTO monitor_admission_outbox'));c.rollback();c.close();passed('legacy_default_writer_order_and_semantics_unchanged')
 (base/'checks.json').write_text(json.dumps({'checks':checks,'groups_passed':len(checks)},indent=2)+'\n');print(json.dumps({'groups_passed':len(checks),'checks':checks},indent=2))
def reopen(base,crash=False):
 resource.setrlimit(resource.RLIMIT_FSIZE,(8_000_000,8_000_000));c=sqlite3.connect(base/'synthetic.sqlite');c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');owner.initialize(c,disposable_root=base,allow_disposable_v2=True);ns=writer()
 if crash:
  c.execute("INSERT INTO wt_attribution_outcomes VALUES('SYNTHETIC_CRASH',?,'CANONICAL_OPERATOR_REACHED','HIGH','{}')",(owner.WATCHTOWER,));c.commit();ns['commit_membership_and_outbox'](c,operation_id=owner.WATCHTOWER,mint='SYNTHETIC_CRASH',source_population_id=owner.OUTBOX_POP,membership_id='WATCHTOWER_CONFIRMED:SYNTHETIC_CRASH:9',committed_at=9,semantic_version='WATCHTOWER_CONFIRMED_V1');os._exit(71)
 before=c.execute('SELECT count(*) FROM operator_launch_membership').fetchone()[0]
 for r in c.execute('SELECT * FROM operator_launch_membership').fetchall():c.execute('INSERT OR IGNORE INTO operator_launch_membership VALUES(?,?,?,?,?)',tuple(r))
 assert c.execute('SELECT count(*) FROM operator_launch_membership').fetchone()[0]==before;assert owner.validate(c);assert c.execute("SELECT outcome_type FROM wt_attribution_outcomes WHERE mint='SYNTHETIC_CONFLICT'").fetchone()[0]=='UNKNOWN_INFRASTRUCTURE';assert c.execute("SELECT state FROM monitor_admission_outbox WHERE mint='SYNTHETIC_CONFLICT'").fetchone()[0]=='PENDING';c.close()
if __name__=='__main__':
 if len(sys.argv)>2:reopen(Path(sys.argv[1]),sys.argv[2]=='crash')
 else:run(Path(sys.argv[1]))
