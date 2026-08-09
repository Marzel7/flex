#!/usr/bin/env python3
"""Read-only compact application-path benchmark over recovered v2.2C.3 state."""
from __future__ import annotations

import hashlib
import json
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.oip_v2_2c3_authority_store import IndexedAuthorityStore

AUTHORITY = ROOT / "database/evidence_platform/oip_v2_2c3_indexed_authority/indexed_authority_compact.sqlite"
CANONICAL = ROOT / "database/evidence_platform/oip_v2_1g_stage_2000_frozen/evidence.db"
COMPACT = ROOT / "database/evidence_platform/oip_v2_2b_compact_provenance/compact_provenance.sqlite"
REPORT = ROOT / "docs/evidence_platform/oip_v2_2c4_compact_read_path_benchmark.json"
MAX_QUERY_SECONDS = 600


class QueryDeadline(RuntimeError): pass


def deadline_handler(_signum, _frame):
    raise QueryDeadline("query exceeded ten-minute recovery ceiling")


def timed_rows(connection, sql: str, parameters=()):
    started = time.perf_counter(); count = 0; digest = hashlib.sha256()
    signal.signal(signal.SIGALRM, deadline_handler); signal.alarm(MAX_QUERY_SECONDS)
    try:
        for row in connection.execute(sql, parameters):
            count += 1
            for value in row:
                digest.update(str(value).encode()); digest.update(b"\0")
    finally:
        signal.alarm(0)
    return {"count": count, "seconds": round(time.perf_counter()-started, 6),
            "stream_digest": digest.hexdigest()}


