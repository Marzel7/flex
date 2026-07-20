"""X29.1.2 — Eliminate Cold-Start Latency for Operational Intelligence.

Tests src/ops/swr_cache.py's stale-while-revalidate + single-flight
behaviour directly (no Flask, no real Operational Intelligence classifiers
-- those are explicitly untouched by this sprint). Uses controllable fake
`compute()` functions (with a threading.Event to simulate a slow refresh)
so concurrency behaviour can be asserted deterministically rather than
relying on real timing.
"""
from __future__ import annotations

import threading
import time

import pytest

from src.ops.swr_cache import SWRCache, FRESH, STALE, REFRESHING


def _sync_executor(fn):
    """Runs the refresh inline (same thread) -- used where a test wants
    the refresh to complete before the get() call returns, to assert
    post-refresh state deterministically without sleeping."""
    fn()


# ─────────────────────── Cold start (unavoidable, first population only) ───────────────────────

def test_first_ever_get_computes_synchronously_and_returns_fresh():
    cache = SWRCache(ttl_seconds=60)
    calls = []
    def compute():
        calls.append(1)
        return "V1"
    value, meta = cache.get("k", compute)
    assert value == "V1"
    assert meta["state"] == FRESH
    assert len(calls) == 1
    assert cache.metrics["cold_computes"] == 1


def test_within_ttl_is_a_cache_hit_and_never_recomputes():
    cache = SWRCache(ttl_seconds=60)
    calls = []
    def compute():
        calls.append(1)
        return f"V{len(calls)}"
    v1, m1 = cache.get("k", compute)
    v2, m2 = cache.get("k", compute)
    assert v1 == v2 == "V1"
    assert len(calls) == 1  # never recomputed
    assert m2["state"] == FRESH
    assert cache.metrics["cache_hits"] == 1  # only the 2nd call counts as a hit (1st was cold)


# ─────────────────────── Stale-while-revalidate: never blocks after first population ───────────────────────

def test_stale_request_returns_previous_value_immediately_not_blocking():
    """Core requirement: 'First request after expiry returns immediately
    using the previous cached result.' Uses a compute() that would block
    forever if awaited synchronously, proving the stale path never waits
    on it."""
    cache = SWRCache(ttl_seconds=0.01)  # expires almost immediately
    block_forever = threading.Event()  # never set -> compute() blocks indefinitely if awaited
    def fast_compute():
        return "FIRST"
    def slow_compute():
        block_forever.wait(timeout=10)  # would hang the calling thread if not backgrounded
        return "SECOND"

    value, meta = cache.get("k", fast_compute)
    assert value == "FIRST"
    time.sleep(0.02)  # let TTL expire

    t0 = time.time()
    value2, meta2 = cache.get("k", slow_compute)
    elapsed = time.time() - t0

    assert value2 == "FIRST"  # still serves the OLD value, not the (unfinished) new one
    assert meta2["state"] in (STALE, REFRESHING)
    assert elapsed < 1.0  # did NOT block on slow_compute (which needs up to 10s)
    block_forever.set()  # release the background thread so it doesn't leak past the test


def test_successful_refresh_atomically_replaces_the_cached_value():
    cache = SWRCache(ttl_seconds=0.01, executor=_sync_executor)
    cache.get("k", lambda: "FIRST")
    time.sleep(0.02)
    value, meta = cache.get("k", lambda: "SECOND")
    assert value == "FIRST"  # the call that TRIGGERED the refresh still gets the stale value
    # but the refresh (run synchronously via _sync_executor) has already completed by the
    # time get() returns, so the NEXT call sees the new value:
    value2, meta2 = cache.get("k", lambda: "THIRD")
    assert value2 == "SECOND"
    assert meta2["state"] == FRESH  # freshly swapped in, timer restarted


