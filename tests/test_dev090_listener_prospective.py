"""Existing launcher switches isolate historical work; no production execution."""
import ast,hashlib,os
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'src/core/pumpfun_curve_listener.py'
LAUNCHER=ROOT/'run_listener.sh'
def gate(setting):
 tree=ast.parse(SOURCE.read_text())
 candidates=[n for n in ast.walk(tree) if isinstance(n,ast.If) and setting in ast.unparse(n.test)]
 assert len(candidates)==1
 n=candidates[0];seen=[]
 class Tasks:
  def create_task(self,value):seen.append(value)
 class Listener:
  def _process_creator_resolution_queue_periodic(self):return 'creator-sweep'
  def _migration_reconciler_loop(self):return 'migration-history'
 exec(compile(ast.Module(body=[n],type_ignores=[]),str(SOURCE),'exec'),{'os':os,'asyncio':Tasks(),'self':Listener(),'log_print':lambda *a,**k:None})
 return seen
@pytest.mark.parametrize('setting',['LISTENER_CREATOR_RESOLUTION_QUEUE_ENABLED','LISTENER_MIGRATION_RECONCILER_ENABLED'])
def test_disabled_startup_restart_no_historical_worker(monkeypatch,setting):
 monkeypatch.setenv(setting,'0')
 for _ in range(2):assert gate(setting)==[]
@pytest.mark.parametrize('setting',['LISTENER_CREATOR_RESOLUTION_QUEUE_ENABLED','LISTENER_MIGRATION_RECONCILER_ENABLED'])
def test_enabled_existing_contract_is_not_changed(monkeypatch,setting):
 monkeypatch.setenv(setting,'1');assert len(gate(setting))==1

def test_launcher_exact_installed_preimage_with_single_configuration_change():
 text=LAUNCHER.read_text()
 assert 'export LISTENER_CREATOR_RESOLUTION_QUEUE_ENABLED=0' in text
 assert text.count('export LISTENER_MIGRATION_RECONCILER_ENABLED=0')==1
 assert 'export PUMPPORTAL_CANONICAL_BIRTH_RETENTION_ENABLED=0' in text
 text='\n'.join(line for line in text.split('\n') if not line.startswith(('export PUMPPORTAL_COMPACT_BIRTH_RECEIPTS_ENABLED=','export PUMPPORTAL_COMPACT_BIRTH_RECEIPTS_PATH=')))
 old=text.replace('export PUMPPORTAL_CANONICAL_BIRTH_RETENTION_ENABLED=0\n','').replace('export LISTENER_MIGRATION_RECONCILER_ENABLED=0','export LISTENER_MIGRATION_RECONCILER_ENABLED=1')
 assert hashlib.sha256(old.encode()).hexdigest()=='d7a0db20c7aa8b8a5210774541e6b43bfdb422bd86723a21d3112d2152f3b8e3'

def test_qualified_listener_and_all_direct_provider_contracts_unchanged():
 assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==__import__('json').loads((ROOT/'docs/audits/dev090/compact-birth-recovery.json').read_text())['candidate_listener_sha256']
 assert 'export LISTENER_PUMPPORTAL_BIRTHS_ENABLED=1' in LAUNCHER.read_text()
 assert 'export LISTENER_DISCOVERY_RPC_CONCURRENCY=3' in LAUNCHER.read_text()
 assert 'export LISTENER_GETTX_RETRY_DELAYS=1' in LAUNCHER.read_text()
