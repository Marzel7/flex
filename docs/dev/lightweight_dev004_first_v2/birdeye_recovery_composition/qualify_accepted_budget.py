import subprocess,sys,json,resource,importlib.util,os,socket
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');O=Path('/private/tmp/dev004-recovery-composition-budget-verified');O.mkdir(exist_ok=True)
s=subprocess.check_output(['git','show','71b6e338cfb54057ec4984d41bfbe02477c90dba:src/ops/dev_provider_budget.py'],cwd=D,text=True);(O/'accepted_dev_provider_budget.py').write_text(s)
child='''import sys,json,resource,socket
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
import importlib.util
s=importlib.util.spec_from_file_location('budget',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
try:r=m.DevProviderBudget(Path(sys.argv[2])).admit('SYNTHETIC_TOKEN','SYNTHETIC_WINDOW',now=1000)
except m.BudgetDenied as e:r={'denied':str(e)}
print(json.dumps(r))
'''
def admit(root):
 p=subprocess.run([sys.executable,'-B','-c',child,str(O/'accepted_dev_provider_budget.py'),str(root)],capture_output=True,text=True,timeout=5,env={'PATH':'/usr/bin:/bin'});assert p.returncode==0,p.stderr;return json.loads(p.stdout)
r=O/'normal';r.mkdir(exist_ok=True);first=admit(r);second=admit(r);third=admit(r);fourth=admit(r);exhausted=admit(r);assert [x['token_calls'] for x in [first,second,third,fourth]]==[1,2,3,4];assert exhausted['denied']=='TOKEN_PROVIDER_BUDGET_EXHAUSTED'
# Non-destructive separate disposable failure-state fixture: an existing malformed ledger.
bad=O/'malformed';bad.mkdir(exist_ok=True);
for i in range(4):assert admit(bad)['token_calls']==i+1
assert admit(bad)['denied']=='TOKEN_PROVIDER_BUDGET_EXHAUSTED'
(bad/'provider_budget.json').write_text('{incomplete');reset=admit(bad);assert reset['token_calls']==1
# Trace the accepted method's real fsync calls on a separate disposable admission.
spec=importlib.util.spec_from_file_location('budget',O/'accepted_dev_provider_budget.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);syncs=[];real=os.fsync
os.fsync=lambda fd:(syncs.append(fd),real(fd))[1]
try:admission=m.DevProviderBudget(O/'sync_trace').admit('SYNTHETIC_TOKEN','SYNTHETIC_WINDOW',now=1000)
finally:os.fsync=real
assert syncs==[]
result={'status':'HOLD_ACCEPTED_BUDGET_DURABILITY','accepted_source_revision':'71b6e338cfb54057ec4984d41bfbe02477c90dba','valid_ledger_fresh_interpreter_counts':[1,2,3,4],'valid_ledger_exhaustion':exhausted,'malformed_existing_ledger_result':reset,'fsync_calls_during_real_admission':len(syncs),'findings':['Normal process restart preserves valid rolling-window counts and four-call token ceiling','Accepted code treats any OSError or ValueError reading state as empty allowance','Accepted publication uses write_text plus os.replace without file or directory fsync; no power-loss durable pre-dispatch barrier demonstrated'],'provider_calls':0,'synthetic_tokens':1,'maximum_single_file_bytes':16000000,'five_acquisition_cases':'NOT_RUN_STOP_RULE','implementation_changes':0}
(O/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
