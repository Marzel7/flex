from pathlib import Path
import importlib.util,sqlite3,tempfile,resource,json,hashlib
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');O=Path('/private/tmp/dev004-startup-integration-final');cases=[]
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
b=load('base',D/'runtime/creator_resolution/src/ops/dev004_core_base_schema.py')
# Preserve old versioned contract and incompatible-index rejection.
assert b.CONTRACT=='2a94ae0258ecb1ed856753d5f56204f6691ffdd11d620bb68d1693b78cd6589d'
for pkg in ['listener','creator_resolution','walkback']:
 m=load('lineage_'+pkg,D/f'runtime/{pkg}/src/ops/generic_living_lineage_metadata.py')
 c=sqlite3.connect(':memory:');m.ensure_lineage_schema(c);c.execute("insert into potential_operation_assessment_lineage values('a','op','GENERIC_DECLARATIVE_V2','v2',NULL,'v1',1)");c.commit();m.ensure_lineage_schema(c);assert c.execute('select count(*) from potential_operation_assessment_lineage').fetchone()[0]==1
 bad=sqlite3.connect(':memory:');bad.execute('create table potential_operation_assessment_lineage(x TEXT)')
 try:m.ensure_lineage_schema(bad)
 except ValueError:pass
 else:raise AssertionError('bad lineage accepted')
 assert not bad.execute("select 1 from sqlite_schema where name='potential_operation_assessment_association_binding'").fetchone()
 partial=sqlite3.connect(':memory:');partial.execute(m.LINEAGE_DDL.split(';')[0]);m.ensure_lineage_schema(partial);m.ensure_lineage_schema(partial)
 cases.append({'case':pkg+'_lineage_repeat_preservation_partial_incompatible','status':'PASS'})
c=sqlite3.connect(O/'core.sqlite');c.execute("update token_analysis set migrated_at=1792747010,source_platform='pumpfun',dex='pumpswap' where mint='DEV004_SYNTHETIC_VALUATION'");c.commit();assert c.execute("select create_tx_signature,migrated_at from token_analysis where mint='DEV004_SYNTHETIC_VALUATION'").fetchone()==('DEV004_SYNTHETIC_CREATE',1792747010)
assert any('idx_ta_pf_ws_creator' in str(r) for r in c.execute('explain query plan select mint from token_analysis where pf_ws_creator=?',('synthetic',)))
assert not c.execute('pragma foreign_key_check').fetchall();c.close();cases.append({'case':'canonical_create_migration_schema_persistence_index_plan_fk','status':'PASS'})
r=Path(tempfile.mkdtemp(prefix='dev004-startup-badindex-',dir='/private/tmp'))
# Copy a compact schema fixture, never production.
import shutil
shutil.copyfile(O/'core.sqlite',r/'core.sqlite');c=sqlite3.connect(r/'core.sqlite');c.execute('alter table token_analysis rename column pf_ws_creator to wrong_creator');c.commit();c.close()
try:b.initialize_creator_index(r/'core.sqlite',disposable_root=r,allow_disposable_v2=True)
except b.BootstrapHold:pass
else:raise AssertionError('incompatible index accepted')
cases.append({'case':'incompatible_index_fail_closed','status':'PASS'})
assert hashlib.sha256((D/'runtime/listener/src/core/birth_retry_quarantine.py').read_bytes()).hexdigest()=='3768da1d2bd8b061200b44d617ea5eec0dd6390e6ce92f178f37254754ae936d';cases.append({'case':'quarantine_exact_installed_bytes','status':'PASS'})
result={'status':'PASS','groups':cases,'sizes':{p.name:p.stat().st_size for p in O.iterdir() if p.is_file() and '.sqlite' in p.name},'maximum_single_file_bytes':16000000,'provider_calls':0};(O/'focused.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
