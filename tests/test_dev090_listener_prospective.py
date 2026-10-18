"""Exact listener discovery boundary: provider-free extracted active method."""
import ast
import asyncio
import os
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'src/core/pumpfun_curve_listener.py'
def method():
 tree=ast.parse(SOURCE.read_text())
 node=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_process_creator_resolution_queue_periodic')
 module=ast.Module(body=[node],type_ignores=[])
 calls=[]
 class Boundary:
  async def sleep(self,*a):
   calls.append('enabled-loop');raise RuntimeError('fixture stops before database or provider')
 namespace={'os':os,'asyncio':Boundary()}
 exec(compile(module,str(SOURCE),'exec'),namespace)
 return namespace[node.name],calls
@pytest.mark.parametrize('value',['0','', 'invalid'])
def test_disabled_startup_restart_never_imports_scans_or_processes(monkeypatch,value):
 monkeypatch.setenv('CRQ_HISTORICAL_DISCOVERY_ENABLED',value)
 f,calls=method()
 for _ in range(2):asyncio.run(f(object()))
 assert calls==[]
@pytest.mark.parametrize('value',[None,'1'])
def test_normal_production_periodic_contract_preserved(monkeypatch,value):
 if value is None:monkeypatch.delenv('CRQ_HISTORICAL_DISCOVERY_ENABLED',raising=False)
 else:monkeypatch.setenv('CRQ_HISTORICAL_DISCOVERY_ENABLED',value)
 f,calls=method()
 with pytest.raises(RuntimeError,match='fixture stops'):asyncio.run(f(object()))
 assert calls==['enabled-loop']
def test_direct_migration_and_birth_paths_not_gated():
 tree=ast.parse(SOURCE.read_text())
 for name in ['_store_analysis','_handle_pumpportal_create_frame','_insert_bonding_curve_token']:
  n=next(n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
  assert 'CRQ_HISTORICAL_DISCOVERY_ENABLED' not in ast.get_source_segment(SOURCE.read_text(),n)

def test_exact_listener_preimage_and_provider_subscription_equivalence():
 import hashlib
 text=SOURCE.read_text()
 guard='        # In prospective-only mode the deployed creator worker owns resolution.\n        # Exit before importing the listener-local historical queue helper.\n        if os.environ.get("CRQ_HISTORICAL_DISCOVERY_ENABLED", "1") != "1":\n            return\n'
 assert text.count(guard)==1
 assert hashlib.sha256(text.replace(guard,'').encode()).hexdigest()=='c2bd4292d7c2ae786f89d1ae98831dccfc0b26011d6a29ceb145b2c2d72792a5'
