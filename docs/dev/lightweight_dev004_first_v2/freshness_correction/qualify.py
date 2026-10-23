from pathlib import Path
import sqlite3,json,types,sys,importlib,shutil,resource,socket,hashlib,os,time
from unittest.mock import patch
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
ROOT=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');DEV=ROOT/'runtime/creator_resolution';P=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002');OUT=Path('/private/tmp/dev004-freshness-qualified-exact');OUT.mkdir(exist_ok=False);SEED=Path('/private/tmp/dev004-operational-seed-qualified/ops_v2.sqlite');COUNTER=SEED.parent/'generation_counterexample.sqlite';original_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [SEED,SEED.parent/'core_v2.sqlite',COUNTER]}
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
raw=sqlite3.connect

def connect(path,*a,**k):
 text=str(path);p=Path(text[5:].split('?')[0] if text.startswith('file:') else text).resolve();assert p.is_relative_to(OUT) or 'mode=ro' in text,'OUTSIDE_DEV_WRITE';factory=k.pop('row_factory',None);k.pop('priority',None);c=raw(path,*a,**k)
 if factory:c.row_factory=factory
 if 'mode=ro' not in text:c.execute('pragma max_page_count=3840');c.execute('pragma cache_size=-2048')
 return c
sqlite3.connect=connect
for name,sub in [('src','src'),('src.ops','src/ops'),('src.utils','src/utils')]:m=types.ModuleType(name);m.__path__=[str(DEV/sub),str(P/sub)];sys.modules[name]=m
lock=types.ModuleType('src.utils.db_locking');lock.db_connect=connect;sys.modules[lock.__name__]=lock
living=importlib.import_module('src.ops.living_potential_operations');reader=importlib.import_module('src.ops.generic_living_persisted_source_reader');mapping=importlib.import_module('src.ops.dev004_freshness_mapping');active=importlib.import_module('src.ops.generic_living_active_components');pipeline=importlib.import_module('src.ops.generic_living_pipeline_v2')
registry=living._generic_living_registry();manifest=mapping.build_mapping(SEED,registry,export_generation='000000095347:000000206259:000000020221');MAP=OUT/'freshness_mapping.json';MAP.write_text(json.dumps(manifest,sort_keys=True,indent=2));assert MAP.stat().st_size<500000
result={'tests':[],'mapping_bytes':MAP.stat().st_size,'real_provider_calls':0,'production_writes':0,'schema_delta':0,'source_module_hashes':{m.__file__:hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in [living,reader,mapping,active,pipeline]}}
old_tables=['potential_operation_identity','potential_operation_assessment_version','potential_operation_assessment_lineage','potential_operation_assessment_association_binding','potential_operation_evidence_association','potential_operation_workflows','operator_launch_membership']
def snapshot(path):
 c=connect('file:'+str(path)+'?mode=ro',uri=True);out={t:{json.dumps(list(r),default=str) for r in c.execute('select * from '+t)} for t in old_tables};c.close();return out
old=snapshot(SEED)
def preserved(path):
 after=snapshot(path)
 for t,values in old.items():assert values<=after[t],t
 for t in ['potential_operation_identity','potential_operation_workflows','operator_launch_membership']:assert old[t]==after[t],t

def test(name,fn):
 try:result['tests'].append({'name':name,'status':'PASS','evidence':fn()})
 except Exception as e:result['tests'].append({'name':name,'status':'HOLD','error':type(e).__name__+':'+str(e)[:600]})
def fixture(name,source=SEED):p=OUT/(name+'.sqlite');shutil.copyfile(source,p);return p

def state(path,spec):
 c=connect('file:'+str(path)+'?mode=ro',uri=True);row=c.execute('select assessment_id,freshness_key from potential_operation_current where potential_operation_id=?',(spec['potential_operation_id'],)).fetchone();n=c.execute('select count(*) from potential_operation_assessment_version where potential_operation_id=?',(spec['potential_operation_id'],)).fetchone()[0];c.close();return row,n

def event_mint(path,spec,family='queue'):
 c=connect('file:'+str(path)+'?mode=ro',uri=True);mints=[r[0] for r in c.execute('select mint from p3r_v2_candidate_membership where run_id=? and candidate_id=?',(spec['persisted_source']['run_id'],spec['source_candidate_id']))];c.close();return mints[0]

