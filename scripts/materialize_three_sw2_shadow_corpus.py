#!/usr/bin/env python3
from __future__ import annotations
import argparse,asyncio,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.ops.three_sw2_shadow_corpus import ThreeSw2ShadowCorpus

def main():
    p=argparse.ArgumentParser(); p.add_argument("--plan-only",action="store_true"); p.add_argument("--freshness-only",action="store_true"); p.add_argument("--output",type=Path,default=Path("database/evidence_platform/three_sw2_shadow_ep3_2a")); a=p.parse_args()
    task=ThreeSw2ShadowCorpus(operations_db=Path("database/wt_ops_v2.db"),main_db=Path("database/flex_complete_database.db"),cache_db=Path("database/transaction_first_lineage.db"),output_root=a.output)
    result=task.plan() if a.plan_only else asyncio.run(task.materialize_creator_histories() if a.freshness_only else task.run()); print(json.dumps(result,indent=2,sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
