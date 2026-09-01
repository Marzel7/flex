#!/usr/bin/env python3
"""Apply PSI0H-A to the retained real observation set without live monitoring."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Mapping

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


def _observations_from_e5_cohort(artifact: Mapping[str, Any]) -> list[dict]:
    execution = artifact.get("execution")
    if not isinstance(execution, Mapping):
        raise RuntimeError("PSI0H_A_E5_EXECUTION_MISSING")
    qualification = execution.get("qualification")
    if not isinstance(qualification, Mapping):
        raise RuntimeError("PSI0H_A_E5_QUALIFICATION_MISSING")
    selected = qualification.get("selected")
    if not isinstance(selected, list):
        raise RuntimeError("PSI0H_A_E5_SELECTED_MISSING")

    observations = []
    for row in selected:
        primitive_id = row.get("primitive_id")
        primitive_type = row.get("primitive_type")
        window_start = row.get("window_start")
        window_end = row.get("window_end")
        generated_at = row.get("generated_at")
        evidence_ids = row.get("evidence_ids")
        if (not isinstance(primitive_id, str) or not primitive_id or
                not isinstance(primitive_type, str) or not primitive_type or
                not isinstance(window_start, int) or not isinstance(window_end, int) or
                window_start > window_end or not isinstance(generated_at, int) or
                generated_at < window_end or not isinstance(evidence_ids, list) or
                not evidence_ids or
                any(not isinstance(item, str) or not item for item in evidence_ids)):
            raise RuntimeError("PSI0H_A_E5_SELECTED_INVALID")
        mechanism_features = sorted({primitive_type})
        edge_features = []
        temporal_features = []
        if primitive_type == "LAUNCH_SIGNER":
            edge_features.append("CREATOR_SIGNED_LAUNCH")
        elif primitive_type == "SYSTEM_TRANSFER":
            edge_features.append("DIRECTED_VALUE_TRANSFER")
        elif primitive_type == "BEHAVIOURAL_TIMING":
            temporal_features.append("BEHAVIOURAL_TIMING_OBSERVED")
        observations.append({
            "observation_id": primitive_id,
            "observation_window": {"start": window_start, "end": window_end},
            "captured_at": generated_at,
            "evidence_ids": sorted(set(evidence_ids)),
            "primitive_ids": [primitive_id],
            "edge_features": edge_features,
            "mechanism_features": mechanism_features,
            "temporal_features": temporal_features,
            "reviewed_label": None,
        })
    return observations


def _load_e5_projection(artifact_path: Path) -> Mapping[str, Any]:
    data = json.loads(artifact_path.read_text())
    execution_status = data.get("execution_status")
    if execution_status not in ("COMPLETED", "HALT", "READY_FOR_AUTHORIZATION", "FAILURE"):
        raise RuntimeError("PSI0H_A_E5_BAD_EXECUTION_STATUS")
    return data


def run(*, cohort_artifact: Path | None = None) -> dict:
    source_manifest = json.loads((B2 / "manifest.json").read_text())
    source_manifest_digest = source_manifest["files"]["operation-runtime.db"]["sha256"]
    source_manifest_size = source_manifest["files"]["operation-runtime.db"]["size_bytes"]
    if cohort_artifact is None:
        database = B2 / "operation-runtime.db"
        if _sha(database) != source_manifest_digest or database.stat().st_size != source_manifest_size:
            raise RuntimeError("PSI0H_A_SOURCE_DRIFT")
        projection = json.loads(D5.read_text())
        surface = json.loads(D8.read_text())
        observations = _observations(database)
        source_path = B2 / "operation-runtime.db"
        source_identity = {
            "source_runtime_sha256": _sha(database),
            "b2_manifest_sha256": _sha(B2 / "manifest.json"),
            "source_path": str(database.relative_to(ROOT)),
        }
        source_cutoff = max(row["observation_window"]["end"] for row in observations)
        observations_source = {
            "source_manifest": source_manifest,
            "source_mode": "retained-b-replay",
            "source_path": str(source_path),
            "source_identity": source_identity,
        }
        source_runtime_digest = _sha(database)
    else:
        cohort_payload = _load_e5_projection(cohort_artifact)
        projection = json.loads(D5.read_text())
        surface = json.loads(D8.read_text())
        observations = _observations_from_e5_cohort(cohort_payload)
        source_identity = {
            "e5_artifact_sha256": _sha(cohort_artifact),
            "e5_execution_status": cohort_payload.get("execution_status"),
            "e5_source": cohort_payload.get("source_identity"),
        }
        source_cutoff = cohort_payload.get("execution", {}).get("qualification", {}).get("cutoff")
        if not isinstance(source_cutoff, int):
            raise RuntimeError("PSI0H_A_E5_CUTOFF_INVALID")
        observations_source = {
            "source_manifest": {
                "run_id": cohort_payload.get("run_id"),
                "source_id": cohort_payload.get("source_id"),
                "source_kind": cohort_payload.get("source_kind"),
            },
            "source_mode": "e5-real-cohort",
            "source_path": str(cohort_artifact),
            "source_identity": source_identity,
        }
    cutoff = source_cutoff
    baseline = {
        "evidence_cutoff": cutoff,
        "observation_ids": projection["candidate"]["supporting_behaviour_observation_ids"],
        "evidence_ids": projection["candidate"]["supporting_evidence_ids"],
        "primitive_ids": projection["candidate"]["supporting_primitive_ids"],
    }
    result = replay_prospective_observations(surface, baseline, observations)
    contract_replay_digest = result.pop("replay_digest")
    result["source"] = {
        "d8_surface_sha256": _sha(D8),
        "d5_projection_sha256": _sha(D5),
        "baseline_evidence_cutoff": cutoff,
        "source": observations_source,
    }
    result["source"].update(source_identity)
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
    parser.add_argument("--cohort-artifact", type=Path, default=None)
    args = parser.parse_args()
    result = run(cohort_artifact=args.cohort_artifact)
    payload = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    if args.output.exists():
        raise FileExistsError(f"PSI0H_A_OUTPUT_EXISTS:{args.output}")
    args.output.write_text(payload)
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
