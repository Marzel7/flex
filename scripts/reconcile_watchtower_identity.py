#!/usr/bin/env python3
"""Idempotently reconcile confirmed WATCHTOWER treasuries to the canonical actor."""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ops.watchtower_alignment import initialize_and_reconcile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        default=os.environ.get("WT_OPS_DB_PATH", "database/wt_ops_v2.db"),
        help="operations database path",
    )
    args = parser.parse_args()
    result = initialize_and_reconcile(os.path.abspath(args.db))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["audit"]["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
