from src.ops.generic_living_pipeline_v2 import *
from src.ops.generic_living_source_coverage import build_coverage_report
from src.ops.generic_living_forward_cutover import qualify_forward_cutover
from src.ops.generic_living_lineage_metadata import *
from src.ops.generic_living_active_components import *
from src.ops.generic_living_reverse_resolver import *

REG={
 'a':{'potential_operation_id':'potential-a','source_candidate_id':'source-a','workflow_status':'PAUSED','freshness_sources':['queue','edges'],'association_rules':[{'source':'members','prefix':'mint','type':'MEMBER','state':'INCLUDED'}],'aggregate_rules':[{'key':'caveat','type':'LIMIT','state':'UNRESOLVED'}],'metrics':{'members':{'source':'members','op':'distinct','label':'Members'},'funders':{'source':'funders','op':'distinct','label':'Funders'}}},
 'b':{'potential_operation_id':'potential-b','source_candidate_id':'source-b','workflow_status':'PAUSED','freshness_sources':['queue','edges'],'association_rules':[{'source':'members','prefix':'mint','type':'MEMBER','state':'INCLUDED'}],'metrics':{'members':{'source':'members','op':'distinct','label':'Members'}}}}
E={'members':['m1','m1'],'funders':['f1','f2'],'highwaters':{'queue':7,'edges':9}}
def test_registry_derivation_and_determinism():
 assert validate_registry(REG); x=compute_generic_living_assessment(REG['a'],E); y=compute_generic_living_assessment(REG['a'],E)
 assert x['digest']==y['digest'] and x['evidence_generation']=='000000000007:000000000009'
 assert x['payload']['typed_metrics']['members']['value']==1
def test_generic_resolution_and_lineage():
 event={'mint':'m1','known_mints':{'source-a':['m1'],'source-b':['m2']}}
 assert resolve_affected_living_candidates(REG,event)==['potential-a']
 assert resolve_living_lineage(REG,'source-b')=='potential-b' and resolve_living_lineage(REG,'none')=='NO_LIVING_MAPPING'
def test_third_candidate_is_configuration_only():
 third={**REG['a'],'potential_operation_id':'potential-c','source_candidate_id':'source-c'}; registry={**REG,'c':third}
 assert validate_registry(registry) and compute_generic_living_assessment(third,E)['payload']['potential_operation_id']=='potential-c'
def test_generic_freshness_normalization():
 assert normalize_freshness({'frozen_boundary':'x'})=='x'
 assert normalize_freshness({'evidence_generation':'x','frozen_boundary':'x'})=='x'
 import pytest
 with pytest.raises(ValueError): normalize_freshness({'evidence_generation':'x','frozen_boundary':'y'})

def test_generic_filtered_source_extraction_is_candidate_agnostic():
 spec={'evidence_sources':(EvidenceSourceSpec('excluded','records','records',where=(('kind','exclude'),)),)}
 assert extract_evidence_sources(spec,{'records':[{'kind':'include'},{'kind':'exclude'}]})['excluded']==[{'kind':'exclude'}]

def test_frozen_association_source_inventory_and_exact_coverage_gate():
 report=build_coverage_report(); wsol,eight=report['candidates']
 assert (wsol['expected_associations'],wsol['initially_represented'],wsol['initially_unrepresented'])==(126,122,4)
 assert (eight['expected_associations'],eight['initially_represented'],eight['initially_unrepresented'])==(57,55,2)
 assert wsol['missing_source_categories']==['EXCLUSION','FALSE_POSITIVE','HISTORICAL_CAVEAT','MAPPED_POPULATION']
 assert eight['missing_source_categories']==['CONTRADICTION','MAPPED_POPULATION']
 assert len(wsol['source_inventory'])==126 and len(eight['source_inventory'])==57
 assert report['candidate_specific_extraction_branches']==0 and report['real_db_writes']==0

def test_source_inventory_marks_missing_caveat_population_exclusion_and_contradiction():
 report=build_coverage_report()
 rows=[row for c in report['candidates'] for row in c['source_inventory']]
 missing={row['required_source_category'] for row in rows if not row['source_category_currently_available']}
 assert {'MAPPED_POPULATION','EXCLUSION','FALSE_POSITIVE','HISTORICAL_CAVEAT','CONTRADICTION'} <= missing

