"""Bounded Helius adapter for DEV-019's isolated mesh-discovery engine.

This module composes the existing single-attempt ``HeliusJsonRpcTransport``.
It has no credential fallback, retry loop, or production persistence path.
Callers must supply the existing authorized ``HELIUS_RPC_URL`` explicitly;
the value is never recorded in compact evidence.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from src.acquisition.b2z_execution_boundary import JsonRpcTransport
from src.ops.treasury_mesh_discovery_engine import DiscoveryError, ProviderLimited


MAX_SUPPORTED_TRANSACTION_VERSION = 1


class HeliusMeshRpcError(DiscoveryError):
    """A non-rate-limited RPC failure; no fallback or retry is attempted."""


def resolve_authoritative_helius_endpoint(environ: Mapping[str, str]) -> str:
    """Accept only the existing explicit Helius RPC authority."""
    endpoint = str(environ.get("HELIUS_RPC_URL") or "")
    if not endpoint.startswith("https://") or "helius-rpc.com" not in endpoint:
        raise HeliusMeshRpcError("AUTHORITATIVE_HELIUS_ENDPOINT_REQUIRED")
    return endpoint


class HeliusMeshDiscoveryClient:
    """One-attempt, sequential RPC adapter with a physical-call ceiling."""

    def __init__(self, *, transport: JsonRpcTransport, max_rpc_calls: int) -> None:
        if max_rpc_calls <= 0:
            raise HeliusMeshRpcError("MESH_RPC_BUDGET_REQUIRED")
        self.transport = transport
        self.max_rpc_calls = max_rpc_calls
        self.rpc_request_count = 0

    def _call(self, method: str, params: list[Any]) -> Any:
        if self.rpc_request_count >= self.max_rpc_calls:
            raise ProviderLimited("MESH_RPC_BUDGET_EXHAUSTED")
        before = getattr(self.transport, "physical_request_count", None)
        response = self.transport.post_json({
            "jsonrpc": "2.0", "id": self.rpc_request_count + 1,
            "method": method, "params": params,
        })
        after = getattr(self.transport, "physical_request_count", None)
        self.rpc_request_count += 1
        if before is not None and after != before + 1:
            raise HeliusMeshRpcError("HELIUS_TRANSPORT_COUNTER_MISMATCH")
        if not isinstance(response, Mapping):
            raise HeliusMeshRpcError("HELIUS_RPC_RESPONSE_MALFORMED")
        error = response.get("error")
        if error is not None:
            code = error.get("code") if isinstance(error, Mapping) else None
            if code == 429:
                raise ProviderLimited("HELIUS_RATE_LIMIT")
            raise HeliusMeshRpcError("HELIUS_RPC_NON_SUCCESS")
        return response.get("result")

    def get_signatures(self, address: str, direction: str, before: str | None, limit: int) -> Iterable[Mapping[str, Any]]:
        # Address history is direction-neutral. Direction is applied only to
        # compact decoded facts, so selected signatures are not omitted.
        if direction not in {"INBOUND", "OUTBOUND"} or not address or limit < 1 or limit > 20:
            raise HeliusMeshRpcError("INVALID_SIGNATURE_PAGE_REQUEST")
        options: dict[str, Any] = {"limit": limit, "commitment": "confirmed"}
        if before:
            options["before"] = before
        result = self._call("getSignaturesForAddress", [address, options])
        if not isinstance(result, list) or not all(isinstance(item, Mapping) for item in result):
            raise HeliusMeshRpcError("HELIUS_SIGNATURE_PAGE_MALFORMED")
        return result

    def get_transaction(self, signature: str, config: Mapping[str, Any]) -> Mapping[str, Any] | None:
        request_config = dict(config)
        if request_config.get("maxSupportedTransactionVersion") != MAX_SUPPORTED_TRANSACTION_VERSION:
            raise HeliusMeshRpcError("TRANSACTION_VERSION_CONTRACT_MISMATCH")
        result = self._call("getTransaction", [signature, request_config])
        if result is not None and not isinstance(result, Mapping):
            raise HeliusMeshRpcError("HELIUS_TRANSACTION_MALFORMED")
        return result
