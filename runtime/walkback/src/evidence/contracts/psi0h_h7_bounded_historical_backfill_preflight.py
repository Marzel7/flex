"""PSI0H-H7 bounded historical backfill capture preflight planning contract.

This boundary is read-only planning. It freezes source selection, reconstruction
requirements, and execution ceilings prior to any separate H8 run.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping

SCHEMA_VERSION = "psi0h-h7.bounded-historical-backfill-preflight.v1"

AUTHORITY = {
    "comparison": False,
    "candidate_generation": False,
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

VERDICT_READY_BOUNDED_BACKFILL = "READY_H7_BOUND_PLAN"
VERDICT_HOLD_NOT_READY = "H7_NOT_READY"

REQUIRED_EVIDENCE_FIELDS = (
    "evidence_rows",
    "primitive_rows",
    "operation_id",
    "subject_roles",
    "topology_fields",
    "mechanism_fields",
    "event_window",
)

BLOCKER_NOT_READY_FOR_BACKFILL = "NO_H6_BACKFILL_BOUNDARY"
SOURCE_CLASS_RECONSTRUCTABLE_OPERATION_SOURCE = "RECONSTRUCTABLE_OPERATION_SOURCE"
SOURCE_CLASS_LEGACY_CANDIDATE_ONLY = "LEGACY_CANDIDATE_ONLY"


class Psi0hH7BoundedHistoricalBackfillPreflightError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class _H6Candidate:
    source_path: str
    source_identity: Mapping[str, int]
    evidence_rows: int
    primitive_rows: int
    has_temporal_windows: bool
    has_topology_role_fields: bool
    provenance_links: int
    blocker_reasons: tuple[str, ...]


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "y"}
    return default


def _is_reconstructable(candidate: _H6Candidate) -> bool:
    if not candidate.source_path:
        return False
    if candidate.evidence_rows <= 0 and candidate.primitive_rows <= 0:
        return False
    if "NO_STABLE_OPERATION_BOUNDARY" in candidate.blocker_reasons:
        return False
    if "SOURCE_WAS_NEVER_RETAINED" in candidate.blocker_reasons:
        return False
    if "SOURCE_LACKS_OPERATION_RELEVANT_SCHEMA" in candidate.blocker_reasons:
        return False
    if "SOURCE_TABLE_SCAN_FAILED" in candidate.blocker_reasons:
        return False
    if candidate.primitive_rows <= 0:
        return False
    if not candidate.has_temporal_windows or not candidate.has_topology_role_fields:
        return False
    if candidate.provenance_links <= 0:
        return False
    return True


def _as_identity(block: Mapping[str, Any]) -> dict[str, int]:
    values = block.get("source_identity")
    if isinstance(values, Mapping):
        out: dict[str, int] = {}
        for key in ("device", "inode", "size_bytes", "mtime_ns"):
            raw = values.get(key)
            if isinstance(raw, int):
                out[key] = int(raw)
        return out
    return {}


def _pick_backfill_candidate_rows(h6_artifact: Mapping[str, Any]) -> list[_H6Candidate]:
    candidates: list[_H6Candidate] = []
    source_rows = h6_artifact.get("source_inventory_rows")
    if not isinstance(source_rows, list):
        return []

    for row in source_rows:
        if not isinstance(row, Mapping):
            continue
        evidence_rows = int(row.get("evidence_rows", 0) or 0)
        primitive_rows = int(row.get("primitive_rows", 0) or 0)
        if evidence_rows <= 0 and primitive_rows <= 0:
            continue
        blockers = tuple(str(x) for x in row.get("blocking_reasons", []) if isinstance(x, str))
        candidates.append(
            _H6Candidate(
                source_path=str(row.get("source_path", "")),
                source_identity=_as_identity(row),
                evidence_rows=evidence_rows,
                primitive_rows=primitive_rows,
                has_temporal_windows=_to_bool(row.get("has_temporal_windows"), False),
                has_topology_role_fields=_to_bool(row.get("has_topology_role_fields"), False),
                provenance_links=int(row.get("provenance_links", 0) or 0),
                blocker_reasons=tuple(sorted(blockers)),
            )
        )

    return sorted(candidates, key=lambda item: (item.source_path, item.evidence_rows, item.primitive_rows), reverse=False)


def _row_limit_from_blockers(blockers: tuple[str, ...]) -> int:
    # Conservative default keeps planning tight if evidence is sparse.
    if "SOURCE_WAS_NEVER_RETAINED" in blockers:
        return 0
    if "NO_STABLE_OPERATION_BOUNDARY" in blockers:
        return 200
    if "ADDRESS_LEVEL_MOTIFS_ONLY" in blockers:
        return 100
    return 300


def qualify_historical_backfill_preflight(
    *,
    h6_artifact: Mapping[str, Any],
    maximum_sources: int = 40,
    cohort_max_rows: int = 500,
    source_max_bytes: int = 256 * 1024 * 1024,
    max_event_gap_seconds: int = 60 * 60 * 24,
) -> dict[str, Any]:
    if not isinstance(h6_artifact, Mapping):
        raise Psi0hH7BoundedHistoricalBackfillPreflightError("PSI0H_H7_H6_ARTIFACT_INVALID")
    if h6_artifact.get("schema_version") != "psi0h-h6.historical-source-retention-availability.v1":
        raise Psi0hH7BoundedHistoricalBackfillPreflightError("PSI0H_H7_H6_BINDING_INVALID")
    if h6_artifact.get("verdict") != "READY_BOUNDED_BACKFILL":
        raise Psi0hH7BoundedHistoricalBackfillPreflightError("PSI0H_H7_H6_VERDICT_NOT_READY_BOUNDED_BACKFILL")
    if not isinstance(maximum_sources, int) or maximum_sources <= 0 or maximum_sources > 1000:
        raise Psi0hH7BoundedHistoricalBackfillPreflightError("PSI0H_H7_MAX_SOURCES_INVALID")
    if cohort_max_rows <= 0:
        raise Psi0hH7BoundedHistoricalBackfillPreflightError("PSI0H_H7_COHORT_MAX_ROWS_INVALID")
    if source_max_bytes <= 0:
        raise Psi0hH7BoundedHistoricalBackfillPreflightError("PSI0H_H7_SOURCE_MAX_BYTES_INVALID")
    if max_event_gap_seconds <= 0:
        raise Psi0hH7BoundedHistoricalBackfillPreflightError("PSI0H_H7_EVENT_GAP_SECONDS_INVALID")

    candidates = _pick_backfill_candidate_rows(h6_artifact)
    if not candidates:
        result = {
            "schema_version": SCHEMA_VERSION,
            "milestone": "PSI0H-H7",
            "status": "PASS",
            "verdict": VERDICT_HOLD_NOT_READY,
            "selection": {
                "h6_artifact_digest": h6_artifact.get("artifact_digest"),
                "h6_verdict": h6_artifact.get("verdict"),
            },
            "boundaries": {
                "max_sources": maximum_sources,
                "max_rows_per_source": cohort_max_rows,
                "max_bytes_per_source": source_max_bytes,
                "max_event_gap_seconds": max_event_gap_seconds,
            },
            "source_plan": {
                "candidate_count": 0,
                "candidate_sources": [],
                "legacy_candidate_sources": [],
                "reconstructable_source_count": 0,
                "legacy_source_count": 0,
                "source_boundaries": {
                    "max_candidate_rows": 0,
                    "max_candidate_provenance_links": 0,
                },
            },
            "operation_reconstruction_requirements": {
                "required_fields": list(REQUIRED_EVIDENCE_FIELDS),
                "required_topology_fields": [
                    "source",
                    "destination",
                    "wallet",
                    "creator",
                    "recipient",
                    "wallets",
                ],
                "required_role_fields": ["funder", "recipient", "signer", "activation_sender"],
                "required_mechanism_fields": ["operation_id", "mechanism", "roles", "event_types"],
                "event_time_semantics": {
                    "anchor": "window",
                    "strict_event_time": True,
                    "allow_observation_time_fallback": False,
                },
            },
            "provider_requirements": {
                "required": False,
                "max_requests": 0,
                "max_rows_per_request": 0,
                "pagination": False,
                "retries_allowed": 0,
                "failover_allowed": False,
            },
            "destination": {
                "isolation_required": True,
                "destination_mode": "isolated_backfill_output",
                "destination_root": "unknown",
                "reuse_previous_destination": False,
            },
            "replay_tamper_controls": {
                "require_source_snapshot": True,
                "require_output_digest": True,
                "require_preflight_digest_match": True,
                "require_row_and_identity_drift_checks": True,
                "stop_if_drift": True,
                "stop_if_source_reused_outside_plan": True,
            },
            "stop_conditions": [
                "No provider calls",
                "No monitoring/comparison/candidate-disposition/identity/policy/activation",
                "Stop if source identity drift",
                "Stop if row/byte ceilings exceeded",
                "Stop if no stable operation boundary remains",
            ],
            "authority": dict(AUTHORITY),
            "scope": {
                "comparison": False,
                "candidate_generation": False,
                "candidate_disposition": False,
                "monitoring": False,
                "policy": False,
                "activation": False,
            },
            "blockers": [BLOCKER_NOT_READY_FOR_BACKFILL],
            "next_action": {
                "decision": "H7_PLAN_INSUFFICIENT",
                "required_authorization": "NONE",
                "instruction": "H6 did not identify reconstructable candidate sources with partial evidence. Revisit H6 with broader source inventory or explicit historical source capture source-of-truth.",
            },
        }
        result["artifact_digest"] = _digest({k: v for k, v in result.items() if k != "artifact_digest"})
        return result

    # Cap scan list and apply coarse ceilings.
    candidates = candidates[: maximum_sources]

    source_plan_rows: list[dict[str, Any]] = []
    reconstructable_rows: list[dict[str, Any]] = []
    legacy_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        reconstructable = _is_reconstructable(candidate)
        source_row = {
            "source_path": candidate.source_path,
            "source_identity": candidate.source_identity,
            "evidence_rows": candidate.evidence_rows,
            "primitive_rows": candidate.primitive_rows,
            "has_temporal_windows": candidate.has_temporal_windows,
            "has_topology_role_fields": candidate.has_topology_role_fields,
            "provenance_links": candidate.provenance_links,
            "blocking_reasons": list(candidate.blocker_reasons),
            "row_reconstruction_ceiling": min(
                cohort_max_rows, _row_limit_from_blockers(candidate.blocker_reasons)
            ),
            "reconstructable": reconstructable,
            "source_class": (
                SOURCE_CLASS_RECONSTRUCTABLE_OPERATION_SOURCE
                if reconstructable
                else SOURCE_CLASS_LEGACY_CANDIDATE_ONLY
            ),
        }
        source_plan_rows.append(source_row)
        if reconstructable:
            reconstructable_rows.append(source_row)
        else:
            legacy_rows.append(source_row)

    if not reconstructable_rows:
        result = {
            "schema_version": SCHEMA_VERSION,
            "milestone": "PSI0H-H7",
            "status": "PASS",
            "verdict": VERDICT_HOLD_NOT_READY,
            "selection": {
                "h6_artifact_digest": h6_artifact.get("artifact_digest"),
                "h6_verdict": h6_artifact.get("verdict"),
                "h6_status": h6_artifact.get("status"),
            },
            "boundaries": {
                "max_sources": maximum_sources,
                "max_rows_per_source": 0,
                "max_bytes_per_source": source_max_bytes,
                "max_event_gap_seconds": max_event_gap_seconds,
                "max_reconstruction_windows": max(1, len(source_plan_rows)),
                "max_total_rows": 0,
            },
            "source_plan": {
                "candidate_count": len(source_plan_rows),
                "candidate_sources": [],
                "legacy_candidate_sources": legacy_rows,
                "reconstructable_source_count": 0,
                "legacy_source_count": len(legacy_rows),
                "source_boundaries": {
                    "max_candidate_rows": 0,
                    "max_candidate_provenance_links": max((c.provenance_links for c in candidates), default=0),
                },
            },
            "operation_reconstruction_requirements": {
                "required_fields": list(REQUIRED_EVIDENCE_FIELDS),
                "required_topology_fields": [
                    "source",
                    "destination",
                    "wallet",
                    "creator",
                    "recipient",
                    "wallets",
                ],
                "required_role_fields": ["funder", "recipient", "signer", "activation_sender"],
                "required_mechanism_fields": ["operation_id", "mechanism", "roles", "event_types"],
                "event_time_semantics": {
                    "anchor": "window",
                    "strict_event_time": True,
                    "allow_observation_time_fallback": False,
                },
            },
            "provider_requirements": {
                "required": False,
                "max_requests": 0,
                "requests_per_source": 0,
                "pagination": False,
                "retries_allowed": 0,
                "failover_allowed": False,
            },
            "destination": {
                "isolation_required": True,
                "destination_mode": "isolated_backfill_output",
                "destination_root": "unknown",
                "reuse_previous_destination": False,
            },
            "replay_tamper_controls": {
                "require_source_snapshot": True,
                "require_output_digest": True,
                "require_preflight_digest_match": True,
                "require_row_and_identity_drift_checks": True,
                "stop_if_drift": True,
                "stop_if_source_reused_outside_plan": True,
            },
            "stop_conditions": [
                "No reconstructable sources in H6 source inventory",
                "No provider calls",
                "No monitoring/comparison/candidate-disposition/identity/policy/activation",
                "Stop if source identity or lineage drift is detected",
                "Stop if row/byte ceilings exceeded",
                "Stop if no event-time reconstruction can be emitted from selected set",
            ],
            "authority": dict(AUTHORITY),
            "scope": {
                "comparison": False,
                "candidate_generation": False,
                "candidate_disposition": False,
                "monitoring": False,
                "policy": False,
                "activation": False,
            },
            "blockers": ["NO_RECONSTRUCTABLE_H7_SOURCE_CLASS"],
            "next_action": {
                "decision": "H7_PLAN_INSUFFICIENT",
                "required_authorization": "NONE",
                "instruction": "H7 identified only legacy-candidate-only sources. Re-run H6/H7 after expanding reconstructable historical evidence sources.",
            },
        }
        result["artifact_digest"] = _digest({k: v for k, v in result.items() if k != "artifact_digest"})
        return result

    max_per_source = max((_row_limit_from_blockers(c.blocker_reasons) for c in candidates), default=0)

    result = {
        "schema_version": SCHEMA_VERSION,
        "milestone": "PSI0H-H7",
        "status": "PASS",
        "verdict": VERDICT_READY_BOUNDED_BACKFILL,
        "selection": {
            "h6_artifact_digest": h6_artifact.get("artifact_digest"),
            "h6_verdict": h6_artifact.get("verdict"),
            "h6_status": h6_artifact.get("status"),
        },
        "boundaries": {
            "max_sources": maximum_sources,
            "max_rows_per_source": min(cohort_max_rows, max_per_source),
            "max_bytes_per_source": source_max_bytes,
            "max_event_gap_seconds": max_event_gap_seconds,
            "max_reconstruction_windows": max(1, len(candidates)),
            "max_total_rows": max(1, len(candidates)) * max(1, min(cohort_max_rows, max_per_source)),
        },
        "source_plan": {
            "candidate_count": len(source_plan_rows),
            "candidate_sources": reconstructable_rows,
            "legacy_candidate_sources": legacy_rows,
            "source_boundaries": {
                "max_candidate_rows": max_per_source,
                "max_candidate_provenance_links": max((c.provenance_links for c in candidates), default=0),
            },
            "reconstructable_source_count": len(reconstructable_rows),
            "legacy_source_count": len(legacy_rows),
        },
        "operation_reconstruction_requirements": {
            "required_fields": list(REQUIRED_EVIDENCE_FIELDS),
            "required_topology_fields": [
                "source",
                "destination",
                "wallet",
                "creator",
                "recipient",
                "wallets",
            ],
            "required_role_fields": ["funder", "recipient", "signer", "activation_sender"],
            "required_mechanism_fields": ["operation_id", "mechanism", "roles", "event_types"],
            "event_time_semantics": {
                "anchor": "window",
                "strict_event_time": True,
                "allow_observation_time_fallback": False,
            },
        },
        "provider_requirements": {
            "required": False,
            "max_requests": 0,
            "requests_per_source": 0,
            "pagination": False,
            "retries_allowed": 0,
            "failover_allowed": False,
        },
        "destination": {
            "isolation_required": True,
            "destination_mode": "isolated_backfill_output",
            "destination_root": "docs/audits/psi0h_h7_historical_backfill",
            "reuse_previous_destination": False,
            "candidate_paths_are_frozen": True,
        },
        "replay_tamper_controls": {
            "require_source_snapshot": True,
            "require_output_digest": True,
            "require_preflight_digest_match": True,
            "require_row_and_identity_drift_checks": True,
            "stop_if_drift": True,
            "stop_if_source_reused_outside_plan": True,
        },
        "stop_conditions": [
            "Stop after bounded source scan rows hit max_sources",
            "Stop after bounded rows hit per-source ceiling",
            "Stop after bounded bytes hit per-source ceiling",
            "Stop after bounded windows if event-time reconstruction exceeds limits",
            "Stop if source identity or lineage drift is detected",
            "Stop if any authority scope expands unexpectedly",
            "Stop if no event-time reconstruction can be emitted from any source",
            "Stop if no stable operation boundary remains and provider calls are required",
        ],
        "authority": dict(AUTHORITY),
        "scope": {
            "comparison": False,
            "candidate_generation": False,
            "candidate_disposition": False,
            "monitoring": False,
            "policy": False,
            "activation": False,
        },
        "blockers": ["READ_ONLY_PLANNING_ONLY"],
        "next_action": {
            "decision": "RUN_BOUNDED_HISTORICAL_BACKFILL_CAPTURE",
            "required_authorization": "PSI0H_H7_AUTH",
            "instruction": "Freeze this plan and only execute after separate H7 explicit authorization. Keep execution isolated, no monitoring or comparison, no candidate generation, no ranking/policy/activation paths.",
            "authority_required": {
                "provider_access": False,
                "service_changes": False,
                "monitoring": False,
                "comparison": False,
                "candidate_disposition": False,
                "ranking": False,
                "policy": False,
                "activation": False,
            },
            "execution_artifacts": {
                "plan_digest": "artifact_digest",
                "status": "PASS",
            },
        },
    }
    result["artifact_digest"] = _digest(result)
    return result


def verify_historical_backfill_preflight(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH7BoundedHistoricalBackfillPreflightError("PSI0H_H7_RECORD_INVALID")

    if record.get("schema_version") != SCHEMA_VERSION:
        raise Psi0hH7BoundedHistoricalBackfillPreflightError("PSI0H_H7_RECORD_SCHEMA_MISMATCH")

    artifact_digest = str(record.get("artifact_digest", ""))
    if len(artifact_digest) != 64 or any(ch not in "0123456789abcdef" for ch in artifact_digest):
        raise Psi0hH7BoundedHistoricalBackfillPreflightError("PSI0H_H7_RECORD_DIGEST_INVALID")

    for key in (
        "milestone",
        "status",
        "verdict",
        "selection",
        "boundaries",
        "source_plan",
        "operation_reconstruction_requirements",
        "provider_requirements",
        "destination",
        "next_action",
    ):
        if key not in record:
            raise Psi0hH7BoundedHistoricalBackfillPreflightError("PSI0H_H7_RECORD_FIELD_MISSING")

    replay = dict(record)
    replay.pop("artifact_digest", None)
    if _digest(replay) != artifact_digest:
        raise Psi0hH7BoundedHistoricalBackfillPreflightError("PSI0H_H7_RECORD_DIGEST_MISMATCH")

    sp = record.get("selection")
    if not isinstance(sp, Mapping):
        raise Psi0hH7BoundedHistoricalBackfillPreflightError("PSI0H_H7_RECORD_SELECTION_INVALID")
    return True
