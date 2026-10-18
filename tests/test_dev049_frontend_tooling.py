"""Regression tests for concrete Node selection and failed gate diagnostics."""
import importlib.util,json,pathlib,subprocess,sys,tempfile,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('dev049_tool',ROOT/'tools/flex_dev.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
class FrontendTooling(unittest.TestCase):
 def test_concrete_runtime_ignores_path_shims(self):
  with patch.dict(tool.os.environ,{'PATH':'/invalid/shim'}):
   r=tool.frontend_runtime();self.assertEqual(r['path'],'/opt/homebrew/Cellar/node@22/22.23.2/bin/node');self.assertEqual(r['version'],'v22.23.2')
 def test_altered_runtime_fails_closed(self):
  with patch.object(tool,'NODE_SHA256','0'*64):
   with self.assertRaises(tool.Hold):tool.frontend_runtime()
 def test_nonzero_status_is_not_pass_and_empty_log_has_receipt(self):
  with tempfile.TemporaryDirectory() as d:
   log=pathlib.Path(d)/'failure.log'
   with self.assertRaises(tool.Hold):tool.run_log([sys.executable,'-I','-c','import sys;sys.exit(7)'],log,ROOT,{'PATH':'/usr/bin:/bin','HOME':d},2)
   r=json.loads(log.with_suffix('.log.command.json').read_text());self.assertEqual(r['returncode'],7);self.assertEqual(r['log_bytes'],0);self.assertEqual(r['status'],'FAIL');self.assertEqual(r['file_limit_bytes'],8388608)
 def test_signal_diagnostic_keeps_gate_closed(self):
  with tempfile.TemporaryDirectory() as d:
   log=pathlib.Path(d)/'signal.log'
   with patch.object(tool.subprocess,'run',return_value=subprocess.CompletedProcess([], -25)):
    with self.assertRaises(tool.Hold):tool.run_log(['fixture-owned-command'],log,ROOT,{},30)
   r=json.loads(log.with_suffix('.log.command.json').read_text());self.assertEqual(r['signal'],'SIGXFSZ');self.assertEqual(r['timeout_seconds'],30)
 def test_timeout_keeps_gate_closed(self):
  with tempfile.TemporaryDirectory() as d:
   log=pathlib.Path(d)/'timeout.log'
   with patch.object(tool.subprocess,'run',side_effect=subprocess.TimeoutExpired(['fixture'],30)):
    with self.assertRaises(tool.Hold):tool.run_log(['fixture-owned-command'],log,ROOT,{},30)
   self.assertEqual(json.loads(log.with_suffix('.log.command.json').read_text())['status'],'TIMEOUT')
if __name__=='__main__':unittest.main()
