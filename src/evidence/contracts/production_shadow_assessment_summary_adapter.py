"""PSI0D-D adapter from immutable PSI0C assessment bytes to PSI0D-B."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Mapping, Tuple

from .production_shadow_assessment import (
    PSI0B_BUNDLE_DIGEST,
    PSI0B_G_DIGEST,
    PSI0C_A_DIGEST,
    QUERY_IDS,
    build_shadow_assessment_contract,
)
from .production_shadow_assessment_summary_consumer import (
    AUTHORITY_KEYS,
    MEMBERSHIP_KEYS,
    PSI0C_B_DIGEST,
    PSI0C_C_ASSESSMENT_IDENTITY,
    PSI0C_C_BUNDLE_IDENTITY,
    PSI0C_D_DIGEST,
    PSI0D_A_DIGEST,
    PRODUCTION_DERIVED_PROVENANCE_CLASS,
    REASON_CODES,
    AssessmentSummaryConsumerContract,
    AssessmentSummaryProjection,
    _normalize_summary,
    _project_normalized_summary,
    build_assessment_summary_consumer_contract,
    verify_assessment_summary_consumer_contract,
)


ADAPTER_VERSION = "psi0d-d.v1"
ENGINEERING_REVISION = "3843a0ffca345344f4d914a4ffff16fe12c7fda9"
PSI0D_C_DIGEST = "6ee6424e38734ed5624c17ceb20b5ff45d071fa0ba5b754fd0df546d669221b8"
PSI0D_B_DIGEST = "a9e368cceede689736ca234891394551bda098df3b51ebfebf2e72f32aeb51f6"
PSI0D_B_QUALIFICATION_DIGEST = "bb05f1b719a518776da4e5b6116ee42c2de32c5149820eec2bb583d4605d4fea"
EXPECTED_ASSESSMENT_DIGEST = PSI0C_C_ASSESSMENT_IDENTITY
EXPECTED_BUNDLE_DIGEST = PSI0C_C_BUNDLE_IDENTITY
SOURCE_PROVENANCE_CLASS = "PRODUCTION_DERIVED_IMMUTABLE_LOCAL_BUNDLE"
FILES = ("assessment.json", "contract.json", "hashes.json")
ASSESSMENT_KEYS = {
    "schema_version", "contract_digest", "input_lineage", "fixture_only",
    "provenance_class", "cohort_count", "membership", "missingness",
    "conflicts", "orphan_unmatched_accounting", "reason_codes", "authority",
    "assessment_digest",
}
SOURCE_MEMBERSHIP_KEYS = MEMBERSHIP_KEYS | {"unmatched_mints"}


class ImmutableAssessmentSummaryAdapterError(RuntimeError):
    """Named fail-closed PSI0D-D adapter violation."""


@dataclass(frozen=True)
class ImmutableAssessmentSummaryAdapterContract:
    adapter_version: str
    engineering_revision: str
    psi0d_c_digest: str
    psi0d_b_digest: str
    psi0d_b_qualification_digest: str
    psi0c_d_digest: str
    expected_assessment_digest: str
    expected_bundle_digest: str
    expected_files: Tuple[str, ...]
    source_provenance_class: str
    output_provenance_class: str
    retries_allowed: bool
    retains_source_values: bool
    grants_policy_authority: bool
    grants_ranking_authority: bool
    grants_integration_authority: bool
    grants_deployment_authority: bool
    grants_activation_authority: bool
    contract_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_immutable_assessment_summary_adapter_contract() -> ImmutableAssessmentSummaryAdapterContract:
    body = {
        "adapter_version": ADAPTER_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0d_c_digest": PSI0D_C_DIGEST,
        "psi0d_b_digest": PSI0D_B_DIGEST,
        "psi0d_b_qualification_digest": PSI0D_B_QUALIFICATION_DIGEST,
        "psi0c_d_digest": PSI0C_D_DIGEST,
        "expected_assessment_digest": EXPECTED_ASSESSMENT_DIGEST,
        "expected_bundle_digest": EXPECTED_BUNDLE_DIGEST,
        "expected_files": FILES,
        "source_provenance_class": SOURCE_PROVENANCE_CLASS,
        "output_provenance_class": PRODUCTION_DERIVED_PROVENANCE_CLASS,
        "retries_allowed": False,
        "retains_source_values": False,
        "grants_policy_authority": False,
        "grants_ranking_authority": False,
        "grants_integration_authority": False,
        "grants_deployment_authority": False,
        "grants_activation_authority": False,
    }
    serial = {key: list(value) if isinstance(value, tuple) else value for key, value in body.items()}
    return ImmutableAssessmentSummaryAdapterContract(**body, contract_digest=_digest(serial))


def verify_immutable_assessment_summary_adapter_contract(
    contract: ImmutableAssessmentSummaryAdapterContract,
) -> bool:
    if contract != build_immutable_assessment_summary_adapter_contract():
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_ADAPTER_CONTRACT_REPLAY_MISMATCH")
    if any((
        contract.retries_allowed, contract.retains_source_values,
        contract.grants_policy_authority, contract.grants_ranking_authority,
        contract.grants_integration_authority, contract.grants_deployment_authority,
        contract.grants_activation_authority,
    )):
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_ADAPTER_AUTHORITY_DRIFT")
    return True


def _parse_canonical(bundle_files: Mapping[str, bytes]) -> dict[str, object]:
    if tuple(sorted(bundle_files)) != FILES:
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_FILE_SET_MISMATCH")
    documents = {}
    for name in FILES:
        payload = bundle_files[name]
        if not isinstance(payload, bytes):
            raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_BYTES_REQUIRED")
        try:
            document = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_INVALID_JSON") from exc
        if payload != _canonical(document):
            raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_NONCANONICAL_JSON")
        documents[name] = document
    return documents


def _expected_assessment_contract_document() -> dict:
    document = asdict(build_shadow_assessment_contract())
    document["result_schemas"] = [[key, list(value)] for key, value in document["result_schemas"]]
    return json.loads(_canonical(document))


def _source_lineage() -> dict[str, str]:
    return {
        "psi0c_a_digest": PSI0C_A_DIGEST,
        "psi0b_g_digest": PSI0B_G_DIGEST,
        "psi0b_bundle_identity_digest": PSI0B_BUNDLE_DIGEST,
    }


def _summary_lineage() -> dict[str, str]:
    return {
        "psi0d_a_digest": PSI0D_A_DIGEST,
        "psi0c_d_digest": PSI0C_D_DIGEST,
        "psi0c_c_assessment_identity": PSI0C_C_ASSESSMENT_IDENTITY,
        "psi0c_c_bundle_identity": PSI0C_C_BUNDLE_IDENTITY,
        "psi0c_b_digest": PSI0C_B_DIGEST,
    }


def _extract_summary(assessment: object) -> dict:
    if not isinstance(assessment, Mapping) or set(assessment) != ASSESSMENT_KEYS:
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_ASSESSMENT_SCHEMA_DRIFT")
    digest = assessment["assessment_digest"]
    without_digest = {key: value for key, value in assessment.items() if key != "assessment_digest"}
    if digest != _digest(without_digest) or digest != EXPECTED_ASSESSMENT_DIGEST:
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_ASSESSMENT_DIGEST_MISMATCH")
    if (assessment["schema_version"] != "psi0c-b.assessment.v1" or
            assessment["contract_digest"] != PSI0C_B_DIGEST or
            dict(assessment["input_lineage"]) != _source_lineage() or
            assessment["fixture_only"] is not False or
            assessment["provenance_class"] != SOURCE_PROVENANCE_CLASS):
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_LINEAGE_OR_PROVENANCE_DRIFT")
    authority = assessment["authority"]
    if not isinstance(authority, Mapping) or set(authority) != AUTHORITY_KEYS or any(authority.values()):
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_AUTHORITY_DRIFT")
    membership = assessment["membership"]
    if not isinstance(membership, Mapping) or set(membership) != set(QUERY_IDS):
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_QUERY_IDENTITY_DRIFT")
    projected_membership = {}
    for query_id in QUERY_IDS:
        item = membership[query_id]
        if not isinstance(item, Mapping) or set(item) != SOURCE_MEMBERSHIP_KEYS:
            raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_MEMBERSHIP_SCHEMA_DRIFT")
        if not isinstance(item["unmatched_mints"], list):
            raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_UNMATCHED_STRUCTURE_INVALID")
        projected_membership[query_id] = {key: item[key] for key in MEMBERSHIP_KEYS}
    missingness = assessment["missingness"]
    if not isinstance(missingness, list) or len(missingness) != assessment["cohort_count"]:
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_MISSINGNESS_STRUCTURE_INVALID")
    for item in missingness:
        if (not isinstance(item, Mapping) or set(item) != {"mint", "surfaces", "negative_outcome_inferred"} or
                item["negative_outcome_inferred"] is not False or
                not isinstance(item["surfaces"], Mapping) or set(item["surfaces"]) != set(QUERY_IDS) or
                any(value not in {"PRESENT", "ABSENT_NOT_NEGATIVE"} for value in item["surfaces"].values())):
            raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_MISSINGNESS_STRUCTURE_INVALID")
    conflicts = assessment["conflicts"]
    if not isinstance(conflicts, list):
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_CONFLICT_STRUCTURE_INVALID")
    orphan = assessment["orphan_unmatched_accounting"]
    if not isinstance(orphan, Mapping) or set(orphan) != {"unmatched_mints", "unmatched_rows"} or not isinstance(orphan["unmatched_mints"], list):
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_ORPHAN_STRUCTURE_INVALID")
    reasons = assessment["reason_codes"]
    if not isinstance(reasons, list) or not set(reasons).issubset(REASON_CODES):
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_REASON_CODE_DRIFT")
    return {
        "schema_version": "psi0d-b.synthetic-summary.v1",
        "input_lineage": _summary_lineage(),
        "fixture_only": False,
        "provenance_class": PRODUCTION_DERIVED_PROVENANCE_CLASS,
        "cohort_count": assessment["cohort_count"],
        "membership": projected_membership,
        "unresolved_conflict_count": len(conflicts),
        "orphan_unmatched_count": orphan["unmatched_rows"],
        "reason_codes": reasons,
        "authority": {key: False for key in AUTHORITY_KEYS},
    }


def adapt_immutable_assessment_summary(
    adapter_contract: ImmutableAssessmentSummaryAdapterContract,
    consumer_contract: AssessmentSummaryConsumerContract,
    *,
    bundle_files: Mapping[str, bytes],
) -> AssessmentSummaryProjection:
    verify_immutable_assessment_summary_adapter_contract(adapter_contract)
    verify_assessment_summary_consumer_contract(consumer_contract)
    if consumer_contract.contract_digest != PSI0D_B_DIGEST:
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_CONSUMER_CONTRACT_DRIFT")
    documents = _parse_canonical(bundle_files)
    if documents["contract.json"] != _expected_assessment_contract_document():
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_SOURCE_CONTRACT_DRIFT")
    expected_hashes = {
        name: sha256(bundle_files[name]).hexdigest()
        for name in ("assessment.json", "contract.json")
    }
    hashes = documents["hashes.json"]
    if (hashes != {"file_digests": expected_hashes, "bundle_digest": _digest(expected_hashes)} or
            hashes["bundle_digest"] != EXPECTED_BUNDLE_DIGEST):
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_BUNDLE_DIGEST_MISMATCH")
    summary = _extract_summary(documents["assessment.json"])
    normalized = _normalize_summary(
        summary,
        expected_fixture_only=False,
        expected_provenance_class=PRODUCTION_DERIVED_PROVENANCE_CLASS,
    )
    return _project_normalized_summary(consumer_contract, normalized)


def verify_immutable_assessment_summary_projection(
    adapter_contract: ImmutableAssessmentSummaryAdapterContract,
    consumer_contract: AssessmentSummaryConsumerContract,
    *,
    bundle_files: Mapping[str, bytes],
    projection: AssessmentSummaryProjection,
) -> bool:
    if projection != adapt_immutable_assessment_summary(
        adapter_contract, consumer_contract, bundle_files=bundle_files,
    ):
        raise ImmutableAssessmentSummaryAdapterError("PSI0D_D_PROJECTION_REPLAY_MISMATCH")
    return True
