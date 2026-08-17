#!/usr/bin/env python3
"""Apply the approved PSI0G-D projection once to the immutable PSI0G-B2 bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0g_operation_projection import project_psi0g_operation_candidate


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/audits/psi0g_runs/psi0g-b-retained-derivation-20260817-01"
OUTPUT = ROOT / "docs/audits/psi0g_runs/psi0g-d1-operation-candidate-20260817-01"


def sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def rows(path: Path, table: str) -> list[dict]:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=0.25)
    connection.execute("PRAGMA query_only=ON")
    try:
        return [json.loads(row[0]) for row in connection.execute(
            f"SELECT payload_json FROM {table} ORDER BY contract_id,output_id")]
    finally:
        connection.close()


def candidate_context(path: Path) -> tuple[int, str]:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=0.25)
    connection.execute("PRAGMA query_only=ON")
    try:
        ids = [row[0] for row in connection.execute(
            "SELECT candidate_id FROM discovery_candidates ORDER BY candidate_id")]
    finally:
        connection.close()
    digest = hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()
    return len(ids), digest


def run(output: Path = OUTPUT) -> dict:
    if output.exists():
        raise FileExistsError(f"PSI0G_D1_OUTPUT_EXISTS:{output}")
    manifest_path = SOURCE / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    expected = manifest["files"]
    for name in ("operation-runtime.db", "discovery.db"):
        path = SOURCE / name
        if sha256_file(path) != expected[name]["sha256"] or path.stat().st_size != expected[name]["size_bytes"]:
            raise RuntimeError(f"PSI0G_D1_SOURCE_DRIFT:{name}")
    count, digest = candidate_context(SOURCE / "discovery.db")
    result = project_psi0g_operation_candidate(
        operations=manifest["operations"],
        behaviours=rows(SOURCE / "operation-runtime.db", "behaviour_observations"),
        topologies=rows(SOURCE / "operation-runtime.db", "topology_revisions"),
        detector_results=rows(SOURCE / "operation-runtime.db", "detector_results"),
        subject_candidate_count=count, subject_candidate_ids_digest=digest,
    )
    # Pure replay before any publication.
    replay = project_psi0g_operation_candidate(
        operations=manifest["operations"],
        behaviours=rows(SOURCE / "operation-runtime.db", "behaviour_observations"),
        topologies=rows(SOURCE / "operation-runtime.db", "topology_revisions"),
        detector_results=rows(SOURCE / "operation-runtime.db", "detector_results"),
        subject_candidate_count=count, subject_candidate_ids_digest=digest,
    )
    if replay.payload != result.payload or replay.projection_digest != result.projection_digest:
        raise RuntimeError("PSI0G_D1_REPLAY_MISMATCH")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    try:
        (staging / "projection.json").write_bytes(result.payload)
        published = {
            "schema_version": "psi0g-d1.publication.v1", "status": "PASS",
            "source_manifest_sha256": sha256_file(manifest_path),
            "source_files": {name: expected[name] for name in sorted(expected)},
            "projection_sha256": result.projection_digest,
            "candidate_id": result.candidate_id,
            "operation_count": 2, "complete_operations": result.complete_operations,
            "disposition": None,
            "authority": {"proposed": False, "supported": False,
                "same_operation": False, "same_human": False,
                "publication": False, "monitoring": False, "activation": False},
        }
        (staging / "manifest.json").write_text(
            json.dumps(published, sort_keys=True, separators=(",", ":")) + "\n")
        os.replace(staging, output)
        return published
    except BaseException:
        for child in staging.iterdir():
            child.unlink()
        staging.rmdir()
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    print(json.dumps(run(args.output.resolve()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
