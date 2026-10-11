#!/usr/bin/env python3
"""Fixed entry point for the one-request lifecycle capability probe."""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding, ProviderTransportOutcome
from src.ops.watchtower_historical_budget import HistoricalForensicsBudgetAdmission
from src.ops.watchtower_lifecycle_probe import OneRequestLifecycleProbe
from src.ops.dev014_batch4_runtime_gate import Batch4RuntimeGate, resolve_runtime_authority

def main() -> int:
 p=argparse.ArgumentParser(); p.add_argument('--authorization',type=Path,required=True); p.add_argument('--journal',type=Path,required=True); p.add_argument('--queue-root',type=Path,required=True); p.add_argument('--supervisor-config',type=Path,required=True); p.add_argument('--test-fixture',action='store_true'); a=p.parse_args()
 auth=json.loads(a.authorization.read_text())
 if a.test_fixture:
  class Gate: 
   def check(self): return None
  calls=[]
  def transport(req): calls.append(req); return ProviderTransportOutcome(200,{'success':True,'data':{'items':[]}}, {})
  probe=OneRequestLifecycleProbe(auth,a.journal,runtime_gate=Gate(),admission=HistoricalForensicsBudgetAdmission(a.queue_root),transport=transport,disk_free=lambda:5*1024**3)
 else:
  if os.environ.get('DEV014_BIRDEYE_ENV_BOUND')!='1': raise SystemExit('BOUND_PROCESS_REQUIRED')
  probe=OneRequestLifecycleProbe(auth,a.journal,runtime_gate=Batch4RuntimeGate(resolve_runtime_authority(a.supervisor_config)),admission=HistoricalForensicsBudgetAdmission(a.queue_root),transport=BirdeyeProductionBinding(),disk_free=lambda:__import__('shutil').disk_usage(a.journal.parent).free)
 result=probe.execute(); print(json.dumps(result,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
