from __future__ import annotations

import asyncio

import pytest

from src.acquisition.transaction import (
    SharedTransactionAcquisition,
    acquisition_scope,
)


class _Response:
    def __init__(self, status, data, headers=None):
        self.status = status
        self._data = data
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._data

    async def text(self):
        return str(self._data)


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)


class _Cache:
    def __init__(self):
        self.values = {}
        self.set_calls = []

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, method):
        self.values[key] = value
        self.set_calls.append((key, value, method))


@pytest.mark.asyncio
async def test_request_metadata_is_complete_and_context_local():
    telemetry = []
    metrics = []
    session = _Session([_Response(200, {"result": [1]})])
    client = SharedTransactionAcquisition(session, telemetry_sink=telemetry.append)

    with acquisition_scope(purpose="creator_funding", creator="creator-A", launch="mint-A"):
        response = await client.request_once(
            http_method="POST",
            url="https://mainnet.helius-rpc.com/",
            json_payload={"method": "getTransaction"},
            timeout_seconds=30,
            request_type="json_rpc",
            method="getTransaction",
            page_number=2,
            cursor="cursor-A",
            cache_state="miss",
            retry_count=1,
            metrics_sink=lambda **kwargs: metrics.append(kwargs),
            metric_fields={"section": "creator_funding", "method": "getTransaction"},
        )

    metadata = response.metadata
    assert metadata.acquisition_id
    assert metadata.purpose == "creator_funding"
    assert metadata.creator == "creator-A"
    assert metadata.launch == "mint-A"
    assert metadata.request_type == "json_rpc"
    assert metadata.provider == "helius_rpc"
    assert metadata.method == "getTransaction"
    assert metadata.page_number == 2
    assert metadata.cursor == "cursor-A"
    assert metadata.timestamp > 0
    assert metadata.cache_state == "miss"
    assert metadata.retry_count == 1
    assert telemetry == [metadata]
    assert metrics[0]["status_code"] == 200
    assert metrics[0]["section"] == "creator_funding"
    assert metrics[0]["method"] == "getTransaction"


@pytest.mark.asyncio
async def test_legacy_failover_preserves_request_order_count_and_result():
    session = _Session([
        _Response(503, {"error": "unavailable"}),
        _Response(200, {"result": {"signature": "sig-A"}}),
    ])
    metrics = []
    telemetry = []
    client = SharedTransactionAcquisition(
        session, semaphore=asyncio.Semaphore(8), telemetry_sink=telemetry.append
    )
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": ["sig-A"]}

    result = await client.json_rpc_legacy(
        payload,
        rpc_urls=["https://mainnet.helius-rpc.com/", "https://api.mainnet-beta.solana.com"],
        max_retries=5,
        timeout_seconds=30,
        metrics_sink=lambda **kwargs: metrics.append(kwargs),
        cache_action="miss",
        credits_saved=0,
    )

    assert result == {"result": {"signature": "sig-A"}}
    assert [call[:2] for call in session.calls] == [
        ("POST", "https://mainnet.helius-rpc.com/"),
        ("POST", "https://api.mainnet-beta.solana.com"),
    ]
    assert all(call[2]["json"] == payload for call in session.calls)
    assert [item["status_code"] for item in metrics] == [503, 200]
    assert [item["retries"] for item in metrics] == [0, 0]
    assert len({item.acquisition_id for item in telemetry}) == 1
    assert [item.provider for item in telemetry] == [
        "helius_rpc", "solana_public_rpc"
    ]


@pytest.mark.asyncio
async def test_non_retryable_rpc_error_stops_without_extra_requests():
    session = _Session([_Response(200, {"error": {"code": -32602}})])
    metrics = []
    client = SharedTransactionAcquisition(session)

    result = await client.json_rpc_legacy(
        {"method": "getTransaction"},
        rpc_urls=["https://mainnet.helius-rpc.com/", "https://api.mainnet-beta.solana.com"],
        max_retries=5,
        timeout_seconds=30,
        metrics_sink=lambda **kwargs: metrics.append(kwargs),
        cache_action="miss",
        credits_saved=0,
    )

    assert result is None
    assert len(session.calls) == 1
    assert metrics[0]["error"] == "RPC error -32602"


def test_cache_adapter_preserves_existing_cache_contract():
    cache = _Cache()
    SharedTransactionAcquisition.cache_set(cache, "key", {"result": 1}, "getTransaction")
    assert SharedTransactionAcquisition.cache_get(cache, "key") == {"result": 1}
    assert cache.set_calls == [("key", {"result": 1}, "getTransaction")]
    assert SharedTransactionAcquisition.cache_get(None, "key") is None


def test_evidence_platform_is_not_imported_or_called():
    source = __import__("inspect").getsource(
        __import__("src.acquisition.transaction", fromlist=["SharedTransactionAcquisition"])
    )
    assert "src.evidence" not in source
    assert "evidence_writer" not in source


def test_creator_funding_has_no_direct_transaction_http_calls():
    import inspect
    from src.extractors.realtime_creator_funding_extractor import (
        RealTimeCreatorFundingExtractor,
    )

    source = inspect.getsource(RealTimeCreatorFundingExtractor)
    assert "self.session.post(" not in source
    assert "self.session.get(" not in source
