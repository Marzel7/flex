"""Compact append-only evidence identities for observed minimum MC results.

The functions here intentionally build records only.  They do not select a
database, open a file, acquire market data, or mutate lifecycle facts.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping


EVIDENCE_VERSION = "WATCHTOWER_OBSERVED_MINIMUM_EVIDENCE_V1"


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def request_identity(*, mint: str, entry_timestamp: int, window_end: int, provider_provenance: str) -> str:
    """Stable identity for one exact provider request shape, without a secret."""
    return hashlib.sha256(_canonical({
        "mint": mint,
        "entry_timestamp": entry_timestamp,
        "window_end": window_end,
        "provider_provenance": provider_provenance,
        "endpoint": "/defi/v3/ohlcv",
        "chart_type": "mcap",
        "currency": "usd",
        "resolution": "1m",
    }).encode()).hexdigest()


def observed_minimum_records(
    *,
    mint: str,
    entry_identity: Mapping[str, Any],
    request_id: str,
    contract_result: Mapping[str, Any],
    record_version: str = EVIDENCE_VERSION,
) -> tuple[dict[str, Any], ...]:
    """Return one compact record/window; reject incomplete identity material."""
    if not mint or not request_id or not record_version:
        raise ValueError("MISSING_EVIDENCE_IDENTITY_COMPONENT")
    if not entry_identity.get("timestamp") or not entry_identity.get("mc_usd"):
        raise ValueError("INVALID_ENTRY_IDENTITY")
    rows: list[dict[str, Any]] = []
    for window_key, result in sorted((contract_result.get("results") or {}).items(), key=lambda item: int(item[0])):
        required = ("window_seconds", "minimum_status", "coverage_status", "candle_interval_seconds", "missing_bucket_timestamps", "invalid_bucket_timestamps", "provenance")
        if any(field not in result for field in required):
            raise ValueError("INCOMPLETE_OBSERVED_MINIMUM_RESULT")
        body = {
            "record_version": record_version,
            "mint": mint,
            "entry_identity": dict(entry_identity),
            "request_identity": request_id,
            "window_seconds": int(window_key),
            "observed_minimum_mc_usd": result.get("observed_minimum_mc_usd"),
            "observed_minimum_timestamp": result.get("observed_minimum_timestamp"),
            "observed_minimum_offset_seconds": result.get("observed_minimum_offset_seconds"),
            "observed_drop_percent": result.get("observed_drawdown_percent"),
            "minimum_status": result["minimum_status"],
            "coverage_status": result["coverage_status"],
            "candle_interval_seconds": result["candle_interval_seconds"],
            "missing_bucket_timestamps": list(result["missing_bucket_timestamps"]),
            "invalid_bucket_timestamps": list(result["invalid_bucket_timestamps"]),
            "provider_provenance": result["provenance"],
        }
        body["evidence_identity"] = hashlib.sha256(_canonical(body).encode()).hexdigest()
        rows.append(body)
    return tuple(rows)


def append_once(existing: Iterable[Mapping[str, Any]], incoming: Mapping[str, Any]) -> tuple[tuple[dict[str, Any], ...], bool]:
    """Model an append-only store: equal identity is a no-op; a conflict fails."""
    identity = incoming.get("evidence_identity")
    if not identity:
        raise ValueError("MISSING_EVIDENCE_IDENTITY")
    retained = tuple(dict(row) for row in existing)
    matches = [row for row in retained if row.get("evidence_identity") == identity]
    if matches:
        if any(_canonical(row) != _canonical(incoming) for row in matches):
            raise ValueError("EVIDENCE_IDENTITY_COLLISION")
        return retained, False
    return retained + (dict(incoming),), True
