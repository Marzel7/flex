"""X65.57 — Persisted Discovery Intelligence Snapshots.

Persists the LATEST SUCCESSFUL result of build_operational_intelligence()/
build_pipeline_health() per (function, window_seconds) cache key, as one
JSON file per key under SNAPSHOT_DIR. Purpose: a fresh process (worker
restart, deploy) can hydrate its in-memory SWRCache from disk before
serving any Discovery request, so a cold PROCESS no longer implies a cold
USER EXPERIENCE — the operator sees the last-known-good result immediately
while a real background refresh runs on the existing prewarm/single-flight
machinery, completely unchanged.

Design choices (why files, not a DB table):
  - These are derived, disposable CACHE ARTEFACTS, not operational data —
    losing one just means the next build repopulates it. SQLite stays the
    single source of truth for real detection/attribution/evidence data;
    this module never touches wt_ops_v2.db or the hot DB at all, so it
    cannot contend with either database's write lock/WAL, and hydration at
    startup does not depend on either database being reachable.
  - One file per cache key (not one shared file) so a corrupt/partial write
    to one key can never affect any other key's snapshot, and so hydration
    can skip a bad file for exactly one key rather than failing wholesale.
  - Atomic write: write to a sibling temp file in the SAME directory, fsync
    it, then os.replace() (atomic rename on POSIX) onto the real path. A
    reader (hydrate_all(), called only at startup, single-threaded) never
    observes a partially-written file — os.replace() is all-or-nothing at
    the filesystem level.
  - A failed refresh must never touch the previous snapshot: callers only
    ever call write_snapshot() after a build has ALREADY succeeded (see
    SWRCache's persist_snapshot hook in swr_cache.py) — this module itself
    has no retry/refresh logic and is never invoked on a failure path.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

# Schema/version metadata: bumped only if the ON-DISK snapshot shape itself
# changes (not when the underlying intelligence payload's own fields
# change — that's the payload's business, not this module's). A mismatch
# means "don't trust this file's shape," not "the data is wrong" — the
# safe response is to skip it and let a normal cold build repopulate it.
#
# X67.28 -- bumped 1 -> 2 to add refresh-lifecycle metadata (snapshot_
# version, refresh_status, worker_id, refresh_reason) needed by the new
# standalone snapshot-refresh scheduler and its diagnostics. A version-1
# file (written before this change) is simply treated as "not found" by
# read_snapshot() and rebuilt fresh -- no migration needed, since these
# are disposable cache artefacts (see module docstring).
SNAPSHOT_SCHEMA_VERSION = 2

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "../.."))
SNAPSHOT_DIR = os.environ.get(
    "WT_INTELLIGENCE_SNAPSHOT_DIR",
    os.path.join(_REPO_ROOT, "database", "intelligence_snapshots"),
)

_log = logging.getLogger(__name__)


@dataclass
class Snapshot:
    function: str
    window_seconds: int
    payload: Any
    computed_at: float
    build_duration_ms: float
    schema_version: int = SNAPSHOT_SCHEMA_VERSION
    # X67.28 -- refresh-lifecycle metadata (task's explicit "Snapshot
    # metadata" requirement). All optional/defaulted so a legacy or
    # minimal write_snapshot() call (e.g. from an old code path or a
    # test) still produces a valid record.
    snapshot_version: int = 0        # monotonically increasing per (function, window_seconds)
    completed_at: Optional[float] = None
    refresh_status: str = "SUCCESS"  # SUCCESS is the only status ever persisted -- see module docstring
    worker_id: Optional[str] = None  # os.getpid() of whichever process wrote this snapshot
    refresh_reason: Optional[str] = None  # e.g. "scheduled", "manual", "startup_cold_build"


def _safe_key_component(value: Any) -> str:
    # window_seconds is always an int in practice; str() is enough to keep
    # filenames readable and stable without inventing a hashing scheme.
    return str(value).replace("/", "_").replace("\\", "_")


def _snapshot_path(function: str, window_seconds: int) -> str:
    filename = f"{_safe_key_component(function)}__{_safe_key_component(window_seconds)}.json"
    return os.path.join(SNAPSHOT_DIR, filename)


# X65.57 follow-up -- a build can be technically SUCCESSFUL (no exception
# raised) yet operationally invalid: a transient/partial DB read, a race
# during startup, etc. can produce a suspiciously small result that would
# otherwise silently become "the last known good snapshot" and get served
# as FRESH for the rest of its TTL after the next restart. This check is
# purely STRUCTURAL (compares one numeric field's magnitude against the
# previous snapshot's own value) -- it knows nothing about WATCHTOWER,
# detection, or what the field means, only that a large relative drop is
# suspicious enough to withhold. Conservative and additive: if there is no
# previous snapshot, or the caller doesn't supply a completeness_key, every
# build is accepted exactly as before this safeguard existed.
DEFAULT_MAX_RELATIVE_DROP = 0.5  # reject if the new value is <50% of the previous one


def _passes_sanity_check(
    previous_payload: Optional[dict], new_payload: Any, completeness_key: Optional[str],
    max_relative_drop: float,
) -> tuple[bool, Optional[str]]:
    """Returns (ok, reason). reason is only set when ok is False."""
    if completeness_key is None or previous_payload is None:
        return True, None
    if not isinstance(new_payload, dict) or not isinstance(previous_payload, dict):
        return True, None  # structural check only applies to dict payloads

    prev_value = previous_payload.get(completeness_key)
    new_value = new_payload.get(completeness_key)
    if not isinstance(prev_value, (int, float)) or not isinstance(new_value, (int, float)):
        return True, None  # can't compare -- don't block on something we can't measure
    if prev_value <= 0:
        return True, None  # nothing to compare a drop against

    if new_value < prev_value * (1 - max_relative_drop):
        return False, (
            f"{completeness_key} dropped from {prev_value} to {new_value} "
            f"(more than {max_relative_drop * 100:.0f}% decrease)"
        )
    return True, None


def write_snapshot(
    function: str, window_seconds: int, payload: Any, *, build_duration_ms: float,
    completeness_key: Optional[str] = None,
    max_relative_drop: float = DEFAULT_MAX_RELATIVE_DROP,
    refresh_reason: Optional[str] = None,
) -> bool:
    """Persists ONLY a completed, successful build — callers must never
    invoke this on a failure path (see swr_cache.py's persist_snapshot hook,
    which only fires from the success branch of _refresh/_cold_build/the
    cold-start compute() call, mirroring get()'s own atomic-swap-on-success
    discipline). Writes atomically: temp file in the same directory,
    fsync'd, then os.replace() onto the final path. Any exception here is
    caught and logged by the caller (SWRCache), never allowed to break the
    build/refresh it is persisting — a snapshot-write failure must not
    affect the in-memory cache's own already-correct behaviour.

    completeness_key (optional): the name of a numeric field in `payload`
    to sanity-check against the EXISTING on-disk snapshot's own value for
    the same field, before overwriting it. If the new value has dropped by
    more than `max_relative_drop` (default 50%), the write is REJECTED —
    the previous snapshot is left completely untouched, and this function
    returns False so the caller can log why. Passing no completeness_key
    (the default) disables this check entirely, preserving the original
    unconditional-persist behaviour.

    Returns True if the snapshot was written, False if it was rejected by
    the sanity check (never raises for a rejection — only a real I/O
    failure propagates, exactly as before this safeguard existed)."""
    previous = read_snapshot(function, window_seconds)
    ok, reason = _passes_sanity_check(
        previous.payload if previous else None, payload, completeness_key, max_relative_drop,
    )
    if not ok:
        _log.warning(
            "intelligence snapshot REJECTED (sanity check failed): function=%s "
            "window_seconds=%s reason=%s -- keeping previous snapshot",
            function, window_seconds, reason,
        )
        return False

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = _snapshot_path(function, window_seconds)
    tmp_path = path + f".tmp.{os.getpid()}.{time.time_ns()}"

    now = time.time()
    # X67.28 -- snapshot_version increments monotonically per (function,
    # window_seconds), independent of computed_at, so a reader/diagnostic
    # can distinguish "this is a newer build" from "the clock moved" (the
    # latter being untrustworthy across process/host boundaries). Derived
    # from the previous snapshot's own version, defaulting to 1 for the
    # very first write ever made for this key.
    next_version = (previous.snapshot_version + 1) if previous else 1

    record = {
        "function": function,
        "window_seconds": window_seconds,
        "payload": payload,
        "computed_at": now,
        "completed_at": now,
        "build_duration_ms": build_duration_ms,
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_version": next_version,
        "refresh_status": "SUCCESS",
        "worker_id": str(os.getpid()),
        "refresh_reason": refresh_reason,
    }

    with open(tmp_path, "w") as f:
        json.dump(record, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)
    return True


def read_snapshot(function: str, window_seconds: int) -> Optional[Snapshot]:
    """Reads and validates one snapshot. Returns None (never raises) for:
    missing file, corrupt/unparsable JSON, or a schema_version mismatch —
    in every one of those cases the caller falls back to a normal cold
    build, exactly as if no snapshot had ever existed. A version mismatch
    is deliberately NOT an error: it means an older/newer process wrote
    this file under a different on-disk shape, not that anything is
    broken."""
    path = _snapshot_path(function, window_seconds)
    try:
        with open(path, "r") as f:
            record = json.load(f)
    except (OSError, ValueError):
        return None

    if not isinstance(record, dict):
        return None
    if record.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        _log.info(
            "intelligence snapshot schema mismatch, ignoring: function=%s window_seconds=%s "
            "found=%r expected=%r",
            function, window_seconds, record.get("schema_version"), SNAPSHOT_SCHEMA_VERSION,
        )
        return None
    required = ("function", "window_seconds", "payload", "computed_at", "build_duration_ms")
    if any(k not in record for k in required):
        return None

    try:
        return Snapshot(
            function=record["function"],
            window_seconds=record["window_seconds"],
            payload=record["payload"],
            computed_at=float(record["computed_at"]),
            build_duration_ms=float(record["build_duration_ms"]),
            schema_version=record["schema_version"],
            snapshot_version=int(record.get("snapshot_version", 0)),
            completed_at=(
                float(record["completed_at"]) if record.get("completed_at") is not None else None
            ),
            refresh_status=record.get("refresh_status", "SUCCESS"),
            worker_id=record.get("worker_id"),
            refresh_reason=record.get("refresh_reason"),
        )
    except (TypeError, ValueError):
        return None
