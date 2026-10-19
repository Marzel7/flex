"""X21D.3 — narrow diagnostics for walkback_worker's cycle, kept permanently
(behind a debug flag) rather than removed after the incident.

Traces the exact boundary at which a walkback cycle fails, and the write-lease
state at that moment, so a future recurrence of "database is locked" /
NestedDatabaseWriteError (or a similar class of bug) can be proven rather than
inferred. This module is diagnosis-only: it does not change any write/commit/
lock behavior, does not retry, does not alter timeouts. It reads thread-local
lease state (via database_write_service._thread_write_lease) purely for
observation.

Verbosity: per-boundary success tracing (trace_boundary) is OFF by default —
it fires every ~45s forever and is pure noise once things are healthy. Enable
with WALKBACK_TRACE_VERBOSE=1 when actively investigating. Failure tracing
(trace_failure) is ALWAYS ON — failures are rare, and this is exactly the
evidence that proved the X21D.3 root cause (a leaked connection in a
different process, not a bug in walkback_worker itself) instead of guessing.

No sensitive payloads (addresses, signatures, amounts) are logged — only
boundary name, elapsed time, PID/thread, and SQLite error codes/messages.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any, Optional

_VERBOSE = os.environ.get("WALKBACK_TRACE_VERBOSE", "0") == "1"


def _lease_snapshot() -> Optional[dict[str, Any]]:
    """Read-only introspection of the CURRENT thread's write-lease ownership,
    if any. Never acquires or releases anything — pure observation."""
    try:
        from src.core.database_write_service import _thread_write_lease
        owner = getattr(_thread_write_lease, "owner", None)
        if owner is None:
            return None
        return {
            "database": owner.get("database_selector"),
            "writer_id": owner.get("writer_id"),
            "command": owner.get("command"),
            "transaction_id": owner.get("transaction_id"),
            "held_seconds": round(time.time() - owner.get("acquired_at", time.time()), 3),
        }
    except Exception:
        return None


def trace_boundary(name: str, *, extra: Optional[dict[str, Any]] = None) -> None:
    """Log a cycle boundary with lease state. `name` is one of the fixed set of
    boundary labels (cycle_started, heartbeat_write_attempted,
    heartbeat_write_completed, queue_inspection_started, queue_claim_attempted,
    queue_claim_completed, maintenance_write_attempted, cycle_completed).
    No-op unless WALKBACK_TRACE_VERBOSE=1 — set it when actively investigating
    a suspected cycle/lock issue, leave it off otherwise."""
    if not _VERBOSE:
        return
    lease = _lease_snapshot()
    fields = {
        "boundary": name,
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "ts": round(time.time(), 3),
    }
    if lease:
        fields["lease_held_by_this_thread"] = lease
    if extra:
        fields.update(extra)
    print(f"[WALKBACK_TRACE] {fields}", flush=True)


def trace_failure(name: str, exc: BaseException, *, elapsed_s: Optional[float] = None) -> None:
    """Log a failed boundary with SQLite error codes (primary + extended, when
    available) and lease state at the moment of failure — the key evidence for
    diagnosing whether a lease was left held across the failing statement."""
    lease = _lease_snapshot()
    sqlite_code = getattr(exc, "sqlite_errorcode", None)
    sqlite_name = getattr(exc, "sqlite_errorname", None)
    fields = {
        "boundary": name,
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "ts": round(time.time(), 3),
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "sqlite_errorcode": sqlite_code,
        "sqlite_errorname": sqlite_name,
        "elapsed_s": round(elapsed_s, 3) if elapsed_s is not None else None,
        "lease_held_by_this_thread_at_failure": lease,
    }
    print(f"[WALKBACK_TRACE_FAILURE] {fields}", flush=True)
