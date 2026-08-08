#!/usr/bin/env python3
"""Run the bounded EP3.0F WATCHTOWER shadow-only recovery."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops.watchtower_shadow_recovery import WatchtowerShadowRecovery


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operations-db", type=Path, default=Path("database/wt_ops_v2.db"))
    parser.add_argument("--main-db", type=Path, default=Path("database/flex_complete_database.db"))
    parser.add_argument("--transaction-cache-db", type=Path,
                        default=Path("database/transaction_first_lineage.db"))
    parser.add_argument("--output", type=Path,
                        default=Path("database/evidence_platform/watchtower_shadow_ep3_0d"))
    parser.add_argument("--resume-without-rpc", action="store_true",
                        help="amend and drain the existing durable queue without acquisition")
    parser.add_argument("--validate-observations-only", action="store_true",
                        help="replay only EP3.0G observations without primitives or RPC")
    args = parser.parse_args()
    recovery = WatchtowerShadowRecovery(
        operations_db=args.operations_db, main_db=args.main_db,
        transaction_cache_db=args.transaction_cache_db, output_root=args.output,
    )
    if args.validate_observations_only:
        result = recovery._validate_amended_observations()
        recovery.materializer._write_json(
            args.output / "ep3_0g_observation_replay.json", result
        )
    elif args.resume_without_rpc:
        result = recovery.resume_without_rpc()
    else:
        result = asyncio.run(recovery.run())
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
