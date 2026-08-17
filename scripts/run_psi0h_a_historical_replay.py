#!/usr/bin/env python3
"""Apply PSI0H-A to the retained real observation set without live monitoring."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_prospective_replay import replay_prospective_observations


ROOT = Path(__file__).resolve().parents[1]
D5 = ROOT / "docs/audits/psi0g_runs/psi0g-d5-real-provenance-retention-20260817-02/projection.json"
D8 = ROOT / "docs/audits/psi0g_runs/psi0g-d8-first-real-provenance-surface-20260817-01/surface.json"
B2 = ROOT / "docs/audits/psi0g_runs/psi0g-b-retained-derivation-20260817-01"
OUTPUT = ROOT / "docs/audits/psi0h_a_real_historical_replay.json"


def _sha(path: Path) -> str:
    value = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _observations(path: Path) -> list[dict]:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    try:
        rows = [json.loads(row[0]) for row in connection.execute(
            "SELECT payload_json FROM behaviour_observations ORDER BY contract_id,output_id"
        )]
    finally:
        connection.close()
    result = []
    for row in rows:
        primitives = sorted((row.get("measured_values", {}).get("by_primitive_type") or {}).keys())
        edges = []
        if "LAUNCH_SIGNER" in primitives:
            edges.append("CREATOR_SIGNED_LAUNCH")
        if "SYSTEM_TRANSFER" in primitives:
            edges.append("DIRECTED_VALUE_TRANSFER")
        temporal = ["BEHAVIOURAL_TIMING_OBSERVED"] if "BEHAVIOURAL_TIMING" in primitives else []
        result.append({
            "observation_id": row["observation_id"],
            "observation_window": row["observation_window"], "captured_at": row["generated_at"],
            "evidence_ids": row["evidence_refs"], "primitive_ids": row["primitive_refs"],
            "edge_features": edges, "mechanism_features": primitives,
            "temporal_features": temporal, "reviewed_label": None,
        })
    return result


def run() -> dict:
    source_manifest = json.loads((B2 / "manifest.json").read_text())
    database = B2 / "operation-runtime.db"
    expected = source_manifest["files"]["operation-runtime.db"]
    if _sha(database) != expected["sha256"] or database.stat().st_size != expected["size_bytes"]:
        raise RuntimeError("PSI0H_A_SOURCE_DRIFT")
    projection = json.loads(D5.read_text())
    surface = json.loads(D8.read_text())
    observations = _observations(database)
    cutoff = max(row["observation_window"]["end"] for row in observations)
    baseline = {
        "evidence_cutoff": cutoff,
        "observation_ids": projection["candidate"]["supporting_behaviour_observation_ids"],
        "evidence_ids": projection["candidate"]["supporting_evidence_ids"],
        "primitive_ids": projection["candidate"]["supporting_primitive_ids"],
    }
    result = replay_prospective_observations(surface, baseline, observations)
    contract_replay_digest = result.pop("replay_digest")
    result["source"] = {
        "d8_surface_sha256": _sha(D8), "d5_projection_sha256": _sha(D5),
        "b2_manifest_sha256": _sha(B2 / "manifest.json"),
        "operation_runtime_sha256": expected["sha256"],
        "baseline_evidence_cutoff": cutoff,
    }
    result["scope"] = {
        "historical_local_only": True, "production_observation": False,
        "provider_or_rpc_calls": 0, "services_accessed": 0,
        "alerts_emitted": 0, "external_actions": 0,
    }
    result["contract_replay_digest"] = contract_replay_digest
    result["artifact_digest"] = _digest(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = run()
    payload = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    if args.output.exists():
        raise FileExistsError(f"PSI0H_A_OUTPUT_EXISTS:{args.output}")
    args.output.write_text(payload)
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