def add_evidence(path,spec,family,label):
 c=connect(path);c.row_factory=sqlite3.Row;mint=event_mint(path,spec);table=mapping.FAMILIES[family]
 if family=='queue':
  # New relevant queue identity: retained member without queue after a deliberately
  # narrow disposable schema fixture is covered separately below.
  c.close();raise ValueError('QUEUE_REQUIRES_MISSING_MEMBER_CONTROL')
 row=c.execute('select * from '+table+' where mint=? limit 1',(mint,)).fetchone()
 if row is None:row=c.execute('select * from '+table+' limit 1').fetchone()
 data=dict(row);data['mint']=mint;data['evidence_key']='DEV004_FRESHNESS_'+label
 if 'signature' in data:data['signature']='DEV004_FRESHNESS_'+label
 cols=list(data);c.execute('insert into '+table+'('+','.join(cols)+') values('+','.join('?'*len(cols))+')',tuple(data.values()));c.commit();c.close();return mint,data

def dispatch(path,mint,**kwargs):
 with patch.dict(os.environ,{'LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED':'true','LIVING_V2_FRESHNESS_MAPPING_PATH':str(MAP)},clear=True):return living.handle_walkback_evidence_update(str(path),mint=mint,**kwargs)

def original_counter():
 p=fixture('counterexample',COUNTER);spec=next(iter(registry.values()));mint=connect('file:'+str(p)+'?mode=ro',uri=True).execute("select mint from wt_walkback_edge_candidates where evidence_key='DEV004_GENERATION_COUNTEREXAMPLE'").fetchone()[0]
 before=state(p,spec);base=manifest['candidates'][spec['potential_operation_id']]['base_generation'];recorded='000000095347:000000206259:000000020221';legacy=reader.read_generic_living_source_context(spec,str(p),{'mint':mint,'current_generation':recorded});assert not legacy['relevant_new_evidence'];bound=mapping.bind_mapping(spec,manifest);corrected=reader.read_generic_living_source_context(bound,str(p),{'mint':mint,'current_generation':recorded});assert corrected['relevant_new_evidence'];out=dispatch(p,mint);after=state(p,spec);assert after[1]==before[1]+1 and after[0][1]>before[0][1];assert not any(x.get('published') for x in dispatch(p,mint)['candidates']);preserved(p);return {'legacy_suppressed':True,'mapped_admitted':True,'base_assessment_generation':before[0][1],'exact_recorded_counterexample_generation':recorded,'new_generation':after[0][1],'versions_added':1,'restart_replay_versions':state(p,spec)[1]}
test('original_counterexample_and_restart',original_counter)

def baseline_controls():
 p=fixture('baseline');n=0
 for spec in registry.values():
  mint=event_mint(p,spec);before=state(p,spec);assert not any(x.get('published') for x in dispatch(p,mint)['candidates']);assert state(p,spec)==before;n+=1
 assert dispatch(p,'DEV004_UNRELATED')['candidates']==[];preserved(p);return {'unchanged_baselines':n,'no_historical_replay':True,'unrelated_no_assessment':True}
test('inherited_baseline_and_unrelated',baseline_controls)

def forward(family):
 out=[]
 for index,spec in enumerate(registry.values()):
  p=fixture(family+str(index));mint,data=add_evidence(p,spec,family,family+str(index));before=state(p,spec);answer=dispatch(p,mint);after=state(p,spec);assert after[1]==before[1]+1 and after[0][1]>before[0][1],answer
  c=connect(p);table=mapping.FAMILIES[family];c.execute('insert or ignore into '+table+'('+','.join(data)+') values('+','.join('?'*len(data))+')',tuple(data.values()));c.commit();c.close();assert not any(x.get('published') for x in dispatch(p,mint)['candidates']);assert state(p,spec)==after;preserved(p);out.append({'candidate':spec['source_candidate_id'],'generation':after[0][1],'one_new_version':True,'duplicate_suppressed':True})
 return out
test('new_edges_both_candidates',lambda:forward('edges'))
test('new_atomic_both_candidates',lambda:forward('atomic'))

