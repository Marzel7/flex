#!/usr/bin/env python3
"""Run one explicit bounded compact-receipt maintenance pass.

This is deliberately an operator command, not a listener worker or scheduler.
It prints compact integrity evidence and makes no provider request or write to
Core/OPS; its database connections are SQLite read-only dependency lookups.
"""
from __future__ import annotations

import argparse
import json

from src.core.pumpportal_receipt_maintenance import CompactReceiptMaintenance, ReceiptDependencyChecker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt-root", required=True)
    parser.add_argument("--core-db", required=True)
    parser.add_argument("--ops-db", required=True)
    parser.add_argument("--start-bucket", type=int, default=0)
    parser.add_argument("--max-buckets", type=int, default=1)
    parser.add_argument("--max-candidates", type=int, default=32)
    parser.add_argument("--include-legacy", action="store_true")
    args = parser.parse_args()
    checker = ReceiptDependencyChecker(args.core_db, args.ops_db)
    try:
        report = CompactReceiptMaintenance(args.receipt_root, checker=checker).run(
            start_bucket=args.start_bucket,
            max_buckets=args.max_buckets,
            max_candidates=args.max_candidates,
            include_legacy=args.include_legacy,
        )
    finally:
        checker.close()
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
