"""X65.52 — Non-Blocking Discovery Cold Cache Handling: SWRCache.try_get().

try_get() is a NEW method, additive to swr_cache.py -- get()'s existing
behaviour (including its documented, unavoidable blocking on a true first
-ever cold key) is completely unchanged and still used by every existing
caller (prewarm, any script/test calling _get_operational_intelligence/
_get_pipeline_health directly). try_get() only changes what happens for a
Discovery-facing caller that explicitly opts in: instead of blocking for
the full cold-build duration, it kicks off the SAME compute() in the
background (single-flight per key, reusing the identical build-lock/cache
-population code path get() uses) and returns immediately with
state="warming". Once the background build lands in the cache, the very
next try_get()/get() call for that key is an ordinary FRESH hit -- no
second source of truth, no separate warm/cold code paths after the first
population.
"""
from __future__ import annotations

import threading
import time

from src.ops.swr_cache import SWRCache, FRESH, STALE, REFRESHING, WARMING


def test_try_get_on_a_cold_key_never_blocks():
    cache = SWRCache(ttl_seconds=60)
    started = threading.Event()
    release = threading.Event()

    def slow_compute():
        started.set()
        release.wait(timeout=5)
        return "V1"

    start = time.perf_counter()
    value, meta = cache.try_get("k", slow_compute)
    elapsed = time.perf_counter() - start

    assert value is None
    assert meta["state"] == WARMING
    assert elapsed < 0.5  # never waited for slow_compute at all
    assert started.wait(timeout=2)  # background build did start
    release.set()  # let it finish so the thread doesn't leak past the test


def test_try_get_kicks_off_exactly_one_background_build_per_key():
    cache = SWRCache(ttl_seconds=60)
    calls = []
    lock = threading.Lock()
    release = threading.Event()

    def slow_compute():
        with lock:
            calls.append(1)
        release.wait(timeout=5)
        return "V1"

    # Multiple concurrent try_get() calls for the SAME cold key must only
    # start ONE background build (single-flight), not one per caller.
    threads = [threading.Thread(target=lambda: cache.try_get("k", slow_compute)) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2)
    release.set()
    time.sleep(0.1)
    assert len(calls) == 1


def test_try_get_becomes_fresh_once_background_build_completes():
    cache = SWRCache(ttl_seconds=60)
    release = threading.Event()

    def slow_compute():
        release.wait(timeout=5)
        return "V1"

    value, meta = cache.try_get("k", slow_compute)
    assert meta["state"] == WARMING

    release.set()
    # Poll until the background build lands -- bounded, not a real sleep loop.
    deadline = time.time() + 2
    while time.time() < deadline:
        value, meta = cache.try_get("k", slow_compute)
        if meta["state"] == FRESH:
            break
        time.sleep(0.01)
    assert meta["state"] == FRESH
    assert value == "V1"


def test_try_get_delegates_to_get_for_already_warm_states():
    cache = SWRCache(ttl_seconds=60)
    calls = []

    def compute():
        calls.append(1)
        return f"V{len(calls)}"

    # First population via get() (the normal, unavoidable-blocking path
    # for a genuinely first-ever key -- unaffected by this task).
    value, meta = cache.get("k", compute)
    assert meta["state"] == FRESH
    assert value == "V1"

    # A subsequent try_get() on the now-warm key must behave EXACTLY like
    # get() would -- same FRESH hit, no re-compute.
    value2, meta2 = cache.try_get("k", compute)
    assert meta2["state"] == FRESH
    assert value2 == "V1"
    assert len(calls) == 1  # not recomputed


def test_try_get_on_a_stale_key_still_serves_previous_value_immediately():
    cache = SWRCache(ttl_seconds=0.05)
    calls = []

    def compute():
        calls.append(1)
        return f"V{len(calls)}"

    cache.get("k", compute)  # first population
    time.sleep(0.1)  # let it go stale

    value, meta = cache.try_get("k", compute)
    assert meta["state"] in (STALE, REFRESHING)
    assert value == "V1"  # the STALE value, served immediately, never None


def test_try_get_failure_releases_the_cold_building_flag_for_retry():
    cache = SWRCache(ttl_seconds=60)
    attempt = {"n": 0}
    release = threading.Event()

    def flaky_compute():
        attempt["n"] += 1
        if attempt["n"] == 1:
            release.wait(timeout=2)
            raise RuntimeError("simulated failure")
        return "V-success"

    value, meta = cache.try_get("k", flaky_compute)
    assert meta["state"] == WARMING
    release.set()

    # Wait for the failed background build to release the cold-building
    # flag, then retry -- must be able to start a NEW background build,
    # never permanently stuck WARMING after a failure.
    deadline = time.time() + 2
    while time.time() < deadline and attempt["n"] < 1:
        time.sleep(0.01)
    time.sleep(0.05)

    value2, meta2 = cache.try_get("k", flaky_compute)
    assert meta2["state"] == WARMING  # kicks off a fresh attempt, not stuck

    deadline = time.time() + 2
    while time.time() < deadline:
        value3, meta3 = cache.try_get("k", flaky_compute)
        if meta3["state"] == FRESH:
            break
        time.sleep(0.01)
    assert meta3["state"] == FRESH
    assert value3 == "V-success"


def test_try_get_never_introduces_a_second_source_of_truth():
    # Once warmed via try_get()'s background path, an ordinary get() call
    # for the same key must see the identical entry -- no separate
    # storage, no divergence between the two access patterns.
    cache = SWRCache(ttl_seconds=60)
    release = threading.Event()

    def compute():
        release.wait(timeout=2)
        return "V1"

    cache.try_get("k", compute)
    release.set()
    deadline = time.time() + 2
    while time.time() < deadline:
        _, meta = cache.try_get("k", compute)
        if meta["state"] == FRESH:
            break
        time.sleep(0.01)

    value, meta = cache.get("k", compute)
    assert meta["state"] == FRESH
    assert value == "V1"


def test_metrics_track_cold_warming_and_suppression():
    cache = SWRCache(ttl_seconds=60)
    release = threading.Event()

    def compute():
        release.wait(timeout=2)
        return "V1"

    cache.try_get("k", compute)
    cache.try_get("k", compute)  # second call while still building -- suppressed
    release.set()

    assert cache.metrics["cold_warming_started"] == 1
    assert cache.metrics["cold_warming_suppressed"] == 1
    assert cache.metrics["warming_serves"] == 2
