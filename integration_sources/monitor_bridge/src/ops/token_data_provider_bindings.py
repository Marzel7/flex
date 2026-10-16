"""Generic one-attempt production transports for token-data provider requests."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PROVIDER_BINDING_CONTRACT_VERSION = "TOKEN_DATA_PROVIDER_BINDING_CONTRACT_V1"
PROVIDER_TIMEOUT_SECONDS = 45
AUTHORITATIVE_BIRDEYE_CREDENTIAL_LABEL = "BIRDEYE"


def build_birdeye_ohlcv_request(*, address: str, interval: str, time_from: int, time_to: int) -> dict[str, Any]:
    """Canonical qualified V3 OHLCV request shared by live and historical paths."""
    if not address or interval not in {"1s", "1m", "15m", "1h", "4h", "1d"}: raise ValueError("UNQUALIFIED_OHLCV_PROFILE")
    if not (int(time_from) < int(time_to)): raise ValueError("INVALID_OHLCV_WINDOW")
    params={"address":address,"chart_type":"mcap","currency":"usd","mode":"range","padding":"false","time_from":int(time_from),"time_to":int(time_to),"type":interval}
    query=urlencode(params)
    return {"endpoint":"/defi/v3/ohlcv","request_parameters":params,"encoded_query":query,"header_names":["accept","X-API-KEY","x-chain"],"url":"https://public-api.birdeye.so/defi/v3/ohlcv?"+query}


def validate_birdeye_credential_label(label: str) -> None:
    """Reject any runtime composition that selects a legacy Birdeye alias."""
    if label != AUTHORITATIVE_BIRDEYE_CREDENTIAL_LABEL:
        raise ValueError("UNAUTHORIZED_BIRDEYE_CREDENTIAL_LABEL")


def birdeye_credential(environ: Mapping[str, str] | None = None) -> str:
    """Resolve only the authority-approved Birdeye credential, or fail closed."""
    values = os.environ if environ is None else environ
    configured = values.get(AUTHORITATIVE_BIRDEYE_CREDENTIAL_LABEL, "")
    credential = configured.strip().strip("'\"")
    if not credential:
        raise RuntimeError("MISSING_AUTHORITATIVE_BIRDEYE_CREDENTIAL")
    return credential


@dataclass(frozen=True)
class ProviderTransportOutcome:
    status_code: int
    payload: Any
    response_headers: Mapping[str, str]
    error_state: str | None = None

    @property
    def retry_after_seconds(self) -> int | None:
        value = self.response_headers.get("Retry-After")
        try:
            return max(0, min(60, int(value))) if value is not None else None
        except (TypeError, ValueError):
            return None


def _urlopen_transport(request: Request, *, timeout_seconds: int) -> ProviderTransportOutcome:
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read()
            return ProviderTransportOutcome(response.status, json.loads(body), dict(response.headers.items()))
    except HTTPError as error:
        body = error.read()
        try: payload = json.loads(body)
        except (TypeError, ValueError): payload = None
        return ProviderTransportOutcome(error.code, payload, dict(error.headers.items()), f"HTTP_{error.code}")
    except (URLError, TimeoutError) as error:
        if isinstance(error, TimeoutError):
            state = "TIMEOUT"
        else:
            reason = getattr(error, "reason", error)
            kind = type(reason).__name__.upper()
            text = str(reason).lower()
            stage = "DNS" if "name or service" in text or "nodename" in text else "TLS" if "ssl" in text or "certificate" in text else "CONNECT"
            state = f"TRANSPORT_{stage}_{kind}"
        return ProviderTransportOutcome(0, None, {}, state)


class HeliusProductionBinding:
    """Single Helius JSON-RPC attempt; retry ownership stays with durability."""
    def __init__(self, endpoint: str | None = None, transport: Callable = _urlopen_transport):
        self.endpoint = endpoint or os.environ.get("HELIUS_RPC_URL", "")
        self.transport = transport

    def __call__(self, request: Mapping[str, Any], *, timeout_seconds: int = PROVIDER_TIMEOUT_SECONDS) -> ProviderTransportOutcome:
        if timeout_seconds != PROVIDER_TIMEOUT_SECONDS: raise ValueError("UNQUALIFIED_TIMEOUT")
        if not self.endpoint: return ProviderTransportOutcome(0, None, {}, "MISSING_HELIUS_ENDPOINT")
        parameters = request["request_parameters"]
        key = "slot" if request["method_endpoint"] == "getBlock" else "signature"
        body = {"jsonrpc": "2.0", "id": 1, "method": request["method_endpoint"], "params": [parameters[key], {k:v for k,v in parameters.items() if k != key}]}
        return self.transport(Request(self.endpoint, data=json.dumps(body).encode(), headers={"Content-Type":"application/json"}), timeout_seconds=timeout_seconds)


class BirdeyeProductionBinding:
    """Single Birdeye attempt; it deliberately contains no retry loop."""
    def __init__(self, api_key: str | None = None, endpoint: str = "https://public-api.birdeye.so/defi/v3/ohlcv", transport: Callable = _urlopen_transport, account_gate: Any | None = None, account_context: Mapping[str, Any] | None = None, credential_label: str = AUTHORITATIVE_BIRDEYE_CREDENTIAL_LABEL):
        validate_birdeye_credential_label(credential_label)
        self.api_key = api_key or birdeye_credential()
        self.endpoint = endpoint
        self.transport = transport
        # The gate is injected by an explicitly configured runtime.  Leaving
        # it absent preserves existing frozen behavior until the separate
        # account-wide wiring review is authorized; tests can use a mock gate.
        self.account_gate = account_gate
        self.account_context = dict(account_context or {})

    def __call__(self, request: Mapping[str, Any], *, timeout_seconds: int = PROVIDER_TIMEOUT_SECONDS) -> ProviderTransportOutcome:
        if timeout_seconds != PROVIDER_TIMEOUT_SECONDS: raise ValueError("UNQUALIFIED_TIMEOUT")
        params = urlencode(request["request_parameters"])
        admission = None
        if self.account_gate is not None:
            values = request.get("request_parameters") or {}
            admission = self.account_gate.admit(
                caller=self.account_context.get("caller"), task=self.account_context.get("task"),
                operation=self.account_context.get("operation"), mint=values.get("address"),
                endpoint="/defi/v3/ohlcv", request_class=self.account_context.get("request_class"),
                query_window={k: values.get(k) for k in ("time_from", "time_to", "type")},
                attempt=int(self.account_context.get("attempt", 1)),
            )
        # This is the qualified Watchtower lifecycle dispatcher contract.
        headers = {"accept":"application/json", "X-API-KEY":self.api_key, "x-chain":"solana"}
        outcome = self.transport(Request(f"{self.endpoint}?{params}", headers=headers), timeout_seconds=timeout_seconds)
        if admission is not None:
            result = "HTTP_SUCCESS" if outcome.status_code == 200 else "HTTP_429" if outcome.status_code == 429 else "TRANSPORT_EXCEPTION" if outcome.status_code == 0 else "HTTP_RETRYABLE" if outcome.status_code >= 500 else "HTTP_TERMINAL"
            self.account_gate.record_transport_outcome(admission, outcome=result, status_code=outcome.status_code or None)
        return outcome


def production_provider_bindings(*, helius_endpoint: str | None = None, birdeye_api_key: str | None = None, helius_transport: Callable = _urlopen_transport, birdeye_transport: Callable = _urlopen_transport, birdeye_credential_label: str = AUTHORITATIVE_BIRDEYE_CREDENTIAL_LABEL) -> dict[tuple[str, str], Callable]:
    helius = HeliusProductionBinding(helius_endpoint, helius_transport)
    return {("Helius JSON-RPC", "getTransaction"): helius, ("Helius JSON-RPC", "getBlock"): helius,
            ("Birdeye", "/defi/v3/ohlcv"): BirdeyeProductionBinding(birdeye_api_key, transport=birdeye_transport, credential_label=birdeye_credential_label)}
