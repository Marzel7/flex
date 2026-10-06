#!/usr/bin/env python3
"""Commit-pinned, disabled-by-default single-mint historical executor launcher."""
from __future__ import annotations
import argparse, os, subprocess, sys
from pathlib import Path
from typing import Any, Callable

COOK_BOUNDARY={"mint":"HRinFbhZjrb2xoqSzxYCYKX7LqJ3H6pn42CURb2cpump","assignment_id":"COOK_BATCH1","migration_signature":"34wTjQhZameyCbJaFHsUmhmWDC7hRKxLguBJxLQ4K3RjBfd6NvzCMmsYsjsBV82PCd3xXHTJqeoQ3H86qhdNXfUE","migration_slot":453827661,"migration_timestamp":1791269958,"pumpswap_pool":"2AixyEkGyaAUdETArBCFSZ1Y8entwd99ptYxjbJL6NkD"}
COOK_TERMINAL={"entry_timestamp":1791269958,"entry_mc_usd":113464.45625213276,"entry_method":"MIGRATION_SECOND_MC_FALLBACK","entry_exactness":"MIGRATION_SECOND_MC_FALLBACK"}
def launch_cook(*, db: str, boundary: dict[str,Any]=COOK_BOUNDARY, binding: Callable|None=None) -> dict[str,Any]:
 if boundary != COOK_BOUNDARY: raise ValueError("COOK_CANONICAL_BOUNDARY_MISMATCH")
 from src.ops.watchtower_historical_backfill import admit
 from src.ops.watchtower_historical_executor import HistoricalExecutor
 job=admit(db,COOK_BOUNDARY,now=0)["job_id"]
 return HistoricalExecutor(db,binding).run(job,COOK_BOUNDARY["mint"],terminal_result=COOK_TERMINAL)
def main() -> int:
 p=argparse.ArgumentParser(); p.add_argument("--sha",required=True); p.add_argument("--mint",required=True); p.add_argument("--db",required=True); p.add_argument("--enable-batch1",action="store_true"); a=p.parse_args()
 root=Path(__file__).resolve().parents[1]
 if not a.enable_batch1: raise SystemExit("HISTORICAL_EXECUTOR_DISABLED_BY_DEFAULT")
 if a.mint != "HRinFbhZjrb2xoqSzxYCYKX7LqJ3H6pn42CURb2cpump": raise SystemExit("EXACT_SINGLE_MINT_REQUIRED")
 subprocess.run([sys.executable,str(root/"scripts/check_clean_dev_authority.py"),"--sha",a.sha,"--worktree",str(root),"--require-clean","--require-tracked","src/ops/watchtower_historical_executor.py"],check=True)
 if Path.cwd().resolve()!=root: raise SystemExit("CROSS_WORKTREE_RUNTIME_IMPORT_REFUSED")
 os.environ["PYTHONPATH"]=str(root)
 result=launch_cook(db=a.db)
 print(result); return 0 if result.get("state")=="COMPLETED" else 2
if __name__=="__main__": raise SystemExit(main())
