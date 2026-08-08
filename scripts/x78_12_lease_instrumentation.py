"""X78.12 Phase 4/5 -- temporary write-lease lifecycle instrumentation.

Not imported by any production code path. Run standalone (or import and call
install()/uninstall() from a throwaway script/REPL attached to a running
process) to record, for every cross-process write-lease acquisition/release
in this process, for the duration this module is installed:

- lease id, PID, thread, caller/command tag
- acquired_at / released_at (monotonic + wall clock)
- lease duration
- gap since the previous release on the same thread (to distinguish "one
  long hold" from "many rapid back-to-back reacquisitions")
- an optional "job context" (creator address, page number) via a
  contextvars.ContextVar that realtime_creator_funding_extractor.py can set
  around extract_for_creator, so every lease acquired during that job --
  regardless of which specific function/connection triggered it -- is
  attributable to that creator/page for per-creator rollups.

This module monkeypatches src.core.database_write_service.acquire_write_lease
and .release_write_lease at the module-function level (the single chokepoint
both TrackedConnection and DatabaseWriteService funnel through, per X78.9's
own design) rather than scattering print statements through the extractor,
so it captures every write-lane acquisition during instrumentation
regardless of call path -- including DomainResolver's separate
_save_address_tag/_db_get/_db_set_many connections, which is exactly the
question this instrumentation exists to answer.

Usage (read-only observation, no production code changes):

    import scripts.x78_12_lease_instrumentation as instr
    instr.install()
    ...  # let creator_funding_worker process jobs normally
    print(instr.summary())
    instr.uninstall()

Or, to attribute leases to a specific creator/page from calling code without
modifying it permanently, set the contextvar directly before/after the call
you want attributed (this script does NOT patch extract_for_creator itself).
"""
from __future__ import annotations

import contextvars
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import src.core.database_write_service as dws

# Set by the caller (e.g. a throwaway wrapper around extract_for_creator)
# around the span of work whose leases should be attributed to a specific
# creator/page. None means "unattributed" -- still recorded, just without
# job context.
current_job_context: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "x78_12_job_context", default=None
)


@dataclass
class LeaseRecord:
    lease_seq: int
    pid: int
    thread_name: str
    thread_ident: int
    command: str
    acquired_at_wall: float
    acquired_at_mono: float
    job_context: Optional[dict]
    released_at_wall: Optional[float] = None
    released_at_mono: Optional[float] = None
    gap_since_prev_release_on_thread_sec: Optional[float] = None

    @property
    def duration_sec(self) -> Optional[float]:
        if self.released_at_mono is None:
            return None
        return round(self.released_at_mono - self.acquired_at_mono, 6)

    def to_dict(self) -> dict:
        return {
            "lease_seq": self.lease_seq,
            "pid": self.pid,
            "thread_name": self.thread_name,
            "thread_ident": self.thread_ident,
            "command": self.command,
            "acquired_at_wall": self.acquired_at_wall,
            "job_context": self.job_context,
            "released_at_wall": self.released_at_wall,
            "duration_sec": self.duration_sec,
            "gap_since_prev_release_on_thread_sec": self.gap_since_prev_release_on_thread_sec,
        }


_lock = threading.Lock()
_records: list[LeaseRecord] = []
_by_lease_object_id: dict[int, LeaseRecord] = {}
_last_release_mono_by_thread: dict[int, float] = {}
_seq = 0
_installed = False

_orig_acquire = None
_orig_release = None


def _patched_acquire_write_lease(*args, **kwargs):
    global _seq
    t0_mono = time.monotonic()
    t0_wall = time.time()
    try:
        lease = _orig_acquire(*args, **kwargs)
    except Exception:
        # Timeouts/NestedDatabaseWriteError are not recorded as acquisitions
        # here (no lease object exists) -- they're already fully captured
        # by X78.9/X78.10/X78.11's own health tracking. This instrumentation
        # is specifically about successful acquisitions' hold duration.
        raise
    ident = threading.get_ident()
    with _lock:
        _seq += 1
        rec = LeaseRecord(
            lease_seq=_seq,
            pid=lease.owner.get("process_pid", -1),
            thread_name=lease.owner.get("thread", "?"),
            thread_ident=ident,
            command=lease.owner.get("command", "?"),
            acquired_at_wall=t0_wall,
            acquired_at_mono=t0_mono,
            job_context=current_job_context.get(),
        )
        _records.append(rec)
        _by_lease_object_id[id(lease)] = rec
    return lease


