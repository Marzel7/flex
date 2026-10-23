from pathlib import Path
import json,sqlite3,subprocess,os,sys,shutil,resource,tempfile,hashlib,time
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');BASE=Path('/private/tmp/dev004-compact-creator-qualified/classification_closure');OUT=Path(tempfile.mkdtemp(prefix='dev004-multi-recovery-',dir='/private/tmp'));P='/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python';START=1800000000;M='DEV004_SYNTHETIC_RECOVERY_LAUNCH';results=[]
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
 e={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1',**env};p=subprocess.run([P,'-B','/private/tmp/dev004-multi-window-child.py',str(D/'runtime/creator_resolution'),'unused',str(r)],env=e,capture_output=True,text=True,timeout=30)
 (r/('last-'+str(len(list(r.glob('last-*'))))+'.log')).write_text(p.stdout[-20000:]+'\n'+p.stderr[-20000:]);return p

def events(r):return [json.loads(x) for x in (r/'events.jsonl').read_text().splitlines()]
def inspect(r):
 c=sqlite3.connect(r/'ops.sqlite');c.row_factory=sqlite3.Row;fact=dict(c.execute('select * from operation_monitor_facts where mint=?',(M,)).fetchone());obs=[dict(x) for x in c.execute('select * from operation_monitor_observations where mint=? order by observation_timestamp',(M,))];hist=[dict(x) for x in c.execute('select * from operation_monitor_facts where mint!=?',(M,))];c.close();assert json.dumps(hist,sort_keys=True)==(r/'historical.json').read_text();return fact,obs

for name in ['normal','before_dispatch','after_response','after_checkpoint','between_windows','before_ack']:
 r=fixture(name);p=run(r,CRASH_AT='' if name=='normal' else name)
 if name=='normal':assert p.returncode==0,p.stderr
 else:assert p.returncode==71,(name,p.stdout,p.stderr);p=run(r,RECOVER='1');assert p.returncode==0,(name,p.stdout,p.stderr)
 fact,obs=inspect(r);ev=events(r);calls=[e for e in ev if e['kind']=='synthetic_response'];bud=json.load(open(r/'queue/provider_budget.json'));finals=[e for e in ev if e['kind']=='finalization_committed']
 assert fact['final_proven_ath_mc']==1000 and fact['final_ath_multiple']==10 and fact['drawdown_percent']==99,(name,fact)
 assert len(obs)==3 and len(finals)==1,(name,len(obs),len(finals))
 assert len(bud['calls'])==(4 if name in ['before_dispatch','after_response'] else 3),(name,bud)
 expected_calls=4 if name=='after_response' else 3;assert len(calls)==expected_calls,(name,calls)
 if name in ['after_checkpoint','between_windows','before_ack']:assert len({(e['from'],e['to']) for e in calls})==len(calls)
 before=fact.copy();p=run(r,RECOVER='1',DUPLICATE_FINALIZED='1');assert p.returncode==0,p.stderr;fact2,obs2=inspect(r);assert fact2==before and obs==obs2 and events(r)==ev
 results.append({'case':name,'status':'PASS','budget_debits':len(bud['calls']),'synthetic_requests':len(calls),'finalization_request_id':fact['ath_finalization_request_id']})
 print(name,'PASS',flush=True)
# Existing price buckets must not lose checkpoint identity on a committed window.
r=fixture('prior_bucket_overlap',overlap=True);p=run(r,CRASH_AT='after_checkpoint');assert p.returncode==71,(p.stdout,p.stderr);p=run(r,RECOVER='1');assert p.returncode==0,(p.stdout,p.stderr);fact,obs=inspect(r);calls=[e for e in events(r) if e['kind']=='synthetic_response'];duplicates=len(calls)!=len({(e['from'],e['to']) for e in calls});results.append({'case':'prior_bucket_overlap','status':'HOLD' if duplicates else 'PASS','completed_window_reacquired':duplicates,'requests':calls})
result={'status':'HOLD' if duplicates else 'PASS','groups':results,'output_root':str(OUT),'real_provider_calls':0};(OUT/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
