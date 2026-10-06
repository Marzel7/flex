#!/usr/bin/env python3
"""Commit-pinned, disabled-by-default single-mint historical executor launcher."""
from __future__ import annotations
import argparse, os, subprocess, sys
from pathlib import Path
from typing import Any, Callable

# Establish this committed repository as import root before any project import.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
 sys.path.insert(0, str(REPOSITORY_ROOT))

COOK_BOUNDARY={"mint":"HRinFbhZjrb2xoqSzxYCYKX7LqJ3H6pn42CURb2cpump","assignment_id":"COOK_BATCH1","migration_signature":"34wTjQhZameyCbJaFHsUmhmWDC7hRKxLguBJxLQ4K3RjBfd6NvzCMmsYsjsBV82PCd3xXHTJqeoQ3H86qhdNXfUE","migration_slot":453827661,"migration_timestamp":1791269958,"pumpswap_pool":"2AixyEkGyaAUdETArBCFSZ1Y8entwd99ptYxjbJL6NkD"}
def launch_cook(*, db: str, boundary: dict[str,Any]=COOK_BOUNDARY, binding: Callable|None=None) -> dict[str,Any]:
 if boundary != COOK_BOUNDARY: raise ValueError("COOK_CANONICAL_BOUNDARY_MISMATCH")
 from src.ops.watchtower_historical_backfill import admit, job_state, resume_terminal_from_retained
 # A terminal-pending COOK job has already exhausted its provider budget.  Read
 # only durable state before constructing a credential-bound executor.
 import sqlite3
 with sqlite3.connect(db) as conn:
  exists=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='watchtower_historical_backfill_jobs'").fetchone()
  row=conn.execute("SELECT job_id FROM watchtower_historical_backfill_jobs WHERE state='TERMINAL_COMMIT_PENDING'").fetchone() if exists else None
 if row:
  return resume_terminal_from_retained(db,row[0],COOK_BOUNDARY["mint"])
 from src.ops.watchtower_historical_executor import HistoricalExecutor
 executor=HistoricalExecutor(db,binding); opening=executor.acquire_opening(COOK_BOUNDARY)
 admission=admit(db,COOK_BOUNDARY,now=0)
 if "job_id" not in admission:
  return admission
 job=admission["job_id"]
 return executor.run(job,COOK_BOUNDARY["mint"],terminal_result=opening)
def main() -> int:
 p=argparse.ArgumentParser(); p.add_argument("--sha",required=True); p.add_argument("--mint",required=True); p.add_argument("--db",required=True); p.add_argument("--enable-batch1",action="store_true"); a=p.parse_args()
 root=REPOSITORY_ROOT
 if not a.enable_batch1: raise SystemExit("HISTORICAL_EXECUTOR_DISABLED_BY_DEFAULT")
 if a.mint != "HRinFbhZjrb2xoqSzxYCYKX7LqJ3H6pn42CURb2cpump": raise SystemExit("EXACT_SINGLE_MINT_REQUIRED")
 subprocess.run([sys.executable,str(root/"scripts/check_clean_dev_authority.py"),"--sha",a.sha,"--worktree",str(root),"--require-clean","--require-tracked","src/ops/watchtower_historical_executor.py"],check=True)
 if Path.cwd().resolve()!=root: raise SystemExit("CROSS_WORKTREE_RUNTIME_IMPORT_REFUSED")
 result=launch_cook(db=a.db)
 print(result); return 0 if result.get("state")=="COMPLETED" else 2
if __name__=="__main__": raise SystemExit(main())
