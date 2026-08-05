#!/usr/bin/env python3
"""X67.28 — Standalone Intelligence Snapshot Refresh Scheduler.

Root cause this module fixes: build_operational_intelligence()/build_
pipeline_health() refreshes previously ran as in-process daemon threads
inside a gunicorn worker (src.ops.swr_cache.SWRCache._refresh, executed via
threading.Thread(daemon=True)). Daemon threads are killed unconditionally
the instant their parent process exits. gunicorn recycles a worker every
~500 requests (config/gunicorn.conf.py's max_requests, deliberate memory-
leak protection, not something to remove) — and the `all`-window build
alone takes ~8-9 minutes. Every recycle during a build silently discarded
that build; write_snapshot() (src/ops/intelligence_snapshots.py) never
fired because it is only called from the SUCCESS branch; the on-disk
snapshot never advanced; the next worker hydrated the same stale snapshot
and immediately re-attempted the same doomed multi-minute build. This is
the "refreshes_started > 0, refreshes_succeeded = 0, snapshot age growing
without bound" cycle X67.28 was opened to eliminate.

Architecture (Option A, chosen — see X67.28 deliverable for the full
comparison): a single standalone process, launched by supervisord exactly
like src.core.operation_scheduler (the existing, proven pattern for
"if this crashes the live app survives; if the live app restarts this
survives"), rebuilds every (function, window_seconds) snapshot on its own
fixed schedule and persists via the EXISTING, unchanged intelligence_
snapshots.write_snapshot() atomic-write path. Gunicorn workers become pure
READERS: SWRCache.hydrate() (unchanged) seeds from whatever this process
last wrote, and the worker's own SWRCache.get()/try_get() background-
refresh machinery is left in place as a fallback (so behaviour degrades
gracefully to the OLD, worker-driven refresh model if this scheduler
process is ever stopped) but under normal operation just re-triggers a
build that finds an already-fresh snapshot and no-ops quickly.

Per-window locking: a dedicated lock file per (function, window_seconds)
key, using the EXACT same PID-liveness-check + stale-reclaim pattern as
src.core.operation_scheduler.acquire_lock()/release_lock() — proven,
already-reviewed code, not reinvented here. If this scheduler process
itself dies mid-build, the next scheduler tick (or a manually restarted
instance) finds the dead PID in the lock file and reclaims it — "stale
lock recovery," the task's explicit requirement, satisfied by construction
rather than a new mechanism.

Run:
    python -m src.core.intelligence_snapshot_scheduler --loop
    python -m src.core.intelligence_snapshot_scheduler --once
    python -m src.core.intelligence_snapshot_scheduler --status
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from typing import Optional

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.ops.discovery_window import WINDOW_ORDER, window_seconds_for  # noqa: E402
from src.ops.intelligence_snapshots import write_snapshot, read_snapshot  # noqa: E402
from src.ops.emerging_operators_snapshot import (  # noqa: E402
    FUNCTION_NAME as EMERGING_OPERATORS_FUNCTION,
    WINDOW_SECONDS as EMERGING_OPERATORS_WINDOW_SECONDS,
    refresh_emerging_operators_snapshot,
)

OPS_DB_PATH = os.environ.get(
    "OPS_V2_DB_PATH", os.path.join(_REPO_ROOT, "database", "wt_ops_v2.db"))
LIVE_DB_PATH = os.environ.get(
    "FLEX_DB_PATH", os.path.join(_REPO_ROOT, "database", "flex_complete_database.db"))

LOCK_DIR = os.environ.get(
    "WT_SNAPSHOT_SCHEDULER_LOCK_DIR",
    os.path.join(_REPO_ROOT, "database", "intelligence_snapshots", "locks"),
)

# X67.28 -- per-window cadence. Different windows genuinely need different
# freshness targets and cost different amounts to rebuild (the `all` window
# scans the full corpus and is the single most expensive build in the
# system, ~8-9 minutes measured live; `24h` is cheap and changes fastest in
# relative terms as new launches land). Env-overridable per the existing
# codebase convention (operation_scheduler.py's own *_INTERVAL_SEC vars).
REFRESH_INTERVAL_SEC = {
    "24h": int(os.environ.get("SNAPSHOT_REFRESH_INTERVAL_24H_SEC", "300")),    # 5 min
    "7d":  int(os.environ.get("SNAPSHOT_REFRESH_INTERVAL_7D_SEC", "900")),     # 15 min
    "30d": int(os.environ.get("SNAPSHOT_REFRESH_INTERVAL_30D_SEC", "1800")),   # 30 min
    "all": int(os.environ.get("SNAPSHOT_REFRESH_INTERVAL_ALL_SEC", "1800")),   # 30 min
}

# X72.0 -- Emerging Operators has no window concept (unlike operational_
# intelligence/pipeline_health above); one fixed cadence covers it. Default
# matches the original in-worker cache's OPERATION_DISCOVERY_REFRESH_SECONDS
# (15s) -- analysts previously got data at most 15s stale at the cost of an
# occasional multi-second block; the background refresh now delivers the
# same freshness with zero request-thread cost.
EMERGING_OPERATORS_REFRESH_INTERVAL_SEC = int(
    os.environ.get("EMERGING_OPERATORS_REFRESH_INTERVAL_SEC", "15")
)
EMERGING_OPERATORS_MAX_AGE_SEC = int(
    os.environ.get("EMERGING_OPERATORS_MAX_AGE_SEC", "60")
)

# Maximum acceptable snapshot age before diagnostics classify a window as
# STALE_FAILED rather than merely STALE_REFRESHING (see
# classify_snapshot_health() below) -- deliberately looser than the refresh
# interval itself (a build can legitimately still be in flight one interval
# late without indicating a genuine failure), tightened only if a build is
# taking pathologically long.
MAX_ACCEPTABLE_AGE_SEC = {
    "24h": int(os.environ.get("SNAPSHOT_MAX_AGE_24H_SEC", "900")),     # 15 min
    "7d":  int(os.environ.get("SNAPSHOT_MAX_AGE_7D_SEC", "2700")),      # 45 min
    "30d": int(os.environ.get("SNAPSHOT_MAX_AGE_30D_SEC", "5400")),     # 90 min
    "all": int(os.environ.get("SNAPSHOT_MAX_AGE_ALL_SEC", "5400")),     # 90 min
}

_FUNCTIONS = ("operational_intelligence", "pipeline_health")

_log = logging.getLogger(__name__)
_STOP = False


def _handle_signal(signum, _frame) -> None:
    global _STOP
    _STOP = True


def _pid_alive(pid: int) -> bool:
    # Identical to operation_scheduler.py's own _pid_alive -- kept as a
    # local copy (not imported) so this module has zero import dependency
    # on that unrelated scheduler, per this codebase's existing convention
    # of small, independent, single-purpose standalone processes.
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _lock_path(function: str, window_seconds: int) -> str:
    return os.path.join(LOCK_DIR, f"{function}__{window_seconds}.lock")


def acquire_window_lock(function: str, window_seconds: int) -> bool:
    """Per-(function, window) lock, single-instance-per-key, stale-owner
    reclamation -- same pattern as operation_scheduler.acquire_lock().
    Returns False if a LIVE owner already holds this exact key (another
    refresh for this window is genuinely running); a dead owner's lock is
    silently reclaimed rather than blocking forever (the task's explicit
    "automatically clear stale locks" / "do not leave windows permanently
    refreshing" requirement)."""
    os.makedirs(LOCK_DIR, exist_ok=True)
    path = _lock_path(function, window_seconds)
    if os.path.exists(path):
        try:
            with open(path) as f:
                owner = int((f.read().strip() or "0"))
        except (ValueError, OSError):
            owner = 0
        if owner and _pid_alive(owner) and owner != os.getpid():
            return False
        try:
            os.remove(path)
        except OSError:
            pass
    try:
        with open(path, "w") as f:
            f.write(str(os.getpid()))
        return True
    except OSError as exc:
        _log.warning("could not write lock file %s: %s", path, exc)
        return False


def release_window_lock(function: str, window_seconds: int) -> None:
    path = _lock_path(function, window_seconds)
    try:
        if os.path.exists(path):
            with open(path) as f:
                if (f.read().strip() or "0") == str(os.getpid()):
                    os.remove(path)
    except OSError:
        pass


def _build_operational_intelligence(window_seconds: int):
    from src.ops.operational_intelligence import build_operational_intelligence
    return build_operational_intelligence(OPS_DB_PATH, LIVE_DB_PATH, window_seconds=window_seconds)


def _build_pipeline_health(window_seconds: int):
    from src.ops.investigation_pipeline import build_pipeline_health
    return build_pipeline_health(OPS_DB_PATH, LIVE_DB_PATH, window_seconds=window_seconds)


_BUILDERS = {
    "operational_intelligence": _build_operational_intelligence,
    "pipeline_health": _build_pipeline_health,
}


def refresh_one(function: str, window_seconds: int, *, reason: str = "scheduled") -> dict:
    """Runs ONE build to completion in THIS process (never a daemon thread
    of some other, shorter-lived process) and persists it via the existing,
    unchanged write_snapshot() atomic-write path. Returns a small result
    dict for logging/--once callers. Never raises -- a build failure is
    caught, logged, and reported; the previous on-disk snapshot is left
    completely untouched (write_snapshot is never called on the failure
    path, exactly mirroring SWRCache._refresh's own success-only
    persistence discipline)."""
    if not acquire_window_lock(function, window_seconds):
        return {"function": function, "window_seconds": window_seconds,
                "status": "SKIPPED_ALREADY_RUNNING"}

    try:
        builder = _BUILDERS[function]
        start = time.perf_counter()
        try:
            payload = builder(window_seconds)
        except Exception as exc:  # noqa: BLE001 -- must never crash the scheduler loop
            _log.warning(
                "snapshot refresh FAILED function=%s window_seconds=%s error=%s",
                function, window_seconds, exc,
            )
            return {"function": function, "window_seconds": window_seconds,
                    "status": "FAILED", "error": str(exc)}
        build_ms = (time.perf_counter() - start) * 1000

        written = write_snapshot(
            function, window_seconds, payload, build_duration_ms=build_ms,
            completeness_key="total_launches", refresh_reason=reason,
        )
        status = "SUCCESS" if written else "REJECTED_SANITY_CHECK"
        _log.info(
            "snapshot refresh %s function=%s window_seconds=%s build_ms=%.1f",
            status, function, window_seconds, build_ms,
        )
        return {"function": function, "window_seconds": window_seconds,
                "status": status, "build_duration_ms": build_ms}
    finally:
        release_window_lock(function, window_seconds)


def _refresh_emerging_operators(reason: str = "scheduled") -> dict:
    """X72.0 -- same lock-file discipline as refresh_one() (per-key lock,
    stale-PID reclaim), routed through the dedicated emerging_operators
    builder instead of the windowed _BUILDERS table."""
    if not acquire_window_lock(EMERGING_OPERATORS_FUNCTION, EMERGING_OPERATORS_WINDOW_SECONDS):
        return {"function": EMERGING_OPERATORS_FUNCTION,
                "window_seconds": EMERGING_OPERATORS_WINDOW_SECONDS,
                "status": "SKIPPED_ALREADY_RUNNING"}
    try:
        result = refresh_emerging_operators_snapshot(OPS_DB_PATH, LIVE_DB_PATH, reason=reason)
        _log.info(
            "snapshot refresh %s function=%s build_ms=%s family_count=%s",
            result.get("status"), EMERGING_OPERATORS_FUNCTION,
            result.get("build_duration_ms"), result.get("family_count"),
        )
        return result
    finally:
        release_window_lock(EMERGING_OPERATORS_FUNCTION, EMERGING_OPERATORS_WINDOW_SECONDS)


def run_once() -> list[dict]:
    """Refreshes every (function, window) pair exactly once, sequentially
    (the existing in-process build already serializes via its own DB
    connections; running these sequentially in one dedicated process is
    simpler and safer than adding new concurrency here, and matches the
    existing _OPERATIONAL_INTELLIGENCE_BUILD_LOCK's own single-flight
    intent from the gunicorn side)."""
    results = []
    for window_param in WINDOW_ORDER:
        window_seconds = window_seconds_for(window_param)
        for function in _FUNCTIONS:
            results.append(refresh_one(function, window_seconds, reason="manual_once"))
    results.append(_refresh_emerging_operators(reason="manual_once"))
    return results


def _due_for_refresh(function: str, window_param: str, window_seconds: int) -> bool:
    snapshot = read_snapshot(function, window_seconds)
    if snapshot is None:
        return True
    age = time.time() - snapshot.computed_at
    return age >= REFRESH_INTERVAL_SEC[window_param]


def _emerging_operators_due_for_refresh() -> bool:
    snapshot = read_snapshot(EMERGING_OPERATORS_FUNCTION, EMERGING_OPERATORS_WINDOW_SECONDS)
    if snapshot is None:
        return True
    age = time.time() - snapshot.computed_at
    return age >= EMERGING_OPERATORS_REFRESH_INTERVAL_SEC


def _emerging_operators_loop_body() -> None:
    tick = min(5, max(1, EMERGING_OPERATORS_REFRESH_INTERVAL_SEC // 3))
    while not _STOP:
        if _emerging_operators_due_for_refresh():
            _refresh_emerging_operators(reason="scheduled")
        for _ in range(tick):
            if _STOP:
                break
            time.sleep(1)


def run_loop(poll_interval_sec: int = 30) -> None:
    """Continuous mode: checks every (function, window) pair on its own
    cadence (REFRESH_INTERVAL_SEC), refreshing whichever ones are due.
    A crash mid-refresh leaves this process's own lock file behind with
    this process's PID; the NEXT scheduler invocation (supervisord's
    autorestart, exactly like operation_scheduler.py) finds that PID dead
    via _pid_alive() and reclaims the lock automatically -- no separate
    cleanup step, no permanently-stuck REFRESHING state.

    X72.0 -- emerging_operators needs its own tight cadence (default 15s)
    independent of the windowed operational_intelligence/pipeline_health
    builds below, each of which can legitimately run for 20-130+ seconds.
    Sequencing it after (or interleaved within) that outer per-window loop
    would make it wait behind whichever windowed build happens to be
    in-flight, silently inflating its effective staleness to minutes. A
    dedicated background thread, scoped to this single standalone scheduler
    PROCESS (never a request-serving gunicorn worker -- the SWRCache/
    X67.28A precedent this codebase already rejected in-worker background
    threads for does not apply here), keeps its cadence genuinely
    independent. It touches only its own (function, window_seconds) lock
    file and its own snapshot key, so it cannot contend with the windowed
    builds' locks or persistence."""
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    _log.info("intelligence_snapshot_scheduler starting (pid=%s)", os.getpid())

    import threading
    emerging_thread = threading.Thread(
        target=_emerging_operators_loop_body, name="emerging-operators-refresh", daemon=True,
    )
    emerging_thread.start()

    while not _STOP:
        for window_param in WINDOW_ORDER:
            if _STOP:
                break
            window_seconds = window_seconds_for(window_param)
            for function in _FUNCTIONS:
                if _STOP:
                    break
                if _due_for_refresh(function, window_param, window_seconds):
                    refresh_one(function, window_seconds, reason="scheduled")
        for _ in range(poll_interval_sec):
            if _STOP:
                break
            time.sleep(1)

    emerging_thread.join(timeout=5)
    _log.info("intelligence_snapshot_scheduler stopping (pid=%s)", os.getpid())


def status() -> dict:
    """Diagnostic snapshot of every (function, window) key's on-disk state
    -- the same information api_intel_snapshot_health() (operation_
    dashboard_routes.py) exposes over HTTP, callable standalone for CLI
    use/manual inspection."""
    from src.core.snapshot_health import classify_snapshot_health

    out = {}
    for window_param in WINDOW_ORDER:
        window_seconds = window_seconds_for(window_param)
        out[window_param] = {}
        for function in _FUNCTIONS:
            out[window_param][function] = classify_snapshot_health(function, window_seconds)
    out["emerging_operators"] = classify_snapshot_health(
        EMERGING_OPERATORS_FUNCTION, EMERGING_OPERATORS_WINDOW_SECONDS,
        max_acceptable_age_sec=EMERGING_OPERATORS_MAX_AGE_SEC,
    )
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--poll-interval", type=int, default=30)
    args = ap.parse_args()

    if args.status:
        print(json.dumps(status(), indent=2, default=str))
    elif args.once:
        print(json.dumps(run_once(), indent=2, default=str))
    elif args.loop:
        run_loop(poll_interval_sec=args.poll_interval)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
