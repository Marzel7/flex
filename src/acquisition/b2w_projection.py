"""B2W's local-only migration-signature projection and one-shot adapter.

There is intentionally no HTTP client, endpoint, credential, environment
lookup, database, queue, or service dependency here.  The caller supplies a
single-call transport, so this module can be fully qualified with a fake.

The projection proves only the migration transaction's mint/signature lineage.
It cannot, in one transaction request, establish creator-funding evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from src.acquisition.b2n_qualification import B2NManifest, OneRequestResponse


@dataclass(frozen=True)
class B2WRequestInput:
    sample_ordinal: int
    mint: str
    census_event_id: str
    migration_signature: str


@dataclass(frozen=True)
class B2WInputProjection:
    members: tuple[B2WRequestInput, ...]

    @classmethod
    def from_census(cls, manifest: B2NManifest, records: list[dict[str, Any]]) -> "B2WInputProjection":
        manifest.validate()
        indexed = {str(row.get("event_id")): row for row in records}
        projected = []
        for member in manifest.members:
            row = indexed.get(member.census_event_id)
            signature = row.get("signature") if isinstance(row, dict) else None
            if not isinstance(signature, str) or not signature:
                raise ValueError("B2W_MIGRATION_SIGNATURE_NOT_FROZEN")
            if row.get("mint") != member.mint or row.get("subscription") != "subscribeMigration":
                raise ValueError("B2W_MIGRATION_LINEAGE_MISMATCH")
            projected.append(B2WRequestInput(member.sample_ordinal, member.mint, member.census_event_id, signature))
        return cls(tuple(projected))


class JsonRpcTransport(Protocol):
    def post_json(self, request: dict[str, Any]) -> dict[str, Any]: ...


class MigrationGetTransactionAdapter:
    """One physical ``getTransaction`` attempt for an already-frozen signature."""
    def __init__(self, transport: JsonRpcTransport, projection: B2WInputProjection) -> None:
        self.transport, self.by_mint = transport, {item.mint: item for item in projection.members}

    def acquire_once(self, *, mint: str) -> OneRequestResponse:
        item = self.by_mint.get(mint)
        if item is None:
            raise ValueError("B2W_MINT_NOT_IN_FROZEN_PROJECTION")
        response = self.transport.post_json({
            "jsonrpc": "2.0", "id": item.sample_ordinal, "method": "getTransaction",
            "params": [item.migration_signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        })
        if not isinstance(response, dict) or response.get("error") is not None:
            return OneRequestResponse("RPC_ERROR", False, False, error_class="B2W_RPC_ERROR")
        result = response.get("result")
        if not isinstance(result, dict):
            return OneRequestResponse("MALFORMED_RESPONSE", False, False, error_class="B2W_RESULT_MISSING")
        keys = result.get("transaction", {}).get("message", {}).get("accountKeys", [])
        addresses = {key if isinstance(key, str) else key.get("pubkey") for key in keys if isinstance(key, (str, dict))}
        if mint not in addresses:
            return OneRequestResponse("MALFORMED_RESPONSE", False, False, provider_signature=item.migration_signature, error_class="B2W_MINT_NOT_IN_TRANSACTION")
        # A migration transaction does not establish a creator-funding edge.
        return OneRequestResponse("SUCCESS", False, True, provider_signature=item.migration_signature, provider_slot=result.get("slot"), error_class="B2W_MIGRATION_LINEAGE_ONLY")
