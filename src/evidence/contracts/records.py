"""Frozen EP1.3 normalized EvidenceRecord and fact-family contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from .identity import evidence_id, logical_fact_id, payload_digest


class FactFamily(str, Enum):
    TRANSACTION = "TransactionFact"
    ACCOUNT_PARTICIPATION = "AccountParticipationFact"
    INSTRUCTION = "InstructionFact"
    BALANCE = "BalanceFact"
    NATIVE_MOVEMENT = "NativeMovementFact"
    TOKEN_MOVEMENT = "TokenMovementFact"
    ACCOUNT_CLOSE = "AccountCloseFact"
    PROGRAM_EVENT = "ProgramEventFact"
    LAUNCH = "LaunchFact"
    ADDRESS_HISTORY = "AddressHistoryObservation"
    TRANSACTION_VERIFICATION = "TransactionVerificationObservation"
    EXTERNAL_REGISTRY = "ExternalRegistryObservation"


FACT_FIELDS: dict[FactFamily, frozenset[str]] = {
    FactFamily.TRANSACTION: frozenset({"signature", "slot", "block_time", "success", "error", "fee", "fee_payer", "message_version", "recent_blockhash", "account_count", "instruction_count", "inner_instructions_available", "logs_available", "confirmation_status"}),
    FactFamily.ACCOUNT_PARTICIPATION: frozenset({"signature", "account_index", "public_key", "is_signer", "is_writable", "is_fee_payer", "account_source", "lookup_table_address"}),
    FactFamily.INSTRUCTION: frozenset({"signature", "outer_instruction_index", "inner_instruction_index", "program_id", "account_indexes", "raw_instruction_data", "parsed_instruction_type", "parsed_fields", "execution_nesting_level"}),
    FactFamily.BALANCE: frozenset({"signature", "account", "account_index", "asset_type", "mint", "owner", "pre_balance", "post_balance", "delta", "decimals", "source_availability"}),
    FactFamily.NATIVE_MOVEMENT: frozenset({"signature", "instruction_position", "source", "destination", "amount_lamports", "program_id", "authority", "decode_method"}),
    FactFamily.TOKEN_MOVEMENT: frozenset({"signature", "instruction_position", "source_token_account", "destination_token_account", "source_owner", "destination_owner", "mint", "raw_amount", "decimals", "authority", "token_program"}),
    FactFamily.ACCOUNT_CLOSE: frozenset({"signature", "instruction_position", "program", "closed_account", "owner", "close_authority", "close_destination", "pre_close_balance", "returned_lamports", "token_mint"}),
    FactFamily.PROGRAM_EVENT: frozenset({"signature", "instruction_position", "program_id", "event_discriminator", "event_type", "event_payload", "event_accounts", "decoder_version"}),
    FactFamily.LAUNCH: frozenset({"mint", "creation_signature", "creation_instruction", "creation_timestamp", "creation_slot", "program_id", "creator_account", "creator_account_index", "creator_signer_state", "fee_payer", "source_platform"}),
    FactFamily.ADDRESS_HISTORY: frozenset({"address", "endpoint_method", "before_cursor", "until_cursor", "minimum_context_slot", "page_size", "returned_signatures", "returned_count", "page_complete", "provider_coverage_statement", "acquisition_timestamp"}),
    FactFamily.TRANSACTION_VERIFICATION: frozenset({"subject_evidence_id", "provider", "verification_method", "verification_result", "finality", "checked_at", "returned_artifact_digest", "error"}),
    FactFamily.EXTERNAL_REGISTRY: frozenset({"subject", "claimed_label", "registry", "registry_version", "source_url", "document_digest", "valid_from", "valid_to", "observed_at"}),
}


@dataclass(frozen=True)
class EvidenceProvenance:
    endpoint_method: str
    request_parameters_digest: str
    upstream_dependency: Optional[str]
    acquisition_path: str
    cache_source: str
    dependency_group: str
    parent_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    logical_fact_id: str
    fact_family: str
    fact_schema_version: str
    chain: str
    network: str
    natural_key: str
    payload: Mapping[str, Any]
    payload_digest: str
    raw_artifact_digest: str
    observed_at: int
    acquired_at: int
    source_id: str
    source_version: str
    provider: str
    provider_request_id: Optional[str]
    parser_id: str
    parser_version: str
    replay_version: str
    verification_state: str
    provenance_quality: str
    provenance: EvidenceProvenance
    corrects_evidence_id: Optional[str] = None
    created_at: int = 0

    @classmethod
    def create(cls, *, family: FactFamily, chain: str, network: str,
               natural_key: str, payload: Mapping[str, Any], raw_artifact_digest: str,
               observed_at: int, acquired_at: int, source_id: str,
               source_version: str, provider: str, provider_request_id: Optional[str],
               parser_id: str, parser_version: str, replay_version: str,
               verification_state: str, provenance_quality: str,
               provenance: EvidenceProvenance, fact_schema_version: str = "1",
               corrects_evidence_id: Optional[str] = None,
               created_at: int = 0) -> "EvidenceRecord":
        unknown = set(payload) - FACT_FIELDS[family]
        if unknown:
            raise ValueError(f"{family.value} contains non-contract fields: {sorted(unknown)}")
        digest = payload_digest(dict(payload))
        logical = logical_fact_id(
            fact_family=family.value, chain=chain, network=network,
            natural_key=natural_key,
        )
        observation = evidence_id(
            fact_family=family.value, fact_schema_version=fact_schema_version,
            logical_fact_id_value=logical, parser_id=parser_id,
            parser_version=parser_version, normalized_payload_digest=digest,
            raw_artifact_digest=raw_artifact_digest,
        )
        return cls(
            evidence_id=observation, logical_fact_id=logical,
            fact_family=family.value, fact_schema_version=fact_schema_version,
            chain=chain, network=network, natural_key=natural_key,
            payload=dict(payload), payload_digest=digest,
            raw_artifact_digest=raw_artifact_digest, observed_at=int(observed_at),
            acquired_at=int(acquired_at), source_id=source_id,
            source_version=source_version, provider=provider,
            provider_request_id=provider_request_id, parser_id=parser_id,
            parser_version=parser_version, replay_version=replay_version,
            verification_state=verification_state,
            provenance_quality=provenance_quality, provenance=provenance,
            corrects_evidence_id=corrects_evidence_id, created_at=int(created_at),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
