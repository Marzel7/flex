import ast,sqlite3,json,time,hashlib,resource
from pathlib import Path
from typing import Any,Optional
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex/docs/dev/lightweight_dev004_first_v2');O=Path('/private/tmp/dev004-creator-history-qualified');O.mkdir(exist_ok=True);SEED=Path('/private/tmp/dev004-watchtower-roles-qualified');NOW=int(time.time());plans={}
class Bounded(sqlite3.Connection):
 def execute(self,sql,args=()):
  if sql.lstrip().upper().startswith('SELECT') and 'sqlite_master' not in sql:
   plan=[r[3] for r in super().execute('explain query plan '+sql,args)];plans[sql]=plan
   if any('SCAN ' in p.upper() for p in plan):raise sqlite3.OperationalError('HOLD_UNINDEXED_SELECTION: '+sql)
  deadline=time.monotonic()+.5;self.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
  return super().execute(sql,args)
def conn(p):
 c=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True,factory=Bounded);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');return c
source={'core':conn('/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db'),'ops':conn('/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db')};v2={k:sqlite3.connect('file:'+str(SEED/(k+'_v2.sqlite'))+'?mode=ro',uri=True) for k in source}
for c in v2.values():c.row_factory=sqlite3.Row
P=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002');profile_path=P/'src/ops/attribution_outcome.py';queue_path=P/'src/core/walkback_queue.py';env={'sqlite3':sqlite3,'Any':Any,'Optional':Optional,'time':time}
for path,names in [(profile_path,{'_table','_columns','_normalize_timestamp','_creator_launch_history_span','evaluate_launcher_profile'}),(queue_path,{'_resolve_treasury_via_op_graph','classify_creator'})]:
 tree=ast.parse(path.read_text());nodes=[n for n in tree.body if (isinstance(n,ast.FunctionDef) and n.name in names) or (isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id in {'MIN_LAUNCHER_HISTORY','MIN_LAUNCHER_OBSERVATION_SECONDS','FRESH_PROVISIONING_SECONDS'} for x in n.targets))];exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),env)
# Supply only the unchanged evaluate function for the installed classifier's lazy import.
import types,sys
module=types.ModuleType('src.ops.attribution_outcome');module.evaluate_launcher_profile=lambda o,c,w:env['evaluate_launcher_profile'](o,c,w,now=NOW);sys.modules[module.__name__]=module
fixed=json.loads((D/'operational_seed/creator_launch_history_gaps.json').read_text());assert len(fixed)==100 and len({x['creator'] for x in fixed})==100
out=[]
for x in fixed:
 w=x['creator'];row={'creator':w,'retained_gap':x};
 try:ids=source['core'].execute('select mint from token_analysis where pf_ws_creator=? limit 20001',(w,)).fetchall();row['source_population_within_20000']=len(ids)<20001
 except sqlite3.Error as e:ids=[];row['source_population_within_20000']=False;row['population_error']=str(e)
 for label,dbs in [('v1',source),('v2',v2)]:
  r={}
  try:
   if label=='v1' and not row['source_population_within_20000']:raise sqlite3.OperationalError('HOLD_CREATOR_ROW_CAP')
   r['profile']=env['evaluate_launcher_profile'](dbs['ops'],dbs['core'],w,now=NOW)
  except sqlite3.Error as e:r['profile_error']=str(e)
  try:r['classification']=list(env['classify_creator'](w,dbs['ops'],dbs['core']))
  except sqlite3.Error as e:r['classification_error']=str(e)
  row[label]=r
 row['classification_equal']=row['v1'].get('classification')==row['v2'].get('classification') if 'classification' in row['v1'] and 'classification' in row['v2'] else None
 row['history_inputs_equal']=row['v1'].get('profile')==row['v2'].get('profile') if 'profile' in row['v1'] and 'profile' in row['v2'] else None
 row['missing_source_mints_exact']= [r['mint'] for r in ids if not v2['core'].execute('select 1 from token_analysis where mint=?',(r['mint'],)).fetchone()] if row['source_population_within_20000'] else None
 row['minimum_input_delta']={k:{'v1':row['v1']['profile'].get(k),'v2':row['v2']['profile'].get(k)} for k in row['v1'].get('profile',{}) if row['v1']['profile'].get(k)!=row['v2'].get('profile',{}).get(k)}
 # Existing compact candidates inspected only for this fixed creator, via PK/index.
 row['existing_compact_records']={t:[dict(z) for z in source['core'].execute('select * from '+t+' where '+col+'=? limit 65',(w,)).fetchmany(65)] for t,col in [('creator_historical_performance','creator_address'),('creator_seed_metrics','creator_wallet')]}
 out.append(row)
result={'fixed_creators':100,'anchor_unix':NOW,'classification_exact':sum(x['classification_equal'] is True for x in out),'classification_different':sum(x['classification_equal'] is False for x in out),'classification_unqualified':sum(x['classification_equal'] is None for x in out),'history_exact':sum(x['history_inputs_equal'] is True for x in out),'history_different':sum(x['history_inputs_equal'] is False for x in out),'history_unqualified':sum(x['history_inputs_equal'] is None for x in out),'source_missing_launch_rows':sum(len(x['missing_source_mints_exact'] or []) for x in out),'cases':out,'source_plans':plans,'installed_source_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [profile_path,queue_path]},'fixture_hashes':{n:hashlib.sha256((SEED/n).read_bytes()).hexdigest() for n in ['core_v2.sqlite','ops_v2.sqlite']},'new_reference_rows':0,'status':'HOLD_EXACT_HISTORY_INPUTS_NOT_REPRESENTED'}
(O/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k not in ['cases','source_plans','installed_source_hashes','fixture_hashes']},indent=2))
