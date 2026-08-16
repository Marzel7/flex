"""PSI0D-B pure fixture-only assessment-summary consumer contract."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping, Tuple

from .production_shadow_assessment import QUERY_IDS


CONTRACT_VERSION = "psi0d-b.v1"
ENGINEERING_REVISION = "256e9e4cad35a6334578efacb431fd403c4874b9"
PSI0D_A_DIGEST = "e19dc0179d292d80535e7aa453a98693d98bb2f0c07e59e7f559aa7d39cb2b76"
PSI0C_D_DIGEST = "7e8c4ce4c27aa0d235a39cfe49e4dad4443be0bfae0750d61188a73d2cfeffb2"
PSI0C_C_ASSESSMENT_IDENTITY = "b5fb2187ad569b422f1ebbc029b7c619300a153d81b547f178c58769377fc7c4"
PSI0C_C_BUNDLE_IDENTITY = "6db46939fdfec9a34a63268809609c9a30a1a7dd86f5504b3d063bd0e8045ac3"
PSI0C_B_DIGEST = "3f2d112ba18b190e7acdf9c0dd9ddf552258b7ed75295ccb7cc470a981cc70e1"
AUTHORITY_CLASS = "FIXTURE_ONLY_NON_AUTHORITATIVE_ASSESSMENT_SUMMARY_CONSUMER"
PROVENANCE_CLASS = "FROZEN_SYNTHETIC_ASSESSMENT_SUMMARY"
REASON_CODES = (
    "PSI0C_B_ABSENCE_IS_NOT_NEGATIVE",
    "PSI0C_B_CONFLICT_PRESERVED_UNRESOLVED",
    "PSI0C_B_UNMATCHED_KEY_RECORDED",
)
SUMMARY_KEYS = {
    "schema_version", "input_lineage", "fixture_only", "provenance_class",
    "cohort_count", "membership", "unresolved_conflict_count",
    "orphan_unmatched_count", "reason_codes", "authority",
}
MEMBERSHIP_KEYS = {
    "row_count", "unique_mint_count", "cohort_present_count",
    "cohort_denominator", "coverage_numerator", "coverage_denominator",
    "duplicate_row_count", "unmatched_row_count",
}
AUTHORITY_KEYS = {"policy", "ranking", "integration", "activation"}


class AssessmentSummaryConsumerError(RuntimeError):
    """Named fail-closed PSI0D-B contract violation."""


@dataclass(frozen=True)
class AssessmentSummaryConsumerContract:
    contract_version: str
    engineering_revision: str
    psi0d_a_digest: str
    psi0c_d_digest: str
    psi0c_c_assessment_identity: str
    psi0c_c_bundle_identity: str
    psi0c_b_digest: str
    query_ids: Tuple[str, ...]
    accepted_summary_keys: Tuple[str, ...]
    accepted_membership_keys: Tuple[str, ...]
    accepted_reason_codes: Tuple[str, ...]
    authority_class: str
    fixture_only: bool
    default_off: bool
    performs_io: bool
    thresholds_allowed: bool
    negative_inference_allowed: bool
    duplicate_collapse_allowed: bool
    conflict_resolution_allowed: bool
    grants_policy_authority: bool
    grants_ranking_authority: bool
    grants_integration_authority: bool
    grants_deployment_authority: bool
    grants_activation_authority: bool
    contract_digest: str


@dataclass(frozen=True)
class AssessmentSummaryProjection:
    canonical_projection: bytes
    input_digest: str
    projection_digest: str
    contract_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _lineage() -> dict[str, str]:
    return {
        "psi0d_a_digest": PSI0D_A_DIGEST,
        "psi0c_d_digest": PSI0C_D_DIGEST,
        "psi0c_c_assessment_identity": PSI0C_C_ASSESSMENT_IDENTITY,
        "psi0c_c_bundle_identity": PSI0C_C_BUNDLE_IDENTITY,
        "psi0c_b_digest": PSI0C_B_DIGEST,
    }


def build_assessment_summary_consumer_contract() -> AssessmentSummaryConsumerContract:
    body = {
        "contract_version": CONTRACT_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0d_a_digest": PSI0D_A_DIGEST,
        "psi0c_d_digest": PSI0C_D_DIGEST,
        "psi0c_c_assessment_identity": PSI0C_C_ASSESSMENT_IDENTITY,
        "psi0c_c_bundle_identity": PSI0C_C_BUNDLE_IDENTITY,
        "psi0c_b_digest": PSI0C_B_DIGEST,
        "query_ids": QUERY_IDS,
        "accepted_summary_keys": tuple(sorted(SUMMARY_KEYS)),
        "accepted_membership_keys": tuple(sorted(MEMBERSHIP_KEYS)),
        "accepted_reason_codes": REASON_CODES,
        "authority_class": AUTHORITY_CLASS,
        "fixture_only": True,
        "default_off": True,
        "performs_io": False,
        "thresholds_allowed": False,
        "negative_inference_allowed": False,
        "duplicate_collapse_allowed": False,
        "conflict_resolution_allowed": False,
        "grants_policy_authority": False,
        "grants_ranking_authority": False,
        "grants_integration_authority": False,
        "grants_deployment_authority": False,
        "grants_activation_authority": False,
    }
    serial = {key: list(value) if isinstance(value, tuple) else value for key, value in body.items()}
    return AssessmentSummaryConsumerContract(**body, contract_digest=_digest(serial))


def verify_assessment_summary_consumer_contract(contract: AssessmentSummaryConsumerContract) -> bool:
    if contract != build_assessment_summary_consumer_contract():
        raise AssessmentSummaryConsumerError("PSI0D_B_CONTRACT_REPLAY_MISMATCH")
    forbidden = (
        contract.performs_io, contract.thresholds_allowed, contract.negative_inference_allowed,
        contract.duplicate_collapse_allowed, contract.conflict_resolution_allowed,
        contract.grants_policy_authority, contract.grants_ranking_authority,
        contract.grants_integration_authority, contract.grants_deployment_authority,
        contract.grants_activation_authority,
    )
    if not contract.fixture_only or not contract.default_off or any(forbidden):
        raise AssessmentSummaryConsumerError("PSI0D_B_AUTHORITY_DRIFT")
    return True


def _count(value: object, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AssessmentSummaryConsumerError(reason)
    return value


def _normalize_summary(summary: object) -> dict:
    if not isinstance(summary, Mapping) or set(summary) != SUMMARY_KEYS:
        raise AssessmentSummaryConsumerError("PSI0D_B_UNKNOWN_SUMMARY_SCHEMA")
    if summary["schema_version"] != "psi0d-b.synthetic-summary.v1":
        raise AssessmentSummaryConsumerError("PSI0D_B_SUMMARY_VERSION_DRIFT")
    if dict(summary["input_lineage"]) != _lineage():
        raise AssessmentSummaryConsumerError("PSI0D_B_STALE_OR_ALTERED_LINEAGE")
    if summary["fixture_only"] is not True or summary["provenance_class"] != PROVENANCE_CLASS:
        raise AssessmentSummaryConsumerError("PSI0D_B_NON_FIXTURE_PROVENANCE")
    authority = summary["authority"]
    if not isinstance(authority, Mapping) or set(authority) != AUTHORITY_KEYS or any(authority.values()):
        raise AssessmentSummaryConsumerError("PSI0D_B_AUTHORITY_DRIFT")
    cohort = _count(summary["cohort_count"], "PSI0D_B_INVALID_COHORT_DENOMINATOR")
    if cohort < 1:
        raise AssessmentSummaryConsumerError("PSI0D_B_INVALID_COHORT_DENOMINATOR")
    membership = summary["membership"]
    if not isinstance(membership, Mapping) or set(membership) != set(QUERY_IDS):
        raise AssessmentSummaryConsumerError("PSI0D_B_QUERY_IDENTITY_DRIFT")
    normalized_membership = {}
    unmatched_total = 0
    for query_id in QUERY_IDS:
        item = membership[query_id]
        if not isinstance(item, Mapping) or set(item) != MEMBERSHIP_KEYS:
            raise AssessmentSummaryConsumerError("PSI0D_B_UNKNOWN_MEMBERSHIP_SCHEMA")
        values = {key: _count(item[key], "PSI0D_B_INVALID_ACCOUNTING") for key in MEMBERSHIP_KEYS}
        if (values["cohort_denominator"] != cohort or values["coverage_denominator"] != cohort or
                values["cohort_present_count"] != values["coverage_numerator"] or
                values["coverage_numerator"] > min(cohort, values["unique_mint_count"]) or
                values["unique_mint_count"] > values["row_count"] or
                values["duplicate_row_count"] != values["row_count"] - values["unique_mint_count"] or
                values["unmatched_row_count"] > values["row_count"]):
            raise AssessmentSummaryConsumerError("PSI0D_B_INCONSISTENT_ACCOUNTING")
        unmatched_total += values["unmatched_row_count"]
        normalized_membership[query_id] = {key: values[key] for key in sorted(MEMBERSHIP_KEYS)}
    conflicts = _count(summary["unresolved_conflict_count"], "PSI0D_B_INVALID_ACCOUNTING")
    unmatched = _count(summary["orphan_unmatched_count"], "PSI0D_B_INVALID_ACCOUNTING")
    if unmatched != unmatched_total:
        raise AssessmentSummaryConsumerError("PSI0D_B_INCONSISTENT_UNMATCHED_ACCOUNTING")
    reasons = summary["reason_codes"]
    if (not isinstance(reasons, (list, tuple)) or any(not isinstance(item, str) for item in reasons) or
            len(set(reasons)) != len(reasons) or not set(reasons).issubset(REASON_CODES) or
            "PSI0C_B_ABSENCE_IS_NOT_NEGATIVE" not in reasons):
        raise AssessmentSummaryConsumerError("PSI0D_B_REASON_CODE_DRIFT")
    if conflicts and "PSI0C_B_CONFLICT_PRESERVED_UNRESOLVED" not in reasons:
        raise AssessmentSummaryConsumerError("PSI0D_B_CONFLICT_REASON_MISSING")
    if unmatched and "PSI0C_B_UNMATCHED_KEY_RECORDED" not in reasons:
        raise AssessmentSummaryConsumerError("PSI0D_B_UNMATCHED_REASON_MISSING")
    return {
        "schema_version": summary["schema_version"],
        "input_lineage": _lineage(),
        "fixture_only": True,
        "provenance_class": PROVENANCE_CLASS,
        "cohort_count": cohort,
        "membership": normalized_membership,
        "unresolved_conflict_count": conflicts,
        "orphan_unmatched_count": unmatched,
        "reason_codes": sorted(reasons),
        "authority": {key: False for key in sorted(AUTHORITY_KEYS)},
    }


def project_fixture_assessment_summary(
    contract: AssessmentSummaryConsumerContract,
    summary: object,
) -> AssessmentSummaryProjection:
    verify_assessment_summary_consumer_contract(contract)
    normalized = _normalize_summary(summary)
    surfaces = {}
    for query_id in QUERY_IDS:
        item = normalized["membership"][query_id]
        surfaces[query_id] = {
            "row_count": item["row_count"],
            "unique_mint_count": item["unique_mint_count"],
            "coverage_numerator": item["coverage_numerator"],
            "coverage_denominator": item["coverage_denominator"],
            "duplicate_row_count": item["duplicate_row_count"],
            "unmatched_row_count": item["unmatched_row_count"],
            "missingness_semantics": "ABSENT_NOT_NEGATIVE",
        }
    projection = {
        "schema_version": "psi0d-b.descriptive-projection.v1",
        "contract_digest": contract.contract_digest,
        "input_lineage": _lineage(),
        "fixture_only": True,
        "default_off": True,
        "provenance_class": PROVENANCE_CLASS,
        "cohort_count": normalized["cohort_count"],
        "surfaces": surfaces,
        "unresolved_conflict_count": normalized["unresolved_conflict_count"],
        "orphan_unmatched_count": normalized["orphan_unmatched_count"],
        "reason_codes": normalized["reason_codes"],
        "interpretation": {
            "threshold_applied": False,
            "negative_outcome_inferred": False,
            "duplicates_collapsed": False,
            "conflicts_resolved": False,
            "entities_ranked_or_selected": False,
        },
        "authority": {
            "policy": False, "ranking": False, "integration": False,
            "deployment": False, "activation": False,
        },
    }
    payload = _canonical(projection)
    return AssessmentSummaryProjection(
        canonical_projection=payload,
        input_digest=sha256(_canonical(normalized)).hexdigest(),
        projection_digest=sha256(payload).hexdigest(),
        contract_digest=contract.contract_digest,
    )


def verify_assessment_summary_projection(
    contract: AssessmentSummaryConsumerContract,
    summary: object,
    projection: AssessmentSummaryProjection,
) -> bool:
    if projection != project_fixture_assessment_summary(contract, summary):
        raise AssessmentSummaryConsumerError("PSI0D_B_PROJECTION_REPLAY_MISMATCH")
    return True
