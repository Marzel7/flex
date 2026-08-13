"""Local-only B2Y creator-funding qualification boundary.

The transport is injected.  This file owns neither a provider endpoint nor
credentials and cannot make a request by itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class B2YTransport(Protocol):
    def get_transaction(self, signature: str) -> dict[str, Any]: ...
    def get_signatures_for_address(self, address: str, *, limit: int) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class B2YMember:
    mint: str
    migration_signature: str


@dataclass(frozen=True)
class B2YResult:
    request_count: int
    outcome: str
    creator: str | None
    candidate_signature: str | None


def _keys(transaction: dict[str, Any]) -> list[dict[str, Any]]:
    keys = transaction.get("transaction", {}).get("message", {}).get("accountKeys", [])
    if not isinstance(keys, list):
        raise ValueError("B2Y_MALFORMED_ACCOUNT_KEYS")
    return [key for key in keys if isinstance(key, dict)]


def _creator(transaction: dict[str, Any], mint: str) -> str:
    keys = _keys(transaction)
    if mint not in {key.get("pubkey") for key in keys}:
        raise ValueError("B2Y_MINT_NOT_IN_MIGRATION_TRANSACTION")
    signers = [key.get("pubkey") for key in keys if key.get("signer") and isinstance(key.get("pubkey"), str)]
    if not signers:
        raise ValueError("B2Y_CREATOR_NOT_RESOLVABLE")
    return signers[0]


class B2YCreatorFundingProbe:
    """Exactly three ordered calls maximum; stop on any non-success response."""
    def __init__(self, transport: B2YTransport) -> None:
        self.transport = transport

    def probe_once(self, member: B2YMember) -> B2YResult:
        migration = self.transport.get_transaction(member.migration_signature)  # request 1
        creator = _creator(migration, member.mint)
        migration_time = migration.get("blockTime")
        if not isinstance(migration_time, int):
            return B2YResult(1, "MALFORMED_RESPONSE", creator, None)
        rows = self.transport.get_signatures_for_address(creator, limit=1000)  # request 2
        candidates = [row for row in rows if isinstance(row, dict) and isinstance(row.get("signature"), str)
                      and isinstance(row.get("blockTime"), int) and row["blockTime"] < migration_time]
        if not candidates:
            return B2YResult(2, "NO_PRE_MIGRATION_CANDIDATE", creator, None)
        candidate = candidates[0]["signature"]
        funding = self.transport.get_transaction(candidate)  # request 3
        if not isinstance(funding, dict) or funding.get("result") is None:
            return B2YResult(3, "MALFORMED_RESPONSE", creator, candidate)
        return B2YResult(3, "SUCCESS", creator, candidate)
