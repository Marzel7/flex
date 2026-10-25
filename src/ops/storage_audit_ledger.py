"""STORAGE-LIFECYCLE-P1: append-only audit ledger (Part 19).

Every FUTURE lifecycle action (not yet authorized to run automatically --
see P2 activation plan) must be recorded here before/after execution.
This module implements the ledger primitive; it does not itself decide
when to write to it (that belongs to a not-yet-authorized cleanup
runner). Append-only: there is no update/delete function in this module.
"""
from __future__ import annotations

import json
import fcntl
import stat
from pathlib import Path
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


# Required lifecycle audit is protected, not disposable seven-day detail.
# Stop the owner at quota; never discard audit history to make room.
MAX_LEDGER_BYTES = 8 * 1024 * 1024
MAX_ENTRY_BYTES = 16 * 1024
DISK_FLOOR_BYTES = 2_000_000_000


class LedgerHold(RuntimeError):
    pass


def _path(ledger_path):
    path = Path(ledger_path).absolute()
    if path.resolve() != path:
        raise LedgerHold("LEDGER_PHYSICAL_PATH_REQUIRED")
    return path


def append_entry(ledger_path: str, entry: LedgerEntry) -> None:
    """Durable bounded append; protected history is never rotated or expired.

    Exclusive flock serializes this owner's admission check and append. Any
    interrupted tail blocks subsequent writes rather than hiding lost audit.
    Maximum record/transient encoding is 16 KiB, total file is 8 MiB.
    """
    payload = (json.dumps(asdict(entry), sort_keys=True) + "\n").encode()
    if len(payload) > MAX_ENTRY_BYTES:
        raise LedgerHold("LEDGER_ENTRY_CAPACITY")
    path = _path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise LedgerHold("LEDGER_FILE_AUTHORITY")
        if info.st_size + len(payload) > MAX_LEDGER_BYTES:
            raise LedgerHold("LEDGER_CAPACITY")
        if info.st_size and os.pread(fd, 1, info.st_size - 1) != b"\n":
            raise LedgerHold("LEDGER_INTERRUPTED_TAIL")
        disk = os.fstatvfs(fd)
        if disk.f_bavail * disk.f_frsize < DISK_FLOOR_BYTES + MAX_ENTRY_BYTES:
            raise LedgerHold("LEDGER_DISK_FLOOR")
        offset = 0
        while offset < len(payload):
            count = os.write(fd, payload[offset:])
            if count <= 0:
                raise LedgerHold("LEDGER_PARTIAL_WRITE")
            offset += count
        os.fsync(fd)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(fd)


def read_ledger(ledger_path: str) -> list[dict]:
    """Bounded read-only replay; ambiguous or oversized history fails closed."""
    path = _path(ledger_path)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return []
    try:
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(MAX_LEDGER_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_LEDGER_BYTES:
        raise LedgerHold("LEDGER_CAPACITY")
    if data and not data.endswith(b"\n"):
        raise LedgerHold("LEDGER_INTERRUPTED_TAIL")
    result = []
    for line in data.splitlines():
        if len(line) + 1 > MAX_ENTRY_BYTES:
            raise LedgerHold("LEDGER_ENTRY_CAPACITY")
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError):
            raise LedgerHold("LEDGER_CORRUPT") from None
        if not isinstance(value, dict):
            raise LedgerHold("LEDGER_CORRUPT")
        result.append(value)
    return result
