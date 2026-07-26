"""X65.57 — Persisted Discovery Intelligence Snapshots.

Tests the two additive extensions to SWRCache added for this task:

  - on_success(key, value, build_duration_ms): fired ONLY after a build
    succeeds (cold-start compute(), a background _refresh(), or a
    background _cold_build()); never on failure, never for an
    already-fresh hit. Used by operation_dashboard_routes.py to persist a
    snapshot after every successful build.

  - hydrate(key, value, computed_at): seeds a key directly from a
    persisted snapshot without calling compute(), using the SNAPSHOT's own
    computed_at (so staleness is measured correctly), and never overwrites
    a key a real build already populated.

A cache constructed with no on_success callback (every existing caller)
must behave EXACTLY as before this change.
"""
from __future__ import annotations

import threading
import time

import pytest

from src.ops.swr_cache import SWRCache, FRESH, STALE, REFRESHING, WARMING


def _sync_executor(fn):
    fn()


# ─────────────────────────── on_success callback ───────────────────────────

def test_on_success_fires_after_cold_start_compute():
    calls = []
    cache = SWRCache(ttl_seconds=60, on_success=lambda k, v, ms: calls.append((k, v, ms)))
    cache.get("k1", lambda: "value1")
    assert len(calls) == 1
    assert calls[0][0] == "k1"
    assert calls[0][1] == "value1"
    assert calls[0][2] >= 0.0


def test_on_success_fires_after_successful_background_refresh():
    calls = []
    cache = SWRCache(ttl_seconds=0, executor=_sync_executor,
                      on_success=lambda k, v, ms: calls.append((k, v)))
    cache.get("k1", lambda: "v1")  # cold start -- fires once
    cache.get("k1", lambda: "v2")  # stale -- synchronous refresh via _sync_executor
    assert calls == [("k1", "v1"), ("k1", "v2")]


def test_on_success_does_not_fire_on_failed_refresh():
    calls = []
    cache = SWRCache(ttl_seconds=0, executor=_sync_executor,
                      on_success=lambda k, v, ms: calls.append((k, v)))
    cache.get("k1", lambda: "v1")

    def boom():
        raise RuntimeError("build failed")
    cache.get("k1", boom)  # refresh fails
    assert calls == [("k1", "v1")]  # only the cold-start success, not the failed refresh


def test_on_success_does_not_fire_for_an_already_fresh_hit():
    calls = []
    cache = SWRCache(ttl_seconds=60, on_success=lambda k, v, ms: calls.append((k, v)))
    cache.get("k1", lambda: "v1")
    cache.get("k1", lambda: "should_not_be_called")  # still FRESH, no compute() at all
    assert calls == [("k1", "v1")]


def test_on_success_fires_after_successful_cold_background_build_via_try_get():
    calls = []
    cache = SWRCache(ttl_seconds=60, executor=_sync_executor,
                      on_success=lambda k, v, ms: calls.append((k, v)))
    value, meta = cache.try_get("k1", lambda: "v1")
    assert meta["state"] == WARMING
    assert value is None
    # _sync_executor ran the cold build inline before try_get() returned.
    assert calls == [("k1", "v1")]


def test_on_success_does_not_fire_when_cold_background_build_fails():
    calls = []

    def boom():
        raise RuntimeError("cold build failed")

    cache = SWRCache(ttl_seconds=60, executor=_sync_executor,
                      on_success=lambda k, v, ms: calls.append((k, v)))
    cache.try_get("k1", boom)
    assert calls == []


def test_a_broken_on_success_callback_never_breaks_the_cache():
    def bad_callback(k, v, ms):
        raise RuntimeError("disk full or whatever")

    cache = SWRCache(ttl_seconds=60, on_success=bad_callback)
    value, meta = cache.get("k1", lambda: "v1")
    assert value == "v1"
    assert meta["state"] == FRESH
    # A second call is a normal fresh hit -- proves the cache's own state
    # was populated correctly despite the callback raising.
    value2, meta2 = cache.get("k1", lambda: "should_not_be_called")
    assert value2 == "v1"


def test_default_cache_with_no_callback_behaves_exactly_as_before():
    cache = SWRCache(ttl_seconds=60)
    value, meta = cache.get("k1", lambda: "v1")
    assert value == "v1"
    assert meta["state"] == FRESH


# ─────────────────────────────── hydrate() ───────────────────────────────

def test_hydrate_seeds_an_empty_key():
    cache = SWRCache(ttl_seconds=300)
    computed_at = time.time() - 10
    seeded = cache.hydrate("k1", "hydrated_value", computed_at)
    assert seeded is True
    assert cache.state_of("k1") == FRESH  # 10s old, well within 300s TTL


def test_hydrate_preserves_the_snapshots_own_age_not_now():
    cache = SWRCache(ttl_seconds=60)
    old_computed_at = time.time() - 120  # older than the 60s TTL
    cache.hydrate("k1", "stale_snapshot_value", old_computed_at)
    assert cache.state_of("k1") == STALE


def test_hydrated_value_is_served_immediately_without_calling_compute():
    cache = SWRCache(ttl_seconds=300)
    cache.hydrate("k1", "hydrated_value", time.time())
    value, meta = cache.get("k1", lambda: (_ for _ in ()).throw(AssertionError("compute() should not run")))
    assert value == "hydrated_value"
    assert meta["state"] == FRESH


def test_hydrate_never_overwrites_an_existing_entry():
    cache = SWRCache(ttl_seconds=300)
    cache.get("k1", lambda: "real_build_value")
    seeded = cache.hydrate("k1", "snapshot_value", time.time())
    assert seeded is False
    value, _ = cache.get("k1", lambda: "should_not_be_called")
    assert value == "real_build_value"


def test_stale_hydrated_entry_triggers_exactly_one_background_refresh():
    cache = SWRCache(ttl_seconds=60, executor=_sync_executor)
    cache.hydrate("k1", "stale_snapshot_value", time.time() - 120)
    value, meta = cache.get("k1", lambda: "refreshed_value")
    assert value == "stale_snapshot_value"  # served immediately, refresh already ran via sync executor
    assert meta["state"] == STALE
    # Next call sees the refreshed value.
    value2, meta2 = cache.get("k1", lambda: "should_not_be_called")
    assert value2 == "refreshed_value"
    assert meta2["state"] == FRESH


def test_hydrate_then_on_success_persists_the_next_real_refresh():
    calls = []
    cache = SWRCache(ttl_seconds=60, executor=_sync_executor,
                      on_success=lambda k, v, ms: calls.append(v))
    cache.hydrate("k1", "snapshot_value", time.time() - 120)  # hydrate does NOT fire on_success
    assert calls == []
    cache.get("k1", lambda: "fresh_build_value")  # stale -> background refresh -> succeeds
    assert calls == ["fresh_build_value"]
