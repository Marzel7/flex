from pathlib import Path
import json,sqlite3,subprocess,os,sys,shutil,resource,tempfile,hashlib,time
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');BASE=Path('/private/tmp/dev004-compact-creator-qualified/classification_closure');OUT=Path(tempfile.mkdtemp(prefix='dev004-collision-recovery-',dir='/private/tmp'));P='/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python';START=1800000000;M='DEV004_SYNTHETIC_RECOVERY_LAUNCH';results=[]
def fixture(name,overlap=False):
 r=OUT/name;r.mkdir()
 for a,b in [('core_v2.sqlite','core.sqlite'),('ops_v2.sqlite','ops.sqlite')]:shutil.copyfile(BASE/a,r/b)
 c=sqlite3.connect(r/'ops.sqlite');c.row_factory=sqlite3.Row
 c.execute('insert into operator_launch_membership(mint,operator_id,assigned_at,event_id) values(?,?,?,?)',(M,'04265d9f-6eb2-568c-a49e-9253091a4dbb',START,'DEV004_SYNTHETIC_ASSIGNMENT'))
 c.execute('''insert into operation_monitor_facts(operation_id,mint,cohort_class,entry_method,entry_timestamp,entry_mc_usd,entry_status,entry_exactness,latest_mc_usd,running_peak_mc_usd,monitor_state,next_observation_at,monitor_completed_at,provenance_digest,created_at,updated_at) values('watchtower',?,'PROSPECTIVE_MONITOR_COHORT','FIRST_FULL_POST_MIGRATION_SECOND_MC',?,100,'QUALIFIED','ADAPTER_FROZEN',10,1000,'PRICE_MONITOR_COMPLETE_COLLAPSED',NULL,?,'SYNTHETIC_PROVENANCE',?,?)''',(M,START,START+2700,START,START))
 if overlap:c.execute("insert into operation_monitor_observations values('watchtower',?,?,150,'15m','BIRDEYE_OHLCV','PREVIOUS_MONITOR_REQUEST','SYNTHETIC_PRIOR',?,100,200,80,150)",(M,START,START))
 historical=[dict(x) for x in c.execute('select * from operation_monitor_facts where mint!=?',(M,))];c.commit();c.close();(r/'historical.json').write_text(json.dumps(historical,sort_keys=True))
 (r/'queue').mkdir();state=r/'queue/provider_budget.json'
 with state.open('w') as f:json.dump({'version':1,'updated_at':int(time.time()),'calls':[]},f);f.flush();os.fsync(f.fileno())
 fd=os.open(state.parent,os.O_RDONLY);os.fsync(fd);os.close(fd)
 return r

