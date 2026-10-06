#!/usr/bin/env python3
"""Commit-pinned, disabled-by-default single-mint historical executor launcher."""
from __future__ import annotations
import argparse, os, subprocess, sys
from pathlib import Path
def main() -> int:
 p=argparse.ArgumentParser(); p.add_argument("--sha",required=True); p.add_argument("--mint",required=True); p.add_argument("--db",required=True); p.add_argument("--enable-batch1",action="store_true"); a=p.parse_args()
 root=Path(__file__).resolve().parents[1]
 if not a.enable_batch1: raise SystemExit("HISTORICAL_EXECUTOR_DISABLED_BY_DEFAULT")
 if a.mint != "HRinFbhZjrb2xoqSzxYCYKX7LqJ3H6pn42CURb2cpump": raise SystemExit("EXACT_SINGLE_MINT_REQUIRED")
 subprocess.run([sys.executable,str(root/"scripts/check_clean_dev_authority.py"),"--sha",a.sha,"--worktree",str(root),"--require-clean","--require-tracked","src/ops/watchtower_historical_executor.py"],check=True)
 if Path.cwd().resolve()!=root: raise SystemExit("CROSS_WORKTREE_RUNTIME_IMPORT_REFUSED")
 os.environ["PYTHONPATH"]=str(root)
 return 0
if __name__=="__main__": raise SystemExit(main())
