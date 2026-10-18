"""Compact provider-free negative controls; no canonical authority is modified."""
import importlib.util,json,pathlib,tempfile,unittest,sys
from unittest.mock import patch
MODULE=pathlib.Path(__file__).resolve().parents[1]/'tools/flex_dev.py'
if not MODULE.exists():MODULE=pathlib.Path('/private/tmp/dev048-tools/flex_dev.py')
spec=importlib.util.spec_from_file_location('flex_dev',MODULE);f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
class Controls(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name);self.dev=f.Dev('DEV099-test',self.root/'work',self.root/'repo');self.dev.source.mkdir(parents=True);self.dev.state.mkdir()
  self.cfg={'id':self.dev.name,'baseline_sha':f.BASE,'source':str(self.dev.source),'state':str(self.dev.state),'canonical_readonly':['CANONICAL_RO'],'queue':str(self.dev.state/'monitor/queue'),'providers':'DENIED','workers':0,'port':8198,'level':1}
  f.save(self.dev.meta,self.cfg)
 def tearDown(self):self.temp.cleanup() # Only newly-created compact unittest fixtures.
 def load(self):
  original=f.read
  with patch.object(f,'read',side_effect=lambda p:{'canonical_readonly':['CANONICAL_RO']} if str(p).endswith('safe-runtime.json') else original(p)):return self.dev.load()
 def test_wrong_database_binding_rejected(self):
  self.cfg['canonical_readonly']=['WRITABLE_PRODUCTION'];f.save(self.dev.meta,self.cfg)
  with self.assertRaisesRegex(f.Hold,'database'):self.load()
 def test_shared_writable_queue_rejected(self):
  self.cfg['queue']='/shared/production/queue';f.save(self.dev.meta,self.cfg)
  with self.assertRaisesRegex(f.Hold,'shared writable queue'):self.load()
 def test_incompatible_parent_rejected(self):
  self.cfg['baseline_sha']='f'*40;f.save(self.dev.meta,self.cfg)
  with self.assertRaisesRegex(f.Hold,'parent baseline'):self.load()
 def test_missing_source_module_rejected_before_startup(self):
  with patch.object(f,'read',return_value=[{'path':'src/required.py'}]):
   with self.assertRaisesRegex(f.Hold,'missing required source'):f.validate_source(self.dev,self.cfg)
 def test_untracked_dependency_rejected(self):
  with patch.object(f,'read',return_value=[]),patch.object(f,'git',return_value='?? src/hidden.py'):
   with self.assertRaisesRegex(f.Hold,'untracked runtime'):f.validate_source(self.dev,self.cfg)
 def test_dirty_candidate_rejected(self):
  with patch.object(f,'read',return_value=[]),patch.object(f,'git',return_value=' M src/required.py'):
   with self.assertRaisesRegex(f.Hold,'dirty candidate'):f.validate_source(self.dev,self.cfg)
 def test_unqualified_browser_rejected(self):
  session={'source_sha':'a'*40,'run_id':'run','port':8198}
  with self.assertRaisesRegex(f.Hold,'unqualified browser'):f.validate_browser({'source_sha':'a'*40,'run_id':'run','status':'UNKNOWN'},session,self.root)
 def test_incorrect_source_sha_rejected(self):
  with self.assertRaisesRegex(f.Hold,'source SHA'):f.validate_browser({'source_sha':'wrong','run_id':'run','status':'PASS'},{'source_sha':'a'*40,'run_id':'run'},self.root)
 def test_missing_rollback_reference_rejected(self):
  d=self.root/'candidate';d.mkdir();doc={'candidate':str(d),'source_sha':'a'*40,'tag':'x'}
  # This specifically tests the rollback gate with prior identity/checksum gates qualified.
  self.dev.head=lambda:'a'*40
  original=f.read
  def read(p):
   name=pathlib.Path(p).name
   if name=='candidate.json':return doc
   if name=='SHA256SUMS.json':return {}
   if name=='release.json':return {'source_sha':'a'*40}
   if name=='rollback.json':raise f.Hold('missing rollback reference')
   return original(p)
  fake_stat=type('S',(),{'st_flags':f.stat.UF_IMMUTABLE})()
  with patch.object(f,'RELEASE',self.root/'golden'),patch.object(pathlib.Path,'stat',return_value=fake_stat),patch.object(f,'read',side_effect=read),patch.object(f,'git',return_value='a'*40):
   # Candidate must be under release-parent/candidates.
   doc['candidate']=str(self.root/'candidates'/'one')
   with self.assertRaisesRegex(f.Hold,'rollback'):f.validate_candidate(self.dev)
 def test_risk_levels_cannot_be_downgraded(self):
  self.assertEqual(f.minimum_level(['docs/database_notes.md']),1);self.assertEqual(f.minimum_level(['templates/a.html']),1);self.assertEqual(f.minimum_level(['src/apis/a.py']),2);self.assertEqual(f.minimum_level(['src/ops/treasury_classification.py']),3)
 def test_artifacts_fail_closed_before_large_file_limit(self):
  with patch.object(f,'SMALL',20):
   with self.assertRaisesRegex(f.Hold,'budget'):f.save(self.root/'oversized.json',{'value':'x'*100})
  self.assertFalse((self.root/'oversized.json').exists())
 def test_retention_exhaustion_rejects_before_writing(self):
  for i in range(8):(self.root/(str(i)+'.tmp')).write_text('compact failed fixture')
  with self.assertRaisesRegex(f.Hold,'retention'):f.save(self.root/'new.json',{'small':1})
  self.assertFalse((self.root/'new.json').exists())
 def test_tool_identity_covers_guarded_runner(self):
  with patch.object(f,'digest',side_effect=['main','runner1']):a=f.tool_id()
  with patch.object(f,'digest',side_effect=['main','runner2']):b=f.tool_id()
  self.assertNotEqual(a,b)
 def test_git_output_budget_is_separate_from_source_file_limit(self):
  import subprocess,sys
  original=subprocess.Popen
  def produce(*args,**kwargs):return original([sys.executable,'-c',"import sys;sys.stdout.write('x'*9000000)"],**kwargs)
  with patch.object(f.subprocess,'Popen',side_effect=produce):
   with self.assertRaisesRegex(f.Hold,'Git output budget'):f.git(self.root,'status')
 def test_unmapped_dependency_version_holds(self):
  with patch.object(f,'digest',return_value='hash'),patch.object(f.importlib.metadata,'packages_distributions',return_value={}):
   with self.assertRaisesRegex(f.Hold,'unmapped runtime'):f.runtime_versions({'/env/site-packages/unknown/file.py':'hash'})
 def test_no_deployment_command_exists(self):
  with self.assertRaises(SystemExit):f.main(['promote','DEV099','--target','production','--runtime-evidence','x'])
 def test_foreign_worktree_dependency_rejected(self):
  data={'source_root':str(self.dev.source),'isolation_events':[],'modules':[{'path':'/private/tmp/foreign/src/module.py','sha256':'bad'}]}
  with patch.object(f,'read',return_value=data):
   with self.assertRaisesRegex(f.Hold,'foreign worktree'):f.loaded(self.dev.source,self.dev.state,{})
 def test_exact_browser_session_and_evidence_required(self):
  with self.assertRaisesRegex(f.Hold,'missing browser'):f.validate_browser({'source_sha':'a','run_id':'r','status':'PASS'},{'source_sha':'a','run_id':'r'},self.root)
if __name__=='__main__':unittest.main()