def test_disposable_forward_cutover_preserves_legacy_and_advances_generic_current():
 report=qualify_forward_cutover()
 for candidate in (report['wsol'],report['eight_hop']):
  assert candidate['legacy_unchanged'] and candidate['newer'] and candidate['current_generic']
  assert candidate['idempotent_version_count'] and candidate['stale_did_not_regress']
  assert candidate['lineages']==['GENERIC_DECLARATIVE_V2','LEGACY_IMMUTABLE']
  assert candidate['inherited_context']>0 and candidate['mapped_non_membership']
 assert report['bridge']=={'wsol_only':True,'unrelated_none':True}
 assert report['real_db_writes']==0 and report['active_path_cutover'] is False
 assert report['cutover_compatibility']=='FORWARD_CUTOVER_REQUIRES_ADDITIVE_LINEAGE_METADATA'

def test_additive_lineage_and_association_bindings_are_atomic_and_idempotent():
 import sqlite3,json
 c=sqlite3.connect(':memory:'); c.executescript('''CREATE TABLE potential_operation_assessment_version(assessment_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_digest TEXT UNIQUE,freshness_key TEXT,payload_json TEXT,created_at INTEGER); CREATE TABLE potential_operation_current(potential_operation_id TEXT PRIMARY KEY,assessment_id TEXT,freshness_key TEXT,updated_at INTEGER);'''); ensure_lineage_schema(c)
 legacy={'assessment_id':'legacy','potential_operation_id':'op','digest':'d1','generation':'0002','payload':json.dumps({'legacy':True})}
 generic={'assessment_id':'generic','potential_operation_id':'op','digest':'d2','generation':'0003','payload':json.dumps({'historical_inherited_context':[{}]})}
 publish_with_lineage(c,legacy,['legacy-a'],PipelineLineage.LEGACY_CANDIDATE_SPECIFIC,pipeline_version='legacy.v1')
 before=c.execute('select evidence_digest,payload_json from potential_operation_assessment_version where assessment_id="legacy"').fetchone()
 publish_with_lineage(c,generic,['generic-a','generic-b'],PipelineLineage.GENERIC_DECLARATIVE_V2)
 publish_with_lineage(c,generic,['generic-a','generic-b'],PipelineLineage.GENERIC_DECLARATIVE_V2)
 assert before==c.execute('select evidence_digest,payload_json from potential_operation_assessment_version where assessment_id="legacy"').fetchone()
 assert c.execute('select assessment_id from potential_operation_current where potential_operation_id="op"').fetchone()[0]=='generic'
 assert c.execute('select count(*) from potential_operation_assessment_lineage').fetchone()[0]==2
 assert c.execute('select count(*) from potential_operation_assessment_association_binding').fetchone()[0]==3
 rows=history_projection(c,'op'); assert [r['pipeline_lineage'] for r in rows]==['LEGACY_CANDIDATE_SPECIFIC','GENERIC_DECLARATIVE_V2'] and rows[-1]['current'] and rows[-1]['inherited_historical_context']

