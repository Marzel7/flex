"""Manifest-scoped bootstrap tests: only disposable paths, blocked providers."""
import hashlib,importlib.util,json,os,pathlib,sqlite3,sys
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[2]
s=importlib.util.spec_from_file_location('bootstrap_pair_support',ROOT/'tests/dev004/test_bounded_paired_transfer.py');p=importlib.util.module_from_spec(s);s.loader.exec_module(p)
env=p.env;no_network=p.no_network;case=p.case
DDL=json.load(open(ROOT/'docs/dev/lightweight_dev004_first_v2/production_profile_bootstrap/accepted_parent_schema.json'))
def load(path,name):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
CORE=load(ROOT/'runtime/creator_resolution/src/ops/dev004_core_base_schema.py','profile_core_owner')
OPS=load(ROOT/'runtime/listener/src/ops/dev004_watchtower_references.py','profile_ops_owner')

class Profile:
 def __init__(self,c):
  self.c=c;self.root=c.env.path/'persistent-v2';self.root.mkdir()
  for kind in ['core','ops']:
   target=self.root/(kind+'.sqlite');c.paths['v2'][kind]=target
   with p.NATIVE(target) as q:
    q.execute('PRAGMA foreign_keys=ON')
    for ddl in DDL[kind].values():q.execute(ddl)
  c.retire();c.prepare_manifest('v2');c.select_authority('v2','TRANSFER');c.bind_environment('v2',True)
  self.file=c.env.path/'frozen-runtime.json';pair=json.load(open(c.manifests['v2']))
  contract=hashlib.sha256((CORE.CONTRACT+':token_analysis').encode()).hexdigest()
  self.value={'production_bootstrap':{'version':1,'root':str(self.root),'generation':pair['generation'],'pair_manifest_path':str(c.manifests['v2']),'pair_manifest_sha256':hashlib.sha256(c.manifests['v2'].read_bytes()).hexdigest(),'authority_path':str(c.authority),'transfer_owner':'fixture-transfer-owner','stores':pair['stores'],'schema_authority':{'core':{'contract':contract,'owner_sha256':hashlib.sha256(pathlib.Path(CORE.__file__).read_bytes()).hexdigest()},'ops':OPS.base._schema_authority()}}}
  self.publish()
 def publish(self):
  self.c.publish_json(self.file,self.value);self.c.env.patch.setenv('DEV004_PRODUCTION_BOOTSTRAP_MANIFEST',str(self.file));self.c.env.patch.setenv('DEV004_PRODUCTION_BOOTSTRAP_SHA256',hashlib.sha256(self.file.read_bytes()).hexdigest())
 def core(self,**kw):return CORE.initialize(self.c.paths['v2']['core'],disposable_root=self.root,allow_disposable_v2=True,price_identity_parent='token_analysis',**kw)
 def ops(self):
  with p.NATIVE(self.c.paths['v2']['ops']) as q:
   q.execute('PRAGMA foreign_keys=ON');return OPS.initialize(q,disposable_root=self.root,allow_disposable_v2=True)

@pytest.fixture
def profile(case):return Profile(case)

def test_authorized_core_ops_repeat(profile):
 assert profile.core()['status']=='INITIALIZED';assert profile.core()['status']=='ALREADY_INITIALIZED';assert profile.ops()['contract']==OPS.VERSION;assert profile.ops()['contract']==OPS.VERSION
 assert profile.c.guard.validate_pair(write_path=str(profile.c.paths['v2']['core']),purpose='write')=='fixture-v2'

@pytest.mark.parametrize('fault',['wrong_generation','wrong_root','wrong_schema','missing_manifest','wrong_hash','wrong_owner','active_phase','identity_substitution','missing_paired_manifest'])
def test_invalid_authority_rejected_before_schema(profile,fault):
 x=profile;path=x.c.paths['v2']['core'];before=path.read_bytes()
 if fault=='wrong_generation':x.value['production_bootstrap']['generation']='not-this-generation';x.publish()
 elif fault=='wrong_root':x.value['production_bootstrap']['root']=str(x.c.env.path);x.publish()
 elif fault=='wrong_schema':x.value['production_bootstrap']['schema_authority']['core']['contract']='0'*64;x.publish()
 elif fault=='missing_manifest':x.file.unlink()
 elif fault=='wrong_hash':x.c.env.patch.setenv('DEV004_PRODUCTION_BOOTSTRAP_SHA256','0'*64)
 elif fault=='wrong_owner':x.c.env.patch.setenv('FLEX_DB_PAIR_OWNER','different')
 elif fault=='active_phase':x.c.select_authority('v2','ACTIVE')
 elif fault=='identity_substitution':
  moved=path.with_suffix('.original');path.rename(moved);path.write_bytes(moved.read_bytes());before=path.read_bytes()
 elif fault=='missing_paired_manifest':x.c.manifests['v2'].unlink()
 with pytest.raises(Exception):x.core()
 assert path.read_bytes()==before
 assert not path.with_name(path.name+'.'+CORE.VERSION+'.canonical-price-identity.json').exists()

