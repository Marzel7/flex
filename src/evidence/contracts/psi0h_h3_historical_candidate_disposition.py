"""PSI0H-H3 historical candidate-disposition qualification contract."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "psi0h-h3.historical-candidate-disposition.v1"
AUTHORITY = {
    "comparison": False, "candidate_generation": True, "candidate_disposition": False,
    "supported": False, "same_operation": False, "same_human": False,
    "alerting": False, "monitoring": False, "consumer": False, "policy": False,
    "ranking": False, "trading": False, "integration": False, "deployment": False,
    "activation": False,
}


class Psi0hH3HistoricalCandidateDispositionError(RuntimeError):
    pass


DISP_INSUFFICIENT = "insufficient_evidence"
DISP_REVIEW_REQUIRED = "review_required"
DISP_NON_AUTHORITATIVE_CANDIDATE = "non_authoritative_candidate"
DISP_CONTINUITY_FLAG = "continuity_candidate_supported_for_review"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _classify_disposition(relationship: str, candidate: Mapping[str, Any]) -> tuple[str, list[str], list[str], bool]:
    if relationship == "insufficient_evidence":
        return DISP_INSUFFICIENT, ["PSI0H_H3_NO_SHARED_BEHAVIOUR"], ["candidate_lacks_behavioural_overlap"], False
    if relationship == "shared_behaviour":
        return (
            DISP_REVIEW_REQUIRED,
            ["PSI0H_H3_SHARED_BEHAVIOUR_FOUND"],
            candidate.get("continuity_evidence", []),
            False,
        )
    if relationship == "possible_operational_family":
        return (
            DISP_NON_AUTHORITATIVE_CANDIDATE,
            ["PSI0H_H3_POSSIBLE_OPERATIONAL_FAMILY"],
            candidate.get("continuity_evidence", []),
            False,
        )
    if relationship == "evidence_of_continuity":
        return (
            DISP_CONTINUITY_FLAG,
            ["PSI0H_H3_CONTINUITY_EVIDENCE_FOUND"],
            candidate.get("continuity_evidence", []),
            False,
        )
    raise Psi0hH3HistoricalCandidateDispositionError(f"PSI0H_H3_RELATIONSHIP_UNKNOWN:{relationship}")


def qualify_historical_candidate_disposition(*, h2_artifact: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(h2_artifact, Mapping):
        raise Psi0hH3HistoricalCandidateDispositionError("PSI0H_H3_ARTIFACT_INVALID")
    if h2_artifact.get("schema_version") != "psi0h-h2.historical-candidate-generation.v1":
        raise Psi0hH3HistoricalCandidateDispositionError("PSI0H_H3_BOUNDARY_BINDING_INVALID")

    candidates = h2_artifact.get("candidate_rows")
    if not isinstance(candidates, list):
        raise Psi0hH3HistoricalCandidateDispositionError("PSI0H_H3_CANDIDATE_ROWS_INVALID")

    reviewed = []
    disposition_counts = {
        DISP_INSUFFICIENT: 0,
        DISP_REVIEW_REQUIRED: 0,
        DISP_NON_AUTHORITATIVE_CANDIDATE: 0,
        DISP_CONTINUITY_FLAG: 0,
    }
    for row in candidates:
        if not isinstance(row, Mapping):
            raise Psi0hH3HistoricalCandidateDispositionError("PSI0H_H3_CANDIDATE_ROW_INVALID")
        candidate_id = str(row.get("continuity_candidate_id") or "").strip()
        relation = row.get("relationship")
        op_ids = row.get("operation_ids")
        if not candidate_id or not isinstance(op_ids, list) or len(op_ids) != 2:
            raise Psi0hH3HistoricalCandidateDispositionError("PSI0H_H3_CANDIDATE_ROW_INVALID")
        if len(set(op_ids)) != 2:
            raise Psi0hH3HistoricalCandidateDispositionError("PSI0H_H3_CANDIDATE_DUPLICATE_OPS")

        disposition, positive, negative, is_supported = _classify_disposition(relation, row)
        reviewed.append({
            "continuity_candidate_id": candidate_id,
            "operation_ids": sorted(op_ids),
            "human_disposition": {
                "provisional_disposition": disposition,
                "review_notes": positive,
                "negative_examples": negative,
                "supported_evidence_flag": is_supported,
                "same_human_claim": False,
                "same_operation_claim": False,
            },
            "identity_guarded": bool(row.get("identity_guarded", True)),
            "relationship": relation,
        })
        disposition_counts[disposition] += 1

    reviewed.sort(key=lambda row: row["continuity_candidate_id"])
    result = {
        "schema_version": SCHEMA_VERSION,
        "milestone": "PSI0H-H3",
        "status": "PASS" if reviewed else "HOLD",
        "verdict": "H3_HISTORICAL_CANDIDATE_DISPOSITION_PASS" if reviewed else "H3_HISTORICAL_CANDIDATE_DISPOSITION_HOLD_EMPTY",
        "candidate_count": len(candidates),
        "reviewed_count": len(reviewed),
        "reviewed_rows": reviewed,
        "disposition_counts": disposition_counts,
        "authority": dict(AUTHORITY),
        "scope": {
            "comparison": False,
            "candidate_disposition": True,
            "candidate_generation": False,
            "provider_or_rpc_calls": 0,
            "monitoring": False,
            "activation": False,
            "same_operation": False,
            "same_human": False,
        },
        "source": {
            "h2_artifact_digest": h2_artifact.get("artifact_digest"),
            "h2_status": h2_artifact.get("status"),
        },
    }
    result["artifact_digest"] = _digest(result)
    return result


def verify_historical_candidate_disposition(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH3HistoricalCandidateDispositionError("PSI0H_H3_RECORD_INVALID")
    for key in ("artifact_digest", "schema_version", "status", "reviewed_rows"):
        if key not in record:
            raise Psi0hH3HistoricalCandidateDispositionError("PSI0H_H3_RECORD_INVALID")
    artifact = str(record["artifact_digest"])
    if len(artifact) != 64 or any(ch not in "0123456789abcdef" for ch in artifact):
        raise Psi0hH3HistoricalCandidateDispositionError("PSI0H_H3_RECORD_DIGEST_INVALID")
    replay = dict(record)
    replay.pop("artifact_digest")
    expected = _digest(replay)
    if expected != artifact:
        raise Psi0hH3HistoricalCandidateDispositionError("PSI0H_H3_RECORD_DIGEST_MISMATCH")
    return True
