#!/usr/bin/env python3
"""Explicit, disabled-by-default DEV-023 retention tick entry point.

This is intentionally not installed in crontab, Supervisor, or a listener
loop.  It is a future scheduler target only after a separate runtime
deployment and live-maintenance approval.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops.dev023_hot_rpc_cache_maintenance import (
    config_from_environment,
    run_retention_tick,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one disabled-by-default DEV-023 retention tick")
    parser.add_argument("--canonical-db-path", required=True)
    parser.add_argument("--cutoff", type=float, default=None)
    args = parser.parse_args(argv)
    config = config_from_environment(
        canonical_database_path=args.canonical_db_path,
        cutoff=time.time() if args.cutoff is None else args.cutoff,
    )
    result = run_retention_tick(config)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "DISABLED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
