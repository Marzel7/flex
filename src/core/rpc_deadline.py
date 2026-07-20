"""X24.3 — Bounded RPC tail-latency protection for getTransaction.

Context (X24.2.3 evidence, proven not assumed): urllib.request.urlopen(timeout=N)
bounds each individual blocking socket operation (connect, send, each recv chunk)
independently, not the logical HTTP request as a whole. A slow connect followed by
a slow response read can each individually stay under the configured budget while
the call as a whole runs close to 2x that budget (observed: ~23.6s against a
configured 12s budget). Python cannot interrupt a thread blocked inside a C-level
socket syscall (concurrent.futures.Future.cancel() returns False once RUNNING,
and asyncio.wait_for() wrapping run_in_executor() was verified empirically to
leave the underlying worker thread running to completion regardless of the
caller-side TimeoutError) — so the only way to enforce a TRUE cumulative deadline
is to run the blocking call on a dedicated thread and give up waiting on it from
the caller's side once the deadline elapses, while bounding how many such
abandoned calls can ever exist concurrently.

This module is intentionally self-contained (no dependency on ws_cascade.py
internals) so it can be unit-tested in isolation and swapped/rolled back by
changing one call site.

Scope discipline (explicit, per X24.3 brief): this module changes ONLY how a
single _get_tx()-shaped call enforces its deadline. It does not know about
sweep scheduling, retry offsets, candidate logic, or detection — those stay
exactly as they are; they just receive one of six explicit outcomes instead of
an implicit None.
"""
from __future__ import annotations

import os
import time
import threading
import concurrent.futures
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


def _log(msg: str) -> None:
    print(f"[RPC_DEADLINE] {msg}", flush=True)


# ── Configuration (env-overridable, matches the codebase's existing convention) ──
RPC_DEADLINE_EXECUTOR_SIZE = int(os.environ.get("RPC_DEADLINE_EXECUTOR_SIZE", "6"))
# Bounded total capacity = running + queued. Deliberately NOT the executor's own
# unbounded queue — explicit rejection once this is reached (design requirement 2).
RPC_DEADLINE_MAX_CAPACITY = int(os.environ.get("RPC_DEADLINE_MAX_CAPACITY", "12"))
RPC_DEADLINE_SECONDS = float(os.environ.get("RPC_DEADLINE_SECONDS", "12"))
# Circuit breaker tuning.
RPC_BREAKER_FAILURE_THRESHOLD = int(os.environ.get("RPC_BREAKER_FAILURE_THRESHOLD", "8"))
RPC_BREAKER_WINDOW_SECONDS = float(os.environ.get("RPC_BREAKER_WINDOW_SECONDS", "30"))
RPC_BREAKER_OPEN_SECONDS = float(os.environ.get("RPC_BREAKER_OPEN_SECONDS", "20"))
RPC_BREAKER_HALF_OPEN_MAX_PROBES = int(os.environ.get("RPC_BREAKER_HALF_OPEN_MAX_PROBES", "1"))
# X24.3 final hardening — the late-result cache must be bounded by BOTH time
# (TTL, unchanged at 30s) AND memory (max entry count). A TTL alone is not
# sufficient: a burst of many distinct hanging signatures within one 30s
# window could otherwise grow the cache without limit for the duration of
# that window.
RPC_LATE_RESULT_MAX_ENTRIES = int(os.environ.get("RPC_LATE_RESULT_MAX_ENTRIES", "1000"))


class Outcome(str, Enum):
    """Explicit outcomes (design requirement 4) — telemetry must distinguish
    these even where the existing retry logic still treats several the same way
    (e.g. by returning None/falsy for all of DEADLINE_EXCEEDED_RUNNING,
    CANCELLED_BEFORE_START, CAPACITY_REJECTED, RPC_ERROR)."""
    SUCCESS = "SUCCESS"
    DEADLINE_EXCEEDED_RUNNING = "DEADLINE_EXCEEDED_RUNNING"
    CANCELLED_BEFORE_START = "CANCELLED_BEFORE_START"
    CAPACITY_REJECTED = "CAPACITY_REJECTED"
    CIRCUIT_OPEN_REJECTED = "CIRCUIT_OPEN_REJECTED"
    RPC_ERROR = "RPC_ERROR"
    NOT_FOUND = "NOT_FOUND"


