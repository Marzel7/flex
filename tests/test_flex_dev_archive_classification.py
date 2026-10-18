"""Historical evidence classification remains hash-pinned and fail closed."""
import importlib.util,pathlib,tempfile,unittest,hashlib
from unittest.mock import patch
P=pathlib.Path
spec=importlib.util.spec_from_file_location('archive_tool',P(__file__).resolve().parents[1]/'tools/flex_dev.py');f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
class ArchiveControls(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=P(self.temp.name);self.dev=f.Dev('DEV099-test',self.root/'home',self.root/'repo');self.dev.source.mkdir(parents=True)
  self.path='docs/audits/dev099/diagnostic.py';self.content='"""Historical experiment only."""\n';p=self.dev.source/self.path;p.parent.mkdir(parents=True);p.write_text(self.content)
  self.sha=hashlib.sha256(self.content.encode()).hexdigest();self.record={self.path:{'sha256':self.sha,'origin_commit':'a'*40,'purpose':'Frozen diagnostic'}}
  self.v={'manifest':{self.path:{'sha256':self.sha}},'changed_files':[self.path]}
 def tearDown(self):self.temp.cleanup()
 def classify(self,imports=(),required=(),origin=None):
  with patch.object(f,'ARCHIVAL_SCRIPTS',self.record),patch.object(f,'read',return_value=[{'path':p} for p in required]),patch.object(f,'git',return_value=self.content if origin is None else origin):
   return f.verify_import_closure(self.dev,self.v,set(imports))
 def test_exact_archival_bytes_preserved_without_execution(self):
  self.assertIn(self.path,self.classify()['archival_evidence']);self.assertEqual((self.dev.source/self.path).read_text(),self.content)
 def test_unknown_docs_python_still_requires_import(self):
  self.v['changed_files'].append('docs/audits/dev099/other.py')
  with self.assertRaisesRegex(f.Hold,'not exercised'):self.classify()
 def test_missing_runtime_import_still_rejected(self):
  self.v['changed_files'].append('src/required.py')
  with self.assertRaisesRegex(f.Hold,'not exercised'):self.classify()
 def test_imported_runtime_dependency_cannot_be_archival(self):
  with self.assertRaisesRegex(f.Hold,'used as runtime'):self.classify([self.path])
 def test_required_restoration_module_cannot_be_archival(self):
  with self.assertRaisesRegex(f.Hold,'used as runtime'):self.classify(required=[self.path])
 def test_changed_archive_hash_rejected(self):
  (self.dev.source/self.path).write_text('changed')
  with self.assertRaisesRegex(f.Hold,'hash mismatch'):self.classify()
 def test_manifest_hash_mismatch_rejected(self):
  self.v['manifest'][self.path]['sha256']='bad'
  with self.assertRaisesRegex(f.Hold,'hash mismatch'):self.classify()
 def test_origin_content_mismatch_rejected(self):
  with self.assertRaisesRegex(f.Hold,'provenance'):self.classify(origin='different')
 def test_runtime_test_and_config_references_rejected(self):
  for name in ['src/runtime.py','tests/restore.py','config/start.conf']:
   p=self.dev.source/name;p.parent.mkdir(exist_ok=True);p.write_text('load '+self.path);self.v['manifest'][name]={'sha256':'fixture'}
   with self.assertRaisesRegex(f.Hold,'referenced by executable'):self.classify()
   del self.v['manifest'][name]
 def test_deleted_historical_artifact_rejected(self):
  self.v['manifest']={}
  with patch.object(f,'ARCHIVAL_SCRIPTS',self.record),patch.object(f,'read',return_value=[]),patch.object(f,'git',return_value='a'*40):
   with self.assertRaisesRegex(f.Hold,'missing preserved'):f.verify_import_closure(self.dev,self.v,set())
 def test_nonarchival_import_requirement_unchanged(self):
  self.v['changed_files'].append('src/current.py');self.assertIn(self.path,self.classify(['src/current.py'])['archival_evidence'])
if __name__=='__main__':unittest.main()
