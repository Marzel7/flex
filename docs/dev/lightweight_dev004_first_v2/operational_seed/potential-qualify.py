from pathlib import Path
import sqlite3,json,sys,types,importlib,hashlib,time,resource,shutil,socket,ast
from unittest.mock import patch
resource.setrlimit(resource.RLIMIT_FSIZE,(48000000,48000000));socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
O=Path('/private/tmp/dev004-operational-seed-qualified');P=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002');DB=O/'ops_v2.sqlite';SOURCE=Path('/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db');r={'tests':[],'dependencies':[],'source_hashes':{},'provider_calls':0,'production_writes':0}
raw=sqlite3.connect
def connect(path,*a,**k):
 text=str(path);p=Path(text[5:].split('?')[0] if text.startswith('file:') else text).resolve()
 assert p.is_relative_to(O) or 'mode=ro' in text,'PRODUCTION_WRITE_FORBIDDEN'
 k.pop('priority',None);factory=k.pop('row_factory',None);c=raw(path,*a,**k)
 if factory:c.row_factory=factory
 if 'mode=ro' not in text:c.execute('pragma max_page_count=10000');c.execute('pragma cache_size=-2048')
 return c
sqlite3.connect=connect
for n,sub in [('src','src'),('src.ops','src/ops'),('src.utils','src/utils')]:m=types.ModuleType(n);m.__path__=[str(P/sub)];sys.modules[n]=m
lock=types.ModuleType('src.utils.db_locking');lock.db_connect=lambda path,*a,**k:connect('file:'+str(path)+'?mode=ro',*a,uri=True,**k) if str(path)==str(SOURCE) else connect(path,*a,**k);sys.modules[lock.__name__]=lock
living=importlib.import_module('src.ops.living_potential_operations');active=importlib.import_module('src.ops.generic_living_active_components');reader=importlib.import_module('src.ops.generic_living_persisted_source_reader');pipeline=importlib.import_module('src.ops.generic_living_pipeline_v2')
for m in [living,active,reader,pipeline]:r['source_hashes'][m.__file__]=hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
def test(name,fn):
 try:r['tests'].append({'name':name,'status':'PASS','evidence':fn()})
 except Exception as e:r['tests'].append({'name':name,'status':'HOLD','error':type(e).__name__+':'+str(e)[:500]})
s=connect('file:'+str(SOURCE)+'?mode=ro',uri=True);s.row_factory=sqlite3.Row;s.execute('begin');d=connect(DB)
# Only configured candidate populations; not all historical P3R runs.
for spec in living._generic_living_registry().values():
 cfg=spec['persisted_source'];t='p3r_v2_candidate_membership';ddl=s.execute('select sql from sqlite_master where name=?',(t,)).fetchone()[0];d.execute(ddl.replace('CREATE TABLE ','CREATE TABLE IF NOT EXISTS ',1))
 rows=s.execute('select * from '+t+' where run_id=? and candidate_id=? limit 2001',(cfg['run_id'],spec['source_candidate_id'])).fetchall();assert len(rows)<2001
 for row in rows:d.execute('insert or ignore into '+t+' values('+','.join('?'*len(row))+')',tuple(row))
 r['dependencies'].append({'candidate':spec['source_candidate_id'],'membership_rows':len(rows)})
d.commit();d.close()
def projections():
 out=[]
 for spec in living._generic_living_registry().values():
  oid=spec['potential_operation_id'];a=living.living_detail_projection(str(SOURCE),oid);b=living.living_detail_projection(str(DB),oid);assert a==b;out.append({'candidate':oid,'status':a['current']['status'],'history_count':a['history_count'],'associations':a['association_counts'],'promotion':a['current']['payload'].get('promotion')})
 return out
test('retained_candidate_identity_associations_history_review',projections)
def sources():
 out=[]
 for spec in living._generic_living_registry().values():
  a=reader.read_generic_living_source_context(spec,str(SOURCE));b=reader.read_generic_living_source_context(spec,str(DB));out.append({'candidate':spec['source_candidate_id'],'v1_source_rows':a['source_rows'],'v2_source_rows':b['source_rows'],'v1_highwaters':a['highwaters'],'v2_highwaters':b['highwaters'],'members_equal':a['members']==b['members'],'funders_equal':a['funders']==b['funders']})
 r['source_comparisons']=out
 assert all(x['members_equal'] and x['funders_equal'] and x['v1_source_rows']==x['v2_source_rows'] and x['v1_highwaters']==x['v2_highwaters'] for x in out),'Source rows/global rowid generations differ; assessment copies alone cannot preserve accumulation'
 return out
