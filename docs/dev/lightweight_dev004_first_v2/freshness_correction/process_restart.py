from pathlib import Path
import json,subprocess,sys,resource
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
O=Path('/private/tmp/dev004-freshness-qualified-exact');code='''import sys,types,sqlite3,json,socket
from pathlib import Path
O=Path(sys.argv[1]);DEV=Path(sys.argv[2]);db=O/'counterexample.sqlite'
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
raw=sqlite3.connect
def connect(path,*a,**k):
 text=str(path);p=Path(text[5:].split('?')[0] if text.startswith('file:') else text).resolve();assert p.is_relative_to(O),'NON_DEV_DATABASE';factory=k.pop('row_factory',None);c=raw(path,*a,**k)
 if factory:c.row_factory=factory
 return c
sqlite3.connect=connect
for name,sub in [('src','src'),('src.ops','src/ops'),('src.utils','src/utils')]:
 m=types.ModuleType(name);m.__path__=[str(DEV/sub)];sys.modules[name]=m
lock=types.ModuleType('src.utils.db_locking');lock.db_connect=connect;sys.modules[lock.__name__]=lock
from src.ops.living_potential_operations import handle_walkback_evidence_update
c=connect(str(db));mint=c.execute("select mint from wt_walkback_edge_candidates where evidence_key='DEV004_GENERATION_COUNTEREXAMPLE'").fetchone()[0];before=c.execute('select count(*) from potential_operation_assessment_version').fetchone()[0];c.close()
out=handle_walkback_evidence_update(str(db),mint=mint)
c=connect(str(db));after=c.execute('select count(*) from potential_operation_assessment_version').fetchone()[0];c.close();assert before==after and not any(x.get('published') for x in out['candidates']);print(json.dumps({'status':'PASS','fresh_interpreter_restart':True,'versions_before':before,'versions_after':after,'new_assessments':0}))
'''
env={'PATH':'/usr/bin:/bin:/opt/homebrew/bin','PYTHONDONTWRITEBYTECODE':'1','LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED':'true','LIVING_V2_FRESHNESS_MAPPING_PATH':str(O/'freshness_mapping.json')}
out=subprocess.check_output([sys.executable,'-c',code,str(O),'/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex/runtime/creator_resolution'],env=env,text=True);value=json.loads(out);(O/'restart_process.json').write_text(json.dumps(value,indent=2)+'\n');r=json.loads((O/'result.json').read_text());r['tests']=[t for t in r['tests'] if t['name']!='fresh_interpreter_restart'];r['tests'].append({'name':'fresh_interpreter_restart','status':'PASS','evidence':value});(O/'result.json').write_text(json.dumps(r,indent=2)+'\n');print(out)