def test_feature_gated_generic_components_are_isolated_and_atomic():
 import sqlite3,json
 registry={'w':{'potential_operation_id':'op-w','source_candidate_id':'w','workflow_status':'PAUSED','freshness_sources':['q'],'association_rules':[{'source':'members','prefix':'mint','type':'MEMBER','state':'INCLUDED'}],'metrics':{'members':{'source':'members','op':'distinct','label':'Members'}}},'e':{'potential_operation_id':'op-e','source_candidate_id':'e','workflow_status':'PAUSED','freshness_sources':['q'],'association_rules':[{'source':'members','prefix':'mint','type':'MEMBER','state':'INCLUDED'}],'metrics':{'members':{'source':'members','op':'distinct','label':'Members'}}}}
 def reader(spec,event): return {'global_advanced':True,'relevant_new_evidence':event['mint']==spec['source_candidate_id'],'members':[event['mint']],'highwaters':{'q':2},'association_ids':['a-'+spec['source_candidate_id']]}
 assert classify_relevance(registry['w'],{'mint':'x'},reader)=='GLOBAL_HIGH_WATER_ADVANCED_BUT_NOT_RELEVANT'
 assert dispatch_bounded_living(registry,{'mint':'w','known_mints':{'w':['w'],'e':['e']}},reader,lambda e:'legacy',env={})['path']=='LEGACY_FALLBACK'
 c=sqlite3.connect(':memory:'); c.executescript('''CREATE TABLE potential_operation_assessment_version(assessment_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_digest TEXT UNIQUE,freshness_key TEXT,payload_json TEXT,created_at INTEGER); CREATE TABLE potential_operation_current(potential_operation_id TEXT PRIMARY KEY,assessment_id TEXT,freshness_key TEXT,updated_at INTEGER);'''); ensure_lineage_schema(c)
 out=dispatch_bounded_living(registry,{'mint':'w','known_mints':{'w':['w'],'e':['e']}},reader,lambda e:None,c,{'LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED':'true'})
 assert out['path']=='GENERIC_DECLARATIVE_V2' and len(out['results'])==1 and c.execute('select count(*) from potential_operation_assessment_lineage').fetchone()[0]==1
 dispatch_bounded_living(registry,{'mint':'w','known_mints':{'w':['w'],'e':['e']}},reader,lambda e:None,c,{'LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED':'true'})
 assert c.execute('select count(*) from potential_operation_assessment_version').fetchone()[0]==1

def test_generic_association_persistence_owns_and_binds_immutably():
 import sqlite3
 c=sqlite3.connect(':memory:'); c.executescript('''CREATE TABLE potential_operation_assessment_version(assessment_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_digest TEXT UNIQUE,freshness_key TEXT,payload_json TEXT,created_at INTEGER); CREATE TABLE potential_operation_current(potential_operation_id TEXT PRIMARY KEY,assessment_id TEXT,freshness_key TEXT,updated_at INTEGER); CREATE TABLE potential_operation_evidence_association(association_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_key TEXT,evidence_type TEXT,state TEXT,rationale_json TEXT,created_at INTEGER);'''); ensure_lineage_schema(c); c.execute('insert into potential_operation_assessment_version values(?,?,?,?,?,?)',('a','op-a','d','2','{}',0)); c.execute('insert into potential_operation_assessment_version values(?,?,?,?,?,?)',('b','op-b','e','2','{}',0)); c.commit()
 shared=lambda owner,state='INCLUDED':{'potential_operation_id':owner,'evidence_identity':'shared','evidence_type':'TEST','association_state':state,'source_key':'s','provenance':{'v':1}}
 c.execute('begin'); a=persist_generic_associations(c,'op-a',[shared('op-a')],'a'); b=persist_generic_associations(c,'op-b',[shared('op-b')],'b'); c.commit(); assert a[0]!=b[0] and c.execute('select count(*) from potential_operation_evidence_association').fetchone()[0]==2
 import pytest
 c.execute('begin')
 with pytest.raises(ValueError,match='candidate ownership mismatch'): persist_generic_associations(c,'op-a',[shared('op-b')],'a')
 c.rollback(); assert c.execute('select count(*) from potential_operation_evidence_association').fetchone()[0]==2
 c.execute('begin'); later=persist_generic_associations(c,'op-a',[shared('op-a','CONTRADICTORY')],'a'); c.commit(); assert later[0]!=a[0]

def test_one_atomic_generic_publication_rolls_back_all_stages():
 import sqlite3,pytest,json
 def db():
  c=sqlite3.connect(':memory:'); c.executescript('''CREATE TABLE potential_operation_assessment_version(assessment_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_digest TEXT UNIQUE,freshness_key TEXT,payload_json TEXT,created_at INTEGER); CREATE TABLE potential_operation_current(potential_operation_id TEXT PRIMARY KEY,assessment_id TEXT,freshness_key TEXT,updated_at INTEGER); CREATE TABLE potential_operation_evidence_association(association_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_key TEXT,evidence_type TEXT,state TEXT,rationale_json TEXT,created_at INTEGER);'''); ensure_lineage_schema(c); return c
 result={'payload':{'potential_operation_id':'op','status':'PAUSED'},'digest':'d','evidence_generation':'0002'}; ass=[{'potential_operation_id':'op','evidence_identity':'x','evidence_type':'T','association_state':'UNRESOLVED','source_key':'s','provenance':{}}]
 for stage in ('assessment','associations','lineage','bindings','current'):
  c=db()
  with pytest.raises(RuntimeError): publish_generic_assessment_atomic(c,result,ass,fail_stage=stage)
  assert [c.execute('select count(*) from '+t).fetchone()[0] for t in ['potential_operation_assessment_version','potential_operation_evidence_association','potential_operation_assessment_lineage','potential_operation_assessment_association_binding','potential_operation_current']]==[0]*5
 c=db(); first=publish_generic_assessment_atomic(c,result,ass); second=publish_generic_assessment_atomic(c,result,ass); assert first['advanced_current'] and not second['advanced_current'] and c.execute('select count(*) from potential_operation_evidence_association').fetchone()[0]==1

