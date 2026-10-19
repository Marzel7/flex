"""Durable, operation-neutral telemetry for bounded acquisition measurements.

This module observes transport outcomes only.  It does not retry requests,
select providers, write Evidence, or alter acquisition semantics.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.acquisition.transaction import AcquisitionResponse


FAILURE_CLASSES = (
    "RECOVERED",
    "MISSING_TRANSACTION",
    "PROVIDER_TIMEOUT",
    "RATE_LIMITED",
    "PROVIDER_HTTP_UNAVAILABLE",
    "MALFORMED_REQUEST",
    "RPC_ERROR_RETRYABLE",
    "RPC_ERROR_TERMINAL",
    "MALFORMED_RESPONSE",
    "TRANSPORT_ERROR",
    "UNKNOWN",
)

RETRYABLE_RPC_CODES = {-32000, -32003, -32004, -32005, -32007, -32008, -32009}


def classify_response(response: AcquisitionResponse) -> tuple[str, str | None]:
    """Classify one measured attempt without inventing provider semantics."""
    if response.error is not None:
        name = type(response.error).__name__
        if name in {"TimeoutError", "ServerTimeoutError"}:
            return "PROVIDER_TIMEOUT", name
        return "TRANSPORT_ERROR", name
    status = response.status
    if status == 429:
        return "RATE_LIMITED", "HTTP_429"
    if status is not None and status >= 500:
        return "PROVIDER_HTTP_UNAVAILABLE", f"HTTP_{status}"
    if status in {400, 404, 405, 415, 422}:
        return "MALFORMED_REQUEST", f"HTTP_{status}"
    if status != 200:
        return "UNKNOWN", None if status is None else f"HTTP_{status}"
    if not isinstance(response.data, dict):
        return "MALFORMED_RESPONSE", "NON_OBJECT_JSON"
    error = response.data.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        if code in {-32600, -32602}:
            return "MALFORMED_REQUEST", f"RPC_{code}"
        if code in RETRYABLE_RPC_CODES:
            return "RPC_ERROR_RETRYABLE", f"RPC_{code}"
        return "RPC_ERROR_TERMINAL", f"RPC_{code}"
    if "result" not in response.data:
        return "MALFORMED_RESPONSE", "RESULT_KEY_ABSENT"
    if response.data["result"] is None:
        return "MISSING_TRANSACTION", "NULL_RESULT"
    return "RECOVERED", None


@dataclass(frozen=True)
class AttemptObservation:
    sequence: int
    signature: str
    launch: str
    purpose: str
    acquisition_id: str
    correlation_id: str
    provider: str
    status_code: int | None
    latency_ms: float
    retry_count: int
    failure_class: str
    failure_code: str | None


class DurableAttemptLog:
    """Append and fsync each attempt so process interruption retains telemetry."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sequence = 0

    def record(self, *, signature: str, launch: str, purpose: str,
               response: AcquisitionResponse) -> AttemptObservation:
        failure_class, failure_code = classify_response(response)
        with self._lock:
            self._sequence += 1
            observation = AttemptObservation(
                sequence=self._sequence, signature=signature, launch=launch,
                purpose=purpose, acquisition_id=response.metadata.acquisition_id,
                correlation_id=response.metadata.correlation_id,
                provider=response.metadata.provider, status_code=response.status,
                latency_ms=round(response.latency_ms, 3),
                retry_count=response.metadata.retry_count,
                failure_class=failure_class, failure_code=failure_code,
            )
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(observation), sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return observation


def summarize_attempt_log(path: Path) -> dict[str, Any]:
    counts = {name: 0 for name in FAILURE_CLASSES}
    latencies: dict[str, list[float]] = {}
    rows = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            payload = json.loads(line); rows += 1
            failure_class = payload["failure_class"]
            if failure_class not in counts:
                raise ValueError(f"unsupported failure class: {failure_class}")
            counts[failure_class] += 1
            latencies.setdefault(failure_class, []).append(float(payload["latency_ms"]))
    return {
        "attempts": rows,
        "failure_classes": counts,
        "latency_ms": {
            key: {"minimum": min(values), "maximum": max(values),
                  "mean": round(sum(values) / len(values), 3)}
            for key, values in sorted(latencies.items())
        },
    }
