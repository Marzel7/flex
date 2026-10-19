"""PSI0H-H1 immutable historical discovery operation eligibility contract."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json
from typing import Any, Mapping


SCHEMA_VERSION = "psi0h-h1.historical-discovery-eligibility.v1"
DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs/audits/psi0g_runs/psi0g-b-retained-derivation-20260817-01/manifest.json"
)
MAX_OPERATIONS = 2000
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


class Psi0hH1HistoricalDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class _OperationEligibility:
    operation_id: str
    source_path: str
    evidence_count: int
    primitive_count: int
    supporting_candidate_count: int
    source_access: str
    source_identity: dict[str, int]
    reasons: tuple[str, ...]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(_canonical(value).rstrip(b"\n")).hexdigest()


def _sha256(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validate_identity(source_identity: Mapping[str, object]) -> bool:
    required = ("device", "inode", "size_bytes", "mtime_ns")
    if set(required) - set(source_identity):
        return False
    return all(isinstance(source_identity[key], int) for key in required)


def _extract_operation_rows(manifest: Mapping[str, Any]) -> tuple[str, list[Mapping[str, Any]]]:
    if manifest.get("schema_version") == "1.0.0" and manifest.get("milestone") == "PSI0G-B":
        rows = manifest.get("operations")
        if not isinstance(rows, list):
            raise Psi0hH1HistoricalDiscoveryError("PSI0H_H1_NO_OPERATION_CONTEXT")
        return "legacy", rows

    if (
        manifest.get("schema_version") == "psi0h-h4.historical-operation-census.v1"
        and manifest.get("milestone") == "PSI0H-H4"
        and isinstance(manifest.get("discovered_populations"), list)
    ):
        return "h4", list(manifest["discovered_populations"])

    raise Psi0hH1HistoricalDiscoveryError("PSI0H_H1_GA_BINDING_INVALID")


def _required_string(value: object, *, allow_empty: bool = False) -> bool:
    return isinstance(value, str) and (allow_empty or value != "")


def build_historical_discovery_eligibility(
    *, manifest: Mapping[str, Any], maximum_operations: int = MAX_OPERATIONS,
) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise Psi0hH1HistoricalDiscoveryError("PSI0H_H1_MANIFEST_INVALID")
    if manifest.get("status") != "PASS":
        raise Psi0hH1HistoricalDiscoveryError("PSI0H_H1_GA_BINDING_INVALID")
    if not isinstance(maximum_operations, int) or not 1 <= maximum_operations <= MAX_OPERATIONS:
        raise Psi0hH1HistoricalDiscoveryError("PSI0H_H1_BOUND_INVALID")

    source_type, operations = _extract_operation_rows(manifest)
    if not operations:
        raise Psi0hH1HistoricalDiscoveryError("PSI0H_H1_NO_OPERATION_CONTEXT")

    rows: list[_OperationEligibility] = []
    for value in operations:
        if not isinstance(value, Mapping):
            raise Psi0hH1HistoricalDiscoveryError("PSI0H_H1_OPERATION_ROW_INVALID")

        if source_type == "legacy":
            operation_id = str(value.get("operation_key") or "").strip()
            source = value.get("source")
            if not isinstance(source, Mapping):
                raise Psi0hH1HistoricalDiscoveryError(f"PSI0H_H1_OPERATION_SOURCE_INVALID:{operation_id}")
            source_path = source.get("path")
            source_access = source.get("access")
            source_identity = source.get("identity")
        else:
            operation_id = str(value.get("operation_id") or "").strip()
            source_path = value.get("source_path")
            source_access = value.get("source_access")
            source_identity = value.get("source_identity")
            if source_access is None:
                source_access = ""
        if not operation_id:
            raise Psi0hH1HistoricalDiscoveryError("PSI0H_H1_OPERATION_ID_INVALID")
        if not _required_string(source_path):
            raise Psi0hH1HistoricalDiscoveryError(f"PSI0H_H1_SOURCE_PATH_INVALID:{operation_id}")
        if not _required_string(source_access, allow_empty=True):
            raise Psi0hH1HistoricalDiscoveryError(f"PSI0H_H1_SOURCE_ACCESS_INVALID:{operation_id}")
        if not isinstance(source_identity, Mapping) or not _validate_identity(source_identity):
            raise Psi0hH1HistoricalDiscoveryError(f"PSI0H_H1_SOURCE_IDENTITY_INVALID:{operation_id}")

        candidate_count = value.get("candidate_count", value.get("subject_count", 0))
        evidence_count = value.get("evidence_count", 0)
        primitive_count = value.get("primitive_count")
        if source_type == "h4" and primitive_count is None:
            primitive_count = value.get("primitive_reference_count")
        if primitive_count is None:
            primitive_refs = value.get("primitive_refs")
            primitive_count = len(primitive_refs) if isinstance(primitive_refs, list) else 0
        if not isinstance(candidate_count, int) or candidate_count < 0:
            raise Psi0hH1HistoricalDiscoveryError(f"PSI0H_H1_CANDIDATE_COUNT_INVALID:{operation_id}")
        if not isinstance(evidence_count, int):
            raise Psi0hH1HistoricalDiscoveryError(f"PSI0H_H1_EVIDENCE_COUNT_INVALID:{operation_id}")
        if not isinstance(primitive_count, int):
            raise Psi0hH1HistoricalDiscoveryError(f"PSI0H_H1_PRIMITIVE_COUNT_INVALID:{operation_id}")

        reasons: tuple[str, ...]
        if evidence_count <= 0:
            reasons = ("EVIDENCE_COUNT_EMPTY",)
        elif primitive_count <= 0:
            reasons = ("PRIMITIVE_COUNT_EMPTY",)
        elif candidate_count <= 0:
            reasons = ("CANDIDATE_COUNT_ZERO",)
        else:
            reasons = ()

        rows.append(
            _OperationEligibility(
                operation_id=operation_id,
                source_path=str(source_path),
                evidence_count=evidence_count,
                primitive_count=primitive_count,
                supporting_candidate_count=candidate_count,
                source_access=str(source_access),
                source_identity={k: int(v) for k, v in source_identity.items() if k in {"device", "inode", "size_bytes", "mtime_ns"}},
                reasons=reasons,
            ),
        )

    rows.sort(key=lambda row: row.operation_id)
    if len(rows) > maximum_operations:
        rows = rows[:maximum_operations]

    eligible_rows = []
    ineligible_rows = []
    for row in rows:
        candidate = {
            "operation_id": row.operation_id,
            "source_path": row.source_path,
            "source_access": row.source_access,
            "evidence_count": row.evidence_count,
            "primitive_count": row.primitive_count,
            "supporting_candidate_count": row.supporting_candidate_count,
            "source_identity": row.source_identity,
        }
        if row.reasons:
            ineligible_rows.append(dict(candidate, reasons=list(row.reasons)))
        else:
            eligible_rows.append(candidate)

    status = "PASS" if eligible_rows else "HOLD"
    result = {
        "schema_version": SCHEMA_VERSION,
        "milestone": "PSI0H-H1",
        "status": status,
        "verdict": "H1_HISTORICAL_DISCOVERY_ELIGIBLE" if status == "PASS" else "H1_HISTORICAL_DISCOVERY_NO_ELIGIBLE_OPERATIONS",
        "required_scope": {
            "producer": "RETENTION_BASED",
            "observation_only": True,
            "comparison": False,
            "candidate_generation": False,
            "provider_or_rpc_calls": 0,
        },
        "maximum_operations": maximum_operations,
        "operation_count": len(rows),
        "eligible_count": len(eligible_rows),
        "ineligible_count": len(ineligible_rows),
        "eligible_operations": eligible_rows,
        "ineligible_operations": ineligible_rows,
        "source": {
            "schema_version": manifest.get("schema_version"),
            "milestone": manifest.get("milestone"),
            "status": manifest.get("status"),
            "run_id": manifest.get("run_id"),
        },
        "manifest_files": manifest.get("files", {}),
        "manifest_digest": _sha256(manifest),
        "manifest_source_path": manifest.get("source", {}).get("path") if isinstance(manifest.get("source"), Mapping) else None,
        "authority": dict(AUTHORITY),
        "scope": {
            "source_read": True,
            "provider_access": False,
            "comparison": False,
            "monitoring": False,
            "activation": False,
        },
    }
    result["artifact_digest"] = _digest(result)
    return result


def verify_historical_discovery(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH1HistoricalDiscoveryError("PSI0H_H1_RECORD_INVALID")
    required = ("artifact_digest", "schema_version", "status", "eligible_operations", "ineligible_operations")
    if not all(k in record for k in required):
        raise Psi0hH1HistoricalDiscoveryError("PSI0H_H1_RECORD_INVALID")
    digest = str(record["artifact_digest"])
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise Psi0hH1HistoricalDiscoveryError("PSI0H_H1_RECORD_DIGEST_INVALID")

    replay = dict(record)
    replay.pop("artifact_digest")
    expected = _digest(replay)
    if expected != digest:
        raise Psi0hH1HistoricalDiscoveryError("PSI0H_H1_RECORD_DIGEST_MISMATCH")
    return True
