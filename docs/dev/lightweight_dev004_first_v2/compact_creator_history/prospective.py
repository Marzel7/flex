# Execute the same DEV evaluator loader as qualification, without rerunning source reads.
import ast,sqlite3,json,time,os,sys,types,importlib.util,shutil,resource
from pathlib import Path
from typing import Any,Optional
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000));D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');O=Path('/private/tmp/dev004-compact-creator-qualified');S=Path('/private/tmp/dev004-watchtower-roles-qualified');manifest=json.loads((O/'history.json').read_text());now=json.loads((O/'equivalence.json').read_text())['anchor_unix'];helper=D/'runtime/creator_resolution/src/ops/dev004_compact_creator_history.py';sp=importlib.util.spec_from_file_location('src.ops.dev004_compact_creator_history',helper);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);sys.modules[sp.name]=m;p=D/'runtime/creator_resolution/src/ops/attribution_outcome.py';tree=ast.parse(p.read_text());ns={'sqlite3':sqlite3,'Any':Any,'time':time};names={'_table','_columns','_normalize_timestamp','_creator_launch_history_span','evaluate_launcher_profile'};exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names or isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'MIN_LAUNCHER_HISTORY','MIN_LAUNCHER_OBSERVATION_SECONDS','FRESH_PROVISIONING_SECONDS'} for t in n.targets)],type_ignores=[]),str(p),'exec'),ns)
file=O/'prospective.sqlite';shutil.copy2(S/'core_v2.sqlite',file);core=sqlite3.connect(file);core.row_factory=sqlite3.Row;core.execute('pragma max_page_count=3840');ops=sqlite3.connect('file:'+str(S/'ops_v2.sqlite')+'?mode=ro',uri=True);ops.row_factory=sqlite3.Row;os.environ['DEV004_CREATOR_HISTORY_PATH']=str(O/'history.json');w=next(iter(manifest['creators']));sample=dict(core.execute('select * from token_analysis limit 1').fetchone());tests=[]
def profile(wallet=w):return ns['evaluate_launcher_profile'](ops,core,wallet,now=now)
def add(mint,wallet=w,ts=now,earliest=None,commit=True):
 r=dict(sample);r.update(mint=mint,pf_ws_creator=wallet,earliest_tx_creator=earliest or wallet)
 for k in ['first_observed_at','analyzed_at','created_at']:r[k]=ts
 core.execute('insert or ignore into token_analysis ('+','.join(r)+') values ('+','.join('?' for _ in r)+')',list(r.values()))
 if commit:core.commit()
def ok(name,value):assert value,name;tests.append({'name':name,'status':'PASS'})
base=profile();add('DEV004_COMPACT_NEW');new=profile();ok('existing_creator_increment',new['launch_count']==base['launch_count']+1 and new['valid_launch_timestamp_count']==base['valid_launch_timestamp_count']+1);add('DEV004_COMPACT_NEW');ok('duplicate_new_birth',profile()==new)
# Historical replay is an exact retained source row, not synthetic provenance.
local={r[0] for r in core.execute('select mint from token_analysis where pf_ws_creator=?',(w,))};mint=next(x for x in manifest['creators'][w]['historical_mints'] if x not in local);source=sqlite3.connect('file:/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db?mode=ro',uri=True);source.row_factory=sqlite3.Row;r=dict(source.execute('select * from token_analysis where mint=?',(mint,)).fetchone());source.close();core.execute('insert into token_analysis ('+','.join(r)+') values ('+','.join('?' for _ in r)+')',list(r.values()));core.commit();ok('historical_replay_not_counted_twice',profile()==new)
core.close();core=sqlite3.connect(file);core.row_factory=sqlite3.Row;ok('restart_baseline_plus_detail',profile()==new);add('DEV004_COMPACT_NEW');ok('restart_duplicate',profile()==new)
add('DEV004_COMPACT_UNCOMMITTED',commit=False);core.close();core=sqlite3.connect(file);core.row_factory=sqlite3.Row;ok('uncommitted_crash_rolls_back',profile()==new)
unseen='DEV004_COMPACT_UNSEEN';ok('unseen_initial_zero',profile(unseen)['launch_count']==0);add('DEV004_COMPACT_UNSEEN_MINT',unseen);a=profile(unseen);ok('unseen_first_launch',a['launch_count']==1 and a['valid_launch_timestamp_count']==1 and a['observation_seconds']==0);add('DEV004_COMPACT_HINT_CONFLICT',unseen,earliest='DEV004_HINT_ONLY');ok('authoritative_creator_not_recovery_hint',profile(unseen)['launch_count']==2 and profile('DEV004_HINT_ONLY')['launch_count']==0)
# Late observed admission has an older event timestamp; identity, not event time, controls count.
add('DEV004_COMPACT_LATE',ts=1);late=profile();ok('late_arrival_extrema',late['launch_count']==new['launch_count']+1 and late['first_seen']==1)
for name,sql,args,wallet in [('retained_timestamp_conflict','update token_analysis set first_observed_at=? where mint=?',(2,mint),w),('historical_identity_reassigned','update token_analysis set pf_ws_creator=? where mint=?',(unseen,mint),unseen)]:
 core.execute(sql,args)
 try:profile(wallet);raise AssertionError('conflict accepted')
 except ValueError as e:
  ok(name,str(e)=='COMPACT_HISTORY_RETAINED_EVIDENCE_CONFLICT')
  if name=='historical_identity_reassigned':
   try:profile(w);raise AssertionError('old-owner conflict accepted')
   except ValueError as old:ok('historical_reassignment_old_owner_also_fails',str(old)=='COMPACT_HISTORY_RETAINED_EVIDENCE_CONFLICT')
 finally:core.rollback()
# Corrupt statistics cannot silently change classification.
bad=json.loads((O/'history.json').read_text());bad['creators'][w]['launch_count']+=1;(O/'bad_history.json').write_text(json.dumps(bad,separators=(',',':')));os.environ['DEV004_CREATOR_HISTORY_PATH']=str(O/'bad_history.json')
try:profile();raise AssertionError('bad manifest accepted')
except ValueError as e:ok('corrupt_statistics_fail_closed',str(e)=='COMPACT_HISTORY_STATISTICS_CONFLICT')
os.environ['DEV004_CREATOR_HISTORY_PATH']=str(O/'history.json');ok('failed_reads_preserve_database',profile()==late)
(O/'prospective_results.json').write_text(json.dumps({'tests':tests,'fixture_bytes':file.stat().st_size,'provider_calls':0,'production_writes':0,'scope':'Actual changed DEV evaluator and helper; source-shaped synthetic admissions and one exact retained row; no listener/provider execution.'},indent=2)+'\n');print('PASS',len(tests),'prospective controls');core.close();ops.close()
