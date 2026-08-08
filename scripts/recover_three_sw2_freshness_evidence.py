#!/usr/bin/env python3
"""Run the bounded EP3.2B four-creator Evidence recovery."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ops.three_sw2_freshness_recovery import ThreeSw2FreshnessRecovery


def task(root: Path) -> ThreeSw2FreshnessRecovery:
    return ThreeSw2FreshnessRecovery(
        operations_db=Path("database/wt_ops_v2.db"),
        main_db=Path("database/flex_complete_database.db"),
        cache_db=Path("database/transaction_first_lineage.db"),
        output_root=root,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path,
                        default=Path("database/evidence_platform/three_sw2_shadow_ep3_2a"))
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    recovery = task(args.output_root)
    report = recovery.plan() if args.plan_only else asyncio.run(recovery.run())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
