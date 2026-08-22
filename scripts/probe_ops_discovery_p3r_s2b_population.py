"""Durable bounded, query-only S2B population reproduction probe."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

QUERY = """
SELECT COUNT(*)
FROM token_analysis AS ta
JOIN pumpfun_migration_verification AS pmv ON ta.mint = pmv.mint
WHERE ta.pf_ws_creator IS NOT NULL
""".strip()

INDEXED_EXISTS_QUERY = """
SELECT COUNT(*)
FROM token_analysis AS ta INDEXED BY idx_ta_pf_ws_creator
WHERE ta.pf_ws_creator IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM pumpfun_migration_verification AS pmv
      WHERE pmv.mint = ta.mint
  )
""".strip()


def write_result(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="database/flex_complete_database.db")
    parser.add_argument("--result-path", required=True)
    parser.add_argument("--expected-count", type=int, default=40301)
    parser.add_argument("--schema-indexed-equivalent", action="store_true")
    parser.add_argument("--wall-seconds", type=float, default=20.0)
    parser.add_argument("--max-progress-calls", type=int, default=100000)
    args = parser.parse_args()
    query = INDEXED_EXISTS_QUERY if args.schema_indexed_equivalent else QUERY
    query_kind = "schema_indexed_exists_equivalent" if args.schema_indexed_equivalent else "frozen_original"
    result_path = Path(args.result_path)
    started = time.monotonic()
    common = {
        "milestone": "OPS-DISCOVERY-P3R-S2B-PROBE",
        "provider_calls_made": 0,
        "production_writes": 0,
        "read_mode": "sqlite_uri_mode_ro_query_only",
        "source_db": args.db,
        "query": query,
        "query_kind": query_kind,
        "expected_population_count": args.expected_count,
        "bounds": {"wall_seconds": args.wall_seconds, "max_progress_calls": args.max_progress_calls, "progress_opcode_interval": 1000, "returned_rows_max": 1},
    }
    write_result(result_path, {**common, "status": "STARTED"})
    calls = 0
    def progress() -> int:
        nonlocal calls
        calls += 1
        return int(calls > args.max_progress_calls or time.monotonic() - started > args.wall_seconds)
    try:
        uri = f"file:{Path(args.db).resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.execute("PRAGMA query_only=ON")
        conn.set_progress_handler(progress, 1000)
        count = conn.execute(query).fetchone()[0]
        conn.close()
        result = {**common, "status": "COMPLETE", "population_count": count, "reproduced": count == args.expected_count, "progress_calls": calls, "elapsed_seconds": round(time.monotonic() - started, 6)}
        write_result(result_path, result)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["reproduced"] else 2
    except sqlite3.OperationalError as exc:
        reason = "BOUND_EXCEEDED" if "interrupted" in str(exc).lower() else "SQLITE_OPERATIONAL_ERROR"
        result = {**common, "status": "HOLD", "failure_reason": reason, "exception_type": type(exc).__name__, "exception": str(exc), "progress_calls": calls, "elapsed_seconds": round(time.monotonic() - started, 6)}
        write_result(result_path, result)
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        return 3
    except Exception as exc:
        result = {**common, "status": "HOLD", "failure_reason": "UNEXPECTED_EXCEPTION", "exception_type": type(exc).__name__, "exception": str(exc), "progress_calls": calls, "elapsed_seconds": round(time.monotonic() - started, 6)}
        write_result(result_path, result)
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        return 4

if __name__ == "__main__":
    raise SystemExit(main())
