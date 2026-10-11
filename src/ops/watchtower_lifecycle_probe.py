"""One-request, compact Watchtower lifecycle capability probe.

This is intentionally not the lifecycle executor: it has one frozen request,
uses the existing runtime gate/budget/transport, and persists no raw payload.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable

from src.ops.dev_provider_budget import BudgetDenied
from src.ops.token_data_provider_bindings import build_birdeye_ohlcv_request

MAX_ARTIFACT_BYTES = 1_000_000
MAX_PROBE_STORAGE_BYTES = 2_000_000


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def request_identity(authorization: dict[str, Any]) -> str:
    core = {key: authorization[key] for key in ("rank", "mint", "creation_timestamp", "time_from", "time_to", "interval")}
    return hashlib.sha256(_canonical(core).encode()).hexdigest()


def validate_authorization(authorization: dict[str, Any]) -> dict[str, Any]:
    required = {"rank", "mint", "creation_timestamp", "time_from", "time_to", "interval", "content_hash"}
    if set(authorization) - required or required - set(authorization):
        raise ValueError("PROBE_AUTHORIZATION_SHAPE_INVALID")
    if authorization["interval"] not in {"1m", "15m", "1h", "4h", "1d"}:
        raise ValueError("PROBE_INTERVAL_UNQUALIFIED")
    if not isinstance(authorization["rank"], int) or not isinstance(authorization["mint"], str):
        raise ValueError("PROBE_IDENTITY_INVALID")
    if not (isinstance(authorization["time_from"], int) and isinstance(authorization["time_to"], int) and authorization["time_from"] < authorization["time_to"]):
        raise ValueError("PROBE_RANGE_INVALID")
    expected = request_identity(authorization)
    if authorization["content_hash"] != expected:
        raise ValueError("PROBE_AUTHORIZATION_HASH_INVALID")
    return authorization


def compact_outcome(payload: Any, request: dict[str, Any], status: int) -> dict[str, Any]:
    if status != 200:
        return {"outcome": "HTTP_FAILURE", "http_status": status}
    if isinstance(payload, dict) and (payload.get("success") is False or payload.get("code") not in (None, 0, "0")):
        error = {"outcome": "PROVIDER_API_ERROR", "http_status": status,
                 "response_envelope_fields": sorted(str(key) for key in payload)[:16]}
        if payload.get("code") is not None: error["provider_error_code"] = str(payload["code"])[:128]
        return error
    try:
        items = payload["data"]["items"]
        if not isinstance(items, list): raise ValueError
    except (KeyError, TypeError, ValueError):
        return {"outcome": "MALFORMED_RESPONSE", "http_status": status}
    rows = []
    for item in items:
        try:
            timestamp = int(item.get("unixTime", item.get("timestamp")))
            high, low = float(item["h"]), float(item["l"])
            if not (math.isfinite(high) and math.isfinite(low) and high > 0 and low > 0): raise ValueError
            rows.append((timestamp, low, high))
        except (AttributeError, KeyError, TypeError, ValueError):
            return {"outcome": "INVALID_CANDLE_VALUES", "http_status": status,
                    "response_envelope_fields": sorted(str(key) for key in payload)[:16] if isinstance(payload, dict) else []}
    if not rows:
        return {"outcome": "NO_VALID_CANDLES", "http_status": status, "returned_candle_count": 0}
    rows.sort()
    if len({row[0] for row in rows}) != len(rows): return {"outcome": "INVALID_CANDLE_VALUES", "http_status": status}
    minimum, maximum = min(rows, key=lambda row: row[1]), max(rows, key=lambda row: row[2])
    return {"outcome": "COMPLETED", "http_status": status, "returned_candle_count": len(rows),
            "first_returned_timestamp": rows[0][0], "last_returned_timestamp": rows[-1][0],
            "observed_minimum_mc_usd": minimum[1], "observed_minimum_timestamp": minimum[0],
            "observed_maximum_mc_usd": maximum[2], "observed_maximum_timestamp": maximum[0],
            "coverage": "OBSERVED_RESPONSE_ONLY"}


class OneRequestLifecycleProbe:
    def __init__(self, authorization: dict[str, Any], journal: Path, *, runtime_gate: Any, admission: Any, transport: Callable[[dict[str, Any]], Any], disk_free: Callable[[], int]):
        self.authorization = validate_authorization(authorization); self.journal = journal
        self.runtime_gate, self.admission, self.transport, self.disk_free = runtime_gate, admission, transport, disk_free

    def _read(self) -> dict[str, Any]:
        return json.loads(self.journal.read_text()) if self.journal.exists() else {"state": "NEW"}

    def _write(self, value: dict[str, Any]) -> None:
        if self.disk_free() < 4 * 1024**3: raise RuntimeError("PROBE_DISK_HEADROOM_DENIED")
        payload = _canonical(value).encode()
        if len(payload) >= MAX_ARTIFACT_BYTES or len(payload) > MAX_PROBE_STORAGE_BYTES: raise RuntimeError("PROBE_STORAGE_DENIED")
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        temp = self.journal.with_suffix(".tmp"); temp.write_bytes(payload); temp.replace(self.journal)

    def execute(self, *, crash_after_admission: bool = False) -> dict[str, Any]:
        prior = self._read()
        if prior["state"] != "NEW": raise RuntimeError("PROBE_ALREADY_ATTEMPTED")
        self.runtime_gate.check()
        identity = request_identity(self.authorization)
        request = build_birdeye_ohlcv_request(address=self.authorization["mint"], interval=self.authorization["interval"], time_from=self.authorization["time_from"], time_to=self.authorization["time_to"])
        self._write({"state": "PENDING", "request_identity": identity})
        self.admission.admit(mint=self.authorization["mint"], request_identity=identity)
        self._write({"state": "ATTEMPTED", "request_identity": identity})
        if crash_after_admission: raise RuntimeError("PROBE_TEST_CRASH_AFTER_ADMISSION")
        response = self.transport(request)
        compact = compact_outcome(response.payload, request, response.status_code)
        record = {"state": compact["outcome"], "request_identity": identity, "rank": self.authorization["rank"], "mint": self.authorization["mint"], "creation_timestamp": self.authorization["creation_timestamp"], "request": {"endpoint": request["endpoint"], "params": request["request_parameters"]}, "provider_provenance": "BIRDEYE_OHLCV_MCAP", **compact}
        self._write(record)
        return record
