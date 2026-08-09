"""Primitive Authority Contract v1 registry and deterministic projection rules."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .contracts import PrimitiveType


class SemanticType(str, Enum):
    EVENT_FACT = "EVENT_FACT"
    IMMUTABLE_DERIVATION = "IMMUTABLE_DERIVATION"
    CURRENT_STATE_AGGREGATE = "CURRENT_STATE_AGGREGATE"
    HISTORICAL_SNAPSHOT = "HISTORICAL_SNAPSHOT"


class CohortSensitivity(str, Enum):
    COHORT_INVARIANT = "COHORT_INVARIANT"
    COHORT_GROWING = "COHORT_GROWING"
    STATE_DEPENDENT = "STATE_DEPENDENT"


class AuthorityRule(str, Enum):
    ALL_CURRENT_VERSION = "ALL_CURRENT_VERSION"
    LATEST_PER_GROUP = "LATEST_PER_GROUP"
    CORRECTED_FRESHNESS = "CORRECTED_FRESHNESS"


@dataclass(frozen=True)
class FamilyAuthorityContract:
    semantic_type: SemanticType
    cohort_sensitivity: CohortSensitivity
    authority_rule: AuthorityRule
    grouping_fields: tuple[str, ...]
    consumer_policy: str = "CURRENT_AUTHORITATIVE"
    replay_policy: str = "CURRENT_STATE_REPLAY"
    historical_access: str = "ALL_PERSISTED_EXPLICIT_QUERY"
    version_policy: str = "SEMANTIC_CHANGE_MUST_INCREMENT"
    current_versions: tuple[str, ...] = ("1",)


CONTRACT_VERSION = "1.0.0"

FAMILY_CONTRACTS: Mapping[str, FamilyAuthorityContract] = {
    PrimitiveType.SYSTEM_TRANSFER.value: FamilyAuthorityContract(
        SemanticType.EVENT_FACT, CohortSensitivity.COHORT_INVARIANT,
        AuthorityRule.ALL_CURRENT_VERSION, ("signature", "source", "destination")),
    PrimitiveType.DIRECT_COUNTERPARTY.value: FamilyAuthorityContract(
        SemanticType.EVENT_FACT, CohortSensitivity.COHORT_INVARIANT,
        AuthorityRule.ALL_CURRENT_VERSION, ("signature", "source", "destination")),
    PrimitiveType.LAUNCH_SIGNER.value: FamilyAuthorityContract(
        SemanticType.IMMUTABLE_DERIVATION, CohortSensitivity.COHORT_INVARIANT,
        AuthorityRule.ALL_CURRENT_VERSION, ("mint", "signature", "wallet")),
    PrimitiveType.WSOL_CLOSE.value: FamilyAuthorityContract(
        SemanticType.IMMUTABLE_DERIVATION, CohortSensitivity.COHORT_INVARIANT,
        AuthorityRule.ALL_CURRENT_VERSION, ("signature", "temporary_wsol_account")),
    PrimitiveType.PROGRAM_INTERACTION.value: FamilyAuthorityContract(
        SemanticType.IMMUTABLE_DERIVATION, CohortSensitivity.COHORT_INVARIANT,
        AuthorityRule.ALL_CURRENT_VERSION, ("signature", "program", "wallet")),
    PrimitiveType.SHARED_TRANSACTION.value: FamilyAuthorityContract(
        SemanticType.IMMUTABLE_DERIVATION, CohortSensitivity.COHORT_INVARIANT,
        AuthorityRule.ALL_CURRENT_VERSION, ("signature",)),
    PrimitiveType.LAUNCH_ACTIVATION.value: FamilyAuthorityContract(
        SemanticType.IMMUTABLE_DERIVATION, CohortSensitivity.COHORT_INVARIANT,
        AuthorityRule.ALL_CURRENT_VERSION, ("mint", "creator", "activation_signature")),
    PrimitiveType.ECONOMIC_FUNDING.value: FamilyAuthorityContract(
        SemanticType.IMMUTABLE_DERIVATION, CohortSensitivity.COHORT_INVARIANT,
        AuthorityRule.ALL_CURRENT_VERSION, ("signature", "funder", "recipient")),
    PrimitiveType.WALLET_FRESH_AT_EVENT.value: FamilyAuthorityContract(
        SemanticType.HISTORICAL_SNAPSHOT, CohortSensitivity.STATE_DEPENDENT,
        AuthorityRule.CORRECTED_FRESHNESS, ("wallet", "reference_event")),
    PrimitiveType.REPEATED_COUNTERPARTY.value: FamilyAuthorityContract(
        SemanticType.CURRENT_STATE_AGGREGATE, CohortSensitivity.COHORT_GROWING,
        AuthorityRule.LATEST_PER_GROUP, ("source", "destination")),
    PrimitiveType.BEHAVIOURAL_TIMING.value: FamilyAuthorityContract(
        SemanticType.CURRENT_STATE_AGGREGATE, CohortSensitivity.COHORT_GROWING,
        AuthorityRule.LATEST_PER_GROUP, ("subject", "ordering", "event_scope")),
}


def contract_for(family: str) -> FamilyAuthorityContract:
    try:
        return FAMILY_CONTRACTS[family]
    except KeyError as exc:
        raise ValueError(f"unregistered Primitive family: {family}") from exc


def corrected_freshness(parameters: Mapping[str, Any]) -> bool:
    return (parameters.get("history_order") == "NEWEST_FIRST" and
            parameters.get("reference_boundary") == "STRICTLY_PRECEDING")


def authority_group(family: str, subjects: tuple[str, ...], parameters: Mapping[str, Any],
                    output: Mapping[str, Any], version: str) -> tuple[Any, ...]:
    contract = contract_for(family)
    if family == PrimitiveType.BEHAVIOURAL_TIMING.value:
        semantic = (subjects[0] if subjects else None, parameters.get("ordering"),
                    parameters.get("event_scope"))
    else:
        semantic = tuple(output.get(field) for field in contract.grouping_fields)
    return (family, version, *semantic)


def authority_rank(family: str, evidence_count: int, window_start: int | None,
                   window_end: int | None, output: Mapping[str, Any],
                   generated_at: int, primitive_id: str) -> tuple[Any, ...]:
    if family == PrimitiveType.REPEATED_COUNTERPARTY.value:
        magnitude = output.get("transaction_count") or 0
    elif family == PrimitiveType.BEHAVIOURAL_TIMING.value:
        magnitude = output.get("sample_count") or 0
    else:
        magnitude = 0
    return (generated_at, window_end is not None, window_end or 0, magnitude, evidence_count,
            window_start is not None, window_start or 0, primitive_id)
