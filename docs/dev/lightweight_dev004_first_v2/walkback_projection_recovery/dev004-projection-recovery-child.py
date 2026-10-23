import sys,os,json,sqlite3
from pathlib import Path
root=Path(sys.argv[1]);phase=sys.argv[2];enabled=sys.argv[3] if len(sys.argv)>3 else '1';os.environ['DEV004_WALKBACK_PROJECTION_RECOVERY_ENABLED']=enabled
source=Path('/private/tmp/dev004-walkback-run-bootstrap.py').read_text().replace('/private/tmp/dev004-walkback-complete-run',str(root)).replace('/Users/kevinkeaveney/Production/flex-walkback-dev002','/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex/runtime/walkback')
exec(compile(source,'dev-bootstrap','exec'))
from src.ops import dev004_walkback_projection_recovery as recovery
ww._rpc=forbidden
mint=json.loads((root/'target.json').read_text())['mint']
ops.close()
class CrashConnection(sqlite3.Connection):
 def executescript(self,sql):
  if phase=='before_membership_commit' and self.execute('select 1 from operator_launch_membership where mint=?',(mint,)).fetchone():os._exit(81)
  return super().executescript(sql)
ops=ww._ops_conn() if phase=='tracked' else connect(root/'ops.sqlite',factory=CrashConnection);ops.row_factory=sqlite3.Row
# The case cursor starts directly before the already-qualified counterexample.
if enabled=='1' and not ops.execute("select 1 from sqlite_master where name='dev004_walkback_projection_cursor'").fetchone():
 ops.execute(recovery.CURSOR_DDL);rowid=ops.execute('select rowid from wt_walkback_queue where mint=?',(mint,)).fetchone()[0];ops.execute('insert into dev004_walkback_projection_cursor values(1,?)',(rowid-1,));ops.commit()
def fault(point):
 if phase=='after_membership_commit' and point=='after_membership_commit':os._exit(82)
 if phase=='after_ack' and point=='after_ack':os._exit(83)
if phase=='vm_budget':recovery.MAX_VM_STEPS=0
if phase=='database_budget':recovery.MAX_DB_BYTES=1
if phase=='outbox_capacity':
 from src.ops import monitor_live_admission
 monitor_live_admission.MAX_PENDING=0
if phase in ('normal','tracked'):result=ww._recover_committed_projections(ops)
else:result=recovery.recover_batch(ops,core_db_path=str(root/'core.sqlite'),fault=fault)
record={'result':result,'membership':[dict(r) for r in ops.execute('select * from operator_launch_membership where mint=?',(mint,))],'queue':dict(ops.execute('select * from wt_walkback_queue where mint=?',(mint,)).fetchone()),'canonical':dict(ops.execute('select * from wt_attribution_outcomes where mint=?',(mint,)).fetchone()) if ops.execute('select 1 from wt_attribution_outcomes where mint=?',(mint,)).fetchone() else None,'outbox':[dict(r) for r in ops.execute('select * from monitor_admission_outbox where mint=?',(mint,))]}
(root/'last.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(result));ops.close()
