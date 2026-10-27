"""Candidate and explicit prior recovery profiles stay exact, never wildcarded."""
import ast,copy,hashlib,subprocess
from pathlib import Path
import pytest
from tests.test_v2_helius_recovery_config import case, base_case
ROOT=Path(__file__).resolve().parents[1]
OLD='565b740c096d3032c9587e92d62382139cc57605ead39239d1a84729b9973256'
NEW=hashlib.sha256((ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_bytes()).hexdigest()

def test_candidate_exact_profile_accepts_and_preserves_gate(case):
 e=case;before=e.gate.path.read_bytes();assert e.ns['HELIUS_GATE_PAYLOAD']['listener']['src/core/pumpfun_curve_listener.py']==NEW
 e.ns['write_config']('v2');e.ns['helius_module']('listener')
 assert e.gate.path.read_bytes()==before

def test_explicit455fallback_not_silently_accepted_as_candidate(case):
 e=case;before=e.gate.path.read_bytes();path=e.package/'src/core/pumpfun_curve_listener.py';candidate=path.read_bytes()
 fallback=subprocess.check_output(['git','show','455f160d:runtime/listener/src/core/pumpfun_curve_listener.py']);assert hashlib.sha256(fallback).hexdigest()==OLD
 path.write_bytes(fallback)
 with pytest.raises(RuntimeError,match='HELIUS_GATE_UNAWARE_FALLBACK'):e.ns['helius_module']('listener')
 prior=copy.deepcopy(e.ns['HELIUS_GATE_PAYLOAD']);prior['listener']['src/core/pumpfun_curve_listener.py']=OLD
 e.ns['HELIUS_GATE_PAYLOAD']=prior;e.ns['helius_module']('listener')
 e.ns['TERMINAL_PAYLOAD']=dict(e.ns['TERMINAL_PAYLOAD'])
 e.ns['TERMINAL_PAYLOAD']['pumpfun_curve_listener.py']=OLD
 e.ns['write_config']('v2')
 assert e.gate.path.read_bytes()==before
 path.write_bytes(candidate)
 with pytest.raises(RuntimeError,match='HELIUS_GATE_UNAWARE_FALLBACK'):e.ns['helius_module']('listener')
 assert e.gate.path.read_bytes()==before

def test_no_unrelated_role_pin_drift():
 text=subprocess.check_output(['git','show','455f160d:docs/dev/lightweight_dev004_first_v2/quarantine_continuation/production_control.py'],text=True)
 def pins(text):return next(ast.literal_eval(n.value) for n in ast.parse(text).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='HELIUS_GATE_PAYLOAD' for t in n.targets))
 old=pins(text);new=pins((ROOT/'docs/dev/lightweight_dev004_first_v2/quarantine_continuation/production_control.py').read_text())
 for name in ('pumpfun_curve_listener.py','migration_signature_journal.py','migration_terminal_evidence.py'):
  rel='src/core/'+name
  if rel in old['listener']:old['listener'][rel]=hashlib.sha256((ROOT/'runtime/listener'/rel).read_bytes()).hexdigest()
 assert new==old