# ── Production metrics (X24.3 final hardening) ───────────────────────────────
# Matches the existing ws_cascade.py convention: a plain name->count dict,
# incremented via a small helper, readable by any external metrics/health
# endpoint the same way Cascade._metric()'s self._subprov_sig_metrics is read
# today. Kept here (not on Cascade) so this module stays independently
# testable and importable without a Cascade instance.
_METRIC_NAMES = (
    "deadline_exceeded", "cancelled_before_start", "abandoned_running",
    "capacity_rejected", "breaker_open", "breaker_half_open", "breaker_closed",
    "late_result_cache_hit", "late_result_cache_miss", "late_result_cache_eviction",
)


class _Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {name: 0 for name in _METRIC_NAMES}

    def inc(self, name: str, by: int = 1) -> None:
        with self._lock:
            self._counts[name] = self._counts.get(name, 0) + by

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)


@dataclass
class DeadlineResult:
    outcome: Outcome
    value: Optional[dict] = None
    wall_ms: float = 0.0
    reused_late_result: bool = False


# ── Circuit breaker (design requirement 3) ───────────────────────────────────
class _CircuitBreaker:
    """CLOSED -> OPEN -> HALF_OPEN -> (CLOSED or OPEN). A sliding window of
    deadline-failure events (DEADLINE_EXCEEDED_RUNNING / CANCELLED_BEFORE_START
    / RPC_ERROR) trips the breaker; while OPEN, calls are rejected immediately
    (CIRCUIT_OPEN_REJECTED) without ever touching the dedicated executor, so a
    genuinely degraded provider cannot keep consuming pool capacity. After
    RPC_BREAKER_OPEN_SECONDS it allows a single HALF_OPEN probe; success closes
    the breaker, failure reopens it for another full OPEN_SECONDS window."""

    def __init__(self, *, failure_threshold: int, window_s: float, open_s: float,
                 half_open_max_probes: int, metrics: Optional["_Metrics"] = None):
        self._failure_threshold = failure_threshold
        self._window_s = window_s
        self._open_s = open_s
        self._half_open_max_probes = half_open_max_probes
        self._lock = threading.Lock()
        self._state = "CLOSED"
        self._failure_times: list[float] = []
        self._opened_at: Optional[float] = None
        self._half_open_probes_in_flight = 0
        self._metrics = metrics

    def state(self) -> str:
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    def _maybe_transition_to_half_open(self) -> None:
        if self._state == "OPEN" and self._opened_at is not None:
            if time.time() - self._opened_at >= self._open_s:
                self._state = "HALF_OPEN"
                self._half_open_probes_in_flight = 0
                if self._metrics is not None:
                    self._metrics.inc("breaker_half_open")

    def allow_request(self) -> bool:
        """Returns True if a request may proceed (CLOSED, or a HALF_OPEN probe
        slot is available), False if it must be rejected (OPEN, or HALF_OPEN
        with a probe already in flight)."""
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state == "CLOSED":
                return True
            if self._state == "HALF_OPEN":
                if self._half_open_probes_in_flight < self._half_open_max_probes:
                    self._half_open_probes_in_flight += 1
                    return True
                return False
            return False  # OPEN

    def record_success(self) -> None:
        with self._lock:
            if self._state == "HALF_OPEN":
                _log("circuit breaker HALF_OPEN probe succeeded -> CLOSED")
                self._state = "CLOSED"
                self._failure_times.clear()
                self._half_open_probes_in_flight = 0
                if self._metrics is not None:
                    self._metrics.inc("breaker_closed")
            elif self._state == "CLOSED":
                # Trim the window opportunistically on successes too, so a
                # long run of successes after a few early failures doesn't
                # leave stale failure timestamps sitting in the list forever.
                self._trim_window()

    def record_failure(self) -> None:
        now = time.time()
        with self._lock:
            if self._state == "HALF_OPEN":
                _log("circuit breaker HALF_OPEN probe failed -> OPEN")
                self._state = "OPEN"
                self._opened_at = now
                self._half_open_probes_in_flight = 0
                if self._metrics is not None:
                    self._metrics.inc("breaker_open")
                return
            self._failure_times.append(now)
            self._trim_window()
            if self._state == "CLOSED" and len(self._failure_times) >= self._failure_threshold:
                _log(f"circuit breaker tripped: {len(self._failure_times)} failures "
                     f"in {self._window_s}s window -> OPEN")
                self._state = "OPEN"
                self._opened_at = now
                self._failure_times.clear()
                if self._metrics is not None:
                    self._metrics.inc("breaker_open")

    def _trim_window(self) -> None:
        cutoff = time.time() - self._window_s
        self._failure_times = [t for t in self._failure_times if t >= cutoff]


