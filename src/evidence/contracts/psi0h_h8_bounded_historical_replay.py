"""PSI0H-H8 replay boundary over a bounded historical backfill execution artifact.

This contract is a non-authoritative, observer-only bridge from H8 execution output
to the existing PSI0H-A replay contract. It accepts only preserved payloads from an
already authorized H8 run and preserves all strict failure semantics:

- no provider access
- no comparison/disposition authority
- no monitoring/service/configuration activation
- bounded evidence reconstruction from the existing artifact only
- deterministic, replay-verifiable output
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from .psi0h_prospective_replay import replay_prospective_observations

SCHEMA_VERSION = "psi0h-h8.bounded-historical-replay.v1"

AUTHORITY = {
    "candidate_disposition": False,
    "supported": False,
    "same_operation": False,
    "same_human": False,
    "alerting": False,
    "monitoring": False,
    "consumer": False,
    "policy": False,
    "ranking": False,
    "trading": False,
    "integration": False,
    "deployment": False,
    "activation": False,
}


class Psi0hH8BoundedReplayBoundaryError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.loads(handle.read())
    if not isinstance(payload, Mapping):
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_ARTIFACT_INVALID")
    return payload


def _coerce_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _coerce_list_of_str(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        return None
    deduped = sorted(set(value))
    return deduped


def _primitive_to_observation(row: Mapping[str, Any]) -> dict[str, Any] | None:
    primitive_id = row.get("primitive_id")
    primitive_type = row.get("primitive_type")
    window_start = _coerce_int(row.get("window_start"))
    window_end = _coerce_int(row.get("window_end"))
    generated_at = _coerce_int(row.get("generated_at"))
    payload = row.get("payload", {})
    payload_refs = payload.get("evidence_refs") if isinstance(payload, Mapping) else None
    evidence_ids = _coerce_list_of_str(payload_refs)
    missing_inputs_json = row.get("missing_inputs_json")
    if evidence_ids is None and isinstance(missing_inputs_json, str):
        try:
            missing = json.loads(missing_inputs_json)
        except json.JSONDecodeError:
            missing = []
        if isinstance(missing, list):
            evidence_ids = _coerce_list_of_str([str(item) for item in missing if str(item)])
    if not isinstance(primitive_id, str) or not primitive_id:
        return None
    if not isinstance(primitive_type, str) or not primitive_type:
        return None
    if window_start is None or window_end is None or generated_at is None:
        return None
    if window_start > window_end or generated_at < window_end:
        return None
    if evidence_ids is None:
        return None

    mechanism_features = [primitive_type]
    edge_features: list[str] = []
    temporal_features: list[str] = []
    if primitive_type == "LAUNCH_SIGNER":
        edge_features.append("CREATOR_SIGNED_LAUNCH")
    elif primitive_type == "SYSTEM_TRANSFER":
        edge_features.append("DIRECTED_VALUE_TRANSFER")
    elif primitive_type == "BEHAVIOURAL_TIMING":
        temporal_features.append("BEHAVIOURAL_TIMING_OBSERVED")

    return {
        "observation_id": primitive_id,
        "observation_window": {"start": window_start, "end": window_end},
        "captured_at": generated_at,
        "evidence_ids": evidence_ids,
        "primitive_ids": [str(primitive_id)],
        "edge_features": sorted(set(edge_features)),
        "mechanism_features": sorted(set(mechanism_features)),
        "temporal_features": sorted(set(temporal_features)),
        "reviewed_label": None,
    }


def qualify_h8_bounded_replay_boundary(
    *,
    h8_artifact: Mapping[str, Any],
    d8_surface: Mapping[str, Any],
    d5_projection: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(h8_artifact, Mapping):
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_H8_ARTIFACT_INVALID")
    if h8_artifact.get("schema_version") != "psi0h-h8.bounded-historical-backfill-execution.v1":
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_H8_SCHEMA_INVALID")
    if h8_artifact.get("status") not in {"PASS", "HOLD"}:
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_H8_STATUS_INVALID")
    if h8_artifact.get("milestone") != "PSI0H-H8":
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_H8_MILESTONE_INVALID")
    if not isinstance(d8_surface, Mapping):
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_D8_SURFACE_INVALID")
    if not str(d8_surface.get("schema_version", "")).startswith("psi0f-b."):
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_D8_SCHEMA_INVALID")
    if not isinstance(d5_projection, Mapping) or not isinstance(d5_projection.get("candidate"), Mapping):
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_D5_PROJECTION_INVALID")

    execution = h8_artifact.get("execution", {})
    if not isinstance(execution, Mapping):
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_EXECUTION_INVALID")
    primitive_rows = execution.get("primitive_rows", [])
    if not isinstance(primitive_rows, list):
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_PRIMITIVE_ROWS_INVALID")

    observations: list[dict[str, Any]] = []
    malformed = []
    for row in primitive_rows:
        if not isinstance(row, Mapping):
            malformed.append("PSI0H_H8_REPLAY_BOUNDARY_PRIMITIVE_ROW_INVALID")
            continue
        converted = _primitive_to_observation(row)
        if converted is None:
            malformed.append("PSI0H_H8_REPLAY_BOUNDARY_OBSERVATION_BUILD_FAILED")
            continue
        observations.append(converted)

    candidate = d5_projection["candidate"]
    baseline = {
        "evidence_cutoff": 0,
        "observation_ids": list(candidate.get("supporting_behaviour_observation_ids", [])),
        "evidence_ids": list(candidate.get("supporting_evidence_ids", [])),
        "primitive_ids": list(candidate.get("supporting_primitive_ids", [])),
    }

    if not observations:
        # Preserve deterministic artifact, even when replay cannot run.
        result = {
            "schema_version": SCHEMA_VERSION,
            "milestone": "PSI0H-H8",
            "status": "HOLD",
            "verdict": "H8_REPLAY_BOUNDARY_EMPTY_PRIMITIVE_POOL",
            "e8_artifact": h8_artifact.get("execution_status"),
            "observation_count": 0,
            "source_snapshot_count": len(execution.get("source_snapshots", [])) if isinstance(execution.get("source_snapshots"), list) else 0,
            "replay_blockers": sorted(set(malformed + ["H8_NO_REPLAYABLE_PRIMITIVES"])),
            "blockers": sorted(set(malformed + ["H8_NO_REPLAYABLE_PRIMITIVES"])),
            "authority": dict(AUTHORITY),
            "scope": {
                "source_read": True,
                "provider_access": False,
                "comparison": False,
                "monitoring": False,
                "candidate_generation": False,
                "candidate_disposition": False,
                "activation": False,
            },
            "output_bindings": {
                "h8_artifact": h8_artifact.get("output_digest_bindings", {}).get("artifact_path") or h8_artifact.get("h7_artifact"),
                "d5_projection_ready": bool(d5_projection),
                "d8_surface_ready": bool(d8_surface),
            },
        }
        result["artifact_digest"] = _digest(result)
        return result

    replay = replay_prospective_observations(d8_surface, baseline, observations)
    replay_digest = replay.pop("replay_digest")

    result = {
        "schema_version": SCHEMA_VERSION,
        "milestone": "PSI0H-H8",
        "status": replay["status"],
        "verdict": "H8_REPLAY_BOUNDARY_COMPLETE" if replay["status"] == "PASS" else "H8_REPLAY_BOUNDARY_HOLD",
        "replay_blockers": replay["blockers"],
        "observation_count": replay["observation_count"],
        "replay": replay,
        "scope": {
            "source_read": True,
            "provider_access": False,
            "comparison": False,
            "monitoring": False,
            "candidate_generation": False,
            "candidate_disposition": False,
            "activation": False,
        },
        "authority": dict(AUTHORITY),
        "source": {
            "h8_artifact_digest": h8_artifact.get("artifact_digest"),
            "h8_source_identity": h8_artifact.get("h7_binding", {}).get("h7_verdict"),
        },
        "contract_replay_digest": replay_digest,
    }
    result["artifact_digest"] = _digest(result)
    return result


def verify_h8_bounded_replay_boundary(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_RECORD_INVALID")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_RECORD_SCHEMA_INVALID")
    digest = str(record.get("artifact_digest", ""))
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_RECORD_DIGEST_INVALID")
    replay = dict(record)
    replay.pop("artifact_digest")
    if _digest(replay) != digest:
        raise Psi0hH8BoundedReplayBoundaryError("PSI0H_H8_REPLAY_BOUNDARY_RECORD_DIGEST_MISMATCH")
    return True