def test_failed_refresh_keeps_previous_value_available():
    """'If refresh fails: keep previous cache, log failure, retry on next
    stale request. The previous valid hierarchy should remain available.'"""
    cache = SWRCache(ttl_seconds=0.01, executor=_sync_executor)
    cache.get("k", lambda: "GOOD")
    time.sleep(0.02)

    def failing_compute():
        raise RuntimeError("boom")

    value, meta = cache.get("k", failing_compute)
    assert value == "GOOD"  # never lost, even though the refresh raised
    assert cache.metrics["refreshes_failed"] == 1

    # Still stale (the failed refresh didn't update computed_at) -- a later
    # call should retry rather than being permanently stuck.
    time.sleep(0.02)
    value2, meta2 = cache.get("k", lambda: "RECOVERED")
    assert value2 == "GOOD"  # this call also gets the stale value (refresh runs in background)
    value3, meta3 = cache.get("k", lambda: "SHOULD_NOT_BE_CALLED_AGAIN")
    assert value3 == "RECOVERED"  # the retry succeeded and is now the fresh value


# ─────────────────────── Single-flight: exactly one refresh under concurrency ───────────────────────

def test_single_flight_exactly_one_refresh_under_concurrent_requests():
    """'10 analysts load the page simultaneously -> Exactly ONE refresh
    starts.' Uses real threads hitting the same stale key concurrently,
    with a controllable compute() that blocks until released, so we can
    assert only one is ever invoked while many requests are in flight."""
    cache = SWRCache(ttl_seconds=0.01)
    cache.get("k", lambda: "INITIAL")
    time.sleep(0.02)  # expire

    refresh_started = threading.Event()
    release_refresh = threading.Event()
    call_count = {"n": 0}
    count_lock = threading.Lock()

    def slow_compute():
        with count_lock:
            call_count["n"] += 1
        refresh_started.set()
        release_refresh.wait(timeout=5)
        return "REFRESHED"

    results = []
    def worker():
        results.append(cache.get("k", slow_compute))

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    assert refresh_started.wait(timeout=2)
    # give the other 9 threads a moment to also reach get() while the one
    # refresh is still in flight
    time.sleep(0.1)
    release_refresh.set()
    for t in threads:
        t.join(timeout=5)

    with count_lock:
        assert call_count["n"] == 1  # exactly one refresh, never duplicated
    assert all(r[0] == "INITIAL" for r in results)  # every one of the 10 got the stale value
    assert cache.metrics["refreshes_started"] == 1
    assert cache.metrics["refreshes_suppressed"] >= 1  # at least one of the other 9 hit the suppressed path


def test_state_of_reports_fresh_stale_refreshing_correctly():
    cache = SWRCache(ttl_seconds=0.05)
    assert cache.state_of("k") is None  # never populated
    cache.get("k", lambda: "V")
    assert cache.state_of("k") == FRESH
    time.sleep(0.06)
    assert cache.state_of("k") == STALE

    release = threading.Event()
    def slow_compute():
        release.wait(timeout=5)
        return "V2"
    cache.get("k", slow_compute)  # triggers a background refresh
    time.sleep(0.02)
    assert cache.state_of("k") == REFRESHING
    release.set()


# ─────────────────────── Metrics ───────────────────────

def test_metrics_track_every_documented_counter():
    cache = SWRCache(ttl_seconds=0.01, executor=_sync_executor)
    cache.get("k", lambda: "A")          # cold_computes=1
    cache.get("k", lambda: "B")          # cache_hits=1
    time.sleep(0.02)
    cache.get("k", lambda: "C")          # stale_serves=1, refreshes_started=1, refreshes_succeeded=1 (sync)
    assert cache.metrics["cold_computes"] == 1
    assert cache.metrics["cache_hits"] == 1
    assert cache.metrics["stale_serves"] == 1
    assert cache.metrics["refreshes_started"] == 1
    assert cache.metrics["refreshes_succeeded"] == 1
    assert cache.metrics["refreshes_failed"] == 0


def test_independent_keys_do_not_interfere():
    """Two different windows (e.g. 24h vs 7d) must have fully independent
    cache lifecycles -- refreshing one must not affect the other."""
    cache = SWRCache(ttl_seconds=60)
    cache.get("24h", lambda: "DAY")
    cache.get("7d", lambda: "WEEK")
    assert cache.state_of("24h") == FRESH
    assert cache.state_of("7d") == FRESH
    v, _ = cache.get("24h", lambda: "SHOULD_NOT_BE_CALLED")
    assert v == "DAY"
