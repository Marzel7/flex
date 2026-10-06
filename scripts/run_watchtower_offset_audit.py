#!/usr/bin/env python3
"""Run the explicitly enabled, bounded Watchtower opening-offset audit."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops.token_data_provider_bindings import BirdeyeProductionBinding, build_birdeye_ohlcv_request
from src.ops.watchtower_offset_audit import OffsetAuditStore, compact_record

COMPLETE_RETAINED_MINTS = {
    "B8S7WGQYteNSBz4rU4Xs1hkr1seGpxYgUuV2wgtbpump",
    "7iXZi55AyKbJv5e9XTQuVcywdT2ejHjGBf3NKuCLpump",
    "3yvK6WWww1moF3qx3UvHngZCHy9kRCVd9Jva8tRrpump",
}


def candidates(*, operations_db: Path, canonical_db: Path) -> list[tuple[str, int]]:
    connection = sqlite3.connect(f"file:{operations_db}?mode=ro", uri=True)
    try:
        connection.execute("attach database ? as canon", (f"file:{canonical_db}?mode=ro",))
        rows = connection.execute("""
            select f.mint, c.migrated_at
              from operation_monitor_facts f join canon.token_analysis c on c.mint=f.mint
             where f.operation_id in ('watchtower','watchtower_deep')
             order by c.migrated_at, f.mint
        """).fetchall()
    finally:
        connection.close()
    if len(rows) != 96:
        raise RuntimeError("UNEXPECTED_WATCHTOWER_COHORT")
    return [(str(mint), int(timestamp)) for mint, timestamp in rows if mint not in COMPLETE_RETAINED_MINTS]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operations-db", type=Path, required=True)
    parser.add_argument("--canonical-db", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    cohort = candidates(operations_db=args.operations_db, canonical_db=args.canonical_db)
    store = OffsetAuditStore(args.ledger)
    existing = {record["mint"] for record in store.records()}
    pending = [(mint, timestamp) for mint, timestamp in cohort if mint not in existing]
    print(json.dumps({"cohort": len(cohort), "already_recorded": len(cohort) - len(pending), "pending": len(pending), "execute": args.execute}))
    if not args.execute:
        return 0
    binding = BirdeyeProductionBinding()
    for sequence, (mint, timestamp) in enumerate(pending, 1):
        request = build_birdeye_ohlcv_request(address=mint, interval="1s", time_from=timestamp, time_to=timestamp + 4)
        try:
            outcome = binding(request)
            record = compact_record(mint=mint, migration_timestamp=timestamp, http_status=outcome.status_code,
                                    payload=outcome.payload if outcome.status_code == 200 else None,
                                    failure_state=outcome.error_state)
        except Exception as exc:
            record = compact_record(mint=mint, migration_timestamp=timestamp, http_status=0,
                                    failure_state=type(exc).__name__)
        bytes_written = store.record(record)
        print(json.dumps({"completed": sequence, "total": len(pending), "ledger_bytes": bytes_written}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
