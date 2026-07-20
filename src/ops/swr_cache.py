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

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

FRESH = "fresh"
STALE = "stale"
REFRESHING = "refreshing"


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

    def __init__(self, ttl_seconds: float, *, executor: Optional[Callable[[Callable[[], None]], None]] = None):
        self.ttl_seconds = ttl_seconds
        self._entries: dict[Any, _Entry] = {}
        self._dict_lock = threading.Lock()
        # executor: how to run the refresh off the calling thread. Defaults
        # to a plain daemon Thread per refresh (no new worker-pool
        # infrastructure introduced); callers may inject a real executor
        # (e.g. a shared ThreadPoolExecutor) if the codebase already has one
        # available -- "implementation choice is left to the codebase," per
        # the brief.
        self._executor = executor or (lambda fn: threading.Thread(target=fn, daemon=True).start())

        # Runtime metrics (X29.1.2's explicit Deliverables requirement).
        self.metrics = {
            "cache_hits": 0,          # FRESH served
            "stale_serves": 0,        # STALE/REFRESHING served (never blocked)
            "refreshes_started": 0,
            "refreshes_succeeded": 0,
            "refreshes_failed": 0,
            "refreshes_suppressed": 0,  # a refresh was already in flight
            "cold_computes": 0,        # true first-ever population (blocking, unavoidable)
        }
        self._metrics_lock = threading.Lock()

    def _inc(self, name: str) -> None:
        with self._metrics_lock:
            self.metrics[name] += 1

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
            value = compute()
            now = time.time()
            new_entry = _Entry(value=value, computed_at=now)
            with self._dict_lock:
                self._entries[key] = new_entry
            return value, {"state": FRESH, "age_seconds": 0.0, "generated_at": now}

        age = time.time() - entry.computed_at
        if age < self.ttl_seconds:
            self._inc("cache_hits")
            return entry.value, {"state": FRESH, "age_seconds": round(age, 3), "generated_at": entry.computed_at}

        # Stale. Serve the previous value immediately, and trigger AT MOST
        # one background refresh (single-flight) -- the core requirement.
        self._inc("stale_serves")
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

    def _refresh(self, key: Any, entry: _Entry, compute: Callable[[], Any]) -> None:
        """Runs off the calling thread. On success, atomically swaps the
        entries[key] pointer to a brand-new _Entry (readers already holding
        a reference to the OLD entry/value are unaffected -- no partial
        state is ever observable). On failure, the previous entry is left
        completely untouched; only entry.refreshing is cleared so a later
        stale request can retry."""
        try:
            new_value = compute()
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: any
            # refresh failure must never crash the background thread or
            # corrupt the cache; log-and-keep-serving-stale is the required
            # behaviour ("Keep previous cache / Log failure / Retry on next
            # stale request").
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

        now = time.time()
        new_entry = _Entry(value=new_value, computed_at=now)
        with self._dict_lock:
            self._entries[key] = new_entry  # single atomic pointer swap
        self._inc("refreshes_succeeded")
