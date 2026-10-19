"""EB1.0A pure cross-stage evidence eligibility contract.

The contract consumes caller-supplied immutable EB0 bundle summaries.  It does
not open bundles, link entities, calculate scores, or make policy decisions.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Iterable, Mapping, Tuple


CONTRACT_VERSION = "eb1.0a.v1"
UPSTREAM_STAGES = ("EB0.1", "EB0.2", "EB0.3", "EB0.4")
AUTHORITY_LANES = {
    "EB0.1": "CANONICAL_BIRTH_VALUATION",
    "EB0.2": "CANONICAL_CREATOR_OUTCOME",
    "EB0.3": "SUPPLEMENTAL_MARKET_NON_AUTHORITATIVE",
    "EB0.4": "NOMINATION_NON_AUTHORITATIVE",
}
COMPLETENESS_STATES = frozenset({"COMPLETE", "PARTIAL", "NOT_OBSERVED"})
ELIGIBILITY_STATES = frozenset({
    "ELIGIBLE", "INELIGIBLE_MISSING", "INELIGIBLE_CONFLICTING", "NOT_APPLICABLE",
})
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_FIELDS = frozenset({
    "upstream_stage", "bundle_schema_version", "bundle_digest",
    "cohort_or_window_identity", "engineering_revision", "authority_lane",
    "applicable", "total_count", "observed_count", "missing_count",
    "conflicting_count", "completeness_state", "provenance_digest",
})
_FORBIDDEN_TERMS = (
    "wallet", "creator", "operation_id", "operator", "owner", "identity_link",
    "profile", "rate", "rank", "score", "policy", "profit", "cashflow",
    "attribution", "activation",
)


class CrossStageEligibilityError(ValueError):
    """Named fail-closed EB1.0A contract error."""


@dataclass(frozen=True)
class StageEligibility:
    upstream_stage: str
    bundle_schema_version: str
    bundle_digest: str
    cohort_or_window_identity: str
    engineering_revision: str
    authority_lane: str
    applicable: bool
    total_count: int
    observed_count: int
    missing_count: int
    conflicting_count: int
    completeness_state: str
    provenance_digest: str
    eligibility_state: str
    reason_codes: Tuple[str, ...]
    eligibility_id: str


@dataclass(frozen=True)
class CrossStageEligibilityProjection:
    contract_version: str
    stage_count: int
    eligibility_counts: Mapping[str, int]
    total_evidence_count: int
    observed_evidence_count: int
    missing_evidence_count: int
    conflicting_evidence_count: int
    stages: Tuple[StageEligibility, ...]
    projection_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _fail(code: str) -> None:
    raise CrossStageEligibilityError(f"EB1_0A_{code}")


def _text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        _fail(f"INVALID_{field.upper()}")
    return value.strip()


def _count(record: Mapping[str, object], field: str) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(f"INVALID_{field.upper()}")
    return value


def _project_stage(record: Mapping[str, object]) -> StageEligibility:
    if not isinstance(record, Mapping) or frozenset(record) != _FIELDS:
        _fail("SCHEMA_DRIFT")
    if any(any(term in str(key).lower() for term in _FORBIDDEN_TERMS) for key in record):
        _fail("FORBIDDEN_FIELD")
    stage = _text(record, "upstream_stage")
    if stage not in AUTHORITY_LANES:
        _fail("UNKNOWN_UPSTREAM_STAGE")
    schema = _text(record, "bundle_schema_version")
    bundle_digest = _text(record, "bundle_digest")
    cohort = _text(record, "cohort_or_window_identity")
    revision = _text(record, "engineering_revision")
    authority = _text(record, "authority_lane")
    provenance = _text(record, "provenance_digest")
    completeness = _text(record, "completeness_state")
    applicable = record.get("applicable")
    if not isinstance(applicable, bool):
        _fail("INVALID_APPLICABLE")
    if authority != AUTHORITY_LANES[stage]:
        _fail("AUTHORITY_LANE_PROMOTION_REJECTED")
    if completeness not in COMPLETENESS_STATES:
        _fail("UNKNOWN_COMPLETENESS_STATE")
    if not _DIGEST.fullmatch(bundle_digest) or not _DIGEST.fullmatch(provenance):
        _fail("INVALID_DIGEST")
    if not _REVISION.fullmatch(revision):
        _fail("INVALID_ENGINEERING_REVISION")
    total = _count(record, "total_count")
    observed = _count(record, "observed_count")
    missing = _count(record, "missing_count")
    conflicting = _count(record, "conflicting_count")
    if observed + missing != total or conflicting > observed:
        _fail("COUNT_RECONCILIATION_FAILED")
    reasons = []
    if not applicable:
        if any((total, observed, missing, conflicting)) or completeness != "NOT_OBSERVED":
            _fail("NOT_APPLICABLE_CONTRADICTION")
        state = "NOT_APPLICABLE"
        reasons.append("STAGE_NOT_APPLICABLE")
    elif conflicting:
        state = "INELIGIBLE_CONFLICTING"
        reasons.append("CONFLICTING_EVIDENCE")
        if missing:
            reasons.append("MISSING_EVIDENCE")
        if completeness != "COMPLETE":
            reasons.append(f"COMPLETENESS_{completeness}")
    elif total == 0 or observed == 0 or missing or completeness != "COMPLETE":
        state = "INELIGIBLE_MISSING"
        if total == 0:
            reasons.append("EMPTY_EVIDENCE_SCOPE")
        if observed == 0 and total:
            reasons.append("NO_OBSERVED_EVIDENCE")
        if missing:
            reasons.append("MISSING_EVIDENCE")
        if completeness != "COMPLETE":
            reasons.append(f"COMPLETENESS_{completeness}")
    else:
        state = "ELIGIBLE"
        reasons.append("COMPLETE_NONCONFLICTING_EVIDENCE")
    body = {
        "contract_version": CONTRACT_VERSION,
        "upstream_stage": stage,
        "bundle_schema_version": schema,
        "bundle_digest": bundle_digest,
        "cohort_or_window_identity": cohort,
        "engineering_revision": revision,
        "authority_lane": authority,
        "applicable": applicable,
        "total_count": total,
        "observed_count": observed,
        "missing_count": missing,
        "conflicting_count": conflicting,
        "completeness_state": completeness,
        "provenance_digest": provenance,
        "eligibility_state": state,
        "reason_codes": tuple(sorted(reasons)),
    }
    return StageEligibility(**{key: body[key] for key in body if key != "contract_version"}, eligibility_id=_digest(body))


def project_cross_stage_eligibility(
    records: Iterable[Mapping[str, object]],
) -> CrossStageEligibilityProjection:
    material = tuple(_project_stage(record) for record in records)
    if len(material) != len(UPSTREAM_STAGES):
        _fail("EXACT_STAGE_SET_REQUIRED")
    if {item.upstream_stage for item in material} != set(UPSTREAM_STAGES):
        _fail("EXACT_STAGE_SET_REQUIRED")
    ordered = tuple(sorted(material, key=lambda item: item.upstream_stage))
    counts = dict(sorted(Counter(item.eligibility_state for item in ordered).items()))
    body = {
        "contract_version": CONTRACT_VERSION,
        "stage_count": len(ordered),
        "eligibility_counts": counts,
        "total_evidence_count": sum(item.total_count for item in ordered),
        "observed_evidence_count": sum(item.observed_count for item in ordered),
        "missing_evidence_count": sum(item.missing_count for item in ordered),
        "conflicting_evidence_count": sum(item.conflicting_count for item in ordered),
        "stages": [asdict(item) for item in ordered],
    }
    return CrossStageEligibilityProjection(
        **{key: body[key] for key in body if key != "stages"},
        stages=ordered,
        projection_digest=_digest(body),
    )


def verify_cross_stage_eligibility(
    projection: CrossStageEligibilityProjection,
    records: Iterable[Mapping[str, object]],
) -> bool:
    if projection.contract_version != CONTRACT_VERSION:
        _fail("CONTRACT_VERSION_MISMATCH")
    if project_cross_stage_eligibility(records) != projection:
        _fail("REPLAY_MISMATCH")
    return True
