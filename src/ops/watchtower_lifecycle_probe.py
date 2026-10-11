"""One-request, compact Watchtower lifecycle capability probe.

This is intentionally not the lifecycle executor: it has one frozen request,
uses the existing runtime gate/budget/transport, and persists no raw payload.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from src.ops.dev_provider_budget import BudgetDenied
from src.ops.token_data_provider_bindings import build_birdeye_ohlcv_request
from src.ops.strict_migration_window import normalize_partial_birdeye_mcap_ohlcv
from src.ops.watchtower_price_fact_contract import summarize_observed_lifecycle

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
        rows, rejected = normalize_partial_birdeye_mcap_ohlcv(payload)
    except ValueError as exc:
        if str(exc) == "INVALID_PROVIDER_SUCCESS":
            return {"outcome": "MALFORMED_RESPONSE", "http_status": status}
        return {"outcome": "MALFORMED_RESPONSE", "http_status": status}
    if not rows:
        return {"outcome": "INVALID_CANDLE_VALUES" if rejected else "NO_VALID_CANDLES", "http_status": status, "returned_candle_count": 0, "rejected_candle_counts": rejected}
    minimum, maximum = min(rows, key=lambda row: float(row["low"])), max(rows, key=lambda row: float(row["high"]))
    params = request["request_parameters"]
    interval_seconds = {"1m": 60, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}[params["type"]]
    lifecycle = summarize_observed_lifecycle(creation_timestamp=int(request.get("creation_timestamp", params["time_from"])),
        candles=rows, interval_seconds=interval_seconds, requested_start=int(params["time_from"]), requested_end=int(params["time_to"]))
    return {"outcome": "PARTIAL_VALID_CANDLES" if rejected else "COMPLETED", "http_status": status, "returned_candle_count": len(rows), "rejected_candle_counts": rejected,
            "first_returned_timestamp": int(rows[0]["timestamp"]), "last_returned_timestamp": int(rows[-1]["timestamp"]),
            "observed_minimum_mc_usd": float(minimum["low"]), "observed_minimum_timestamp": int(minimum["timestamp"]),
            "observed_maximum_mc_usd": float(maximum["high"]), "observed_maximum_timestamp": int(maximum["timestamp"]),
            "coverage": "OBSERVED_RESPONSE_ONLY", "lifecycle_metrics": lifecycle}


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
        request["creation_timestamp"] = self.authorization["creation_timestamp"]
        self._write({"state": "PENDING", "request_identity": identity})
        self.admission.admit(mint=self.authorization["mint"], request_identity=identity)
        self._write({"state": "ATTEMPTED", "request_identity": identity})
        if crash_after_admission: raise RuntimeError("PROBE_TEST_CRASH_AFTER_ADMISSION")
        response = self.transport(request)
        compact = compact_outcome(response.payload, request, response.status_code)
        record = {"state": compact["outcome"], "request_identity": identity, "rank": self.authorization["rank"], "mint": self.authorization["mint"], "creation_timestamp": self.authorization["creation_timestamp"], "request": {"endpoint": request["endpoint"], "params": request["request_parameters"]}, "provider_provenance": "BIRDEYE_OHLCV_MCAP", **compact}
        self._write(record)
        return record
