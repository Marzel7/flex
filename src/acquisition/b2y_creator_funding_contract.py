"""Local-only amended creator-funding evidence contract.

The transport is injected. This module owns no endpoint, credential, HTTP
client, database, queue, service, retry, pagination, or cache dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class B2YTransport(Protocol):
    def get_transaction(self, signature: str) -> dict[str, Any]: ...
    def get_enhanced_transactions(self, address: str, *, limit: int) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class B2YMember:
    mint: str
    migration_signature: str


@dataclass(frozen=True)
class ResponseProjection:
    request_number: int
    response_kind: str
    signature: str | None
    block_time: int | None
    creator: str | None
    source: str | None
    destination: str | None
    lamports: int | None
    lineage_valid: bool


@dataclass(frozen=True)
class B2YResult:
    request_count: int
    outcome: str
    creator: str | None
    candidate_signature: str | None
    projections: tuple[ResponseProjection, ...]


def _transaction_result(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result") if isinstance(response, dict) else None
    # Fakes and previously qualified callers may inject the result directly.
    if isinstance(result, dict):
        return result
    if isinstance(response, dict) and isinstance(response.get("transaction"), dict):
        return response
    raise ValueError("B2Y_TRANSACTION_RESULT_MISSING")


def _keys(transaction: dict[str, Any]) -> list[dict[str, Any]]:
    keys = transaction.get("transaction", {}).get("message", {}).get("accountKeys", [])
    if not isinstance(keys, list):
        raise ValueError("B2Y_MALFORMED_ACCOUNT_KEYS")
    normalized = []
    for key in keys:
        if isinstance(key, str):
            normalized.append({"pubkey": key, "signer": False})
        elif isinstance(key, dict) and isinstance(key.get("pubkey"), str):
            normalized.append(key)
    return normalized


def _creator(transaction: dict[str, Any], mint: str) -> str:
    keys = _keys(transaction)
    if mint not in {key.get("pubkey") for key in keys}:
        raise ValueError("B2Y_MINT_NOT_IN_MIGRATION_TRANSACTION")
    signers = [key["pubkey"] for key in keys if key.get("signer") is True]
    if len(signers) != 1:
        raise ValueError("B2Y_CREATOR_NOT_UNIQUE")
    return signers[0]


def _enhanced_inbound_candidate(
    rows: list[dict[str, Any]], creator: str, migration_time: int
) -> tuple[str, int, str, int] | None:
    """Select the first provider-ordered row that itself proves inbound SOL."""
    for row in rows:
        signature, timestamp = row.get("signature"), row.get("timestamp")
        if not isinstance(timestamp, int):
            timestamp = row.get("blockTime")
        if not isinstance(signature, str) or not isinstance(timestamp, int) or timestamp >= migration_time:
            continue
        transfers = row.get("nativeTransfers")
        if not isinstance(transfers, list):
            continue
        for transfer in transfers:
            if not isinstance(transfer, dict) or transfer.get("toUserAccount") != creator:
                continue
            source, amount = transfer.get("fromUserAccount"), transfer.get("amount")
            if isinstance(source, str) and source != creator and isinstance(amount, int) and amount > 0:
                return signature, timestamp, source, amount
    return None


def _parsed_inbound_transfer(transaction: dict[str, Any], creator: str) -> tuple[str, int] | None:
    message = transaction.get("transaction", {}).get("message", {})
    instructions = message.get("instructions", [])
    if not isinstance(instructions, list):
        raise ValueError("B2Y_INSTRUCTIONS_MALFORMED")
    combined = list(instructions)
    for group in transaction.get("meta", {}).get("innerInstructions", []):
        if isinstance(group, dict) and isinstance(group.get("instructions"), list):
            combined.extend(group["instructions"])
    for instruction in combined:
        if not isinstance(instruction, dict) or instruction.get("program") != "system":
            continue
        parsed = instruction.get("parsed")
        if not isinstance(parsed, dict) or parsed.get("type") not in {"transfer", "transferWithSeed"}:
            continue
        info = parsed.get("info", {})
        source, lamports = info.get("source"), info.get("lamports")
        if (info.get("destination") == creator and isinstance(source, str) and source != creator
                and isinstance(lamports, int) and lamports > 0):
            return source, lamports
    return None


class B2YCreatorFundingProbe:
    """Exactly three ordered calls maximum; transfer semantics precede proof selection."""

    def __init__(self, transport: B2YTransport, *, enhanced_limit: int = 100) -> None:
        if enhanced_limit != 100:
            raise ValueError("B2Y_ENHANCED_LIMIT_MUST_BE_100")
        self.transport = transport
        self.enhanced_limit = enhanced_limit

    def probe_once(self, member: B2YMember) -> B2YResult:
        projections: list[ResponseProjection] = []
        migration = _transaction_result(self.transport.get_transaction(member.migration_signature))
        creator = _creator(migration, member.mint)
        migration_time = migration.get("blockTime")
        projections.append(ResponseProjection(
            1, "MIGRATION_LINEAGE", member.migration_signature,
            migration_time if isinstance(migration_time, int) else None,
            creator, None, None, None, isinstance(migration_time, int),
        ))
        if not isinstance(migration_time, int):
            return B2YResult(1, "MALFORMED_RESPONSE", creator, None, tuple(projections))

        enhanced = self.transport.get_enhanced_transactions(creator, limit=self.enhanced_limit)
        if not isinstance(enhanced, list):
            projections.append(ResponseProjection(2, "ENHANCED_HISTORY", None, None, creator,
                                                  None, None, None, False))
            return B2YResult(2, "MALFORMED_RESPONSE", creator, None, tuple(projections))
        candidate = _enhanced_inbound_candidate(enhanced, creator, migration_time)
        if candidate is None:
            projections.append(ResponseProjection(2, "ENHANCED_INBOUND_SOL", None, None, creator,
                                                  None, creator, None, False))
            return B2YResult(2, "NO_PRE_MIGRATION_INBOUND_SOL_CANDIDATE", creator, None, tuple(projections))
        signature, candidate_time, source, amount = candidate
        projections.append(ResponseProjection(
            2, "ENHANCED_INBOUND_SOL", signature, candidate_time, creator,
            source, creator, amount, True,
        ))

        proof = _transaction_result(self.transport.get_transaction(signature))
        proof_time = proof.get("blockTime")
        parsed = _parsed_inbound_transfer(proof, creator)
        source_proof, amount_proof = parsed if parsed else (None, None)
        valid = (isinstance(proof_time, int) and proof_time < migration_time and parsed is not None
                 and source_proof == source and amount_proof == amount)
        projections.append(ResponseProjection(
            3, "PARSED_TRANSACTION_PROOF", signature,
            proof_time if isinstance(proof_time, int) else None, creator,
            source_proof, creator, amount_proof, valid,
        ))
        outcome = "SUCCESS" if valid else "CANDIDATE_PROOF_MISMATCH"
        return B2YResult(3, outcome, creator, signature, tuple(projections))