def test_canonical_failure_injector_rolls_back_every_named_stage():
 import sqlite3,pytest
 def db():
  c=sqlite3.connect(':memory:'); c.executescript('''CREATE TABLE potential_operation_assessment_version(assessment_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_digest TEXT UNIQUE,freshness_key TEXT,payload_json TEXT,created_at INTEGER); CREATE TABLE potential_operation_current(potential_operation_id TEXT PRIMARY KEY,assessment_id TEXT,freshness_key TEXT,updated_at INTEGER); CREATE TABLE potential_operation_evidence_association(association_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_key TEXT,evidence_type TEXT,state TEXT,rationale_json TEXT,created_at INTEGER);'''); ensure_lineage_schema(c); return c
 result={'payload':{'potential_operation_id':'op','status':'PAUSED'},'digest':'d','evidence_generation':'0002'}; ass=[{'potential_operation_id':'op','evidence_identity':'x','evidence_type':'T','association_state':'UNRESOLVED','source_key':'s','provenance':{}}]
 for stage in ('after_assessment','after_associations','after_lineage','after_bindings','after_current_before_commit'):
  c=db()
  with pytest.raises(RuntimeError): publish_generic_assessment_atomic(c,result,ass,failure_injector=lambda seen,_ctx: (_ for _ in ()).throw(RuntimeError(seen)) if seen==stage else None)
  assert [c.execute('select count(*) from '+t).fetchone()[0] for t in ['potential_operation_assessment_version','potential_operation_evidence_association','potential_operation_assessment_lineage','potential_operation_assessment_association_binding','potential_operation_current']]==[0]*5

def test_physical_wal_busy_snapshot_restarts_canonical_publication(tmp_path):
 """A real WAL BUSY_SNAPSHOT restarts the complete canonical publisher."""
 import sqlite3,threading
 for iteration in range(10):
  path=str(tmp_path/f'physical-wal-{iteration}.db'); setup=sqlite3.connect(path)
  assert setup.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower()=='wal'
  setup.executescript('''CREATE TABLE potential_operation_assessment_version(assessment_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_digest TEXT UNIQUE,freshness_key TEXT,payload_json TEXT,created_at INTEGER); CREATE TABLE potential_operation_current(potential_operation_id TEXT PRIMARY KEY,assessment_id TEXT,freshness_key TEXT,updated_at INTEGER); CREATE TABLE potential_operation_evidence_association(association_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_key TEXT,evidence_type TEXT,state TEXT,rationale_json TEXT,created_at INTEGER);'''); ensure_lineage_schema(setup); setup.close()
  a_absent=threading.Event(); b_committed=threading.Event(); result={}
  association={'potential_operation_id':'op','evidence_identity':'e','evidence_type':'T','association_state':'INCLUDED','source_key':'derived','provenance':{}}
  publication={'payload':{'potential_operation_id':'op','status':'PAUSED'},'digest':f'physical-{iteration}','evidence_generation':'0002'}
  def writer_a():
   c=sqlite3.connect(path,timeout=.2); paused=[False]
   def observed(stage,_aid,absent):
    if stage=='after_association_pre_lookup' and not paused[0]:
     paused[0]=True; result['pre_lookup']='absent' if absent else 'present'; a_absent.set(); b_committed.wait(2)
   result['publication']=publish_generic_assessment_atomic(c,publication,[association],transaction_observer=observed); c.close()
  def writer_b():
   a_absent.wait(2); c=sqlite3.connect(path,timeout=.2); c.execute('BEGIN')
   aid=generic_association_id(association); rationale=json.dumps({'source_key':'derived','provenance':{}},sort_keys=True)
   c.execute('INSERT INTO potential_operation_evidence_association VALUES(?,?,?,?,?,?,?)',(aid,'op','e','T','INCLUDED',rationale,0)); c.commit(); c.close(); b_committed.set()
  a=threading.Thread(target=writer_a); b=threading.Thread(target=writer_b); a.start(); b.start(); a.join(3); b.join(3)
  out=result['publication']; assert result['pre_lookup']=='absent' and b_committed.is_set()
  assert out['attempt_ids']==[1,2] and out['retry_causes']==['SQLITE_BUSY_SNAPSHOT'] and out['full_transaction_restart']
  check=sqlite3.connect(path); assert check.execute('SELECT count(*) FROM potential_operation_evidence_association').fetchone()[0]==1; assert check.execute('SELECT count(*) FROM potential_operation_assessment_lineage').fetchone()[0]==1; assert check.execute('SELECT count(*) FROM potential_operation_assessment_association_binding').fetchone()[0]==1; assert check.execute('PRAGMA integrity_check').fetchone()[0]=='ok'; check.close()

