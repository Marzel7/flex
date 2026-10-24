"""Focused composition tests; no production imports, databases or network."""
import ast,hashlib,importlib.util,json,pathlib,socket,subprocess,sys,tempfile,types,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[4];FILE=ROOT/'runtime/listener/src/core/pumpportal_birth_audit.py'
SPEC=importlib.util.spec_from_file_location('reconciled_receipt',FILE);M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)
OLD=subprocess.check_output(['git','show','ccabf8cf:runtime/listener/src/core/pumpportal_birth_audit.py'],cwd=ROOT,text=True)
DEPLOYED=subprocess.check_output(['git','show','3c8d0d22:runtime/listener/src/core/pumpportal_birth_audit.py'],cwd=ROOT,text=True)
def methods(text):return {n.name:ast.dump(n,include_attributes=False) for c in ast.parse(text).body if isinstance(c,ast.ClassDef) and c.name=='PumpPortalBirthAudit' for n in c.body if isinstance(n,ast.FunctionDef)}
class Error(RuntimeError):pass
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='dev004-receipt-merge-');self.root=pathlib.Path(self.temp.name);self.a=M.PumpPortalBirthAudit(enabled=False,path=str(self.root/'raw'),compact_path=str(self.root/'receipts'));self.a._validation=lambda *a:'IDENTITIES_VALID_UNVERIFIED_CHAIN';self.block=patch.object(socket.socket,'connect',side_effect=AssertionError('NETWORK_FORBIDDEN'));self.block.start()
 def tearDown(self):self.block.stop();self.temp.cleanup()
 def admit(self):return self.a._compact_record('mint','creator','signature',1)
 def guard(self,fn):
  g=types.ModuleType('src.core.paired_binding_guard');g.validate_pair=fn;g.PairedBindingError=Error;return patch.dict(sys.modules,{'src.core.paired_binding_guard':g})
 def test_exact_deployed_capacity_with_only_existing_DEV_ack_difference(self):
  current=methods(FILE.read_text());old=methods(OLD);deployed=methods(DEPLOYED)
  self.assertEqual([k for k in current if current[k]!=old[k]],['_bucket_has_capacity']);self.assertEqual(current['_bucket_has_capacity'],deployed['_bucket_has_capacity']);self.assertEqual([k for k in current if current[k]!=deployed[k]],['record_outcome']);self.assertEqual(M.PumpPortalBirthAudit.MAX_RECEIPTS_PER_BUCKET,1536);self.assertEqual(M.PumpPortalBirthAudit.RECEIPT_WARNING_THRESHOLD,1152)
 def test_full_old_bucket_and_paired_ack(self):
  ident,_=self.a._identity('mint','creator','signature');d=self.a.compact_path/ident[:2];d.mkdir(parents=True)
  for n in range(384):(d/(str(n)+'.json')).write_text('{}')
  self.assertEqual(self.admit(),ident);calls=[]
  with self.guard(lambda **kw:calls.append(kw) or 'GENERATION_V2'):self.assertTrue(self.a.record_outcome(ident,outcome='COMMITTED',canonical=('creator','creator','signature')))
  row=json.loads(self.a._receipt_target(ident).read_bytes());self.assertEqual(row['persistence_outcome'],'COMMITTED');self.assertEqual(len(calls),2);self.assertEqual(len(list(d.iterdir())),385)
 def test_invalid_pair_cannot_ack_or_change_receipt(self):
  ident=self.admit();p=self.a._receipt_target(ident);raw=p.read_bytes()
  def invalid(**kw):raise Error('PAIR_MIXED_GENERATION')
  with self.guard(invalid),self.assertRaises(Error):self.a.record_outcome(ident,outcome='COMMITTED',canonical=('creator','creator','signature'))
  self.assertEqual(raw,p.read_bytes())
 def test_pair_change_between_checks_cannot_ack(self):
  ident=self.admit();p=self.a._receipt_target(ident);raw=p.read_bytes();values=iter(['V2','V1'])
  with self.guard(lambda **kw:next(values)),self.assertRaises(Error):self.a.record_outcome(ident,outcome='COMMITTED',canonical=('creator','creator','signature'))
  self.assertEqual(raw,p.read_bytes())
 def test_legacy_identity_and_repeated_read_preserved(self):
  ident=self.admit();p=self.a._receipt_target(ident);legacy=self.a.compact_path/(ident+'.json');p.rename(legacy);raw=legacy.read_bytes();self.assertEqual(self.admit(),ident);self.assertEqual(self.a._receipt_target(ident),legacy);self.assertEqual(raw,legacy.read_bytes())
if __name__=='__main__':unittest.main(verbosity=2)
