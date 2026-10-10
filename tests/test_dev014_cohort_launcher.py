import importlib.util
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('dev014_launcher',ROOT/'scripts/run_dev014_historical_cohort.py')
launcher=importlib.util.module_from_spec(SPEC); assert SPEC.loader; SPEC.loader.exec_module(launcher)

def test_launcher_requires_explicit_bound_birdeye_before_roots(monkeypatch,tmp_path):
 monkeypatch.delenv('BIRDEYE',raising=False); monkeypatch.delenv(launcher.BOUND,raising=False)
 with pytest.raises(launcher.LaunchDenied,match='BOUND_BIRDEYE_ENV_REQUIRED'): launcher._run(tmp_path/'never-created')
 assert not (tmp_path/'never-created').exists()

def test_launcher_root_and_exclusive_lock_are_fail_closed(tmp_path):
 root=launcher._root(tmp_path/'cohort')
 with launcher._exclusive_lock(root):
  with pytest.raises(launcher.LaunchDenied,match='COHORT_ALREADY_RUNNING'):
   with launcher._exclusive_lock(root): pass
 escaped=tmp_path/'escaped'; escaped.mkdir(); (tmp_path/'link').symlink_to(escaped)
 with pytest.raises(launcher.LaunchDenied,match='COHORT_ROOT_UNSAFE'): launcher._root(tmp_path/'link')

def test_launcher_delegates_environment_loading_to_fixed_preflight_source():
 text=(ROOT/'scripts/run_dev014_historical_cohort.py').read_text()
 assert 'run_dev014_birdeye_preflight.py' in text and 'BIRDEYE_KK' not in text

def test_launcher_binds_existing_coordinator_cli_and_four_journals(monkeypatch,tmp_path):
 monkeypatch.setenv('BIRDEYE','fixture-only'); monkeypatch.setenv(launcher.BOUND,'1')
 class Gate:
  def __init__(self,_): pass
  def check(self): return None
 captured={}
 def run(controller,authorization,**kwargs):
  captured.update({'controller':controller,'authorization':authorization,'kwargs':kwargs})
  return {'status':'EXHAUSTED','completed':[],'sessions':[]}
 monkeypatch.setattr(launcher,'resolve_runtime_authority',lambda _:object())
 monkeypatch.setattr(launcher,'Batch4RuntimeGate',Gate)
 monkeypatch.setattr(launcher,'execute_authorized_sessions',run)
 result=launcher._run(tmp_path/'cohort')
 assert result=={'status':'EXHAUSTED','completed':0,'sessions':0}
 assert len(captured['kwargs']['authoritative_journals'])==4
 assert captured['kwargs']['session_journal'].parent.name=='evidence'
 assert captured['kwargs']['manifest_root'].name=='manifests'
