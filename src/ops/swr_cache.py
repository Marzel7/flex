"""X29.1.2 — Stale-while-revalidate cache with single-flight refresh.

Generic, dependency-free (no Flask import) so it can be unit-tested without
a running server. Used by src/core/operation_dashboard_routes.py's
Operational Intelligence route to eliminate the ~31s cold-start latency
X29.1's plain TTL cache had: the first request after expiry previously
blocked on a full recomputation before returning anything.

States, per entry (X29.1.2's explicit Cache States requirement):
  FRESH      -- within TTL. Served immediately, no refresh triggered.
  STALE      -- TTL exceeded, no refresh currently running. Served
               immediately (the stale value), AND triggers exactly one
               background refresh.
  REFRESHING -- TTL exceeded, a refresh is already in flight. Served
               immediately (the stale value), does NOT start a second
               refresh (single-flight).

No entry ever blocks the calling thread on a compute() call unless the key
has NEVER been populated at all (a true cold cache, unavoidable -- there is
no "previous result" to serve yet). That first-ever population is
synchronous by necessity; every expiry after that is non-blocking.

Atomic replacement: a successful refresh swaps in a brand-new dict via one
attribute assignment (single Python bytecode-level pointer swap under the
GIL) -- readers never observe a partially-updated entry. A failed refresh
leaves the previous entry completely untouched and logs the failure; the
next stale request retries.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

FRESH = "fresh"
STALE = "stale"
REFRESHING = "refreshing"
WARMING = "warming"

# X67.28A -- EMERGENCY PERFORMANCE STABILISATION FEATURE FLAG.
#
# Incident: build_operational_intelligence()/build_pipeline_health()
# refreshes run as in-process daemon threads (see SWRCache._refresh/
# _cold_build below). Under production load this pinned a gunicorn worker
# at ~150-200% CPU for minutes at a time (multiple ~8-9-minute `all`-window
# builds overlapping), degrading responsiveness for every OTHER request
# that worker was supposed to be serving concurrently. X67.28's standalone
# intelligence_snapshot_scheduler process (src/core/intelligence_snapshot_
# scheduler.py) is the real, permanent fix -- it runs these same builds to
# completion in a SEPARATE process, off the request-serving worker
# entirely. Until that scheduler is confirmed running continuously in
# production, this flag lets request-driven (in-worker) rebuilds be
# disabled immediately, with ZERO other behavioural change: hydration
# (SWRCache.hydrate()), cache reads, and every existing code path are
# completely untouched -- only the "start a NEW background rebuild from
# inside a request" trigger is skipped. A request against a cold key (no
# entry has ever been hydrated/computed) is NOT affected by this flag --
# see get()'s own cold-start branch, which this flag never touches, since
# disabling that would mean serving nothing at all for a never-populated
# key, which is a worse outage than the one this flag exists to fix.
#
# Default: "0" (rebuilds enabled) -- preserves exact pre-X67.28A behaviour
# unless explicitly opted out via WATCHTOWER_DISABLE_REQUEST_REBUILDS=1.
# This is a TEMPORARY stabilisation lever, not a permanent design decision
# -- see X67.28A's own deliverable for the plan to remove it once the
# standalone scheduler is running continuously and this incident is closed.
def request_driven_rebuilds_disabled() -> bool:
    return os.environ.get("WATCHTOWER_DISABLE_REQUEST_REBUILDS", "0").strip().lower() in (
        "1", "true", "yes",
    )


@dataclass
class _Entry:
    value: Any
    computed_at: float
    refreshing: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class SWRCache:
    """One cache, keyed by an arbitrary hashable key (e.g. window_seconds).
    Thread-safe: entries dict access and each entry's own refresh flag are
    both guarded by locks, so concurrent requests across gthread worker
    threads (the deployment's actual concurrency model) cannot race."""

    def __init__(
        self, ttl_seconds: float, *,
        executor: Optional[Callable[[Callable[[], None]], None]] = None,
        on_success: Optional[Callable[[Any, Any, float], None]] = None,
    ):
        self.ttl_seconds = ttl_seconds
        self._entries: dict[Any, _Entry] = {}
        self._dict_lock = threading.Lock()
        # X65.57 — optional callback fired ONLY after a build SUCCEEDS
        # (cold-start compute(), a background _refresh(), or a background
        # _cold_build()), never on failure and never for an already-FRESH
        # cache hit. Signature: on_success(key, value, build_duration_ms).
        # Used by the Discovery routes to persist a snapshot to disk; a
        # cache with no callback (the default) behaves EXACTLY as before
        # this change — on_success is simply never called.
        self._on_success = on_success
        # executor: how to run the refresh off the calling thread. Defaults
        # to a plain daemon Thread per refresh (no new worker-pool
        # infrastructure introduced); callers may inject a real executor
        # (e.g. a shared ThreadPoolExecutor) if the codebase already has one
        # available -- "implementation choice is left to the codebase," per
        # the brief.
        self._executor = executor or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        # X65.52 -- tracks which keys have a cold-build (never-populated-yet)
        # already in flight via try_get(), separate from _Entry.refreshing
        # (which only applies to a key that already has SOME value). A
        # plain dict + lock, not an _Entry, since there is nothing to swap
        # in yet -- try_get()'s corresponding entry in _entries appears only
        # once the background compute() finishes.
        self._cold_building: set[Any] = set()
        self._cold_building_lock = threading.Lock()

        # Runtime metrics (X29.1.2's explicit Deliverables requirement).
        self.metrics = {
            "cache_hits": 0,          # FRESH served
            "stale_serves": 0,        # STALE/REFRESHING served (never blocked)
            "refreshes_started": 0,
            "refreshes_succeeded": 0,
            "refreshes_failed": 0,
            "refreshes_suppressed": 0,  # a refresh was already in flight
            "cold_computes": 0,        # true first-ever population (blocking, unavoidable)
            # X65.52 -- non-blocking cold-path metrics (try_get() only).
            "cold_warming_started": 0,   # a NEW background cold build was kicked off
            "cold_warming_suppressed": 0,  # a cold build was already in flight for this key
            "warming_serves": 0,       # a caller got the WARMING sentinel (never blocked)
        }
        self._metrics_lock = threading.Lock()

    def _inc(self, name: str) -> None:
        with self._metrics_lock:
            self.metrics[name] += 1

    def _fire_on_success(self, key: Any, value: Any, build_duration_ms: float) -> None:
        # X65.57 -- best-effort only. A persistence failure (disk full,
        # permissions, whatever) must never break the in-memory cache's own
        # already-correct behaviour, so this is caught and logged, never
        # re-raised -- identical discipline to compute()'s own failure
        # handling in _refresh()/_cold_build().
        if self._on_success is None:
            return
        try:
            self._on_success(key, value, build_duration_ms)
        except Exception as exc:  # noqa: BLE001
            try:
                import logging
                logging.getLogger(__name__).warning(
                    "SWRCache on_success callback failed for key=%r: %s", key, exc)
            except Exception:
                pass

    def state_of(self, key: Any) -> Optional[str]:
        """For observability/tests: FRESH/STALE/REFRESHING, or None if the
        key has never been populated."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        with entry.lock:
            if entry.refreshing:
                return REFRESHING
            age = time.time() - entry.computed_at
            return FRESH if age < self.ttl_seconds else STALE

    def hydrate(self, key: Any, value: Any, computed_at: float) -> bool:
        """X65.57 — seeds this key directly from a persisted snapshot,
        WITHOUT calling compute(). Intended to run once, at process
        startup, before any request or prewarm touches this key. Uses
        `computed_at` from the snapshot itself (not time.time()) so a
        snapshot's true age is preserved -- a snapshot written 20 minutes
        ago is correctly STALE (and will trigger exactly one background
        refresh on first access, same as any other stale entry) rather
        than being reported as freshly computed right now.

        Returns False (no-op) if this key already has an entry -- hydrate()
        must never clobber a value that a real build (e.g. a prewarm that
        raced ahead of hydration) already produced; disk snapshots are only
        ever a FALLBACK for a key nothing has populated yet this process."""
        with self._dict_lock:
            if key in self._entries:
                return False
            self._entries[key] = _Entry(value=value, computed_at=computed_at)
            return True

    def get(self, key: Any, compute: Callable[[], Any]) -> tuple[Any, dict]:
        """Returns (value, meta) where meta = {state, age_seconds,
        generated_at}. `compute` is the (potentially expensive) function
        that produces a fresh value for this key -- called synchronously
        only on a true cold cache (key never populated before); otherwise
        the previous value is returned immediately and, if stale, a refresh
        is scheduled in the background via `compute` run off-thread."""
        with self._dict_lock:
            entry = self._entries.get(key)

        if entry is None:
            # True cold start: no previous value exists at all. There is
            # nothing to serve "immediately" -- this one call must compute
            # synchronously. Every subsequent expiry for this key is
            # non-blocking (X29.1.2's whole point).
            self._inc("cold_computes")
            _build_start = time.perf_counter()
            value = compute()
            _build_ms = (time.perf_counter() - _build_start) * 1000
            now = time.time()
            new_entry = _Entry(value=value, computed_at=now)
            with self._dict_lock:
                self._entries[key] = new_entry
            self._fire_on_success(key, value, _build_ms)
            return value, {"state": FRESH, "age_seconds": 0.0, "generated_at": now}

        age = time.time() - entry.computed_at
        if age < self.ttl_seconds:
            self._inc("cache_hits")
            return entry.value, {"state": FRESH, "age_seconds": round(age, 3), "generated_at": entry.computed_at}

        # Stale. Serve the previous value immediately, and trigger AT MOST
        # one background refresh (single-flight) -- the core requirement.
        self._inc("stale_serves")
        # X67.28A -- emergency stabilisation: skip starting a NEW in-worker
        # background rebuild entirely when the flag is set. The stale
        # value is still served immediately below exactly as before --
        # only the "kick off an expensive rebuild from inside this
        # request's worker" side effect is suppressed.
        if request_driven_rebuilds_disabled():
            state = REFRESHING if entry.refreshing else STALE
            return entry.value, {"state": state, "age_seconds": round(age, 3), "generated_at": entry.computed_at}

        should_refresh = False
        with entry.lock:
            if entry.refreshing:
                self._inc("refreshes_suppressed")
            else:
                entry.refreshing = True
                should_refresh = True

        if should_refresh:
            self._inc("refreshes_started")
            self._executor(lambda: self._refresh(key, entry, compute))

        state = REFRESHING if (entry.refreshing and not should_refresh) else STALE
        return entry.value, {"state": state, "age_seconds": round(age, 3), "generated_at": entry.computed_at}

    def try_get(self, key: Any, compute: Callable[[], Any]) -> tuple[Optional[Any], dict]:
        """X65.52 — non-blocking counterpart to get(). Identical behaviour
        for every state EXCEPT true cold (never-populated) keys: instead of
        calling compute() synchronously and blocking the caller for the
        full cold-build duration (get()'s documented, unavoidable-for-a-
        first-request behaviour), this kicks off compute() in the
        background at most once per key (single-flight, via
        _cold_building) and returns immediately with value=None and
        state=WARMING. A caller in this state has NOTHING to render for
        this key yet -- by construction, get()'s "serve the previous value
        while it refreshes" trick doesn't apply, because there IS no
        previous value. Once the background compute() finishes, the
        result lands in self._entries exactly as get() would have put it,
        so the VERY NEXT try_get() (or get()) call for this key returns
        FRESH normally -- no separate code path, no second source of
        truth. Every already-warm state (FRESH/STALE/REFRESHING) behaves
        identically to get() and is delegated to it directly."""
        with self._dict_lock:
            entry = self._entries.get(key)

        if entry is not None:
            return self.get(key, compute)

        should_start = False
        with self._cold_building_lock:
            if key in self._cold_building:
                self._inc("cold_warming_suppressed")
            else:
                self._cold_building.add(key)
                should_start = True

        if should_start:
            self._inc("cold_warming_started")
            self._executor(lambda: self._cold_build(key, compute))

        self._inc("warming_serves")
        return None, {"state": WARMING, "age_seconds": None, "generated_at": None}

    def _cold_build(self, key: Any, compute: Callable[[], Any]) -> None:
        """Runs off the calling thread. Populates self._entries on success
        exactly as get()'s cold-start branch would have -- a subsequent
        try_get()/get() for this key then behaves as an ordinary FRESH hit,
        no different from a key that happened to be prewarmed. Failure is
        logged and the key is released from _cold_building so a later
        try_get() can retry the build (never permanently stuck WARMING)."""
        _build_start = time.perf_counter()
        try:
            value = compute()
        except Exception as exc:  # noqa: BLE001 -- must never crash this background thread
            try:
                import logging
                logging.getLogger(__name__).warning(
                    "SWRCache cold background build failed for key=%r: %s", key, exc)
            except Exception:
                pass
            with self._cold_building_lock:
                self._cold_building.discard(key)
            return
        _build_ms = (time.perf_counter() - _build_start) * 1000

        now = time.time()
        new_entry = _Entry(value=value, computed_at=now)
        with self._dict_lock:
            self._entries[key] = new_entry
        with self._cold_building_lock:
            self._cold_building.discard(key)
        self._fire_on_success(key, value, _build_ms)

    def _refresh(self, key: Any, entry: _Entry, compute: Callable[[], Any]) -> None:
        """Runs off the calling thread. On success, atomically swaps the
        entries[key] pointer to a brand-new _Entry (readers already holding
        a reference to the OLD entry/value are unaffected -- no partial
        state is ever observable). On failure, the previous entry is left
        completely untouched; only entry.refreshing is cleared so a later
        stale request can retry."""
        _build_start = time.perf_counter()
        try:
            new_value = compute()
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: any
            # refresh failure must never crash the background thread or
            # corrupt the cache; log-and-keep-serving-stale is the required
            # behaviour ("Keep previous cache / Log failure / Retry on next
            # stale request"). Note this also means a failed refresh never
            # touches the on-disk snapshot (X65.57) -- _fire_on_success is
            # only called below, on the success path.
            self._inc("refreshes_failed")
            try:
                import logging
                logging.getLogger(__name__).warning(
                    "SWRCache refresh failed for key=%r: %s", key, exc)
            except Exception:
                pass
            with entry.lock:
                entry.refreshing = False
            return
        _build_ms = (time.perf_counter() - _build_start) * 1000

        now = time.time()
        new_entry = _Entry(value=new_value, computed_at=now)
        with self._dict_lock:
            self._entries[key] = new_entry  # single atomic pointer swap
        self._inc("refreshes_succeeded")
        self._fire_on_success(key, new_value, _build_ms)