# ── In-flight guard (design requirement 5) + late-result cache (requirement 6) ──
class _InFlightRegistry:
    """At most one physical getTransaction request may exist per signature at a
    time. A caller whose deadline elapses while the physical request is still
    running does NOT start a second physical request for the same signature —
    it registers as a waiter on the existing future instead. If that abandoned
    request eventually succeeds, the result is cached briefly so a subsequent
    retry (or a second concurrent caller for the same sig) can reuse it instead
    of paying the RPC cost twice.

    Also owns the "physical requests currently occupying the dedicated pool"
    counter (design requirement 2's running+queued bound) — incremented on
    submit, decremented only when the PHYSICAL request itself completes (via
    the done-callback), never when a caller merely stops waiting on it. This
    is what correctly reflects how many slots of the dedicated executor are
    actually occupied, as opposed to how many callers are currently blocked
    in fut.result(timeout=...)."""

    _LATE_RESULT_TTL_S = 30.0

    def __init__(self, on_physical_done: Optional[Callable[[], None]] = None,
                 metrics: Optional["_Metrics"] = None,
                 max_entries: int = RPC_LATE_RESULT_MAX_ENTRIES):
        # RLock, not Lock: concurrent.futures.Future.add_done_callback() invokes
        # the callback SYNCHRONOUSLY, on the calling thread, if the future is
        # already done by the time the callback is registered (this happens
        # routinely for fast/trivial calls completing before add_done_callback
        # is even reached). Since _reserve_and_submit() calls add_done_callback
        # while still inside this class's own get_or_register() lock scope, the
        # callback (_on_physical_request_done, which itself acquires this same
        # lock) can run on the SAME thread that already holds it — a plain Lock
        # deadlocks on that self-reacquisition; RLock permits it safely.
        self._lock = threading.RLock()
        self._in_flight: dict[str, concurrent.futures.Future] = {}
        # X24.3 final hardening — bounded LRU, not a plain dict. Eviction policy:
        # OrderedDict in insertion/access order; every read (take_late_result)
        # moves the entry to the end (move_to_end) marking it "most recently
        # used"; every write (on physical-request completion) also appends at
        # the end. When the map exceeds max_entries, the LEAST recently used
        # entry (popitem(last=False), i.e. the front of the ordering) is
        # evicted — a real LRU, not merely insertion-order FIFO, so a
        # signature that keeps getting looked up (e.g. by repeated retries)
        # is protected from eviction ahead of ones nobody has asked about since
        # they were written. TTL (30s) is still enforced independently on read.
        self._late_results: "OrderedDict[str, tuple[float, Optional[dict]]]" = OrderedDict()
        self._max_entries = max(1, max_entries)
        self._on_physical_done = on_physical_done
        self._metrics = metrics

    def get_or_register(self, sig: str,
                         submit_fn: Callable[[], Optional[concurrent.futures.Future]]
                         ) -> tuple[Optional[concurrent.futures.Future], bool]:
        """Returns (future, is_new). If a physical request for this signature is
        already in flight, returns its future with is_new=False — submit_fn is
        NOT called, so no second physical request is ever submitted for a
        signature that already has one running (design requirement 5).

        Otherwise submit_fn() is called WHILE HOLDING THIS LOCK, so the
        "is one already in flight" check and "submit a new one" action are
        atomic with respect to every other caller of this method — submit_fn
        is expected to perform its own capacity check/reservation before
        actually calling executor.submit(), and to return None if it declines
        to submit (e.g. capacity exhausted), in which case is_new is still
        True but future is None — the caller must treat that as a rejection,
        not as "join an existing future"."""
        with self._lock:
            existing = self._in_flight.get(sig)
            if existing is not None and not existing.done():
                return existing, False
            fut = submit_fn()
            if fut is not None:
                self._in_flight[sig] = fut
            return fut, True

    def take_late_result(self, sig: str) -> Optional[tuple[bool, Optional[dict]]]:
        """Non-destructively check for a cached late result. Returns
        (found, value) or None if nothing cached / expired. A hit moves the
        entry to the most-recently-used position (LRU touch); an expired
        entry is evicted on read regardless of LRU order (TTL always wins)."""
        with self._lock:
            entry = self._late_results.get(sig)
            if entry is None:
                if self._metrics is not None:
                    self._metrics.inc("late_result_cache_miss")
                return None
            cached_at, value = entry
            if time.time() - cached_at > self._LATE_RESULT_TTL_S:
                del self._late_results[sig]
                if self._metrics is not None:
                    self._metrics.inc("late_result_cache_miss")
                return None
            self._late_results.move_to_end(sig)  # LRU touch
            if self._metrics is not None:
                self._metrics.inc("late_result_cache_hit")
            return True, value

    def _on_physical_request_done(self, sig: str, fut: concurrent.futures.Future) -> None:
        """Callback attached to every physical request's future. Runs on
        whichever thread completes the future (the dedicated executor's worker
        thread) regardless of whether any caller is still waiting on it — this
        is exactly what makes late-result reuse possible for an abandoned
        request that eventually completes, AND is the only place the physical
        occupancy counter is decremented.

        Eviction policy (X24.3 final hardening — bounded by both time AND
        memory): after inserting/updating this signature's entry, if the map
        now exceeds max_entries, the single least-recently-used entry (the
        first item in the OrderedDict, i.e. the one touched/inserted longest
        ago) is evicted. This is a real LRU eviction, not FIFO-by-insertion —
        take_late_result()'s move_to_end() on every hit means a
        frequently-retried signature is never the eviction target while it
        keeps being asked about."""
        try:
            value = fut.result()
        except Exception:
            value = None
        with self._lock:
            if self._in_flight.get(sig) is fut:
                del self._in_flight[sig]
            self._late_results[sig] = (time.time(), value)
            self._late_results.move_to_end(sig)
            while len(self._late_results) > self._max_entries:
                evicted_sig, _ = self._late_results.popitem(last=False)
                if self._metrics is not None:
                    self._metrics.inc("late_result_cache_eviction")
                _log(f"late-result cache evicted sig={evicted_sig[:16]}… "
                     f"(max_entries={self._max_entries} exceeded)")
        if self._on_physical_done is not None:
            self._on_physical_done()


