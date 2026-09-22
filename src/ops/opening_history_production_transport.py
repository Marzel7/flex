"""Bounded JSON-RPC transport for the opening-history production runner."""
from __future__ import annotations
import json, os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

VERSION = "OPENING_HISTORY_PRODUCTION_RPC_TRANSPORT_V1"
METHODS = frozenset({"getTransaction", "getBlock"})

class ProductionTransportError(RuntimeError): pass
class TransportAmbiguousError(ProductionTransportError): pass

class OpeningHistoryProductionTransport:
    def __init__(self, endpoint=None, *, timeout=30, opener=urlopen):
        self.endpoint = endpoint or os.environ.get("HELIUS_RPC_URL")
        if not self.endpoint: raise ProductionTransportError("OPENING_HISTORY_RPC_ENDPOINT_UNAVAILABLE")
        self.timeout, self.opener, self.calls = timeout, opener, 0
    def __call__(self, method, params):
        if method not in METHODS or not isinstance(params, list): raise ProductionTransportError("OPENING_HISTORY_RPC_METHOD_FORBIDDEN")
        body = json.dumps({"jsonrpc":"2.0","id":self.calls + 1,"method":method,"params":params}, separators=(",", ":")).encode()
        request = Request(self.endpoint, data=body, headers={"Content-Type":"application/json"})
        try:
            with self.opener(request, timeout=self.timeout) as response:
                if getattr(response, "status", 200) != 200: raise ProductionTransportError("OPENING_HISTORY_RPC_HTTP_ERROR")
                raw = response.read()
        except HTTPError as exc: raise ProductionTransportError("OPENING_HISTORY_RPC_HTTP_ERROR") from exc
        except (TimeoutError, URLError) as exc: raise TransportAmbiguousError("OPENING_HISTORY_RPC_TRANSPORT_AMBIGUOUS") from exc
        try: payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc: raise ProductionTransportError("OPENING_HISTORY_RPC_MALFORMED_RESPONSE") from exc
        if not isinstance(payload, dict) or payload.get("error") is not None: raise ProductionTransportError("OPENING_HISTORY_RPC_ERROR")
        if payload.get("jsonrpc") != "2.0" or "result" not in payload: raise ProductionTransportError("OPENING_HISTORY_RPC_MALFORMED_RESPONSE")
        self.calls += 1
        return raw
