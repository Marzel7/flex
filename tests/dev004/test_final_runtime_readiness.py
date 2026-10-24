import hashlib,importlib.util,json,os,pathlib,sqlite3,subprocess,sys
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[2]
s=importlib.util.spec_from_file_location('final_pair_support',ROOT/'tests/dev004/test_bounded_paired_transfer.py');p=importlib.util.module_from_spec(s);s.loader.exec_module(p)
env=p.env;no_network=p.no_network;case=p.case

def aux(case):
 c=case;db=c.env.path/'creator-reference.sqlite'
 with p.NATIVE(db) as q:q.execute('CREATE TABLE creator_reference(id TEXT PRIMARY KEY,value TEXT)')
 path=c.env.path/'auxiliary-authority.json';manifest=json.loads(c.manifests['v1'].read_text());value={'version':1,'generation':manifest['generation'],'pair_manifest_sha256':hashlib.sha256(c.manifests['v1'].read_bytes()).hexdigest(),'stores':{'creator_repository':c.guard.database_identity(str(db),manifest['generation'],'creator_repository')}};c.publish_json(path,value)
 c.env.patch.setenv('FLEX_DB_AUXILIARY_MANIFEST',str(path));c.env.patch.setenv('FLEX_DB_AUXILIARY_SHA256',hashlib.sha256(path.read_bytes()).hexdigest());c.env.patch.setenv('CREATOR_DB_PATH',str(db));return db,path,value

def test_exact_auxiliary_write_authority(case):
 db,path,value=aux(case)
 with case.conn(db) as q:q.execute("INSERT INTO creator_reference VALUES('new','retained')")
 with p.NATIVE(db) as q:assert q.execute('SELECT value FROM creator_reference').fetchone()[0]=='retained'
 assert case.guard.validate_pair(write_path=str(db),purpose='write')=='fixture-v1'

@pytest.mark.parametrize('fault',['unknown_target','changed_aux_identity','wrong_generation','changed_metadata_hash','retired_aux','wrong_core_binding'])
def test_auxiliary_fails_closed(case,fault):
 db,path,value=aux(case)
 if fault=='unknown_target':db=case.env.path/'unknown.sqlite';db.write_bytes(b'')
 elif fault=='changed_aux_identity':value['stores']['creator_repository']['inode']+=1;case.publish_json(path,value);case.env.patch.setenv('FLEX_DB_AUXILIARY_SHA256',hashlib.sha256(path.read_bytes()).hexdigest())
 elif fault=='wrong_generation':value['generation']='wrong';case.publish_json(path,value);case.env.patch.setenv('FLEX_DB_AUXILIARY_SHA256',hashlib.sha256(path.read_bytes()).hexdigest())
 elif fault=='changed_metadata_hash':path.write_text('{}')
 elif fault=='retired_aux':pathlib.Path(str(db)+'.retired').write_text('{}')
 elif fault=='wrong_core_binding':case.env.patch.setenv('DB_PATH',str(case.paths['v2']['core']))
 with pytest.raises(case.guard.PairedBindingError):case.guard.validate_pair(write_path=str(db),purpose='write')

@pytest.mark.parametrize('component,module',[('listener','src.core.pumpfun_curve_listener'),('creator_resolution','src.core.creator_resolution_worker'),('walkback','src.core.walkback_worker')])
def test_same_process_entrypoint_pair_and_kernel_bound(case,component,module):
 db,path,value=aux(case);e=dict(os.environ);e.update(PYTHONPATH=str(ROOT/'runtime'/component),TMPDIR=str(case.env.path),SQLITE_TMPDIR=str(case.env.path),PYTHONDONTWRITEBYTECODE='1')
 child=subprocess.run([sys.executable,'-B','-m','src.core.lightweight_runtime_entry',module,'--check-only'],cwd=case.env.path,env=e,capture_output=True,text=True,timeout=10)
 assert child.returncode==0,child.stderr
 result=json.loads(child.stdout);assert result['generation']=='fixture-v1';assert result['core']==str(case.paths['v1']['core']);assert 0<result['kernel_single_file_limit'][0]<=499_000_000;assert result['kernel_single_file_limit'][0]==result['kernel_single_file_limit'][1]

@pytest.mark.parametrize('owner_name',['core','typed_membership'])
def test_production_style_bootstrap_rejection_is_exact_blocker(case,owner_name):
 # Real disposable files, but deliberately outside the owner's dev004-* root
 # namespace. A valid paired TRANSFER owner does not override this DEV-only veto.
 c=case;root=c.env.path/'production-bound-v2';root.mkdir()
 for kind in ['core','ops']:
  path=root/(kind+'.sqlite')
  with p.NATIVE(path) as conn:conn.execute('CREATE TABLE placeholder(id INTEGER PRIMARY KEY)')
  c.paths['v2'][kind]=path
 c.retire();c.prepare_manifest('v2');c.select_authority('v2','TRANSFER');c.bind_environment('v2',True)
 assert c.guard.validate_pair(write_path=str(c.paths['v2']['core']),purpose='write')=='fixture-v2'
 if owner_name=='core':
  spec=importlib.util.spec_from_file_location('accepted_core_bootstrap_owner',ROOT/'runtime/creator_resolution/src/ops/dev004_core_base_schema.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  before=c.paths['v2']['core'].read_bytes()
  with pytest.raises(m.BootstrapHold,match='DISPOSABLE_ROOT_REQUIRED'):m.initialize(c.paths['v2']['core'],disposable_root=root,allow_disposable_v2=True,price_identity_parent='token_analysis')
  assert c.paths['v2']['core'].read_bytes()==before
 else:
  spec=importlib.util.spec_from_file_location('accepted_typed_bootstrap_owner',ROOT/'runtime/listener/src/ops/dev004_watchtower_references.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  with p.NATIVE(c.paths['v2']['ops']) as conn:
   conn.execute('PRAGMA foreign_keys=ON');before=conn.execute('SELECT name FROM sqlite_schema').fetchall()
   with pytest.raises(m.base.ReferenceContractError,match='EXPLICIT_DISPOSABLE_FK_PROFILE_REQUIRED'):m.initialize(conn,disposable_root=root,allow_disposable_v2=True)
   assert conn.execute('SELECT name FROM sqlite_schema').fetchall()==before
