"""Generic one-attempt production transports for token-data provider requests."""
from __future__ import annotations

import json
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PROVIDER_BINDING_CONTRACT_VERSION = "TOKEN_DATA_PROVIDER_BINDING_CONTRACT_V1"
PROVIDER_TIMEOUT_SECONDS = 45


def build_birdeye_ohlcv_request(*, address: str, interval: str, time_from: int, time_to: int) -> dict[str, Any]:
    """Canonical qualified V3 OHLCV request shared by live and historical paths."""
    if not address or interval not in {"1s", "1m", "15m", "1h", "4h", "1d"}: raise ValueError("UNQUALIFIED_OHLCV_PROFILE")
    if not (int(time_from) < int(time_to)): raise ValueError("INVALID_OHLCV_WINDOW")
    params={"address":address,"chart_type":"mcap","currency":"usd","mode":"range","padding":"false","time_from":int(time_from),"time_to":int(time_to),"type":interval}
    query=urlencode(params)
    return {"endpoint":"/defi/v3/ohlcv","request_parameters":params,"encoded_query":query,"header_names":["accept","X-API-KEY","x-chain"],"url":"https://public-api.birdeye.so/defi/v3/ohlcv?"+query}


def birdeye_credential() -> str:
    """Repository-standard local configuration resolution; never logs a value."""
    configured = os.environ.get("BIRDEYE_KK") or os.environ.get("BIRDEYE")
    if configured:
        return configured.strip().strip("'\"")
    env_file = Path(__file__).resolve().parents[2] / ".env"
    values: dict[str, str] = {}
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].lstrip()
            if line.startswith("BIRDEYE_KK=") or line.startswith("BIRDEYE="):
                name, value = line.split("=", 1)
                values[name] = value.split("#", 1)[0].strip().strip("'\"")
    except OSError:
        pass
    return values.get("BIRDEYE_KK") or values.get("BIRDEYE", "")


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
    def __init__(self, api_key: str | None = None, endpoint: str = "https://public-api.birdeye.so/defi/v3/ohlcv", transport: Callable = _urlopen_transport):
        self.api_key = api_key or birdeye_credential()
        self.endpoint = endpoint
        self.transport = transport

    def __call__(self, request: Mapping[str, Any], *, timeout_seconds: int = PROVIDER_TIMEOUT_SECONDS) -> ProviderTransportOutcome:
        if timeout_seconds != PROVIDER_TIMEOUT_SECONDS: raise ValueError("UNQUALIFIED_TIMEOUT")
        params = urlencode(request["request_parameters"])
        # This is the qualified Watchtower lifecycle dispatcher contract.
        headers = {"accept":"application/json", "X-API-KEY":self.api_key, "x-chain":"solana"}
        return self.transport(Request(f"{self.endpoint}?{params}", headers=headers), timeout_seconds=timeout_seconds)


def production_provider_bindings(*, helius_endpoint: str | None = None, birdeye_api_key: str | None = None, helius_transport: Callable = _urlopen_transport, birdeye_transport: Callable = _urlopen_transport) -> dict[tuple[str, str], Callable]:
    helius = HeliusProductionBinding(helius_endpoint, helius_transport)
    return {("Helius JSON-RPC", "getTransaction"): helius, ("Helius JSON-RPC", "getBlock"): helius,
            ("Birdeye", "/defi/v3/ohlcv"): BirdeyeProductionBinding(birdeye_api_key, transport=birdeye_transport)}