@pytest.mark.parametrize('fault',['symlink','traversal'])
def test_path_escape_rejected(profile,fault):
 x=profile
 if fault=='symlink':alias=x.c.env.path/'alias';alias.symlink_to(x.root,target_is_directory=True);root=alias;path=alias/'core.sqlite'
 else:root=x.root/'..'/'persistent-v2';path=root/'core.sqlite'
 with pytest.raises(Exception):CORE.initialize(path,disposable_root=root,allow_disposable_v2=True,price_identity_parent='token_analysis')

@pytest.mark.parametrize('stage',['before_ddl','after_peak','before_commit','after_commit','before_marker_replace'])
def test_core_interruption_restart(profile,stage):
 def crash(at):
  if at==stage:raise RuntimeError('synthetic interruption')
 with pytest.raises(RuntimeError,match='synthetic interruption'):profile.core(fault=crash)
 assert profile.core()['status'] in ['INITIALIZED','ALREADY_INITIALIZED'];assert profile.core()['status']=='ALREADY_INITIALIZED';profile.ops()


def test_manifest_substitution_during_ddl_rolls_back(profile):
 def fault(at):
  if at=='before_commit':profile.file.write_text('{}')
 with pytest.raises(Exception):profile.core(fault=fault)
 with p.NATIVE(profile.c.paths['v2']['core']) as q:assert not q.execute("SELECT 1 FROM sqlite_schema WHERE name='token_market_cap_peaks'").fetchone()
 profile.publish();assert profile.core()['status']=='INITIALIZED'


def test_typed_interruption_restart(profile):
 class Interrupted:
  def __init__(self,c):self.c=c
  def execute(self,sql,*a):
   if sql.startswith('CREATE TRIGGER'):raise RuntimeError('synthetic typed interruption')
   return self.c.execute(sql,*a)
 with p.NATIVE(profile.c.paths['v2']['ops']) as q:
  q.execute('PRAGMA foreign_keys=ON')
  with pytest.raises(RuntimeError,match='synthetic typed interruption'):OPS.initialize(Interrupted(q),disposable_root=profile.root,allow_disposable_v2=True)
 assert profile.ops()['contract']==OPS.VERSION


def test_dev_compatibility(case):
 root=case.env.path/'dev004-legacy';root.mkdir();path=root/'core.sqlite'
 with p.NATIVE(path) as q:q.execute(DDL['core']['token_analysis'])
 assert CORE.initialize(path,disposable_root=root,allow_disposable_v2=True,price_identity_parent='token_analysis')['status']=='INITIALIZED'

@pytest.mark.parametrize('fault',['wrong_generation','wrong_schema','wrong_owner','active_phase','missing_manifest','symlink_root','traversal_root'])
def test_typed_profile_rejects_invalid_configuration(profile,fault):
 x=profile;root=x.root;path=x.c.paths['v2']['ops'];before=path.read_bytes()
 if fault=='wrong_generation':x.value['production_bootstrap']['generation']='wrong';x.publish()
 elif fault=='wrong_schema':x.value['production_bootstrap']['schema_authority']['ops']['contract']='0'*64;x.publish()
 elif fault=='wrong_owner':x.c.env.patch.setenv('FLEX_DB_PAIR_OWNER','wrong')
 elif fault=='active_phase':x.c.select_authority('v2','ACTIVE')
 elif fault=='missing_manifest':x.file.unlink()
 elif fault=='symlink_root':root=x.c.env.path/'ops-alias';root.symlink_to(x.root,target_is_directory=True)
 elif fault=='traversal_root':root=x.root/'..'/'persistent-v2'
 with p.NATIVE(path) as q:
  q.execute('PRAGMA foreign_keys=ON')
  with pytest.raises(Exception):OPS.initialize(q,disposable_root=root,allow_disposable_v2=True)
 assert path.read_bytes()==before
