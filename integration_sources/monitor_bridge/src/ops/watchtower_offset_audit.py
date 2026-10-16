"""Bounded durable evidence for Watchtower early-second offset audits.

This module deliberately stores normalized evidence only.  Provider payloads,
credentials, request URLs, and headers never cross its public boundary.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.ops.strict_migration_window import normalize_opening_ohlcv


SCHEMA_VERSION = 1
MAX_RECORDS = 128
MAX_RECORD_BYTES = 2_048
MAX_FILE_BYTES = 262_144
MAX_TRANSIENT_BYTES = MAX_FILE_BYTES * 2
MAX_NORMALIZED_TIMESTAMPS = 8
RAW_PROVIDER_PAYLOAD_RETENTION_ENABLED = False


def compact_record(*, mint: str, migration_timestamp: int, http_status: int,
                   payload: Mapping[str, Any] | None = None,
                   failure_state: str | None = None) -> dict[str, Any]:
    """Project one response into the fixed early-second audit contract."""
    if not mint or int(migration_timestamp) <= 0:
        raise ValueError("OFFSET_AUDIT_IDENTITY_REQUIRED")
    record: dict[str, Any] = {
        "mint": str(mint), "migration_timestamp": int(migration_timestamp),
        "http_status": int(http_status), "normalization_state": "NOT_RUN",
        "normalized_item_count": 0, "normalized_timestamps": [],
        "offsets": {}, "duplicate_offsets": [],
        "failure_state": str(failure_state or "NONE")[:80],
    }
    if int(http_status) != 200:
        return _validate_record(record)
    try:
        candles = normalize_opening_ohlcv(payload or {})
    except ValueError as exc:
        record["normalization_state"] = "FAILED"
        record["failure_state"] = str(exc)[:80]
        return _validate_record(record)
    record["normalization_state"] = "COMPLETE"
    record["normalized_item_count"] = len(candles)
    record["normalized_timestamps"] = [int(row["timestamp"]) for row in candles[:MAX_NORMALIZED_TIMESTAMPS]]
    relevant = [row for row in candles if int(migration_timestamp) <= int(row["timestamp"]) <= int(migration_timestamp) + 4]
    counts = Counter(int(row["timestamp"]) for row in relevant)
    record["duplicate_offsets"] = [timestamp - int(migration_timestamp) for timestamp, count in sorted(counts.items()) if count > 1]
    for row in relevant:
        timestamp = int(row["timestamp"])
        if counts[timestamp] == 1:
            record["offsets"][str(timestamp - int(migration_timestamp))] = float(row["close"])
    return _validate_record(record)


def _validate_record(record: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(record)
    prohibited = {"payload", "raw_payload", "headers", "url", "credential", "api_key"}
    if prohibited.intersection(value):
        raise ValueError("RAW_PROVIDER_RETENTION_FORBIDDEN")
    offsets = value.get("offsets") or {}
    if not isinstance(offsets, Mapping) or any(str(key) not in {"0", "1", "2", "3", "4"} for key in offsets):
        raise ValueError("OFFSET_AUDIT_OFFSET_INVALID")
    if len(value.get("normalized_timestamps") or []) > MAX_NORMALIZED_TIMESTAMPS:
        raise ValueError("OFFSET_AUDIT_TIMESTAMP_CAP")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_RECORD_BYTES:
        raise ValueError("OFFSET_AUDIT_RECORD_OVERSIZE")
    return value


class OffsetAuditStore:
    """A bounded, atomically replaced, mint-idempotent compact audit ledger."""
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        if self.path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("OFFSET_AUDIT_STORAGE_BOUND")
        try:
            document = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError("OFFSET_AUDIT_STORE_INVALID") from exc
        if document.get("schema_version") != SCHEMA_VERSION or not isinstance(document.get("records"), list):
            raise ValueError("OFFSET_AUDIT_STORE_INVALID")
        records = [_validate_record(row) for row in document["records"]]
        if len(records) > MAX_RECORDS:
            raise ValueError("OFFSET_AUDIT_STORAGE_BOUND")
        return records

    def record(self, value: Mapping[str, Any]) -> int:
        value = _validate_record(value)
        records = {str(row["mint"]): row for row in self.records()}
        if value["mint"] not in records and len(records) >= MAX_RECORDS:
            raise ValueError("OFFSET_AUDIT_STORAGE_BOUND")
        records[str(value["mint"])] = value
        document = {"schema_version": SCHEMA_VERSION, "raw_provider_payload_retention": False,
                    "records": [records[mint] for mint in sorted(records)]}
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) > MAX_FILE_BYTES:
            raise ValueError("OFFSET_AUDIT_STORAGE_BOUND")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        if len(encoded) * 2 > MAX_TRANSIENT_BYTES:
            raise ValueError("OFFSET_AUDIT_TRANSIENT_STORAGE_BOUND")
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        return len(encoded)


def product_projection(path: str | Path) -> dict[str, dict[str, Any]]:
    """Read-only audit projection; it is never an Entry or lifecycle authority."""
    projected: dict[str, dict[str, Any]] = {}
    for record in OffsetAuditStore(path).records():
        values = {int(key): value for key, value in (record.get("offsets") or {}).items()}
        current = 1 if 1 in values else 0 if 0 in values else None
        proposed = current if current is not None else 2 if 2 in values else None
        label = ("Historical audit candidate: +2" if proposed == 2 else
                 "NO_VALID_OPENING_CANDLE_THROUGH_PLUS4" if not values else "Historical audit evidence")
        projected[record["mint"]] = {
            "audit_version": f"watchtower-opening-offset-audit.v{SCHEMA_VERSION}",
            "audit_status": record["normalization_state"],
            "canonical_migration_timestamp": record["migration_timestamp"],
            "t_present": 0 in values, "t_plus1_present": 1 in values,
            "t_plus2_present": 2 in values, "t_plus3_present": 3 in values,
            "t_plus4_present": 4 in values,
            "current_policy_classification": "T_PLUS1" if current == 1 else "T" if current == 0 else "NONE_CURRENT",
            "proposed_plus2_classification": "PLUS2" if proposed == 2 else "CURRENT" if proposed is not None else "NO_VALID_OPENING_CANDLE_THROUGH_PLUS4",
            "simulated_selected_timestamp": int(record["migration_timestamp"]) + proposed if proposed is not None else None,
            "simulated_selected_mc": values.get(proposed) if proposed is not None else None,
            "simulated_offset_seconds": proposed,
            "audit_label": label,
        }
    return projected
