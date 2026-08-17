#!/usr/bin/env python3
"""Freeze a bounded post-D8 observation-only cohort from retained local stores."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_prospective_cohort import freeze_prospective_observation_cohort


ROOT = Path(__file__).resolve().parents[1]
CUTOFF = 1786109860
D5 = ROOT / "docs/audits/psi0g_runs/psi0g-d5-real-provenance-retention-20260817-02/projection.json"
SOURCES = (
    ROOT / "database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db",
    ROOT / "database/evidence_platform/three_sw2_shadow_ep3_2a/evidence.db",
)
OUTPUT = ROOT / "docs/audits/psi0h_b_real_prospective_cohort.json"


def _identity(path: Path) -> dict[str, int]:
    value = path.stat()
    return {"device": value.st_dev, "inode": value.st_ino, "size_bytes": value.st_size,
            "mtime_ns": value.st_mtime_ns}


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _read_source(path: Path) -> tuple[dict, list[dict]]:
    before = _identity(path)
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=0.25)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        stats = dict(connection.execute("""
            SELECT COUNT(*) primitive_count,
                   SUM(window_start>?) post_cutoff_start_count,
                   SUM(window_end>?) post_cutoff_end_count,
                   SUM(generated_at>?) post_cutoff_generation_count
            FROM primitive_observations
        """, (CUTOFF, CUTOFF, CUTOFF)).fetchone())
        evidence_stats = dict(connection.execute("""
            SELECT COUNT(*) evidence_count,
                   SUM(observed_at>?) post_cutoff_observed_count,
                   SUM(acquired_at>?) post_cutoff_acquired_count
            FROM normalized_evidence_records
        """, (CUTOFF, CUTOFF)).fetchone())
        families = [dict(row) for row in connection.execute("""
            SELECT fact_family,COUNT(*) count,MIN(observed_at) minimum_observed_at,
                   MAX(observed_at) maximum_observed_at
            FROM normalized_evidence_records WHERE observed_at>?
            GROUP BY fact_family ORDER BY fact_family
        """, (CUTOFF,))]
        primitive_rows = []
        rows = connection.execute("""
            SELECT primitive_id,primitive_type,window_start,window_end,generated_at
            FROM primitive_observations WHERE window_start>?
            ORDER BY window_start,window_end,primitive_id LIMIT 21
        """, (CUTOFF,)).fetchall()
        for row in rows:
            evidence = [dict(item) for item in connection.execute("""
                SELECT n.evidence_id,n.observed_at
                FROM primitive_evidence_inputs p JOIN normalized_evidence_records n
                  ON n.evidence_id=p.evidence_id
                WHERE p.primitive_id=? ORDER BY n.evidence_id
            """, (row["primitive_id"],))]
            primitive_rows.append({
                "primitive_id": row["primitive_id"], "primitive_type": row["primitive_type"],
                "observation_window": {"start": row["window_start"], "end": row["window_end"]},
                "generated_at": row["generated_at"], "evidence": evidence,
            })
    finally:
        connection.close()
    after = _identity(path)
    if before != after:
        raise RuntimeError(f"PSI0H_B_SOURCE_CHANGED_DURING_READ:{path}")
    return {
        "path": str(path.relative_to(ROOT)), "identity": before,
        **stats, **evidence_stats, "post_cutoff_fact_families": families,
        "access": "sqlite_uri_mode_ro_and_query_only",
    }, primitive_rows


def run() -> dict:
    projection = json.loads(D5.read_text())
    source_stats = []
    candidates = []
    for path in SOURCES:
        stats, rows = _read_source(path)
        source_stats.append(stats)
        candidates.extend(rows)
    baseline = projection["candidate"]
    result = freeze_prospective_observation_cohort(
        cutoff=CUTOFF,
        baseline_observation_ids=baseline["supporting_behaviour_observation_ids"],
        baseline_evidence_ids=baseline["supporting_evidence_ids"],
        baseline_primitive_ids=baseline["supporting_primitive_ids"],
        primitive_rows=candidates, maximum=20,
    )
    contract_replay_digest = result.pop("replay_digest")
    result["source_stats"] = source_stats
    result["contract_replay_digest"] = contract_replay_digest
    result["scope"] = {
        "observation_only": True, "comparison_performed": False,
        "production_writes": 0, "provider_or_rpc_calls": 0,
        "service_or_configuration_changes": 0, "alerts_emitted": 0,
        "external_actions": 0,
    }
    result["artifact_digest"] = _digest(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"PSI0H_B_OUTPUT_EXISTS:{args.output}")
    result = run()
    payload = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    with args.output.open("xb") as handle:
        handle.write(payload.encode())
        handle.flush()
        os.fsync(handle.fileno())
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
