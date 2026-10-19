"""PSI0H-E1 safe-local source and collector selection contract."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "psi0h-e1.source-collector-preflight.v1"
REQUIRED_FAMILIES = (
    "TransactionFact", "AccountParticipationFact", "InstructionFact", "LaunchFact",
)


class Psi0hSourceCollectorPreflightError(RuntimeError):
    pass


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def select_source_collector(*, candidates: Sequence[Mapping[str, Any]], maximum_units: int = 20) -> dict[str, Any]:
    if not 1 <= maximum_units <= 20 or not candidates:
        raise Psi0hSourceCollectorPreflightError("PSI0H_E1_BOUND_INVALID")
    evaluated = []
    selected = []
    for source in candidates:
        row = dict(source)
        required = {
            "candidate_id", "operation_neutral", "live_event_time", "fresh_signature",
            "exact_artifact", "supported_families", "existing_source_active",
            "requires_provider_requests", "requires_service_change", "code_identities",
        }
        if set(row) != required or not isinstance(row["supported_families"], (list, tuple)):
            raise Psi0hSourceCollectorPreflightError("PSI0H_E1_CANDIDATE_INVALID")
        identities = row["code_identities"]
        if (not isinstance(identities, Mapping) or not identities or
                any(not isinstance(path, str) or not isinstance(digest, str) or len(digest) != 64
                    for path, digest in identities.items())):
            raise Psi0hSourceCollectorPreflightError("PSI0H_E1_CODE_IDENTITY_INVALID")
        missing = sorted(set(REQUIRED_FAMILIES) - set(row["supported_families"]))
        reasons = []
        for key in ("operation_neutral", "live_event_time", "fresh_signature", "exact_artifact"):
            if row[key] is not True:
                reasons.append(key.upper() + "_ABSENT")
        if missing:
            reasons.append("REQUIRED_FAMILIES_MISSING")
        status = "ELIGIBLE" if not reasons else "INELIGIBLE"
        item = {"candidate_id": row["candidate_id"], "status": status,
                "missing_families": missing, "reasons": reasons,
                "requires_provider_requests": bool(row["requires_provider_requests"]),
                "requires_service_change": bool(row["requires_service_change"]),
                "existing_source_active": bool(row["existing_source_active"]),
                "collector_contract_digest": _digest({
                    "candidate_id": row["candidate_id"],
                    "code_identities": dict(sorted(identities.items())),
                    "supported_families": sorted(row["supported_families"]),
                })}
        evaluated.append(item)
        if status == "ELIGIBLE":
            selected.append(item)
    if len(selected) > 1:
        raise Psi0hSourceCollectorPreflightError("PSI0H_E1_AMBIGUOUS_SELECTION")
    choice = selected[0] if selected else None
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "READY" if choice else "HOLD",
        "maximum_units": maximum_units,
        "required_families": list(REQUIRED_FAMILIES),
        "evaluated": sorted(evaluated, key=lambda value: value["candidate_id"]),
        "selected": choice,
        "authorization_materialized": False, "capture_performed": False,
        "provider_requests": 0, "service_changes": 0,
    }
    result["preflight_digest"] = _digest(result)
    return result
