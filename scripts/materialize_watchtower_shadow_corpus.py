#!/usr/bin/env python3
"""Materialize EP3.0C from existing local cache; performs no RPC."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops.watchtower_shadow_corpus import WatchtowerShadowCorpusMaterializer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operations-db", type=Path, default=Path("database/wt_ops_v2.db"))
    parser.add_argument("--transaction-cache-db", type=Path,
                        default=Path("database/transaction_first_lineage.db"))
    parser.add_argument("--output", type=Path,
                        default=Path("database/evidence_platform/watchtower_shadow"))
    args = parser.parse_args()
    result = WatchtowerShadowCorpusMaterializer(
        operations_db=args.operations_db, transaction_cache_db=args.transaction_cache_db,
        output_root=args.output,
    ).materialize()
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
