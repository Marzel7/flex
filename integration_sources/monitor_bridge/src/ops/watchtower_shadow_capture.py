"""Default-off compact capture of an already-acquired Watchtower Opening response.

This module is intentionally separate from authoritative Opening persistence.
It never constructs a provider request: its caller supplies the response that
the existing strict Opening path has already received.  The retained result is
the bounded offset-audit schema consumed by the pure shadow evaluator.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from src.ops.watchtower_offset_audit import OffsetAuditStore, compact_record


SHADOW_CAPTURE_ENABLED_ENV = "WATCHTOWER_SHADOW_CAPTURE_ENABLED"
SHADOW_CAPTURE_LEDGER_ENV = "WATCHTOWER_SHADOW_CAPTURE_LEDGER_PATH"


def capture_enabled(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return str(source.get(SHADOW_CAPTURE_ENABLED_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def capture_already_acquired_opening(*, mint: str, migration_timestamp: int,
                                     http_status: int, payload: Mapping[str, Any] | None,
                                     failure_state: str | None = None,
                                     environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Persist only compact normalized evidence when explicitly enabled.

    Disabled capture has no filesystem side effect.  The audit store enforces
    its 128-record/256KiB bounded retention and rejects raw provider fields;
    no raw response is retained after ``compact_record`` returns.
    """
    source = os.environ if environ is None else environ
    if not capture_enabled(source):
        return {"state": "DISABLED", "provider_calls": 0, "database_writes": 0, "queue_writes": 0}
    target = str(source.get(SHADOW_CAPTURE_LEDGER_ENV, "")).strip()
    if not target:
        return {"state": "REFUSED_LEDGER_PATH_REQUIRED", "provider_calls": 0, "database_writes": 0, "queue_writes": 0}
    record = compact_record(
        mint=mint,
        migration_timestamp=migration_timestamp,
        http_status=http_status,
        payload=payload if int(http_status) == 200 else None,
        failure_state=failure_state,
    )
    size = OffsetAuditStore(Path(target)).record(record)
    return {
        "state": "CAPTURED",
        "record": record,
        "ledger_bytes": size,
        "provider_calls": 0,
        "database_writes": 0,
        "queue_writes": 0,
    }
