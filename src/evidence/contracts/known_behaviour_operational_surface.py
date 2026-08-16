"""PSI0F-B pure fixture-only known-behaviour operational surface."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping, Tuple

from .production_shadow_assessment import QUERY_IDS


CONTRACT_VERSION = "psi0f-b.v1"
ENGINEERING_REVISION = "0b3687fb0b61529034702f6117eb60071c43074f"
PSI0F_A_DIGEST = "c68d273ceb8248521b330f7fe06ee6ed21614b07df783496f23f4de36cae0534"
PSI0E_H_DIGEST = "ae0994f1f3647f53fbcd3cabf0c8e46efa26b504805fecb95e8d2851d9e98c16"
PSI0E_BUNDLE_DIGEST = "88c7de3156a4dc07b3c3b2461b4e1e37e85d5bd06d217d904472e4f4bc6f4d9c"
PSI0E_ENVELOPE_DIGEST = "c8827678b2137f1aec864f86623514a92affb0c6df092d13b24c160f8fb90a9d"
PROVENANCE_CLASS = "FROZEN_SYNTHETIC_KNOWN_BEHAVIOUR_OPERATIONAL_SURFACE"
AUTHORITY_CLASS = "FIXTURE_ONLY_NON_AUTHORITATIVE_OPERATIONAL_SURFACING"
NOMINATION_STATES = {"PROPOSED", "SUPPORTED"}
QUALITY_STATES = {"OBSERVED", "CONFLICTING", "DEGRADED"}
COMPLETENESS_STATES = {"COMPLETE", "PARTIAL", "NOT_OBSERVED"}
REASON_CODES = {
    "PSI0C_B_ABSENCE_IS_NOT_NEGATIVE",
    "PSI0F_B_BEHAVIOURAL_SIMILARITY_IS_NOMINATION_ONLY",
    "PSI0F_B_CONFLICT_PRESERVED_UNRESOLVED",
    "PSI0F_B_UNMATCHED_PRESERVED",
    "PSI0F_B_GLOBAL_AVAILABILITY_NOT_OPERATION_LINKAGE",
}
AUTHORITY_KEYS = {"policy", "ranking", "attribution", "integration", "deployment", "activation"}
PSI_KEYS = {
    "schema_version", "lineage", "fixture_only", "default_off", "consumer_enabled",
    "cohort_count", "surfaces", "unresolved_conflict_count", "orphan_unmatched_count",
    "reason_codes", "authority",
}
SURFACE_KEYS = {
    "coverage_numerator", "coverage_denominator", "row_count", "unique_mint_count",
    "duplicate_row_count", "unmatched_row_count", "missingness_semantics",
}
FAMILY_KEYS = {
    "schema_version", "lineage", "fixture_only", "identity_basis", "allowed_roles",
    "nominations", "authority",
}
NOMINATION_KEYS = {
    "primary_role", "nomination_state", "member_operation_ids", "supporting_fact_ids",
    "shared_edge_features", "shared_mechanism_features", "shared_temporal_features",
    "supporting_sources", "quality_state", "completeness_state", "conflict_count",
    "operator_identity_asserted",
}
_FORBIDDEN = (
    "operator", "owner", "attribution", "confidence", "score", "rank", "profit",
    "cashflow", "policy", "selection", "threshold",
)


class KnownBehaviourOperationalSurfaceError(RuntimeError):
    """Named fail-closed PSI0F-B violation."""


@dataclass(frozen=True)
class KnownBehaviourOperationalSurfaceContract:
    contract_version: str
    engineering_revision: str
    psi0f_a_digest: str
    psi0e_h_digest: str
    psi0e_bundle_digest: str
    psi0e_envelope_digest: str
    query_ids: Tuple[str, ...]
    nomination_states: Tuple[str, ...]
    quality_states: Tuple[str, ...]
    completeness_states: Tuple[str, ...]
    accepted_reason_codes: Tuple[str, ...]
    authority_class: str
    fixture_only: bool
    default_off: bool
    performs_io: bool
    cross_layer_join_allowed: bool
    thresholds_allowed: bool
    negative_inference_allowed: bool
    duplicate_collapse_allowed: bool
    conflict_resolution_allowed: bool
    operator_identity_allowed: bool
    grants_policy_authority: bool
    grants_ranking_authority: bool
    grants_attribution_authority: bool
    grants_integration_authority: bool
    grants_deployment_authority: bool
    grants_activation_authority: bool
    contract_digest: str


@dataclass(frozen=True)
class KnownBehaviourOperationalSurface:
    canonical_surface: bytes
    input_digest: str
    surface_digest: str
    contract_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _lineage() -> dict[str, str]:
    return {
        "psi0f_a_digest": PSI0F_A_DIGEST,
        "psi0e_h_digest": PSI0E_H_DIGEST,
        "psi0e_bundle_digest": PSI0E_BUNDLE_DIGEST,
        "psi0e_envelope_digest": PSI0E_ENVELOPE_DIGEST,
    }


def build_known_behaviour_operational_surface_contract() -> KnownBehaviourOperationalSurfaceContract:
    body = {
        "contract_version": CONTRACT_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0f_a_digest": PSI0F_A_DIGEST,
        "psi0e_h_digest": PSI0E_H_DIGEST,
        "psi0e_bundle_digest": PSI0E_BUNDLE_DIGEST,
        "psi0e_envelope_digest": PSI0E_ENVELOPE_DIGEST,
        "query_ids": QUERY_IDS,
        "nomination_states": tuple(sorted(NOMINATION_STATES)),
        "quality_states": tuple(sorted(QUALITY_STATES)),
        "completeness_states": tuple(sorted(COMPLETENESS_STATES)),
        "accepted_reason_codes": tuple(sorted(REASON_CODES)),
        "authority_class": AUTHORITY_CLASS,
        "fixture_only": True, "default_off": True, "performs_io": False,
        "cross_layer_join_allowed": False, "thresholds_allowed": False,
        "negative_inference_allowed": False, "duplicate_collapse_allowed": False,
        "conflict_resolution_allowed": False, "operator_identity_allowed": False,
        "grants_policy_authority": False, "grants_ranking_authority": False,
        "grants_attribution_authority": False, "grants_integration_authority": False,
        "grants_deployment_authority": False, "grants_activation_authority": False,
    }
    serial = {k: list(v) if isinstance(v, tuple) else v for k, v in body.items()}
    return KnownBehaviourOperationalSurfaceContract(**body, contract_digest=_digest(serial))


def verify_known_behaviour_operational_surface_contract(contract: KnownBehaviourOperationalSurfaceContract) -> bool:
    if contract != build_known_behaviour_operational_surface_contract():
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_CONTRACT_REPLAY_MISMATCH")
    forbidden = (
        contract.performs_io, contract.cross_layer_join_allowed, contract.thresholds_allowed,
        contract.negative_inference_allowed, contract.duplicate_collapse_allowed,
        contract.conflict_resolution_allowed, contract.operator_identity_allowed,
        contract.grants_policy_authority, contract.grants_ranking_authority,
        contract.grants_attribution_authority, contract.grants_integration_authority,
        contract.grants_deployment_authority, contract.grants_activation_authority,
    )
    if not contract.fixture_only or not contract.default_off or any(forbidden):
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_AUTHORITY_DRIFT")
    return True


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_INVALID_ACCOUNTING")
    return value


def _false_authority(value: object) -> dict[str, bool]:
    if not isinstance(value, Mapping) or set(value) != AUTHORITY_KEYS or any(value.values()):
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_AUTHORITY_DRIFT")
    return {key: False for key in sorted(AUTHORITY_KEYS)}


def _strings(value: object, *, allow_empty: bool = True) -> Tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_INVALID_DESCRIPTOR")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise KnownBehaviourOperationalSurfaceError("PSI0F_B_INVALID_DESCRIPTOR")
        item = item.strip()
        if any(term in item.lower() for term in _FORBIDDEN):
            raise KnownBehaviourOperationalSurfaceError("PSI0F_B_PROHIBITED_SEMANTICS")
        result.append(item)
    if len(result) != len(set(result)) or (not allow_empty and not result):
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_INVALID_DESCRIPTOR")
    return tuple(sorted(result))


def _normalize_psi0e(value: object) -> dict:
    if not isinstance(value, Mapping) or set(value) != PSI_KEYS:
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_UNKNOWN_PSI0E_SCHEMA")
    if (value["schema_version"] != "psi0f-b.synthetic-psi0e-summary.v1" or
            dict(value["lineage"]) != _lineage() or value["fixture_only"] is not True or
            value["default_off"] is not True or value["consumer_enabled"] is not False):
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_PSI0E_LINEAGE_OR_STATE_DRIFT")
    authority = _false_authority(value["authority"])
    cohort = _count(value["cohort_count"])
    if cohort < 1:
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_INVALID_ACCOUNTING")
    surfaces = value["surfaces"]
    if not isinstance(surfaces, Mapping) or set(surfaces) != set(QUERY_IDS):
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_SURFACE_IDENTITY_DRIFT")
    normalized, unmatched_total = {}, 0
    for query_id in QUERY_IDS:
        item = surfaces[query_id]
        if not isinstance(item, Mapping) or set(item) != SURFACE_KEYS:
            raise KnownBehaviourOperationalSurfaceError("PSI0F_B_UNKNOWN_SURFACE_SCHEMA")
        counts = {k: _count(item[k]) for k in SURFACE_KEYS - {"missingness_semantics"}}
        if (item["missingness_semantics"] != "ABSENT_NOT_NEGATIVE" or
                counts["coverage_denominator"] != cohort or
                counts["coverage_numerator"] > min(cohort, counts["unique_mint_count"]) or
                counts["unique_mint_count"] > counts["row_count"] or
                counts["duplicate_row_count"] != counts["row_count"] - counts["unique_mint_count"] or
                counts["unmatched_row_count"] > counts["row_count"]):
            raise KnownBehaviourOperationalSurfaceError("PSI0F_B_INCONSISTENT_ACCOUNTING")
        unmatched_total += counts["unmatched_row_count"]
        normalized[query_id] = {**{k: counts[k] for k in sorted(counts)}, "missingness_semantics": "ABSENT_NOT_NEGATIVE"}
    conflicts = _count(value["unresolved_conflict_count"])
    unmatched = _count(value["orphan_unmatched_count"])
    reasons = value["reason_codes"]
    if (unmatched != unmatched_total or not isinstance(reasons, (list, tuple)) or
            len(reasons) != len(set(reasons)) or not set(reasons).issubset(REASON_CODES) or
            "PSI0C_B_ABSENCE_IS_NOT_NEGATIVE" not in reasons):
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_REASON_OR_ACCOUNTING_DRIFT")
    if conflicts and "PSI0F_B_CONFLICT_PRESERVED_UNRESOLVED" not in reasons:
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_REASON_OR_ACCOUNTING_DRIFT")
    if unmatched and "PSI0F_B_UNMATCHED_PRESERVED" not in reasons:
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_REASON_OR_ACCOUNTING_DRIFT")
    return {"cohort_count": cohort, "surfaces": normalized, "unresolved_conflict_count": conflicts,
            "orphan_unmatched_count": unmatched, "reason_codes": sorted(reasons), "authority": authority}


def _normalize_families(value: object) -> dict:
    if not isinstance(value, Mapping) or set(value) != FAMILY_KEYS:
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_UNKNOWN_FAMILY_SCHEMA")
    if (value["schema_version"] != "psi0f-b.synthetic-operational-families.v1" or
            dict(value["lineage"]) != _lineage() or value["fixture_only"] is not True or
            value["identity_basis"] != "PLATFORM_OPERATION_ID"):
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_FAMILY_LINEAGE_OR_IDENTITY_DRIFT")
    _false_authority(value["authority"])
    roles = _strings(value["allowed_roles"], allow_empty=False)
    nominations = value["nominations"]
    if not isinstance(nominations, (list, tuple)):
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_UNKNOWN_NOMINATION_SCHEMA")
    normalized = []
    for item in nominations:
        if not isinstance(item, Mapping) or set(item) != NOMINATION_KEYS:
            raise KnownBehaviourOperationalSurfaceError("PSI0F_B_UNKNOWN_NOMINATION_SCHEMA")
        role, state = item["primary_role"], item["nomination_state"]
        if role not in roles or state not in NOMINATION_STATES:
            raise KnownBehaviourOperationalSurfaceError("PSI0F_B_ROLE_OR_NOMINATION_DRIFT")
        members = _strings(item["member_operation_ids"], allow_empty=False)
        facts = _strings(item["supporting_fact_ids"], allow_empty=False)
        edges = _strings(item["shared_edge_features"])
        mechanisms = _strings(item["shared_mechanism_features"])
        temporal = _strings(item["shared_temporal_features"])
        sources = _strings(item["supporting_sources"], allow_empty=False)
        quality, completeness = item["quality_state"], item["completeness_state"]
        conflicts = _count(item["conflict_count"])
        if len(members) < 2 or (not mechanisms and not temporal):
            raise KnownBehaviourOperationalSurfaceError("PSI0F_B_TOPOLOGY_ONLY_OR_INSUFFICIENT_SUPPORT")
        if quality not in QUALITY_STATES or completeness not in COMPLETENESS_STATES:
            raise KnownBehaviourOperationalSurfaceError("PSI0F_B_QUALITY_OR_COMPLETENESS_DRIFT")
        if item["operator_identity_asserted"] is not False:
            raise KnownBehaviourOperationalSurfaceError("PSI0F_B_OPERATOR_IDENTITY_FORBIDDEN")
        if state == "SUPPORTED" and (not mechanisms or not temporal or len(sources) < 2 or
                                     completeness != "COMPLETE" or conflicts != 0):
            raise KnownBehaviourOperationalSurfaceError("PSI0F_B_UNSUPPORTED_SUPPORTED_NOMINATION")
        normalized.append({
            "primary_role": role, "nomination_state": state, "member_count": len(members),
            "supporting_fact_count": len(facts), "shared_edge_features": list(edges),
            "shared_mechanism_features": list(mechanisms), "shared_temporal_features": list(temporal),
            "supporting_source_count": len(sources), "quality_state": quality,
            "completeness_state": completeness, "conflict_count": conflicts,
            "operator_identity_asserted": False,
        })
    normalized.sort(key=lambda x: _canonical(x))
    return {"allowed_roles": list(roles), "nominations": normalized}


def project_fixture_known_behaviour_operational_surface(
    contract: KnownBehaviourOperationalSurfaceContract,
    *,
    psi0e_summary: object,
    operational_families: object,
) -> KnownBehaviourOperationalSurface:
    verify_known_behaviour_operational_surface_contract(contract)
    psi0e = _normalize_psi0e(psi0e_summary)
    families = _normalize_families(operational_families)
    by_role = {}
    for role in families["allowed_roles"]:
        selected = [item for item in families["nominations"] if item["primary_role"] == role]
        by_role[role] = {
            "nomination_count": len(selected),
            "proposed_count": sum(item["nomination_state"] == "PROPOSED" for item in selected),
            "supported_count": sum(item["nomination_state"] == "SUPPORTED" for item in selected),
            "member_operation_reference_count": sum(item["member_count"] for item in selected),
            "supporting_fact_count": sum(item["supporting_fact_count"] for item in selected),
            "supporting_source_count": sum(item["supporting_source_count"] for item in selected),
            "unresolved_conflict_count": sum(item["conflict_count"] for item in selected),
            "nominations": selected,
        }
    normalized = {"psi0e": psi0e, "families": families}
    output = {
        "schema_version": "psi0f-b.descriptive-known-behaviour-operational-surface.v1",
        "contract_digest": contract.contract_digest,
        "fixture_only": True, "default_off": True, "consumer_enabled": False,
        "provenance_class": PROVENANCE_CLASS,
        "operational_roles": by_role,
        "global_evidence_availability_context": psi0e,
        "cross_layer_join_performed": False,
        "reason_codes": sorted(set(psi0e["reason_codes"]) | {
            "PSI0F_B_BEHAVIOURAL_SIMILARITY_IS_NOMINATION_ONLY",
            "PSI0F_B_GLOBAL_AVAILABILITY_NOT_OPERATION_LINKAGE",
        }),
        "interpretation": {
            "negative_outcome_inferred": False, "duplicates_collapsed": False,
            "conflicts_resolved": False, "operation_specific_coverage_inferred": False,
            "operator_identity_inferred": False, "entities_ranked_or_selected": False,
        },
        "authority": {key: False for key in sorted(AUTHORITY_KEYS)},
    }
    payload = _canonical(output)
    return KnownBehaviourOperationalSurface(
        canonical_surface=payload,
        input_digest=sha256(_canonical(normalized)).hexdigest(),
        surface_digest=sha256(payload).hexdigest(),
        contract_digest=contract.contract_digest,
    )


def verify_known_behaviour_operational_surface(
    contract: KnownBehaviourOperationalSurfaceContract,
    *,
    psi0e_summary: object,
    operational_families: object,
    surface: KnownBehaviourOperationalSurface,
) -> bool:
    expected = project_fixture_known_behaviour_operational_surface(
        contract, psi0e_summary=psi0e_summary, operational_families=operational_families,
    )
    if surface != expected:
        raise KnownBehaviourOperationalSurfaceError("PSI0F_B_SURFACE_REPLAY_MISMATCH")
    return True
