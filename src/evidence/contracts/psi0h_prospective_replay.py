"""PSI0H-A shadow-only historical replay against a real PSI0F surface."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "psi0h-a.prospective-historical-replay.v1"
AUTHORITY = {
    "candidate_disposition": False, "supported": False, "same_operation": False,
    "same_human": False, "alerting": False, "monitoring": False, "consumer": False,
    "policy": False, "ranking": False, "trading": False, "integration": False,
    "deployment": False, "activation": False,
}


class Psi0hProspectiveReplayError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(_canonical(value).rstrip(b"\n")).hexdigest()


def _payload_digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _texts(value: object, code: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) or not item for item in value):
        raise Psi0hProspectiveReplayError(f"PSI0H_A_{code}")
    result = sorted(value)
    if len(result) != len(set(result)):
        raise Psi0hProspectiveReplayError(f"PSI0H_A_{code}")
    return result


def replay_prospective_observations(
    surface: Mapping[str, Any], baseline: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if (surface.get("fixture_only") is not False or
            surface.get("provenance_class") != "RETAINED_REAL_KNOWN_BEHAVIOUR_OPERATIONAL_SURFACE" or
            surface.get("consumer_enabled") is not False or surface.get("default_off") is not True or
            any(surface.get("authority", {}).values()) or any(surface.get("interpretation", {}).values())):
        raise Psi0hProspectiveReplayError("PSI0H_A_SURFACE_AUTHORITY_OR_PROVENANCE_INVALID")
    roles = surface.get("operational_roles")
    if not isinstance(roles, Mapping) or not roles:
        raise Psi0hProspectiveReplayError("PSI0H_A_SURFACE_ROLE_INVALID")
    cutoff = baseline.get("evidence_cutoff")
    if not isinstance(cutoff, int) or cutoff < 0:
        raise Psi0hProspectiveReplayError("PSI0H_A_BASELINE_CUTOFF_INVALID")
    baseline_observations = set(_texts(baseline.get("observation_ids"), "BASELINE_OBSERVATIONS_INVALID"))
    baseline_evidence = set(_texts(baseline.get("evidence_ids"), "BASELINE_EVIDENCE_INVALID"))
    baseline_primitives = set(_texts(baseline.get("primitive_ids"), "BASELINE_PRIMITIVES_INVALID"))
    if not isinstance(observations, (list, tuple)) or not observations:
        raise Psi0hProspectiveReplayError("PSI0H_A_OBSERVATIONS_INVALID")

    blockers: list[str] = []
    normalized = []
    seen = set()
    for value in observations:
        if not isinstance(value, Mapping):
            raise Psi0hProspectiveReplayError("PSI0H_A_OBSERVATION_INVALID")
        row = dict(value)
        observation_id = row.get("observation_id")
        window = row.get("observation_window")
        captured_at = row.get("captured_at")
        if (not isinstance(observation_id, str) or not observation_id or observation_id in seen or
                not isinstance(window, Mapping) or not isinstance(window.get("start"), int) or
                not isinstance(window.get("end"), int) or window["start"] > window["end"] or
                not isinstance(captured_at, int) or captured_at < window["end"]):
            raise Psi0hProspectiveReplayError("PSI0H_A_OBSERVATION_INVALID")
        seen.add(observation_id)
        evidence = set(_texts(row.get("evidence_ids"), "OBSERVATION_EVIDENCE_INVALID"))
        primitives = set(_texts(row.get("primitive_ids"), "OBSERVATION_PRIMITIVES_INVALID"))
        edges = _texts(row.get("edge_features"), "OBSERVATION_FEATURES_INVALID")
        mechanisms = _texts(row.get("mechanism_features"), "OBSERVATION_FEATURES_INVALID")
        temporal = _texts(row.get("temporal_features"), "OBSERVATION_FEATURES_INVALID")
        if observation_id in baseline_observations:
            blockers.append(f"OBSERVATION_ALREADY_IN_BASELINE:{observation_id}")
        if window["start"] <= cutoff:
            blockers.append(f"OBSERVATION_NOT_STRICTLY_AFTER_CUTOFF:{observation_id}")
        evidence_overlap = len(evidence & baseline_evidence)
        primitive_overlap = len(primitives & baseline_primitives)
        if evidence_overlap:
            blockers.append(f"EVIDENCE_LEAKAGE:{observation_id}:{evidence_overlap}")
        if primitive_overlap:
            blockers.append(f"PRIMITIVE_LEAKAGE:{observation_id}:{primitive_overlap}")
        normalized.append({
            "observation_id": observation_id, "observation_window": dict(window),
            "captured_at": captured_at, "evidence_ids": sorted(evidence),
            "primitive_ids": sorted(primitives), "edge_features": edges,
            "mechanism_features": mechanisms, "temporal_features": temporal,
            "reviewed_label": row.get("reviewed_label"),
        })
    normalized.sort(key=lambda row: row["observation_id"])
    blockers = sorted(set(blockers))
    candidates = []
    latencies = []
    if not blockers:
        for row in normalized:
            latencies.append(row["captured_at"] - row["observation_window"]["end"])
            for role, role_surface in sorted(roles.items()):
                for nomination in role_surface.get("nominations", []):
                    required = {
                        "edge_features": sorted(nomination["shared_edge_features"]),
                        "mechanism_features": sorted(nomination["shared_mechanism_features"]),
                        "temporal_features": sorted(nomination["shared_temporal_features"]),
                    }
                    proven = all(set(required[key]).issubset(row[key]) for key in required)
                    if proven:
                        identity = {
                            "surface_digest": _payload_digest(surface), "observation_id": row["observation_id"],
                            "role": role, "required_features": required,
                        }
                        candidates.append({
                            "continuity_candidate_id": _digest(identity),
                            "observation_id": row["observation_id"], "role": role,
                            "comparison_state": "BEHAVIOURAL_CONTINUITY_CANDIDATE",
                            "required_features": required,
                            "candidate_disposition": None,
                            "same_operation_claim": False, "same_human_or_operator_claim": False,
                        })
    labeled = [row for row in normalized if row["reviewed_label"] in ("POSITIVE", "NEGATIVE")]
    candidate_ids = {row["observation_id"] for row in candidates}
    false_positives = sum(row["reviewed_label"] == "NEGATIVE" and row["observation_id"] in candidate_ids for row in labeled)
    negatives = sum(row["reviewed_label"] == "NEGATIVE" for row in labeled)
    output = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if not blockers else "HOLD",
        "surface_digest": _payload_digest(surface), "baseline_digest": _digest(baseline),
        "observation_count": len(normalized), "blockers": blockers,
        "continuity_candidates": candidates,
        "metrics": {
            "candidate_count": len(candidates), "reviewed_label_count": len(labeled),
            "reviewed_negative_count": negatives, "false_positive_count": false_positives,
            "false_positive_rate": false_positives / negatives if negatives else None,
            "max_logical_latency_seconds": max(latencies) if latencies else None,
            "mean_logical_latency_seconds": sum(latencies) / len(latencies) if latencies else None,
        },
        "shadow_only": True, "consumer_enabled": False, "default_off": True,
        "authority": dict(AUTHORITY),
    }
    output["replay_digest"] = _digest(output)
    return output