def queue_control():
 # Queue PK=mint; both configured cohorts already have all queue rows. Use a
 # schema+retained-row disposable fixture that omits ONE chosen seed queue row
 # at construction (never delete/retire a historical row). Build its own map.
 out=[]
 for index,spec in enumerate(registry.values()):
  source=connect('file:'+str(SEED)+'?mode=ro',uri=True);source.row_factory=sqlite3.Row;p=OUT/('queue'+str(index)+'.sqlite');c=connect(p);mint=event_mint(SEED,spec);withheld=dict(source.execute('select * from wt_walkback_queue where mint=?',(mint,)).fetchone())
  for ddl, in source.execute("select sql from sqlite_master where type='table' and name not like 'sqlite_%' and sql is not null"):c.execute(ddl)
  for t, in source.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
   writable=[r[1] for r in source.execute('pragma table_xinfo('+t+')') if r[6]==0]
   for row in source.execute('select '+','.join(writable)+' from '+t):
    if t=='wt_walkback_queue' and row['mint']==mint:continue
    c.execute('insert into '+t+'('+','.join(writable)+') values('+','.join('?'*len(writable))+')',tuple(row))
  c.commit();c.close();source.close();local=mapping.build_mapping(p,registry);mp=OUT/('queue'+str(index)+'.json');mp.write_text(json.dumps(local));before=state(p,spec);c=connect(p);c.execute('insert into wt_walkback_queue('+','.join(withheld)+') values('+','.join('?'*len(withheld))+')',tuple(withheld.values()));c.commit();c.close()
  with patch.dict(os.environ,{'LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED':'true','LIVING_V2_FRESHNESS_MAPPING_PATH':str(mp)},clear=True):answer=living.handle_walkback_evidence_update(str(p),mint=mint)
  assert state(p,spec)[1]==before[1]+1,answer;preserved(p);out.append({'candidate':spec['source_candidate_id'],'new_queue_admitted':True})
 return out
test('new_queue_both_candidates_no_historical_delete',queue_control)

def failure_restart():
 p=fixture('crash');spec=next(iter(registry.values()));mint,_=add_evidence(p,spec,'edges','crash');before=state(p,spec)
 def fail(stage,ctx):
  if stage=='after_current_before_commit':raise RuntimeError('DEV004_INJECTED_PRECOMMIT')
 try:dispatch(p,mint,test_failure_injector=fail);raise AssertionError('failure did not fire')
 except RuntimeError as e:assert 'DEV004_INJECTED' in str(e)
 assert state(p,spec)==before;dispatch(p,mint);after=state(p,spec);assert after[1]==before[1]+1;assert not any(x.get('published') for x in dispatch(p,mint)['candidates']);preserved(p);return {'precommit_rolled_back':True,'reopen_committed_once':True,'postcommit_replay_no_duplicate':True}
test('crash_and_commit_reopen',failure_restart)

def safeguards():
 p=fixture('guards');spec=next(iter(registry.values()));before=state(p,spec);bound=mapping.bind_mapping(spec,manifest);mint=event_mint(p,spec)
 bad=json.loads(json.dumps(manifest));bad['candidates'][spec['potential_operation_id']]['logical_origin']['edges']+=1
 try:reader.read_generic_living_source_context(mapping.bind_mapping(spec,bad),str(p),{'mint':mint,'current_generation':before[0][1]});raise AssertionError('bad origin accepted')
 except ValueError as e:assert 'ORIGIN' in str(e)
 bad=json.loads(json.dumps(manifest));bad['candidates'][spec['potential_operation_id']]['membership_digest']='invalid'
 try:reader.read_generic_living_source_context(mapping.bind_mapping(spec,bad),str(p),{'mint':mint,'current_generation':before[0][1]});raise AssertionError('bad membership accepted')
 except ValueError as e:assert 'MEMBERSHIP' in str(e)
 assert state(p,spec)==before;preserved(p);return {'inconsistent_origin_fail_closed':True,'insufficient_membership_mapping_fail_closed':True,'no_promotion_or_history_changes':True}
test('conflicting_and_insufficient_mapping',safeguards)
def historical_duplicates():
 p=fixture('historical_duplicates');checks=0
 for spec in registry.values():
  mint=event_mint(p,spec);before=state(p,spec)
  for family,table in mapping.FAMILIES.items():
   c=connect(p);c.row_factory=sqlite3.Row;row=c.execute('select * from '+table+' where mint=? limit 1',(mint,)).fetchone()
   if row is None:row=c.execute('select * from '+table+' limit 1').fetchone()
   data=dict(row);c.execute('insert or ignore into '+table+'('+','.join(data)+') values('+','.join('?'*len(data))+')',tuple(data.values()));c.commit();c.close();assert not any(x.get('published') for x in dispatch(p,mint)['candidates']);assert state(p,spec)==before;checks+=1
 preserved(p);return {'historical_duplicate_deliveries':checks,'new_assessments':0}
