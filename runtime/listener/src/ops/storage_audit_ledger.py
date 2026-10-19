"""STORAGE-LIFECYCLE-P1: append-only audit ledger (Part 19).

Every FUTURE lifecycle action (not yet authorized to run automatically --
see P2 activation plan) must be recorded here before/after execution.
This module implements the ledger primitive; it does not itself decide
when to write to it (that belongs to a not-yet-authorized cleanup
runner). Append-only: there is no update/delete function in this module.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class LedgerEntry:
    run_id: str
    timestamp: float
    policy_version: str
    path: str
    action: str  # e.g. "RETAIN" | "COMPACT" | "ROTATE" | "RETIRE_SEGMENT" | "DELETE_BATCH" | "SKIPPED_BUSY" | "SKIPPED_PROTECTED"
    reason: str
    lifecycle_class: str
    preflight_result: str
    bytes_before: int
    bytes_after: int
    action_result: str  # "SUCCESS" | "FAILED" | "SKIPPED" | "DRY_RUN_ONLY"
    error: str | None = None
    digest_or_reference: str | None = None
    disk_state_free_bytes: int | None = None
    pressure_state: str | None = None


def new_run_id() -> str:
    return f"storage-lifecycle-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"


def append_entry(ledger_path: str, entry: LedgerEntry) -> None:
    """Append-only write: one JSON line per call, opened in append mode.
    Never truncates, never rewrites prior lines."""
    os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
    with open(ledger_path, "a") as f:
        f.write(json.dumps(asdict(entry), sort_keys=True) + "\n")


def read_ledger(ledger_path: str) -> list[dict]:
    """Read-only replay of the ledger, for audit purposes."""
    if not os.path.exists(ledger_path):
        return []
    entries = []
    with open(ledger_path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries
