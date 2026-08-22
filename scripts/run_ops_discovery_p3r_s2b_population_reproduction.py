"""One bounded read-only reproduction of the S2B eligible population."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

QUERY = """
SELECT ta.mint, ta.pf_ws_creator
FROM token_analysis AS ta INDEXED BY idx_ta_pf_ws_creator
WHERE ta.pf_ws_creator IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM pumpfun_migration_verification AS pmv
      WHERE pmv.mint = ta.mint
  )
ORDER BY ta.mint ASC, ta.pf_ws_creator ASC
""".strip()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    os.replace(temp, path)


def materialize(conn: sqlite3.Connection, *, output: Path, deadline: float, max_progress: int) -> tuple[int, str, int]:
    calls = 0
    def progress() -> int:
        nonlocal calls
        calls += 1
        return int(calls > max_progress or time.monotonic() > deadline)
    conn.set_progress_handler(progress, 1000)
    digest = hashlib.sha256()
    count = 0
    temp = output.with_suffix(output.suffix + '.tmp')
    with temp.open('w') as handle:
        for mint, creator in conn.execute(QUERY):
            row = {'population_ordinal': count + 1, 'mint': mint, 'create_creator': creator,
                   'source_table': 'token_analysis', 'migration_verification_table': 'pumpfun_migration_verification'}
            encoded = json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n'
            handle.write(encoded)
            digest.update(encoded.encode())
            count += 1
    os.replace(temp, output)
    return count, digest.hexdigest(), calls


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default='database/flex_complete_database.db')
    parser.add_argument('--manifest-path', required=True)
    parser.add_argument('--audit-path', required=True)
    parser.add_argument('--expected-count', type=int, default=40301)
    parser.add_argument('--wall-seconds', type=float, default=120.0)
    parser.add_argument('--max-progress-calls', type=int, default=100000)
    parser.add_argument('--equivalence-digest', required=True)
    args = parser.parse_args()
    started = time.monotonic()
    audit_path, manifest = Path(args.audit_path), Path(args.manifest_path)
    common = {'milestone': 'OPS-DISCOVERY-P3R-S2B-REPRODUCTION', 'provider_calls_made': 0, 'production_writes': 0,
              'read_mode': 'sqlite_uri_mode_ro_query_only', 'source_db': args.db, 'query': QUERY,
              'query_sha256': hashlib.sha256(QUERY.encode()).hexdigest(), 'equivalence_artifact_sha256': args.equivalence_digest,
              'bounds': {'wall_seconds': args.wall_seconds, 'max_progress_calls_per_pass': args.max_progress_calls, 'progress_opcode_interval': 1000, 'passes': 2},
              'identity_boundary': 'Canonical population manifest only; no labels, ranks, quotas, or cohort selection.'}
    atomic_json(audit_path, {**common, 'status': 'STARTED'})
    try:
        uri = f'file:{Path(args.db).resolve()}?mode=ro'
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.execute('PRAGMA query_only=ON')
        deadline = started + args.wall_seconds
        count1, digest1, calls1 = materialize(conn, output=manifest, deadline=deadline, max_progress=args.max_progress_calls)
        replay = manifest.with_suffix(manifest.suffix + '.replay')
        count2, digest2, calls2 = materialize(conn, output=replay, deadline=deadline, max_progress=args.max_progress_calls)
        conn.close()
        replay.unlink()
        result = {**common, 'status': 'COMPLETE', 'population_count': count1, 'population_manifest_sha256': digest1,
                  'replay': {'population_count': count2, 'population_manifest_sha256': digest2, 'identical': count1 == count2 and digest1 == digest2},
                  'expected_count_match': count1 == args.expected_count, 'progress_calls': {'pass_1': calls1, 'pass_2': calls2},
                  'elapsed_seconds': round(time.monotonic() - started, 6), 'manifest_path': str(manifest)}
        atomic_json(audit_path, result)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['expected_count_match'] and result['replay']['identical'] else 2
    except Exception as exc:
        result = {**common, 'status': 'HOLD', 'failure_reason': 'BOUND_EXCEEDED' if 'interrupted' in str(exc).lower() else 'EXECUTION_ERROR',
                  'exception_type': type(exc).__name__, 'exception': str(exc), 'elapsed_seconds': round(time.monotonic() - started, 6)}
        atomic_json(audit_path, result)
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        return 3

if __name__ == '__main__':
    raise SystemExit(main())