def _patched_release_write_lease(lease, *args, **kwargs):
    ident = threading.get_ident()
    t0_mono = time.monotonic()
    t0_wall = time.time()
    try:
        return _orig_release(lease, *args, **kwargs)
    finally:
        with _lock:
            rec = _by_lease_object_id.pop(id(lease), None)
            if rec is not None:
                rec.released_at_mono = t0_mono
                rec.released_at_wall = t0_wall
                prev = _last_release_mono_by_thread.get(ident)
                if prev is not None:
                    rec.gap_since_prev_release_on_thread_sec = round(t0_mono - prev, 6)
                _last_release_mono_by_thread[ident] = t0_mono


def install() -> None:
    global _installed, _orig_acquire, _orig_release
    if _installed:
        return
    _orig_acquire = dws.acquire_write_lease
    _orig_release = dws.release_write_lease
    dws.acquire_write_lease = _patched_acquire_write_lease
    dws.release_write_lease = _patched_release_write_lease
    _installed = True


def uninstall() -> None:
    global _installed
    if not _installed:
        return
    dws.acquire_write_lease = _orig_acquire
    dws.release_write_lease = _orig_release
    _installed = False


def reset() -> None:
    with _lock:
        _records.clear()
        _by_lease_object_id.clear()
        _last_release_mono_by_thread.clear()


def raw_records() -> list[dict]:
    with _lock:
        return [r.to_dict() for r in _records]


def summary(job_filter: Optional[str] = None) -> dict:
    """Aggregate stats. If job_filter is given, only leases whose
    job_context.get('creator') == job_filter are included."""
    with _lock:
        recs = list(_records)
    if job_filter is not None:
        recs = [r for r in recs if (r.job_context or {}).get("creator") == job_filter]

    completed = [r for r in recs if r.duration_sec is not None]
    still_open = [r for r in recs if r.duration_sec is None]

    durations = sorted(r.duration_sec for r in completed)
    gaps = sorted(
        r.gap_since_prev_release_on_thread_sec
        for r in completed
        if r.gap_since_prev_release_on_thread_sec is not None
    )

    def pct(sorted_vals, p):
        if not sorted_vals:
            return None
        k = int(round((p / 100.0) * (len(sorted_vals) - 1)))
        return round(sorted_vals[k], 6)

    by_command: dict[str, int] = {}
    for r in recs:
        by_command[r.command] = by_command.get(r.command, 0) + 1

    span_sec = None
    if recs:
        earliest = min(r.acquired_at_mono for r in recs)
        latest_release = max(
            (r.released_at_mono for r in completed), default=None
        )
        if latest_release is not None:
            span_sec = round(latest_release - earliest, 6)

    return {
        "total_lease_acquisitions": len(recs),
        "completed": len(completed),
        "still_open_at_summary_time": len(still_open),
        "by_command_count": dict(sorted(by_command.items(), key=lambda kv: -kv[1])),
        "duration_sec": {
            "count": len(durations),
            "min": durations[0] if durations else None,
            "p50": pct(durations, 50),
            "p95": pct(durations, 95),
            "p99": pct(durations, 99),
            "max": durations[-1] if durations else None,
            "sum_cumulative_lease_time_sec": round(sum(durations), 6) if durations else 0.0,
        },
        "gap_between_releases_same_thread_sec": {
            "count": len(gaps),
            "min": gaps[0] if gaps else None,
            "p50": pct(gaps, 50),
            "max": gaps[-1] if gaps else None,
        },
        "wall_clock_span_earliest_acquire_to_latest_release_sec": span_sec,
        "still_open_records": [r.to_dict() for r in still_open],
    }


if __name__ == "__main__":
    print(json.dumps({
        "note": "This module is meant to be imported into a running process "
                "(or a script sharing its DB_WRITE_SERIALIZE state) via "
                "install()/summary()/uninstall(). Running it standalone does "
                "nothing by itself.",
    }, indent=2))
