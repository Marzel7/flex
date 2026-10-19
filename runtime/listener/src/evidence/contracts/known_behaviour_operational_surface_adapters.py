"""PSI0F-D pure immutable-summary byte adapters for PSI0F-B."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Mapping, Tuple

from .known_behaviour_operational_surface import (
    AUTHORITY_KEYS,
    KnownBehaviourOperationalSurface,
    KnownBehaviourOperationalSurfaceContract,
    _lineage,
    project_fixture_known_behaviour_operational_surface,
)
from .operational_family_bundle import BUNDLE_SCHEMA_VERSION, _corpus, _json, _manifest
from .operational_family_corpus import verify_operational_family_corpora
from .operational_family_manifest import verify_operational_family_manifest
from .production_shadow_integration_envelope_publisher import (
    FILES as PSI0E_FILES,
    _canonical as psi0e_canonical,
    _manifest as psi0e_manifest,
    _validate_envelope,
    build_integration_envelope_publication_contract,
)


ADAPTER_VERSION = "psi0f-d.v1"
ENGINEERING_REVISION = "3013be47017a000c1db3c70fbf323102d0fc40d3"
PSI0F_C_DIGEST = "bac68ff658eaeffbc77c09ff4a827a221c60e9b6c20e3e65f7b195299dd76cf0"
PSI0F_B_DIGEST = "042568c68b0eb86ef41bc65037568d65214d02d9d9753988adf88c86c14222ef"
PSI0E_BUNDLE_DIGEST = "88c7de3156a4dc07b3c3b2461b4e1e37e85d5bd06d217d904472e4f4bc6f4d9c"
PSI0E_PROVENANCE = "PRODUCTION_DERIVED_IMMUTABLE_LOCAL_DESCRIPTIVE_INTEGRATION_ENVELOPE"
EB0_4_PROVENANCE = "PRODUCTION_DERIVED_IMMUTABLE_LOCAL_OPERATIONAL_FAMILY_SUMMARY"
EB0_4_FILES = ("accounting.json", "corpora.json", "hashes.json", "manifests.json", "run.json")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class KnownBehaviourOperationalSurfaceAdapterError(RuntimeError):
    """Named fail-closed PSI0F-D adapter violation."""


@dataclass(frozen=True)
class KnownBehaviourOperationalSurfaceAdapterContract:
    adapter_version: str
    engineering_revision: str
    psi0f_c_digest: str
    psi0f_b_digest: str
    expected_psi0e_bundle_digest: str
    psi0e_files: Tuple[str, ...]
    eb0_4_files: Tuple[str, ...]
    psi0e_source_provenance: str
    eb0_4_source_provenance: str
    fixture_only: bool
    performs_io: bool
    retries_allowed: bool
    retains_source_values: bool
    cross_layer_join_allowed: bool
    grants_policy_authority: bool
    grants_ranking_authority: bool
    grants_attribution_authority: bool
    grants_integration_authority: bool
    grants_deployment_authority: bool
    grants_activation_authority: bool
    contract_digest: str


@dataclass(frozen=True)
class AdaptedOperationalSurface:
    surface: KnownBehaviourOperationalSurface
    psi0e_bundle_digest: str
    eb0_4_bundle_digest: str
    psi0e_source_provenance: str
    eb0_4_source_provenance: str
    adapter_contract_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_known_behaviour_operational_surface_adapter_contract() -> KnownBehaviourOperationalSurfaceAdapterContract:
    body = {
        "adapter_version": ADAPTER_VERSION, "engineering_revision": ENGINEERING_REVISION,
        "psi0f_c_digest": PSI0F_C_DIGEST, "psi0f_b_digest": PSI0F_B_DIGEST,
        "expected_psi0e_bundle_digest": PSI0E_BUNDLE_DIGEST,
        "psi0e_files": PSI0E_FILES, "eb0_4_files": EB0_4_FILES,
        "psi0e_source_provenance": PSI0E_PROVENANCE,
        "eb0_4_source_provenance": EB0_4_PROVENANCE,
        "fixture_only": True, "performs_io": False, "retries_allowed": False,
        "retains_source_values": False, "cross_layer_join_allowed": False,
        "grants_policy_authority": False, "grants_ranking_authority": False,
        "grants_attribution_authority": False, "grants_integration_authority": False,
        "grants_deployment_authority": False, "grants_activation_authority": False,
    }
    serial = {k: list(v) if isinstance(v, tuple) else v for k, v in body.items()}
    return KnownBehaviourOperationalSurfaceAdapterContract(**body, contract_digest=_digest(serial))


def verify_known_behaviour_operational_surface_adapter_contract(contract: KnownBehaviourOperationalSurfaceAdapterContract) -> bool:
    if contract != build_known_behaviour_operational_surface_adapter_contract():
        raise KnownBehaviourOperationalSurfaceAdapterError("PSI0F_D_CONTRACT_REPLAY_MISMATCH")
    forbidden = (
        contract.performs_io, contract.retries_allowed, contract.retains_source_values,
        contract.cross_layer_join_allowed, contract.grants_policy_authority,
        contract.grants_ranking_authority, contract.grants_attribution_authority,
        contract.grants_integration_authority, contract.grants_deployment_authority,
        contract.grants_activation_authority,
    )
    if not contract.fixture_only or any(forbidden):
        raise KnownBehaviourOperationalSurfaceAdapterError("PSI0F_D_AUTHORITY_DRIFT")
    return True


def _documents(files: object, exact: Tuple[str, ...], prefix: str) -> tuple[dict[str, bytes], dict[str, object]]:
    if not isinstance(files, Mapping) or set(files) != set(exact):
        raise KnownBehaviourOperationalSurfaceAdapterError(f"PSI0F_D_{prefix}_FILE_SET_MISMATCH")
    payloads, documents = {}, {}
    for name in exact:
        payload = files[name]
        if not isinstance(payload, bytes):
            raise KnownBehaviourOperationalSurfaceAdapterError(f"PSI0F_D_{prefix}_BYTES_REQUIRED")
        try:
            document = json.loads(payload)
        except Exception as exc:
            raise KnownBehaviourOperationalSurfaceAdapterError(f"PSI0F_D_{prefix}_INVALID_JSON") from exc
        if payload != _canonical(document):
            raise KnownBehaviourOperationalSurfaceAdapterError(f"PSI0F_D_{prefix}_NONCANONICAL_BYTES")
        payloads[name], documents[name] = payload, document
    return payloads, documents


def adapt_psi0e_bundle_bytes(contract: KnownBehaviourOperationalSurfaceAdapterContract, files: object) -> tuple[dict, str]:
    verify_known_behaviour_operational_surface_adapter_contract(contract)
    payloads, documents = _documents(files, contract.psi0e_files, "PSI0E")
    publication = build_integration_envelope_publication_contract()
    envelope = _validate_envelope(publication, payloads["envelope.json"])
    if payloads["contract.json"] != psi0e_canonical(psi0e_manifest(publication)):
        raise KnownBehaviourOperationalSurfaceAdapterError("PSI0F_D_PSI0E_CONTRACT_DRIFT")
    hashes = documents["hashes.json"]
    file_digests = {name: sha256(payloads[name]).hexdigest() for name in ("contract.json", "envelope.json")}
    bundle_digest = _digest(file_digests)
    if hashes != {"file_digests": file_digests, "bundle_digest": bundle_digest}:
        raise KnownBehaviourOperationalSurfaceAdapterError("PSI0F_D_PSI0E_HASH_REPLAY_MISMATCH")
    if bundle_digest != contract.expected_psi0e_bundle_digest:
        raise KnownBehaviourOperationalSurfaceAdapterError("PSI0F_D_PSI0E_BUNDLE_IDENTITY_DRIFT")
    summary = {
        "schema_version": "psi0f-b.synthetic-psi0e-summary.v1", "lineage": _lineage(),
        "fixture_only": True, "default_off": envelope["default_off"],
        "consumer_enabled": envelope["consumer_enabled"], "cohort_count": envelope["cohort_count"],
        "surfaces": envelope["surfaces"],
        "unresolved_conflict_count": envelope["unresolved_conflict_count"],
        "orphan_unmatched_count": envelope["orphan_unmatched_count"],
        "reason_codes": envelope["reason_codes"],
        "authority": {key: False for key in sorted(AUTHORITY_KEYS)},
    }
    return summary, bundle_digest


def adapt_eb0_4_bundle_bytes(
    contract: KnownBehaviourOperationalSurfaceAdapterContract,
    files: object,
    *,
    expected_bundle_digest: str,
) -> tuple[dict, str]:
    verify_known_behaviour_operational_surface_adapter_contract(contract)
    if not isinstance(expected_bundle_digest, str) or not _DIGEST.fullmatch(expected_bundle_digest):
        raise KnownBehaviourOperationalSurfaceAdapterError("PSI0F_D_EB0_4_EXPECTED_DIGEST_REQUIRED")
    payloads, documents = _documents(files, contract.eb0_4_files, "EB0_4")
    hashes = documents["hashes.json"]
    data_files = ("run.json", "accounting.json", "manifests.json", "corpora.json")
    actual = {name: sha256(payloads[name]).hexdigest() for name in data_files}
    bundle_digest = sha256(_json(actual)).hexdigest()
    if (not isinstance(hashes, Mapping) or hashes.get("bundle_schema_version") != BUNDLE_SCHEMA_VERSION or
            hashes.get("files") != actual or hashes.get("bundle_digest") != bundle_digest):
        raise KnownBehaviourOperationalSurfaceAdapterError("PSI0F_D_EB0_4_HASH_REPLAY_MISMATCH")
    if bundle_digest != expected_bundle_digest:
        raise KnownBehaviourOperationalSurfaceAdapterError("PSI0F_D_EB0_4_BUNDLE_IDENTITY_DRIFT")
    try:
        manifests = tuple(_manifest(item) for item in documents["manifests.json"]["manifests"])
        corpora = tuple(_corpus(item) for item in documents["corpora.json"]["corpora"])
        for manifest in manifests:
            verify_operational_family_manifest(manifest, manifest.facts, manifest.nominations)
        verify_operational_family_corpora(corpora, manifests)
    except Exception as exc:
        raise KnownBehaviourOperationalSurfaceAdapterError("PSI0F_D_EB0_4_CONTENT_REPLAY_FAILED") from exc
    roles = sorted({corpus.primary_role for corpus in corpora})
    nominations = {}
    for corpus in corpora:
        for item in corpus.nominations:
            nominations[item.nomination_id] = {
                "primary_role": item.primary_role, "nomination_state": item.nomination_state,
                "member_operation_ids": list(item.member_operation_ids),
                "supporting_fact_ids": list(item.supporting_fact_ids),
                "shared_edge_features": list(item.shared_edge_features),
                "shared_mechanism_features": list(item.shared_mechanism_features),
                "shared_temporal_features": list(item.shared_temporal_features),
                "supporting_sources": list(item.supporting_sources),
                "quality_state": item.quality_state, "completeness_state": item.completeness_state,
                "conflict_count": len(item.conflict_group_ids),
                "operator_identity_asserted": item.operator_identity_asserted,
            }
    summary = {
        "schema_version": "psi0f-b.synthetic-operational-families.v1", "lineage": _lineage(),
        "fixture_only": True, "identity_basis": "PLATFORM_OPERATION_ID",
        "allowed_roles": roles, "nominations": [nominations[key] for key in sorted(nominations)],
        "authority": {key: False for key in sorted(AUTHORITY_KEYS)},
    }
    return summary, bundle_digest


def project_immutable_summary_bytes(
    adapter_contract: KnownBehaviourOperationalSurfaceAdapterContract,
    surface_contract: KnownBehaviourOperationalSurfaceContract,
    *,
    psi0e_files: object,
    eb0_4_files: object,
    expected_eb0_4_bundle_digest: str,
) -> AdaptedOperationalSurface:
    verify_known_behaviour_operational_surface_adapter_contract(adapter_contract)
    if surface_contract.contract_digest != adapter_contract.psi0f_b_digest:
        raise KnownBehaviourOperationalSurfaceAdapterError("PSI0F_D_SURFACE_CONTRACT_DRIFT")
    psi0e, psi0e_digest = adapt_psi0e_bundle_bytes(adapter_contract, psi0e_files)
    families, eb0_4_digest = adapt_eb0_4_bundle_bytes(
        adapter_contract, eb0_4_files, expected_bundle_digest=expected_eb0_4_bundle_digest,
    )
    surface = project_fixture_known_behaviour_operational_surface(
        surface_contract, psi0e_summary=psi0e, operational_families=families,
    )
    return AdaptedOperationalSurface(
        surface=surface, psi0e_bundle_digest=psi0e_digest, eb0_4_bundle_digest=eb0_4_digest,
        psi0e_source_provenance=adapter_contract.psi0e_source_provenance,
        eb0_4_source_provenance=adapter_contract.eb0_4_source_provenance,
        adapter_contract_digest=adapter_contract.contract_digest,
    )