def test_busy_snapshot_retry_exhaustion_is_bounded(monkeypatch):
 import sqlite3,pytest
 import src.ops.generic_living_active_components as active
 c=sqlite3.connect(':memory:'); c.executescript('''CREATE TABLE potential_operation_assessment_version(assessment_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_digest TEXT UNIQUE,freshness_key TEXT,payload_json TEXT,created_at INTEGER); CREATE TABLE potential_operation_current(potential_operation_id TEXT PRIMARY KEY,assessment_id TEXT,freshness_key TEXT,updated_at INTEGER); CREATE TABLE potential_operation_evidence_association(association_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_key TEXT,evidence_type TEXT,state TEXT,rationale_json TEXT,created_at INTEGER);'''); ensure_lineage_schema(c)
 result={'payload':{'potential_operation_id':'op','status':'PAUSED'},'digest':'exhaust','evidence_generation':'0002'}; associations=[{'potential_operation_id':'op','evidence_identity':'e','evidence_type':'T','association_state':'INCLUDED','source_key':'derived','provenance':{}}]; calls=[]
 def busy(*_args):
  calls.append(1); exc=sqlite3.OperationalError('database is locked'); exc.sqlite_errorcode=sqlite3.SQLITE_BUSY; exc.sqlite_errorname='SQLITE_BUSY_SNAPSHOT'; raise exc
 monkeypatch.setattr(active,'persist_generic_associations',busy)
 with pytest.raises(active.ConcurrentPublicationRetryExhausted,match='after 4 attempts'): active.publish_generic_assessment_atomic(c,result,associations)
 assert len(calls)==active.MAX_PUBLICATION_CONCURRENCY_RETRIES+1
 assert [c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('potential_operation_assessment_version','potential_operation_assessment_lineage','potential_operation_evidence_association','potential_operation_assessment_association_binding','potential_operation_current')]==[0]*5; c.close()