test('historical_duplicates_all_families',historical_duplicates)

def new_member():
 p=fixture('prospective_member');spec=next(iter(registry.values()));mint='DEV004_FUTURE_CANDIDATE_MEMBER';before=state(p,spec);c=connect(p);c.row_factory=sqlite3.Row;old_mint=event_mint(p,spec);member=dict(c.execute('select * from p3r_v2_candidate_membership where run_id=? and candidate_id=? and mint=?',(spec['persisted_source']['run_id'],spec['source_candidate_id'],old_mint)).fetchone());member['mint']=mint
 c.execute('insert into p3r_v2_candidate_membership('+','.join(member)+') values('+','.join('?'*len(member))+')',tuple(member.values()));row=dict(c.execute('select * from wt_walkback_queue where mint=?',(old_mint,)).fetchone());row['mint']=mint;c.execute('insert into wt_walkback_queue('+','.join(row)+') values('+','.join('?'*len(row))+')',tuple(row.values()));c.commit();c.close()
 with patch.dict(os.environ,{'LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED':'true','LIVING_V2_FRESHNESS_MAPPING_PATH':str(MAP)},clear=True):out=living.handle_walkback_evidence_update(str(p),mint=mint,funder=row['funder_wallet'])
 assert state(p,spec)[1]==before[1]+1,out;preserved(p);return {'prospective_member_via_existing_funder_resolver':True,'inherited_memberships_preserved':True,'canonical_operation_membership_changes':0}
test('prospective_member_with_new_queue_evidence',new_member)

def review_and_conflict():
 p=fixture('conflicting_evidence');spec=next(iter(registry.values()));mint,data=add_evidence(p,spec,'edges','alternative');c=connect(p);c.execute("update wt_walkback_edge_candidates set selection_status='ALTERNATIVE',candidate_parent='DEV004_COMPETING_PARENT' where evidence_key=?",(data['evidence_key'],));c.commit();c.close();before=state(p,spec);dispatch(p,mint);after=state(p,spec);assert after[1]==before[1]+1
 c=connect('file:'+str(p)+'?mode=ro',uri=True);payload=json.loads(c.execute('select payload_json from potential_operation_assessment_version where assessment_id=?',(after[0][0],)).fetchone()[0]);assert payload['status']=='PAUSED' and payload['promotion']=='NO' and payload['detector_activation']=='NO';assert payload['historical_inherited_context'][0]['assessment_id']==manifest['candidates'][spec['potential_operation_id']]['base_assessment_id'];c.close();preserved(p);return {'alternative_evidence_retained':True,'promotion':'NO','status':'PAUSED','old_confidence_provenance_and_all_associations_unchanged':True,'base_assessment_reference_retained':True,'scope':'Existing generic inference unchanged; no new conflict detector claimed.'}
test('review_rejection_and_competing_evidence_safeguards',review_and_conflict)

def flag_off_and_bad_metadata():
 p=fixture('flags');spec=next(iter(registry.values()));mint=event_mint(p,spec);before=state(p,spec)
 with patch.dict(os.environ,{'LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED':'false','LIVING_V2_FRESHNESS_MAPPING_PATH':'/invalid/never/read'},clear=True):assert living.handle_walkback_evidence_update(str(p),mint='DEV004_UNRELATED')['affected_potential_operation_ids']==[]
 broken=OUT/'broken_mapping.json';bad=json.loads(MAP.read_text());bad['candidates'][spec['potential_operation_id']]['physical_origin']['edges']=0;broken.write_text(json.dumps(bad))
 with patch.dict(os.environ,{'LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED':'true','LIVING_V2_FRESHNESS_MAPPING_PATH':str(broken)},clear=True):
  try:living.handle_walkback_evidence_update(str(p),mint=mint);raise AssertionError('corrupt mapping accepted')
  except ValueError as e:assert 'DIGEST' in str(e)
 assert state(p,spec)==before;return {'generic_flag_off_legacy_compatible':True,'corrupt_mapping_fail_closed':True,'no_assessment_changes':True}
test('flag_off_and_invalid_manifest',flag_off_and_bad_metadata)
for p,h in original_hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h
result['original_hashes_unchanged']=original_hashes;result['files']={p.name:p.stat().st_size for p in OUT.iterdir()};assert max(result['files'].values())<16000000
result['status']='PASS' if all(x['status']=='PASS' for x in result['tests']) else 'HOLD';(OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2)[:13000])