def main() -> int:
    store = IndexedAuthorityStore(AUTHORITY, canonical=CANONICAL, compact=COMPACT, read_only=True)
    db = store.connection
    try:
        ids = store.ids("CURRENT_AUTHORITATIVE")
        discovery_ids = store.ids("CURRENT_AUTHORITATIVE", minimum_subjects=2)
        db.execute("DROP TABLE IF EXISTS temp.selected_primitives")
        db.execute("CREATE TEMP TABLE selected_primitives(primitive_id TEXT PRIMARY KEY) WITHOUT ROWID")
        db.executemany("INSERT INTO selected_primitives VALUES(?)", ((value,) for value in ids))
        db.execute("DROP TABLE IF EXISTS temp.selected_primitive_keys")
        db.execute("""CREATE TEMP TABLE selected_primitive_keys(
          primitive_key INTEGER PRIMARY KEY,primitive_id TEXT NOT NULL UNIQUE) WITHOUT ROWID""")
        db.execute("""INSERT INTO selected_primitive_keys SELECT p.primitive_key,p.primitive_id
          FROM compact.primitive_identity p JOIN selected_primitives s USING(primitive_id)""")
        compact_sql = """SELECT s.primitive_id,e.evidence_id
          FROM selected_primitive_keys s
          CROSS JOIN compact.compact_primitive_evidence_inputs i
            ON i.primitive_key=s.primitive_key
          JOIN compact.evidence_identity e ON e.evidence_key=i.evidence_key"""
        canonical_sql = """SELECT i.primitive_id,i.evidence_id FROM selected_primitives s
          CROSS JOIN canonical.primitive_evidence_inputs i
            ON i.primitive_id=s.primitive_id"""
        plans = {
            "optimized_compact": store.explain(compact_sql),
            "canonical": store.explain(canonical_sql),
            "rejected_compatibility": store.explain("""SELECT p.primitive_id,e.evidence_id
              FROM selected_primitives s JOIN compact.primitive_identity p USING(primitive_id)
              JOIN compact.compact_primitive_evidence_inputs i USING(primitive_key)
              JOIN compact.evidence_identity e USING(evidence_key)
              ORDER BY p.primitive_id,e.evidence_id"""),
        }
        canonical_full = timed_rows(db, canonical_sql)
        compact_full = timed_rows(db, compact_sql)

        started = time.perf_counter(); canonical_discovery = store.load_primitives(
            "CURRENT_AUTHORITATIVE", minimum_subjects=2, compact=False)
        canonical_discovery_seconds = round(time.perf_counter()-started, 6)
        started = time.perf_counter(); compact_discovery = store.load_primitives(
            "CURRENT_AUTHORITATIVE", minimum_subjects=2, compact=True)
        compact_discovery_seconds = round(time.perf_counter()-started, 6)

        representative = tuple(discovery_ids[index] for index in range(
            0, len(discovery_ids), max(1, len(discovery_ids)//100)))[:100]
        one_subject = db.execute("SELECT subject FROM current_authority_subject ORDER BY subject LIMIT 1").fetchone()[0]
        one_subject_result = store.benchmark_ids("CURRENT_AUTHORITATIVE", subjects=(one_subject,))
        sample_refs = {item.primitive_id: item.evidence_ids for item in compact_discovery
                       if item.primitive_id in set(representative)}
        high = max(compact_discovery, key=lambda item: (len(item.evidence_ids), item.primitive_id))
        reverse_evidence = tuple(sorted({value for item in compact_discovery[:100]
                                         for value in item.evidence_ids}))[:100]

        # Bounded repository-shaped lookups use integer key resolution once.
        db.execute("DROP TABLE IF EXISTS temp.benchmark_primitive_keys")
        db.execute("""CREATE TEMP TABLE benchmark_primitive_keys(
          primitive_key INTEGER PRIMARY KEY,primitive_id TEXT NOT NULL UNIQUE) WITHOUT ROWID""")
        db.executemany("""INSERT INTO benchmark_primitive_keys SELECT primitive_key,primitive_id
          FROM compact.primitive_identity WHERE primitive_id=?""", ((value,) for value in representative))
        p100 = timed_rows(db, """SELECT s.primitive_id,e.evidence_id FROM benchmark_primitive_keys s
          CROSS JOIN compact.compact_primitive_evidence_inputs i ON i.primitive_key=s.primitive_key
          JOIN compact.evidence_identity e ON e.evidence_key=i.evidence_key""")

        db.execute("DROP TABLE IF EXISTS temp.benchmark_evidence_keys")
        db.execute("""CREATE TEMP TABLE benchmark_evidence_keys(
          evidence_key INTEGER PRIMARY KEY,evidence_id TEXT NOT NULL UNIQUE) WITHOUT ROWID""")
        db.executemany("""INSERT INTO benchmark_evidence_keys SELECT evidence_key,evidence_id
          FROM compact.evidence_identity WHERE evidence_id=?""", ((value,) for value in reverse_evidence))
        e100 = timed_rows(db, """SELECT s.evidence_id,p.primitive_id FROM benchmark_evidence_keys s
          CROSS JOIN compact.compact_primitive_evidence_inputs i ON i.evidence_key=s.evidence_key
          JOIN compact.primitive_identity p ON p.primitive_key=i.primitive_key""")

        report = {
            "milestone": "OIP v2.2C.4", "mode": "READ_ONLY_SHADOW",
            "population": {"current_authoritative": len(ids), "discovery_eligible": len(discovery_ids)},
            "query_plans": plans,
            "full_current_authority_stream": {"canonical": canonical_full,
                "compact_integer_key": compact_full,
                "speed_ratio_compact_to_canonical": round(compact_full["seconds"] / canonical_full["seconds"], 6)},
            "discovery_hydration": {"canonical_seconds": canonical_discovery_seconds,
                "compact_seconds": compact_discovery_seconds,
                "primitive_count": len(compact_discovery),
                "provenance_pairs": sum(len(item.evidence_ids) for item in compact_discovery),
                "exact_object_equality": canonical_discovery == compact_discovery},
            "bounded_consumers": {"one_subject": {"subject": one_subject, **one_subject_result},
                "primitive_to_evidence_100": p100,
                "evidence_to_primitive_100": e100,
                "high_fan_out": {"primitive_id": high.primitive_id,
                    "evidence_count": len(high.evidence_ids)},
                "exact_pair": {"primitive_id": high.primitive_id,
                    "evidence_id": high.evidence_ids[0],
                    "present": db.execute("""SELECT 1 FROM compact.primitive_identity p
                      JOIN compact.compact_primitive_evidence_inputs i USING(primitive_key)
                      JOIN compact.evidence_identity e USING(evidence_key)
                      WHERE p.primitive_id=? AND e.evidence_id=?""",
                      (high.primitive_id, high.evidence_ids[0])).fetchone() is not None}},
            "constraints": {"rpc_calls": 0, "production_interaction": False,
                "semantic_changes": 0, "source_writes": 0},
        }
        REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"report": str(REPORT), "full": report["full_current_authority_stream"],
                          "hydration": report["discovery_hydration"]}, sort_keys=True))
        return 0 if report["discovery_hydration"]["exact_object_equality"] else 1
    finally:
        store.close()


if __name__ == "__main__": raise SystemExit(main())
