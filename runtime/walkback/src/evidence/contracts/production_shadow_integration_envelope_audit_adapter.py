"""PSI0E-G3 pure adapter from retained PSI0D-F audit bytes to PSI0E-A."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping, Tuple

from .production_shadow_assessment import QUERY_IDS
from .production_shadow_assessment_summary_consumer import (
    PRODUCTION_DERIVED_PROVENANCE_CLASS,
    REASON_CODES,
    build_assessment_summary_consumer_contract,
)
from .production_shadow_integration_envelope import (
    DescriptiveIntegrationEnvelope,
    IntegrationEnvelopeContract,
    _project_envelope,
    build_integration_envelope_contract,
    verify_integration_envelope_contract,
)


ADAPTER_VERSION = "psi0e-g3.v1"
ENGINEERING_REVISION = "145363100675244dea8f616c4c0af63bc928ceb9"
PSI0E_G2_DIGEST = "cd05b86b26313152e704bd556454d6d8e620b3ab5cec26a375ff99f6d6826cdb"
PSI0E_F_DIGEST = "533ee09789ec52b87350cac49ff5664484d827898cc98b5341c59662a4d35ad1"
PSI0E_E_DIGEST = "b893198fed71329c33083176a7ce93d7c86f119ab66b9488dbf9c42db6435cc3"
EXPECTED_AUDIT_DIGEST = "9e755dcc6fceef6ef27664eb8531cff0478b0b6cc252ded22ee84adfefd1efac"
EXPECTED_INPUT_DIGEST = "89c8dee5c976bd587aa6fc7ec3a6194e38caeba5508013c8ddca976489038f35"
EXPECTED_ENVELOPE_DIGEST = "c8827678b2137f1aec864f86623514a92affb0c6df092d13b24c160f8fb90a9d"
PSI0E_A_DIGEST = "2c8b2a296cb55ceb7da59ea4b063557749842041ba35251ec605e3f5f231350f"
PSI0D_D_DIGEST = "48e4480d741793c78dda8413ce8fa233c849d3781538bc32c0283a436f00bd7d"
PSI0D_B_DIGEST = "a9e368cceede689736ca234891394551bda098df3b51ebfebf2e72f32aeb51f6"
PSI0D_PROJECTION_DIGEST = "482461d10319e657bbb4df37b4cfa4be526ada21b4dbd3186ce43834ecb2d136"
PSI0D_F_ENGINEERING_COMMIT = "9ef7467327b74dbf435fc00ddc26c99a77f5b33e"

AUDIT_KEYS = {
    "schema_version", "milestone", "applied_at_utc", "engineering_commit",
    "status", "verdict", "bound_inputs", "execution", "descriptive_projection",
    "authority", "scope_proof", "next_action",
}
BOUND_INPUT_KEYS = {
    "psi0d_e_closure_digest", "psi0d_d_adapter_contract_digest",
    "psi0d_b_consumer_contract_digest", "psi0c_d_closure_digest",
    "psi0c_c_assessment_digest", "psi0c_c_assessment_bundle_digest",
}
EXPECTED_BOUND_INPUTS = {
    "psi0d_e_closure_digest": "5452c1f3a00e7fceeb0685c8688f5515f98e60e8030fecd6db34071556dd5729",
    "psi0d_d_adapter_contract_digest": PSI0D_D_DIGEST,
    "psi0d_b_consumer_contract_digest": PSI0D_B_DIGEST,
    "psi0c_d_closure_digest": "7e8c4ce4c27aa0d235a39cfe49e4dad4443be0bfae0750d61188a73d2cfeffb2",
    "psi0c_c_assessment_digest": "b5fb2187ad569b422f1ebbc029b7c619300a153d81b547f178c58769377fc7c4",
    "psi0c_c_assessment_bundle_digest": "6db46939fdfec9a34a63268809609c9a30a1a7dd86f5504b3d063bd0e8045ac3",
}
EXECUTION_KEYS = {
    "source_files_read_once", "adapter_applications", "normalized_input_digest",
    "projection_digest", "projection_replay_method", "cohort_denominator",
    "provenance_class",
}
SURFACE_KEYS = {
    "row_count", "unique_mint_count", "coverage_numerator",
    "coverage_denominator", "duplicate_row_count", "unmatched_row_count",
    "missingness_semantics",
}
AGGREGATE_KEYS = set(QUERY_IDS) | {
    "unresolved_conflict_count", "orphan_unmatched_count", "reason_codes",
}
AUTHORITY_KEYS = {"policy", "ranking", "integration", "deployment", "activation"}
SCOPE_KEYS = {
    "source_bundle_altered", "source_values_recorded_or_disclosed",
    "consumer_output_published", "consumer_output_directory_created",
    "production_or_runtime_access", "database_or_network_calls",
}


class IntegrationEnvelopeAuditAdapterError(RuntimeError):
    """Named fail-closed PSI0E-G3 adapter violation."""


@dataclass(frozen=True)
class IntegrationEnvelopeAuditAdapterContract:
    adapter_version: str
    engineering_revision: str
    psi0e_g2_digest: str
    psi0e_f_digest: str
    psi0e_e_digest: str
    expected_audit_digest: str
    expected_input_digest: str
    expected_envelope_digest: str
    psi0e_a_digest: str
    psi0d_d_digest: str
    psi0d_b_digest: str
    psi0d_projection_digest: str
    accepted_audit_keys: Tuple[str, ...]
    accepted_aggregate_keys: Tuple[str, ...]
    performs_io: bool
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


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def build_integration_envelope_audit_adapter_contract() -> IntegrationEnvelopeAuditAdapterContract:
    body = {
        "adapter_version": ADAPTER_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0e_g2_digest": PSI0E_G2_DIGEST,
        "psi0e_f_digest": PSI0E_F_DIGEST,
        "psi0e_e_digest": PSI0E_E_DIGEST,
        "expected_audit_digest": EXPECTED_AUDIT_DIGEST,
        "expected_input_digest": EXPECTED_INPUT_DIGEST,
        "expected_envelope_digest": EXPECTED_ENVELOPE_DIGEST,
        "psi0e_a_digest": PSI0E_A_DIGEST,
        "psi0d_d_digest": PSI0D_D_DIGEST,
        "psi0d_b_digest": PSI0D_B_DIGEST,
        "psi0d_projection_digest": PSI0D_PROJECTION_DIGEST,
        "accepted_audit_keys": tuple(sorted(AUDIT_KEYS)),
        "accepted_aggregate_keys": tuple(sorted(AGGREGATE_KEYS)),
        "performs_io": False,
        "retries_allowed": False,
        "retains_source_values": False,
        "grants_policy_authority": False,
        "grants_ranking_authority": False,
        "grants_integration_authority": False,
        "grants_deployment_authority": False,
        "grants_activation_authority": False,
    }
    serial = {key: list(value) if isinstance(value, tuple) else value for key, value in body.items()}
    return IntegrationEnvelopeAuditAdapterContract(**body, contract_digest=_digest(serial))


def verify_integration_envelope_audit_adapter_contract(
    contract: IntegrationEnvelopeAuditAdapterContract,
) -> bool:
    if contract != build_integration_envelope_audit_adapter_contract():
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_CONTRACT_REPLAY_MISMATCH")
    forbidden = (
        contract.performs_io, contract.retries_allowed, contract.retains_source_values,
        contract.grants_policy_authority, contract.grants_ranking_authority,
        contract.grants_integration_authority, contract.grants_deployment_authority,
        contract.grants_activation_authority,
    )
    if any(forbidden):
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_AUTHORITY_DRIFT")
    return True


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_INVALID_ACCOUNTING")
    return value


def _parse_audit(contract: IntegrationEnvelopeAuditAdapterContract, audit_bytes: bytes) -> dict:
    if not isinstance(audit_bytes, bytes):
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_BYTES_REQUIRED")
    if sha256(audit_bytes).hexdigest() != contract.expected_audit_digest:
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_AUDIT_DIGEST_MISMATCH")
    try:
        audit = json.loads(audit_bytes)
    except (TypeError, ValueError) as exc:
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_INVALID_JSON") from exc
    if not isinstance(audit, Mapping) or set(audit) != AUDIT_KEYS:
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_AUDIT_SCHEMA_DRIFT")
    if (audit["milestone"] != "PSI0D-F" or audit["engineering_commit"] != PSI0D_F_ENGINEERING_COMMIT or
            audit["status"] != "PASS" or not isinstance(audit["schema_version"], str) or
            not isinstance(audit["applied_at_utc"], str) or not isinstance(audit["verdict"], str) or
            not isinstance(audit["next_action"], str)):
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_AUDIT_IDENTITY_DRIFT")
    bound = audit["bound_inputs"]
    if not isinstance(bound, Mapping) or set(bound) != BOUND_INPUT_KEYS or dict(bound) != EXPECTED_BOUND_INPUTS:
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_LINEAGE_DRIFT")
    return dict(audit)


def _projection_from_audit(contract: IntegrationEnvelopeAuditAdapterContract, audit: Mapping) -> dict:
    execution = audit["execution"]
    if not isinstance(execution, Mapping) or set(execution) != EXECUTION_KEYS:
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_EXECUTION_SCHEMA_DRIFT")
    if (execution["source_files_read_once"] is not True or execution["adapter_applications"] != 1 or
            not _is_digest(execution["normalized_input_digest"]) or
            execution["projection_digest"] != contract.psi0d_projection_digest or
            not isinstance(execution["projection_replay_method"], str) or
            not execution["projection_replay_method"] or
            execution["provenance_class"] != PRODUCTION_DERIVED_PROVENANCE_CLASS):
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_EXECUTION_DRIFT")
    cohort = _count(execution["cohort_denominator"])
    if cohort < 1:
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_INVALID_ACCOUNTING")

    authority = audit["authority"]
    if not isinstance(authority, Mapping) or set(authority) != AUTHORITY_KEYS or any(authority.values()):
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_AUTHORITY_DRIFT")
    scope = audit["scope_proof"]
    if (not isinstance(scope, Mapping) or set(scope) != SCOPE_KEYS or
            scope["database_or_network_calls"] != 0 or
            any(scope[key] is not False for key in SCOPE_KEYS if key != "database_or_network_calls")):
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_SCOPE_PROOF_DRIFT")

    aggregate = audit["descriptive_projection"]
    if not isinstance(aggregate, Mapping) or set(aggregate) != AGGREGATE_KEYS:
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_AGGREGATE_SCHEMA_DRIFT")
    surfaces = {}
    unmatched_total = 0
    for query_id in QUERY_IDS:
        item = aggregate[query_id]
        if not isinstance(item, Mapping) or set(item) != SURFACE_KEYS:
            raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_SURFACE_SCHEMA_DRIFT")
        values = {key: _count(item[key]) for key in SURFACE_KEYS if key != "missingness_semantics"}
        if (item["missingness_semantics"] != "ABSENT_NOT_NEGATIVE" or
                values["coverage_denominator"] != cohort or
                values["coverage_numerator"] > min(cohort, values["unique_mint_count"]) or
                values["unique_mint_count"] > values["row_count"] or
                values["duplicate_row_count"] != values["row_count"] - values["unique_mint_count"] or
                values["unmatched_row_count"] > values["row_count"]):
            raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_INCONSISTENT_ACCOUNTING")
        surfaces[query_id] = {key: item[key] for key in sorted(SURFACE_KEYS)}
        unmatched_total += values["unmatched_row_count"]
    conflicts = _count(aggregate["unresolved_conflict_count"])
    unmatched = _count(aggregate["orphan_unmatched_count"])
    reasons = aggregate["reason_codes"]
    if unmatched != unmatched_total:
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_INCONSISTENT_ACCOUNTING")
    if (not isinstance(reasons, list) or reasons != sorted(set(reasons)) or
            not set(reasons).issubset(REASON_CODES) or
            "PSI0C_B_ABSENCE_IS_NOT_NEGATIVE" not in reasons or
            (conflicts > 0 and "PSI0C_B_CONFLICT_PRESERVED_UNRESOLVED" not in reasons) or
            (unmatched > 0 and "PSI0C_B_UNMATCHED_KEY_RECORDED" not in reasons)):
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_REASON_CODE_DRIFT")

    consumer = build_assessment_summary_consumer_contract()
    if consumer.contract_digest != contract.psi0d_b_digest:
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_CONSUMER_CONTRACT_DRIFT")
    projection = {
        "schema_version": "psi0d-b.descriptive-projection.v1",
        "contract_digest": consumer.contract_digest,
        "input_lineage": {
            "psi0d_a_digest": consumer.psi0d_a_digest,
            "psi0c_d_digest": consumer.psi0c_d_digest,
            "psi0c_c_assessment_identity": consumer.psi0c_c_assessment_identity,
            "psi0c_c_bundle_identity": consumer.psi0c_c_bundle_identity,
            "psi0c_b_digest": consumer.psi0c_b_digest,
        },
        "fixture_only": False,
        "default_off": True,
        "provenance_class": PRODUCTION_DERIVED_PROVENANCE_CLASS,
        "cohort_count": cohort,
        "surfaces": surfaces,
        "unresolved_conflict_count": conflicts,
        "orphan_unmatched_count": unmatched,
        "reason_codes": reasons,
        "interpretation": {
            "threshold_applied": False, "negative_outcome_inferred": False,
            "duplicates_collapsed": False, "conflicts_resolved": False,
            "entities_ranked_or_selected": False,
        },
        "authority": {
            "policy": False, "ranking": False, "integration": False,
            "deployment": False, "activation": False,
        },
    }
    if sha256(_canonical(projection)).hexdigest() != contract.psi0d_projection_digest:
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_PROJECTION_REPLAY_MISMATCH")
    return projection


def adapt_psi0d_f_audit_to_integration_envelope(
    adapter_contract: IntegrationEnvelopeAuditAdapterContract,
    envelope_contract: IntegrationEnvelopeContract,
    *,
    audit_bytes: bytes,
) -> DescriptiveIntegrationEnvelope:
    verify_integration_envelope_audit_adapter_contract(adapter_contract)
    try:
        verify_integration_envelope_contract(envelope_contract)
    except Exception as exc:
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_ENVELOPE_CONTRACT_DRIFT") from exc
    if envelope_contract.contract_digest != adapter_contract.psi0e_a_digest:
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_ENVELOPE_CONTRACT_DRIFT")
    audit = _parse_audit(adapter_contract, audit_bytes)
    projection = _projection_from_audit(adapter_contract, audit)
    envelope = _project_envelope(
        envelope_contract, projection, input_digest=adapter_contract.expected_input_digest,
    )
    if (envelope.input_digest != adapter_contract.expected_input_digest or
            envelope.envelope_digest != adapter_contract.expected_envelope_digest or
            envelope.contract_digest != adapter_contract.psi0e_a_digest):
        raise IntegrationEnvelopeAuditAdapterError("PSI0E_G3_ENVELOPE_REPLAY_MISMATCH")
    return envelope