# ── Bounded dedicated executor (design requirements 1, 2, 7) ─────────────────
class RpcDeadlineGuard:
    """Owns one dedicated ThreadPoolExecutor, isolated from every other pool in
    the process (the shared asyncio default executor, _ACTIVE_CATCHUP0_EXECUTOR,
    and any DB-write path). Capacity is explicitly bounded at
    running + queued <= max_capacity — once reached, new calls are rejected
    immediately (CAPACITY_REJECTED) rather than queuing unboundedly on the
    executor's own internal queue.
    """

    def __init__(self, *, pool_size: int = RPC_DEADLINE_EXECUTOR_SIZE,
                 max_capacity: int = RPC_DEADLINE_MAX_CAPACITY,
                 deadline_s: float = RPC_DEADLINE_SECONDS,
                 breaker_failure_threshold: int = RPC_BREAKER_FAILURE_THRESHOLD,
                 breaker_window_s: float = RPC_BREAKER_WINDOW_SECONDS,
                 breaker_open_s: float = RPC_BREAKER_OPEN_SECONDS,
                 breaker_half_open_max_probes: int = RPC_BREAKER_HALF_OPEN_MAX_PROBES):
        self._pool_size = max(1, pool_size)
        self._max_capacity = max(self._pool_size, max_capacity)
        self._deadline_s = deadline_s
        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._lifecycle_lock = threading.Lock()
        self._inflight_count = 0  # running + queued, explicitly tracked (not derived from the executor)
        self._count_lock = threading.Lock()
        # X24.3 final hardening — production_metrics exposes exactly the
        # counter names requested for external monitoring (deadline_exceeded,
        # cancelled_before_start, abandoned_running, capacity_rejected,
        # breaker_open/half_open/closed, late_result_cache_hit/miss/eviction).
        # `metrics` (below) is kept as-is: it's the existing, more granular
        # per-Outcome dict already covered by tests — production_metrics is
        # additive, not a replacement.
        self.production_metrics = _Metrics()
        self._breaker = _CircuitBreaker(
            failure_threshold=breaker_failure_threshold, window_s=breaker_window_s,
            open_s=breaker_open_s, half_open_max_probes=breaker_half_open_max_probes,
            metrics=self.production_metrics)
        self._registry = _InFlightRegistry(on_physical_done=self._on_physical_done,
                                            metrics=self.production_metrics)
        self.metrics: dict[str, int] = {
            "success": 0, "deadline_exceeded_running": 0, "cancelled_before_start": 0,
            "capacity_rejected": 0, "circuit_open_rejected": 0, "rpc_error": 0,
            "not_found": 0, "late_result_reused": 0, "duplicate_suppressed": 0,
        }
        self._metrics_lock = threading.Lock()
        self.start()

    def production_metrics_snapshot(self) -> dict[str, int]:
        """Read-only snapshot of the production counters, safe to expose via a
        health/metrics endpoint the same way ws_cascade.py's other _metric()
        counters are read today."""
        return self.production_metrics.snapshot()

    # -- lifecycle (design requirement 7) --------------------------------
    def start(self) -> None:
        with self._lifecycle_lock:
            if self._executor is not None:
                return
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._pool_size, thread_name_prefix="wt-rpc-deadline")
            _log(f"executor started: pool_size={self._pool_size} max_capacity={self._max_capacity} "
                 f"deadline_s={self._deadline_s}")

    def shutdown(self, wait: bool = False) -> None:
        """wait=False (the default, matching a daemon-restart context): does not
        block on already-running (abandoned) calls — they are bounded in number
        (<= pool_size) and will exit on their own as the interpreter tears down
        the process; there is nothing further this call needs to wait for."""
        with self._lifecycle_lock:
            if self._executor is None:
                return
            _log(f"executor shutdown requested (wait={wait})")
            self._executor.shutdown(wait=wait, cancel_futures=True)
            self._executor = None

    def restart(self) -> None:
        self.shutdown(wait=False)
        self.start()

    def is_running(self) -> bool:
        return self._executor is not None

    # -- metrics ----------------------------------------------------------
    def _metric(self, name: str) -> None:
        with self._metrics_lock:
            self.metrics[name] = self.metrics.get(name, 0) + 1

    def breaker_state(self) -> str:
        return self._breaker.state()

    def current_capacity_used(self) -> int:
        with self._count_lock:
            return self._inflight_count

    def _on_physical_done(self) -> None:
        """Decrements the physical-occupancy counter. Called exactly once per
        genuinely-submitted physical request, when that request itself
        completes (success, error, or naturally finishing after being
        abandoned) — never when a caller merely stops waiting on it."""
        with self._count_lock:
            self._inflight_count = max(0, self._inflight_count - 1)

    # -- the guarded call ---------------------------------------------------
    def call_with_deadline(self, sig: str, fn: Callable[[], Optional[dict]]) -> DeadlineResult:
        """Run fn() (expected: a blocking _get_tx-shaped call keyed by sig) under
        this guard: circuit breaker check -> capacity check -> in-flight dedup
        -> submit -> wait up to deadline_s -> return an explicit DeadlineResult.

        fn takes no arguments; sig is used only for dedup/late-result keying
        (the caller is responsible for fn actually fetching `sig`).
        """
        if self._executor is None:
            # Guard has been shut down (e.g. mid-restart) — fail closed, safe default.
            self._metric("rpc_error")
            return DeadlineResult(outcome=Outcome.RPC_ERROR, wall_ms=0.0)

        # Late-result reuse check FIRST — if an earlier abandoned request for
        # this exact signature already completed, use it and skip everything
        # else (design requirement 6: avoid paying the RPC cost twice).
        cached = self._registry.take_late_result(sig)
        if cached is not None:
            _found, value = cached
            self._metric("late_result_reused")
            return DeadlineResult(outcome=Outcome.SUCCESS if value else Outcome.NOT_FOUND,
                                   value=value, wall_ms=0.0, reused_late_result=True)

        if not self._breaker.allow_request():
            self._metric("circuit_open_rejected")
            return DeadlineResult(outcome=Outcome.CIRCUIT_OPEN_REJECTED, wall_ms=0.0)

        _t0 = time.time()

        def _reserve_and_submit() -> Optional[concurrent.futures.Future]:
            # Runs INSIDE the registry's lock (see get_or_register) — atomic
            # with respect to every other caller, so the capacity check and
            # the actual submit can never race against a concurrent duplicate
            # or a concurrent capacity check for a different signature.
            with self._count_lock:
                if self._inflight_count >= self._max_capacity:
                    return None
                self._inflight_count += 1
            f = self._executor.submit(fn)
            f.add_done_callback(lambda fut: self._registry._on_physical_request_done(sig, fut))
            return f

        fut, is_new = self._registry.get_or_register(sig, _reserve_and_submit)
        if fut is None:
            # is_new is True here (no existing in-flight future was found), but
            # _reserve_and_submit declined due to capacity — a real rejection,
            # not a dedup. Nothing was submitted, so no reservation to release.
            self._metric("capacity_rejected")
            self.production_metrics.inc("capacity_rejected")
            return DeadlineResult(outcome=Outcome.CAPACITY_REJECTED, wall_ms=0.0)
        if not is_new:
            self._metric("duplicate_suppressed")

        try:
            value = fut.result(timeout=self._deadline_s)
            wall_ms = round((time.time() - _t0) * 1000, 1)
            self._breaker.record_success()
            if value:
                self._metric("success")
                return DeadlineResult(outcome=Outcome.SUCCESS, value=value, wall_ms=wall_ms)
            self._metric("not_found")
            return DeadlineResult(outcome=Outcome.NOT_FOUND, value=None, wall_ms=wall_ms)
        except concurrent.futures.TimeoutError:
            wall_ms = round((time.time() - _t0) * 1000, 1)
            cancelled = fut.cancel()
            self._breaker.record_failure()
            self.production_metrics.inc("deadline_exceeded")
            if cancelled:
                self._metric("cancelled_before_start")
                self.production_metrics.inc("cancelled_before_start")
                _log(f"sig={sig[:16]}… cancelled before start (was still queued) wall_ms={wall_ms}")
                return DeadlineResult(outcome=Outcome.CANCELLED_BEFORE_START, wall_ms=wall_ms)
            self._metric("deadline_exceeded_running")
            self.production_metrics.inc("abandoned_running")
            _log(f"sig={sig[:16]}… deadline exceeded while RUNNING (abandoned, "
                 f"bounded by pool_size={self._pool_size}) wall_ms={wall_ms}")
            return DeadlineResult(outcome=Outcome.DEADLINE_EXCEEDED_RUNNING, wall_ms=wall_ms)
        except Exception as exc:
            wall_ms = round((time.time() - _t0) * 1000, 1)
            self._breaker.record_failure()
            self._metric("rpc_error")
            _log(f"sig={sig[:16]}… RPC_ERROR: {exc!r} wall_ms={wall_ms}")
            return DeadlineResult(outcome=Outcome.RPC_ERROR, wall_ms=wall_ms)