def run(r,**env):
 e={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1',**env};p=subprocess.run([P,'-B','/private/tmp/dev004-collision-child.py',str(D/'runtime/creator_resolution'),'unused',str(r)],env=e,capture_output=True,text=True,timeout=30)
 (r/('last-'+str(len(list(r.glob('last-*'))))+'.log')).write_text(p.stdout[-20000:]+'\n'+p.stderr[-20000:]);return p

def events(r):return [json.loads(x) for x in (r/'events.jsonl').read_text().splitlines()]
def inspect(r):
 c=sqlite3.connect(r/'ops.sqlite');c.row_factory=sqlite3.Row;fact=dict(c.execute('select * from operation_monitor_facts where mint=?',(M,)).fetchone());obs=[dict(x) for x in c.execute('select * from operation_monitor_observations where mint=? order by observation_timestamp',(M,))];hist=[dict(x) for x in c.execute('select * from operation_monitor_facts where mint!=?',(M,))];c.close();assert json.dumps(hist,sort_keys=True)==(r/'historical.json').read_text();return fact,obs

for name in ['normal','before_dispatch','after_response','after_checkpoint','between_windows','before_ack','before_checkpoint']:
 r=fixture(name);p=run(r,CRASH_AT='' if name=='normal' else name)
 if name=='normal':assert p.returncode==0,p.stderr
 else:assert p.returncode==71,(name,p.stdout,p.stderr);p=run(r,RECOVER='1');assert p.returncode==0,(name,p.stdout,p.stderr)
 fact,obs=inspect(r);ev=events(r);calls=[e for e in ev if e['kind']=='synthetic_response'];bud=json.load(open(r/'queue/provider_budget.json'));finals=[e for e in ev if e['kind']=='finalization_committed']
 assert fact['final_proven_ath_mc']==1000 and fact['final_ath_multiple']==10 and fact['drawdown_percent']==99,(name,fact)
 assert len(obs)==3 and len(finals)==1,(name,len(obs),len(finals))
 assert len(bud['calls'])==(4 if name in ['before_dispatch','after_response','before_checkpoint'] else 3),(name,bud)
 expected_calls=4 if name in ['after_response','before_checkpoint'] else 3;assert len(calls)==expected_calls,(name,calls)
 if name in ['after_checkpoint','between_windows','before_ack']:assert len({(e['from'],e['to']) for e in calls})==len(calls)
 before=fact.copy();p=run(r,RECOVER='1',DUPLICATE_FINALIZED='1');assert p.returncode==0,p.stderr;fact2,obs2=inspect(r);assert fact2==before and obs==obs2 and events(r)==ev
 results.append({'case':name,'status':'PASS','budget_debits':len(bud['calls']),'synthetic_requests':len(calls),'finalization_request_id':fact['ath_finalization_request_id']})
 print(name,'PASS',flush=True)
# Existing price buckets must not lose checkpoint identity on a committed window.
r=fixture('prior_bucket_overlap',overlap=True);prior=inspect(r)[1];p=run(r,CRASH_AT='after_checkpoint');assert p.returncode==71,(p.stdout,p.stderr);p=run(r,RECOVER='1');assert p.returncode==0,(p.stdout,p.stderr);fact,obs=inspect(r);calls=[e for e in events(r) if e['kind']=='synthetic_response'];duplicates=len(calls)!=len({(e['from'],e['to']) for e in calls});assert not duplicates;assert obs[0]==prior[0];results.append({'case':'prior_bucket_overlap','status':'HOLD' if duplicates else 'PASS','completed_window_reacquired':duplicates,'requests':calls})

# Independently process equivalent evidence without overwriting acquisition identity.
for name,mutation,expected in [
 ('identical_existing',None,'PASS'),
 ('compatible_distinct_identity',None,'PASS'),
 ('conflicting_value',"update operation_monitor_observations set high_mc_usd=201 where mint=?",'HOLD'),
 ('ambiguous_missing_ohlc',"update operation_monitor_observations set high_mc_usd=NULL where mint=?",'HOLD'),
 ('unknown_provenance',"update operation_monitor_observations set source='UNKNOWN' where mint=?",'HOLD')]:
 r=fixture(name,overlap=True)
 c=sqlite3.connect(r/'ops.sqlite')
 if name=='identical_existing':
  ident=hashlib.sha256(json.dumps({'version':'watchtower-terminal-ath.v1','mint':M,'resolution':'15m','time_from':START,'time_to':START+900},sort_keys=True,separators=(',',':')).encode()).hexdigest()
  c.execute("update operation_monitor_observations set source='BIRDEYE_TERMINAL_ATH_CHUNK',request_identity=? where mint=?",(ident,M))
 if mutation:c.execute(mutation,(M,))
 c.commit();c.close();before=inspect(r)[1]
 p=run(r,DIRECT_CHECKPOINT='1')
 if expected=='PASS':
  assert p.returncode==0,(name,p.stdout,p.stderr)
  c=sqlite3.connect(r/'ops.sqlite');assert c.execute('select count(*) from operation_monitor_window_completion where mint=?',(M,)).fetchone()[0]==1;c.close()
 else:
  assert p.returncode!=0,(name,p.stdout)
  c=sqlite3.connect(r/'ops.sqlite');exists=c.execute("select 1 from sqlite_schema where name='operation_monitor_window_completion'").fetchone()
  if exists:assert c.execute('select count(*) from operation_monitor_window_completion where mint=?',(M,)).fetchone()[0]==0
  c.close()
 assert inspect(r)[1]==before
 assert not (r/'events.jsonl').exists() or not any(e['kind']=='synthetic_response' for e in events(r))
 assert json.load(open(r/'queue/provider_budget.json'))['calls']==[]
 results.append({'case':name,'status':'PASS','window_decision':expected,'canonical_rows_unchanged':True})
# Mixed-window conflict rolls back an earlier new row and completion together.
r=fixture('atomic_mixed_conflict',overlap=True)
c=sqlite3.connect(r/'ops.sqlite');c.execute('update operation_monitor_observations set observation_timestamp=?,high_mc_usd=201 where mint=?',(START+900,M));c.commit();c.close();before=inspect(r)[1]
# Use the child helper directly with two observations in one request window.
child_source=Path('/private/tmp/dev004-collision-child.py').read_text();direct=child_source.replace("'time_to':START+900", "'time_to':START+1800").replace("chunk,candles,START)","chunk,candles+[{'timestamp':START+900,'open':150.0,'high':500.0,'low':140.0,'close':250.0}],START)").replace("'15m',START,START+900,chunk", "'15m',START,START+1800,chunk")
Path('/private/tmp/dev004-collision-mixed-child.py').write_text(direct)
p=subprocess.run([P,'-B','/private/tmp/dev004-collision-mixed-child.py',str(D/'runtime/creator_resolution'),'unused',str(r)],env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1','DIRECT_CHECKPOINT':'1'},capture_output=True,text=True,timeout=30)
assert p.returncode!=0 and inspect(r)[1]==before,(p.stdout,p.stderr)
results.append({'case':'atomic_mixed_conflict','status':'PASS','window_decision':'HOLD','new_rows_rolled_back':True})

# Reproduce the exact old collision using the frozen starting finalizer.
r=fixture('original_collision_control',overlap=True)
old=subprocess.check_output(['git','show','5b72bb26:runtime/creator_resolution/src/ops/watchtower_terminal_ath_finalizer.py'],cwd=D,text=True);(r/'original_finalizer.py').write_text(old)
original_child=Path('/private/tmp/dev004-multi-window-child.py').read_text()
needle_import='from src.ops import operation_monitor_worker as w,watchtower_terminal_ath_finalizer as f'
injection="""import importlib.util
spec=importlib.util.spec_from_file_location('src.ops.watchtower_terminal_ath_finalizer',out/'original_finalizer.py');original_module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=original_module;spec.loader.exec_module(original_module)
from src.ops import operation_monitor_worker as w,watchtower_terminal_ath_finalizer as f"""
assert needle_import in original_child;original_child=original_child.replace(needle_import,injection);Path('/private/tmp/dev004-original-collision-child.py').write_text(original_child)
def original_run(**kw):
 return subprocess.run([P,'-B','/private/tmp/dev004-original-collision-child.py',str(D/'runtime/creator_resolution'),'unused',str(r)],env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1',**kw},capture_output=True,text=True,timeout=30)
p=original_run(CRASH_AT='after_checkpoint');assert p.returncode==71,(p.stdout,p.stderr);p=original_run(RECOVER='1');assert p.returncode==0,(p.stdout,p.stderr)
old_calls=[e for e in events(r) if e['kind']=='synthetic_response'];assert len(old_calls)==4 and old_calls[0]==old_calls[1]
results.append({'case':'original_collision_reproduced','status':'PASS','old_requests':4,'fixed_requests':3})
result={'status':'HOLD' if duplicates else 'PASS','groups':results,'output_root':str(OUT),'real_provider_calls':0};(OUT/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
