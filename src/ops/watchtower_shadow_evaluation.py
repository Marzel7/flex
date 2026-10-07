"""Pure, default-off Watchtower Opening shadow evaluation.

The module deliberately accepts only compact retained audit evidence.  It does
not read a ledger, open a database, call a provider, enqueue work, or import
the Monitor worker.  A separate, explicitly authorized runner may pass newly
observed tokens to it in a future gate.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from src.ops.strict_migration_window import reduce_retained_offsets


SHADOW_MODE = "RETAINED_EVIDENCE_ONLY"
MAX_SHADOW_RESULTS = 128
RAW_EVIDENCE_FIELDS = frozenset({"payload", "raw_payload", "headers", "url", "credential", "api_key"})


def evaluate_retained_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a compact comparison without changing any authoritative state."""
    if not isinstance(record, Mapping) or RAW_EVIDENCE_FIELDS.intersection(record):
        return _insufficient(record, "SHADOW_RETAINED_EVIDENCE_INVALID")
    try:
        mint = str(record["mint"])
        migration_timestamp = int(record["migration_timestamp"])
    except (KeyError, TypeError, ValueError):
        return _insufficient(record, "SHADOW_RETAINED_EVIDENCE_INVALID")
    if not mint or migration_timestamp <= 0 or int(record.get("http_status", 0)) != 200:
        return _insufficient(record, "SHADOW_RETAINED_EVIDENCE_INVALID")
    if record.get("normalization_state") != "COMPLETE":
        return _insufficient(record, "SHADOW_RETAINED_EVIDENCE_INCOMPLETE")
    selected = reduce_retained_offsets(
        migration_timestamp=migration_timestamp,
        offsets=record.get("offsets") or {},
        duplicate_offsets=record.get("duplicate_offsets") or (),
    )
    result = _result_identity(mint, migration_timestamp)
    result.update(selected)
    return result


def evaluate_retained_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Evaluate a bounded supplied sequence deterministically, without I/O."""
    if len(records) > MAX_SHADOW_RESULTS:
        raise ValueError("WATCHTOWER_SHADOW_RESULT_BOUND")
    # The copy protects callers' retained evidence from accidental mutation by
    # future presentation code as well as making the non-authoritative contract explicit.
    return [evaluate_retained_record(deepcopy(record)) for record in records]


def _result_identity(mint: str, migration_timestamp: int) -> dict[str, Any]:
    return {
        "mint": mint,
        "migration_timestamp": migration_timestamp,
        "shadow_mode": SHADOW_MODE,
        "authoritative_mutation": False,
        "provider_calls": 0,
        "database_writes": 0,
        "queue_writes": 0,
        "background_workers_started": 0,
    }


def _insufficient(record: Mapping[str, Any] | object, reason: str) -> dict[str, Any]:
    source = record if isinstance(record, Mapping) else {}
    try:
        migration_timestamp = int(source.get("migration_timestamp") or 0)
    except (TypeError, ValueError):
        migration_timestamp = 0
    result = _result_identity(str(source.get("mint") or ""), migration_timestamp)
    result.update({"state": "INSUFFICIENT_EVIDENCE", "reason": reason})
    return result
