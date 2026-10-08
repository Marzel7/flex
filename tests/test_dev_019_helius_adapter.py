from __future__ import annotations

import pytest

from src.ops.treasury_mesh_discovery_engine import ProviderLimited
from src.ops.treasury_mesh_helius_adapter import HeliusMeshDiscoveryClient, HeliusMeshRpcError, resolve_authoritative_helius_endpoint


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.physical_request_count = 0

    def post_json(self, request):
        self.requests.append(request)
        self.physical_request_count += 1
        return self.responses.pop(0)


def test_existing_helius_authority_must_be_explicit_and_no_fallback_is_constructed():
    assert resolve_authoritative_helius_endpoint({"HELIUS_RPC_URL": "https://mainnet.helius-rpc.com/?api-key=redacted"}).startswith("https://")
    for value in ({}, {"HELIUS_RPC_URL": "https://example.invalid"}, {"HELIUS_RPC_URL": "http://helius-rpc.com"}):
        with pytest.raises(HeliusMeshRpcError):
            resolve_authoritative_helius_endpoint(value)


def test_adapter_uses_complete_direction_neutral_pages_and_version_one_decoder_contract():
    transport = FakeTransport([{"result": [{"signature": "sig-a"}]}, {"result": {"version": 1, "transaction": {}, "meta": {}}}])
    client = HeliusMeshDiscoveryClient(transport=transport, max_rpc_calls=2)
    assert list(client.get_signatures("A", "OUTBOUND", None, 20)) == [{"signature": "sig-a"}]
    assert client.get_transaction("sig-a", {"encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 1})["version"] == 1
    assert transport.requests[0]["method"] == "getSignaturesForAddress"
    assert transport.requests[1]["params"][1]["maxSupportedTransactionVersion"] == 1
    assert client.rpc_request_count == 2


def test_rate_limit_and_budget_stop_without_retry():
    transport = FakeTransport([{"error": {"code": 429}}])
    client = HeliusMeshDiscoveryClient(transport=transport, max_rpc_calls=1)
    with pytest.raises(ProviderLimited):
        list(client.get_signatures("A", "INBOUND", None, 1))
    assert client.rpc_request_count == 1 and len(transport.requests) == 1
    with pytest.raises(ProviderLimited):
        list(client.get_signatures("A", "INBOUND", None, 1))
    assert len(transport.requests) == 1