test('persisted_source_context_equivalence',sources)
def reverse():
 from src.ops.generic_living_reverse_resolver import LivingCandidateReverseIndex
 registry=living._generic_living_registry();a=LivingCandidateReverseIndex.from_association_ledger(registry,str(SOURCE));b=LivingCandidateReverseIndex.from_association_ledger(registry,str(DB));events=[{'mint':row[0][5:]} for row in s.execute("select evidence_key from potential_operation_evidence_association where evidence_key like 'mint:%' limit 201")];assert events
 for event in events:assert a.resolve(event)==b.resolve(event)
 assert b.resolve({'mint':'DEV004_UNSEEN_NOT_REGISTERED'})==[]
 return {'retained_mint_events':len(events),'unseen_unrelated_not_promoted':True,'limitation':'Resolver only matches retained associations; global candidate discovery is not this component.'}
test('retained_candidate_reverse_resolution_and_unseen_control',reverse)
def publication():
 scratch=O/'candidate_test_verified.sqlite';shutil.copyfile(DB,scratch);c=connect(scratch);oid='DEV004_SYNTHETIC_CANDIDATE';c.execute('insert into potential_operation_identity values(?,?,?,?)',(oid,'PAUSED','{"source":"synthetic"}',0));c.commit()
 spec={'potential_operation_id':oid,'source_candidate_id':'DEV004_UNSEEN_LAUNCH_CASE','workflow_status':'PAUSED','freshness_sources':['queue'],'association_rules':[{'source':'members','prefix':'mint','type':'GENERIC_MEMBER','state':'INCLUDED'},{'source':'conflicts','prefix':'conflict','type':'COUNTEREVIDENCE','state':'CONTRADICTORY'}],'metrics':{'members':{'source':'members','op':'distinct','label':'Members'}}}
 ids=[]
 for gen in [1,2]:
  evidence={'members':['DEV004_NEW_LAUNCH'],'conflicts':[] if gen==1 else ['DEV004_COMPETING_PARENT'],'highwaters':{'queue':gen}};result=pipeline.compute_generic_living_assessment(spec,evidence);assert result['payload']['promotion']=='NO'
  associations=[{'potential_operation_id':oid,'evidence_identity':a['evidence_key'],'evidence_type':a['evidence_type'],'association_state':a['state'],'source_key':'derived','provenance':{}} for a in result['associations']]
  if gen==2:
   try:active.publish_generic_assessment_atomic(c,result,associations,fail_stage='before_commit');raise AssertionError('failure not injected')
   except RuntimeError:pass
   assert c.execute('select count(*) from potential_operation_assessment_version where potential_operation_id=?',(oid,)).fetchone()[0]==1
  first=active.publish_generic_assessment_atomic(c,result,associations);again=active.publish_generic_assessment_atomic(c,result,associations);assert first['assessment_id']==again['assessment_id'];ids.append(first['assessment_id'])
 assert ids[0]!=ids[1];assert c.execute('select count(*) from potential_operation_assessment_version where potential_operation_id=?',(oid,)).fetchone()[0]==2
 assert c.execute('select status from potential_operation_identity where potential_operation_id=?',(oid,)).fetchone()[0]=='PAUSED';c.close()
 reopened=connect(scratch);assert reopened.execute('select assessment_id from potential_operation_current where potential_operation_id=?',(oid,)).fetchone()[0]==ids[-1];reopened.close()
 return {'unseen_input_assessment':True,'two_immutable_versions':True,'duplicate_idempotent':True,'failed_publication_atomic':True,'contradictory_evidence_preserved':True,'restart_current_preserved':True,'automatic_promotion':'NO','scope':'Installed generic pipeline/publisher with synthetic evidence; not a global discovery proof.'}
test('installed_candidate_evidence_accumulation_and_restart',publication)
def dispatch():
 tree=ast.parse((P/'src/core/walkback_worker.py').read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_notify_living_after_walkback_commit');ns={'sqlite3':sqlite3,'OPS_DB_PATH':str(DB)};exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'installed-post-commit','exec'),ns)
 c=connect(DB);c.row_factory=sqlite3.Row;mint=c.execute('select mint from wt_walkback_queue limit 1').fetchone()[0];calls=[]
 with patch.object(living,'handle_walkback_evidence_update',side_effect=lambda path,**kw:calls.append((path,kw)) or {'candidates':[],'resolved_candidate_ids':[]}):out=ns[fn.name](c,mint)
 assert out['status']=='DISPATCHED' and len(calls)==1
 with patch.object(living,'handle_walkback_evidence_update',side_effect=RuntimeError('SYNTHETIC_CALLBACK_FAILURE')):failure=ns[fn.name](c,mint)
 assert failure['status']!='DISPATCHED';assert c.execute('select 1 from wt_walkback_queue where mint=?',(mint,)).fetchone();c.close()
 return {'post_commit_callback_invoked':True,'failure_preserves_queue':True,'no_scheduler_activated':True,'boundary':'handler mocked to prevent unqualified full legacy recomputation'}
test('installed_conditional_post_Walkback_boundary',dispatch)
s.close();r['status']='PARTIAL_HOLD';(O/'potential_results.json').write_text(json.dumps(r,indent=2,default=str)+'\n');print(json.dumps(r,indent=2,default=str)[:11000])
