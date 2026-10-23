import ast,sqlite3,json,time,resource
from typing import Any
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000));O=Path('/private/tmp/dev004-creator-history-qualified');S=Path('/private/tmp/dev004-watchtower-roles-qualified');p=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002/src/ops/attribution_outcome.py');tree=ast.parse(p.read_text());names={'_table','_columns','_normalize_timestamp','_creator_launch_history_span','evaluate_launcher_profile'};ns={'sqlite3':sqlite3,'Any':Any,'time':time}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names or isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'MIN_LAUNCHER_HISTORY','MIN_LAUNCHER_OBSERVATION_SECONDS','FRESH_PROVISIONING_SECONDS'} for t in n.targets)],type_ignores=[]),str(p),'exec'),ns)
source=sqlite3.connect('file:'+str(S/'core_v2.sqlite')+'?mode=ro',uri=True);core=sqlite3.connect(O/'prospective_core.sqlite');source.backup(core);source.close();core.row_factory=sqlite3.Row;core.execute('pragma max_page_count=3840');ops=sqlite3.connect('file:'+str(S/'ops_v2.sqlite')+'?mode=ro',uri=True);ops.row_factory=sqlite3.Row;now=json.loads((O/'result.json').read_text())['anchor_unix'];fixed=json.loads((O/'result.json').read_text())['cases'];creator=next(x['creator'] for x in fixed if x['v2']['profile']['launch_count']>0)
def profile(w):return ns['evaluate_launcher_profile'](ops,core,w,now=now)
def birth(mint,w,other=None):
 row=dict(core.execute('select * from token_analysis where pf_ws_creator=? limit 1',(creator,)).fetchone());row.update(mint=mint,pf_ws_creator=w,earliest_tx_creator=other or w)
 for k in ['first_observed_at','analyzed_at','created_at']:
  if k in row:row[k]=now
 core.execute('insert or ignore into token_analysis ('+','.join(row)+') values ('+','.join('?' for _ in row)+')',list(row.values()));core.commit()
before=profile(creator);birth('DEV004_CREATOR_PROSPECTIVE_EXISTING',creator);after=profile(creator);assert after['launch_count']==before['launch_count']+1
birth('DEV004_CREATOR_PROSPECTIVE_EXISTING',creator);assert profile(creator)==after
unseen='DEV004_UNSEEN_CREATOR';empty=profile(unseen);birth('DEV004_CREATOR_PROSPECTIVE_UNSEEN',unseen);single=profile(unseen);assert empty['launch_count']==0 and single['launch_count']==1 and single['observation_seconds']==0 and not single['established']
conflict='DEV004_RECOVERY_HINT_ONLY';birth('DEV004_CREATOR_CONFLICT',unseen,conflict);assert profile(unseen)['launch_count']==2 and profile(conflict)['launch_count']==0
saved=profile(creator);core.close();core=sqlite3.connect(O/'prospective_core.sqlite');core.row_factory=sqlite3.Row;assert profile(creator)==saved;birth('DEV004_CREATOR_PROSPECTIVE_EXISTING',creator);assert profile(creator)==saved
result={'existing_creator_count_increment':'PASS','previously_unseen_creator_no_fabricated_span':'PASS','duplicate_birth_identity':'PASS','reopen_and_dedup':'PASS','pf_ws_creator_authority_over_conflicting_recovery_hint':'PASS','scope':'Unchanged installed history-profile evaluator on disposable source-shaped rows; INSERT OR IGNORE models mint admission. Does not qualify missing V1 historical baseline or full listener parsing.','production_writes':0,'provider_calls':0,'bytes':(O/'prospective_core.sqlite').stat().st_size}
(O/'prospective.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2));core.close();ops.close()
