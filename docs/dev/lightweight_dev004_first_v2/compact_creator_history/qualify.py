import ast,sqlite3,json,time,os,types,sys,importlib.util,hashlib,resource
from pathlib import Path
from typing import Any,Optional
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');O=Path('/private/tmp/dev004-compact-creator-qualified');S=Path('/private/tmp/dev004-watchtower-roles-qualified');P=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002');NOW=int(time.time());plans={}
class Bounded(sqlite3.Connection):
 def execute(self,sql,args=()):
  if sql.lstrip().upper().startswith('SELECT') and 'sqlite_master' not in sql:
   plan=[r[3] for r in super().execute('explain query plan '+sql,args)];plans[sql]=plan
   if any('SCAN ' in p.upper() for p in plan):raise sqlite3.OperationalError('HOLD_UNINDEXED_SELECTION: '+sql)
  deadline=time.monotonic()+2;self.set_progress_handler(lambda:int(time.monotonic()>deadline),1000);return super().execute(sql,args)
def conn(p,bounded=False):
 c=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True,factory=Bounded if bounded else sqlite3.Connection);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');return c
src={k:conn('/Users/kevinkeaveney/Dev/claude/flex/database/'+n,True) for k,n in [('core','flex_complete_database.db'),('ops','wt_ops_v2.db')]};v2={k:conn(S/(k+'_v2.sqlite')) for k in src}
helper=D/'runtime/creator_resolution/src/ops/dev004_compact_creator_history.py';spec=importlib.util.spec_from_file_location('src.ops.dev004_compact_creator_history',helper);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);sys.modules[spec.name]=module
names={'_table','_columns','_normalize_timestamp','_creator_launch_history_span','evaluate_launcher_profile','_resolve_treasury_via_op_graph','classify_creator'}
def load(profile):
 ns={'sqlite3':sqlite3,'Any':Any,'Optional':Optional,'time':time}
 for p in [profile,P/'src/core/walkback_queue.py']:
  tree=ast.parse(p.read_text());nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names or isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id in {'MIN_LAUNCHER_HISTORY','MIN_LAUNCHER_OBSERVATION_SECONDS','FRESH_PROVISIONING_SECONDS'} for x in n.targets)];exec(compile(ast.Module(body=nodes,type_ignores=[]),str(p),'exec'),ns)
 return ns
legacy=load(P/'src/ops/attribution_outcome.py');dev=load(D/'runtime/creator_resolution/src/ops/attribution_outcome.py');manifest=json.loads((O/'history.json').read_text());results=[]
def classify(ns,db,w,compact):
 os.environ.pop('DEV004_CREATOR_HISTORY_PATH',None)
 if compact:os.environ['DEV004_CREATOR_HISTORY_PATH']=str(O/'history.json')
 mod=types.ModuleType('src.ops.attribution_outcome');mod.evaluate_launcher_profile=lambda o,c,w:ns['evaluate_launcher_profile'](o,c,w,now=NOW);sys.modules[mod.__name__]=mod
 return list(ns['classify_creator'](w,db['ops'],db['core']))
for w in manifest['creators']:
 r={'creator':w}
 for label,ns,db,compact in [('v1',legacy,src,False),('v2',dev,v2,True)]:
  try:
   if compact:os.environ['DEV004_CREATOR_HISTORY_PATH']=str(O/'history.json')
   else:os.environ.pop('DEV004_CREATOR_HISTORY_PATH',None)
   r[label+'_profile']=ns['evaluate_launcher_profile'](db['ops'],db['core'],w,now=NOW)
  except (sqlite3.Error,ValueError) as e:r[label+'_profile_error']=str(e)
  try:r[label+'_class']=classify(ns,db,w,compact)
  except (sqlite3.Error,ValueError) as e:r[label+'_class_error']=str(e)
 r['statistics_equal']=r.get('v1_profile')==r.get('v2_profile') if 'v1_profile' in r and 'v2_profile' in r else None
 r['classification_equal']=r.get('v1_class')==r.get('v2_class') if 'v1_class' in r and 'v2_class' in r else None
 results.append(r)
# Flag-off identity against unchanged installed evaluator on same compact seed.
os.environ.pop('DEV004_CREATOR_HISTORY_PATH',None);flagoff=all(legacy['evaluate_launcher_profile'](v2['ops'],v2['core'],w,now=NOW)==dev['evaluate_launcher_profile'](v2['ops'],v2['core'],w,now=NOW) for w in manifest['creators']);assert flagoff
summary={'verified_baseline_creators':len(manifest['creators']),'statistics_exact':sum(x['statistics_equal'] is True for x in results),'statistics_different':sum(x['statistics_equal'] is False for x in results),'statistics_unknown':sum(x['statistics_equal'] is None for x in results),'classification_exact':sum(x['classification_equal'] is True for x in results),'classification_different':sum(x['classification_equal'] is False for x in results),'classification_unknown':sum(x['classification_equal'] is None for x in results),'flag_off_exact':flagoff,'cases':results,'source_plans':plans,'anchor_unix':NOW,'manifest_sha256':hashlib.sha256((O/'history.json').read_bytes()).hexdigest(),'manifest_bytes':(O/'history.json').stat().st_size}
(O/'equivalence.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps({k:v for k,v in summary.items() if k not in ['cases','source_plans']},indent=2))