def test_physical_ordinary_busy_restarts_complete_publication(tmp_path):
 import sqlite3,threading,time
 import src.ops.generic_living_active_components as active
 path=str(tmp_path/'ordinary-busy.db'); setup=sqlite3.connect(path); assert setup.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower()=='wal'
 setup.executescript('''CREATE TABLE potential_operation_assessment_version(assessment_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_digest TEXT UNIQUE,freshness_key TEXT,payload_json TEXT,created_at INTEGER); CREATE TABLE potential_operation_current(potential_operation_id TEXT PRIMARY KEY,assessment_id TEXT,freshness_key TEXT,updated_at INTEGER); CREATE TABLE potential_operation_evidence_association(association_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_key TEXT,evidence_type TEXT,state TEXT,rationale_json TEXT,created_at INTEGER);'''); ensure_lineage_schema(setup); setup.close()
 held=threading.Event(); release=threading.Event()
 def blocker():
  b=sqlite3.connect(path,timeout=.1); b.execute('BEGIN IMMEDIATE'); b.execute("INSERT INTO potential_operation_evidence_association VALUES('block','other','block','T','INCLUDED','{}',0)"); held.set(); release.wait(2); b.rollback(); b.close()
 t=threading.Thread(target=blocker); t.start(); assert held.wait(1)
 def delayed_release(): time.sleep(.025); release.set()
 threading.Thread(target=delayed_release).start()
 a=sqlite3.connect(path,timeout=.01); result={'payload':{'potential_operation_id':'op','status':'PAUSED'},'digest':'ordinary-busy','evidence_generation':'0002'}; ass=[{'potential_operation_id':'op','evidence_identity':'e','evidence_type':'T','association_state':'INCLUDED','source_key':'derived','provenance':{}}]
 out=active.publish_generic_assessment_atomic(a,result,ass); t.join(2)
 assert out['attempt_ids'] in ([1,2],[1,2,3]) and set(out['retry_causes'])=={'SQLITE_BUSY'} and out['retry_reason']=='SQLITE_BUSY'
 assert a.execute('SELECT count(*) FROM potential_operation_assessment_lineage').fetchone()[0]==1 and a.execute('PRAGMA integrity_check').fetchone()[0]=='ok'; a.close()

def test_physical_ordinary_busy_exhaustion_is_bounded(tmp_path):
 import sqlite3,threading,pytest
 import src.ops.generic_living_active_components as active
 path=str(tmp_path/'ordinary-busy-exhaust.db'); setup=sqlite3.connect(path); assert setup.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower()=='wal'
 setup.executescript('''CREATE TABLE potential_operation_assessment_version(assessment_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_digest TEXT UNIQUE,freshness_key TEXT,payload_json TEXT,created_at INTEGER); CREATE TABLE potential_operation_current(potential_operation_id TEXT PRIMARY KEY,assessment_id TEXT,freshness_key TEXT,updated_at INTEGER); CREATE TABLE potential_operation_evidence_association(association_id TEXT PRIMARY KEY,potential_operation_id TEXT,evidence_key TEXT,evidence_type TEXT,state TEXT,rationale_json TEXT,created_at INTEGER);'''); ensure_lineage_schema(setup); setup.close()
 held=threading.Event(); release=threading.Event()
 def blocker():
  b=sqlite3.connect(path,timeout=.1); b.execute('BEGIN IMMEDIATE'); b.execute("INSERT INTO potential_operation_evidence_association VALUES('block','other','block','T','INCLUDED','{}',0)"); held.set(); release.wait(2); b.rollback(); b.close()
 t=threading.Thread(target=blocker); t.start(); assert held.wait(1)
 a=sqlite3.connect(path,timeout=.01); result={'payload':{'potential_operation_id':'op','status':'PAUSED'},'digest':'ordinary-busy-exhaust','evidence_generation':'0002'}; ass=[{'potential_operation_id':'op','evidence_identity':'e','evidence_type':'T','association_state':'INCLUDED','source_key':'derived','provenance':{}}]
 with pytest.raises(active.ConcurrentPublicationRetryExhausted,match='after 4 attempts'): active.publish_generic_assessment_atomic(a,result,ass)
 release.set(); t.join(2); assert [a.execute('SELECT count(*) FROM '+x).fetchone()[0] for x in ('potential_operation_assessment_version','potential_operation_assessment_lineage','potential_operation_assessment_association_binding','potential_operation_current')]==[0]*4; a.close()

def test_registry_owned_reverse_resolver_breaks_reader_cycle():
 registry={'a':{'potential_operation_id':'op-a','resolver_keys':(('mint','ma'),('funder','shared'))},'b':{'potential_operation_id':'op-b','resolver_keys':(('mint','mb'),('funder','shared'))}}
 idx=LivingCandidateReverseIndex(registry)
 assert idx.resolve({'mint':'ma'})==['op-a'] and idx.resolve({'funder':'shared'})==['op-a','op-b'] and idx.resolve({'mint':'none'})==[]
 assert idx.resolve({'mint':'mb','funder':'shared'})==['op-a','op-b']
 import pytest
 with pytest.raises(ValueError): ResolverKey('mint','')
