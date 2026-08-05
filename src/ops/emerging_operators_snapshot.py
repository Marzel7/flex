"""X72.0 — Emerging Operators snapshot builder.

Separates SNAPSHOT GENERATION from SNAPSHOT SERVING for the Emerging
Operators / Operation Registry projection (src.ops.emerging_operator_
service.EmergingOperatorService). Root cause this fixes: _compose() (the
canonical family projection) and list()'s own reconciliation pass
(build_reconciliation_metadata, ~7.5s measured) previously ran synchronously
on the request thread whenever the 15s in-process cache expired, so every
analyst who happened to load the page right after expiry waited 4-8+ seconds
staring at a loading spinner. Neither of those computations is
request-specific -- they read persisted, already-committed evidence and
produce the same result for every concurrent caller.

This module runs the SAME EmergingOperatorService computation this
codebase already had (zero behavioural change to what gets computed), but
in the standalone scheduler process (X67.28's intelligence_snapshot_
scheduler.py, already running under supervisord), and persists the result
through the EXISTING, unchanged atomic-write path (src.ops.intelligence_
snapshots.write_snapshot -- temp file + fsync + os.replace, sanity-checked
against the previous snapshot). EmergingOperatorService becomes a pure
reader of the last published snapshot; it never invokes _compose() (or the
reconciliation pass) from a request again after the first snapshot exists.

Snapshot contents (Phase 2's "immutable snapshot object"):
  families         -- the raw _compose() output (used by get()/recent_events())
  list_max         -- the full list(limit=MAX_LIST_LIMIT, debug=True) payload;
                       every smaller `limit=` request slices this in memory
                       (list() itself never re-queries the DB)
  generated_at     -- wall-clock time the build completed (also carried in
                       the outer Snapshot record's computed_at, duplicated
                       here so callers reading only the payload still see it)
  build_duration_ms-- also duplicated from the outer Snapshot record

There is no window concept for this projection (unlike operational_
intelligence/pipeline_health) -- window_seconds=0 is used as the fixed
snapshot key.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from src.ops.intelligence_snapshots import Snapshot, read_snapshot, write_snapshot

FUNCTION_NAME = "emerging_operators"
WINDOW_SECONDS = 0

# The full-payload snapshot is generated once at this limit; list() with any
# limit <= this slices the pre-built list in memory. 500 matches
# EmergingOperatorService.list()'s own existing upper clamp, so no caller can
# ever request more than what a single build already covers.
MAX_LIST_LIMIT = 500


def build_emerging_operators_snapshot(ops_db_path: str, live_db_path: str) -> dict[str, Any]:
    """Runs the existing EmergingOperatorService computation to completion
    and returns a JSON-serialisable payload for write_snapshot(). Never
    invoked from a request thread -- only from the standalone scheduler
    process or a manual --once/backfill call. Raises on failure (the
    scheduler's refresh_one() catches and logs; the previous on-disk
    snapshot is left untouched, per write_snapshot's own success-only
    persistence discipline)."""
    from src.ops.emerging_operator_service import EmergingOperatorService

    from src.ops.reconciliation_metadata import build_reconciliation_metadata

    service = EmergingOperatorService(ops_db_path, live_db_path)
    start = time.perf_counter()
    families = service._compose()
    # Snapshotted once here so get() never calls build_reconciliation_metadata
    # on a request thread either -- measured at ~7.4s standalone, the single
    # largest cost in the whole projection, larger than _compose() itself.
    try:
        reconciliation_by_family = build_reconciliation_metadata(service, families)
    except Exception:
        reconciliation_by_family = {}
    # debug=False here deliberately -- the debug block duplicates full
    # (uncarded) copies of every hidden/candidate/dormant/retired family
    # (~5.7MB measured), and is only ever needed for the rare ?debug=1
    # diagnostic request. EmergingOperatorService.list() computes it
    # on-demand from the snapshot's own `families` list when actually
    # requested, at zero DB/RPC cost -- see EmergingOperatorService._debug_
    # block(). Baking it into every snapshot write would roughly double
    # on-disk size and JSON-parse cost for every single warm request.
    list_max = service._list_uncached(limit=MAX_LIST_LIMIT, debug=False)
    build_ms = (time.perf_counter() - start) * 1000

    return {
        "families": families,
        "family_count": len(families),  # numeric proxy for write_snapshot's sanity-drop check
        "reconciliation_by_family": reconciliation_by_family,
        "list_max": list_max,
        "list_max_limit": MAX_LIST_LIMIT,
        "generated_at": time.time(),
        "build_duration_ms": build_ms,
    }


def refresh_emerging_operators_snapshot(
    ops_db_path: str, live_db_path: str, *, reason: str = "scheduled",
) -> dict[str, Any]:
    """Runs one build and persists it via the existing atomic write_snapshot()
    path, sanity-checked against the previous snapshot's own family count
    (Phase 8: a build that suspiciously drops in size is REJECTED, and the
    previous snapshot is left completely untouched -- never removed, never
    replaced with empty/partial data). Mirrors intelligence_snapshot_
    scheduler.refresh_one()'s own contract exactly; never raises -- a
    failure is caught, logged, and reported so the caller (the scheduler
    loop, or a manual --once run) can log it and retry on the next tick."""
    try:
        payload = build_emerging_operators_snapshot(ops_db_path, live_db_path)
    except Exception as exc:  # noqa: BLE001 -- must never crash the scheduler loop
        return {
            "function": FUNCTION_NAME, "window_seconds": WINDOW_SECONDS,
            "status": "FAILED", "error": str(exc),
        }

    written = write_snapshot(
        FUNCTION_NAME, WINDOW_SECONDS, payload,
        build_duration_ms=payload["build_duration_ms"],
        completeness_key="family_count",
        refresh_reason=reason,
    )
    return {
        "function": FUNCTION_NAME, "window_seconds": WINDOW_SECONDS,
        "status": "SUCCESS" if written else "REJECTED_SANITY_CHECK",
        "build_duration_ms": payload["build_duration_ms"],
        "family_count": payload["family_count"],
    }


def read_emerging_operators_snapshot() -> Optional[Snapshot]:
    """Reads the last published snapshot. Returns None if none has ever
    been written (Phase 4's startup case) or the on-disk file is missing/
    corrupt/schema-mismatched -- read_snapshot() already treats all of
    those identically, so no extra handling is needed here."""
    return read_snapshot(FUNCTION_NAME, WINDOW_SECONDS)
