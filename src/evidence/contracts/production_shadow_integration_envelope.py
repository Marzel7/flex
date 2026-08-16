"""PSI0E-A pure default-off integration envelope over injected PSI0D bytes."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping, Tuple

from .production_shadow_assessment import QUERY_IDS
from .production_shadow_projection_publisher import (
    FILES,
    _canonical,
    _digest,
    _manifest,
    _validate_projection,
    build_projection_publication_contract,
    verify_projection_publication_contract,
)


CONTRACT_VERSION = "psi0e-a.v1"
ENGINEERING_REVISION = "89dd764be84fa5362c54ef5518c23563dacd79d7"
PSI0D_BUNDLE_DIGEST = "4f208d7cc00e7b451c796ad16b5c28862cd79c30e476f41c22d7b4fa589e9477"
PSI0D_PROJECTION_DIGEST = "482461d10319e657bbb4df37b4cfa4be526ada21b4dbd3186ce43834ecb2d136"
PSI0D_HASHES_FILE_DIGEST = "5c3bb6bb162b275c98d699e70543c700adf7b05a32a4044612d52e25bf8e90c8"
PSI0D_H_CONTRACT_DIGEST = "b448d37d6f95a6b92bd838f224e98d1f68e0325cdb2e1827a1ef11c27b583770"
PSI0D_B_CONSUMER_DIGEST = "a9e368cceede689736ca234891394551bda098df3b51ebfebf2e72f32aeb51f6"
SOURCE_PROVENANCE = "PRODUCTION_DERIVED_IMMUTABLE_LOCAL_ASSESSMENT_SUMMARY"
OUTPUT_PROVENANCE = "PRODUCTION_DERIVED_IMMUTABLE_LOCAL_DESCRIPTIVE_INTEGRATION_ENVELOPE"
AUTHORITY_KEYS = {"policy", "ranking", "integration", "deployment", "activation"}


class IntegrationEnvelopeError(RuntimeError):
    """Named fail-closed PSI0E-A envelope violation."""


@dataclass(frozen=True)
class IntegrationEnvelopeContract:
    contract_version: str
    engineering_revision: str
    psi0d_bundle_digest: str
    psi0d_projection_digest: str
    psi0d_hashes_file_digest: str
    psi0d_h_contract_digest: str
    psi0d_b_consumer_digest: str
    expected_files: Tuple[str, ...]
    source_provenance: str
    output_provenance: str
    default_off: bool
    performs_io: bool
    retries_allowed: bool
    retains_source_values: bool
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
class DescriptiveIntegrationEnvelope:
    canonical_envelope: bytes
    input_digest: str
    envelope_digest: str
    contract_digest: str


def build_integration_envelope_contract() -> IntegrationEnvelopeContract:
    body = {
        "contract_version": CONTRACT_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0d_bundle_digest": PSI0D_BUNDLE_DIGEST,
        "psi0d_projection_digest": PSI0D_PROJECTION_DIGEST,
        "psi0d_hashes_file_digest": PSI0D_HASHES_FILE_DIGEST,
        "psi0d_h_contract_digest": PSI0D_H_CONTRACT_DIGEST,
        "psi0d_b_consumer_digest": PSI0D_B_CONSUMER_DIGEST,
        "expected_files": FILES,
        "source_provenance": SOURCE_PROVENANCE,
        "output_provenance": OUTPUT_PROVENANCE,
        "default_off": True,
        "performs_io": False,
        "retries_allowed": False,
        "retains_source_values": False,
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
    return IntegrationEnvelopeContract(**body, contract_digest=_digest(serial))


def verify_integration_envelope_contract(contract: IntegrationEnvelopeContract) -> bool:
    if contract != build_integration_envelope_contract():
        raise IntegrationEnvelopeError("PSI0E_A_CONTRACT_REPLAY_MISMATCH")
    forbidden = (
        contract.performs_io, contract.retries_allowed, contract.retains_source_values,
        contract.thresholds_allowed, contract.negative_inference_allowed,
        contract.duplicate_collapse_allowed, contract.conflict_resolution_allowed,
        contract.grants_policy_authority, contract.grants_ranking_authority,
        contract.grants_integration_authority, contract.grants_deployment_authority,
        contract.grants_activation_authority,
    )
    if not contract.default_off or any(forbidden):
        raise IntegrationEnvelopeError("PSI0E_A_AUTHORITY_DRIFT")
    return True


def _parse_canonical_bundle(bundle_files: Mapping[str, bytes]) -> dict[str, object]:
    if tuple(sorted(bundle_files)) != FILES:
        raise IntegrationEnvelopeError("PSI0E_A_FILE_SET_MISMATCH")
    documents = {}
    for name in FILES:
        payload = bundle_files[name]
        if not isinstance(payload, bytes):
            raise IntegrationEnvelopeError("PSI0E_A_BYTES_REQUIRED")
        try:
            document = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise IntegrationEnvelopeError("PSI0E_A_INVALID_JSON") from exc
        if payload != _canonical(document):
            raise IntegrationEnvelopeError("PSI0E_A_NONCANONICAL_BUNDLE")
        documents[name] = document
    return documents


def _validated_projection(
    contract: IntegrationEnvelopeContract,
    bundle_files: Mapping[str, bytes],
) -> dict:
    documents = _parse_canonical_bundle(bundle_files)
    publication_contract = build_projection_publication_contract()
    verify_projection_publication_contract(publication_contract)
    if publication_contract.contract_digest != contract.psi0d_h_contract_digest:
        raise IntegrationEnvelopeError("PSI0E_A_PUBLICATION_CONTRACT_DRIFT")
    if bundle_files["contract.json"] != _canonical(_manifest(publication_contract)):
        raise IntegrationEnvelopeError("PSI0E_A_PUBLICATION_MANIFEST_DRIFT")
    projection_digest = sha256(bundle_files["projection.json"]).hexdigest()
    if projection_digest != contract.psi0d_projection_digest:
        raise IntegrationEnvelopeError("PSI0E_A_PROJECTION_DIGEST_MISMATCH")
    file_digests = {
        name: sha256(bundle_files[name]).hexdigest()
        for name in ("contract.json", "projection.json")
    }
    expected_hashes = {"file_digests": file_digests, "bundle_digest": _digest(file_digests)}
    if (documents["hashes.json"] != expected_hashes or
            expected_hashes["bundle_digest"] != contract.psi0d_bundle_digest or
            sha256(bundle_files["hashes.json"]).hexdigest() != contract.psi0d_hashes_file_digest):
        raise IntegrationEnvelopeError("PSI0E_A_BUNDLE_HASH_REPLAY_MISMATCH")
    try:
        projection = _validate_projection(publication_contract, bundle_files["projection.json"])
    except Exception as exc:
        raise IntegrationEnvelopeError("PSI0E_A_PROJECTION_VALIDATION_FAILED") from exc
    if projection["provenance_class"] != contract.source_provenance:
        raise IntegrationEnvelopeError("PSI0E_A_PROVENANCE_DRIFT")
    return projection


def _project_envelope(
    contract: IntegrationEnvelopeContract,
    projection: Mapping[str, object],
    *,
    input_digest: str,
) -> DescriptiveIntegrationEnvelope:
    """Pure descriptive core; callers must validate injected provenance first."""
    surfaces = {}
    for query_id in QUERY_IDS:
        item = projection["surfaces"][query_id]
        surfaces[query_id] = {
            "coverage_numerator": item["coverage_numerator"],
            "coverage_denominator": item["coverage_denominator"],
            "row_count": item["row_count"],
            "unique_mint_count": item["unique_mint_count"],
            "duplicate_row_count": item["duplicate_row_count"],
            "unmatched_row_count": item["unmatched_row_count"],
            "missingness_semantics": item["missingness_semantics"],
        }
    envelope = {
        "schema_version": "psi0e-a.descriptive-integration-envelope.v1",
        "contract_digest": contract.contract_digest,
        "source_identities": {
            "psi0d_bundle_digest": contract.psi0d_bundle_digest,
            "psi0d_projection_digest": contract.psi0d_projection_digest,
            "psi0d_hashes_file_digest": contract.psi0d_hashes_file_digest,
            "psi0d_h_contract_digest": contract.psi0d_h_contract_digest,
            "psi0d_b_consumer_digest": contract.psi0d_b_consumer_digest,
        },
        "default_off": True,
        "consumer_enabled": False,
        "provenance_class": contract.output_provenance,
        "source_provenance_class": contract.source_provenance,
        "cohort_count": projection["cohort_count"],
        "surfaces": surfaces,
        "unresolved_conflict_count": projection["unresolved_conflict_count"],
        "orphan_unmatched_count": projection["orphan_unmatched_count"],
        "reason_codes": projection["reason_codes"],
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
    payload = _canonical(envelope)
    return DescriptiveIntegrationEnvelope(
        canonical_envelope=payload,
        input_digest=input_digest,
        envelope_digest=sha256(payload).hexdigest(),
        contract_digest=contract.contract_digest,
    )


def project_published_projection_fixture(
    contract: IntegrationEnvelopeContract,
    *,
    bundle_files: Mapping[str, bytes],
) -> DescriptiveIntegrationEnvelope:
    verify_integration_envelope_contract(contract)
    projection = _validated_projection(contract, bundle_files)
    input_digest = _digest({name: sha256(bundle_files[name]).hexdigest() for name in FILES})
    return _project_envelope(contract, projection, input_digest=input_digest)


def verify_integration_envelope(
    contract: IntegrationEnvelopeContract,
    *,
    bundle_files: Mapping[str, bytes],
    envelope: DescriptiveIntegrationEnvelope,
) -> bool:
    if envelope != project_published_projection_fixture(contract, bundle_files=bundle_files):
        raise IntegrationEnvelopeError("PSI0E_A_ENVELOPE_REPLAY_MISMATCH")
    return True
