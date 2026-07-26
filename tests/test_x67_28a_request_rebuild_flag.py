"""X67.28A -- Emergency performance stabilisation: WATCHTOWER_DISABLE_
REQUEST_REBUILDS. Verifies the flag suppresses request-driven in-worker
background rebuilds without touching hydration, cold-start, or normal
cache-read behaviour.
"""
import time

import pytest

import src.ops.swr_cache as swr_cache
from src.ops.swr_cache import SWRCache


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("WATCHTOWER_DISABLE_REQUEST_REBUILDS", raising=False)


def test_flag_defaults_to_disabled_rebuilds_enabled():
    assert swr_cache.request_driven_rebuilds_disabled() is False


def test_flag_enabled_via_env_var(monkeypatch):
    monkeypatch.setenv("WATCHTOWER_DISABLE_REQUEST_REBUILDS", "1")
    assert swr_cache.request_driven_rebuilds_disabled() is True


def test_flag_accepts_true_and_yes(monkeypatch):
    for value in ("true", "yes", "TRUE", "Yes"):
        monkeypatch.setenv("WATCHTOWER_DISABLE_REQUEST_REBUILDS", value)
        assert swr_cache.request_driven_rebuilds_disabled() is True


def test_flag_off_stale_key_still_triggers_background_refresh(monkeypatch):
    """Default (flag unset): exact prior behaviour -- a stale key starts
    exactly one background refresh."""
    monkeypatch.delenv("WATCHTOWER_DISABLE_REQUEST_REBUILDS", raising=False)
    calls = []
    cache = SWRCache(ttl_seconds=0.01)  # near-instant staleness
    cache.get("k", lambda: (calls.append(1), "v1")[1])
    time.sleep(0.02)
    cache.get("k", lambda: (calls.append(2), "v2")[1])
    time.sleep(0.05)  # let the background refresh thread finish
    assert cache.metrics["refreshes_started"] == 1


def test_flag_on_stale_key_never_triggers_background_refresh(monkeypatch):
    """The core X67.28A behaviour: with the flag set, a stale key is still
    served immediately (same value, same non-blocking guarantee), but NO
    new background rebuild is started."""
    monkeypatch.setenv("WATCHTOWER_DISABLE_REQUEST_REBUILDS", "1")
    calls = []
    cache = SWRCache(ttl_seconds=0.01)
    cache.get("k", lambda: (calls.append(1), "v1")[1])
    time.sleep(0.02)
    value, meta = cache.get("k", lambda: (calls.append(2), "v2")[1])
    time.sleep(0.05)
    assert value == "v1"  # still serves the previous value
    assert meta["state"] in ("stale", "refreshing")
    assert cache.metrics["refreshes_started"] == 0
    assert calls == [1]  # compute() never called a second time


def test_flag_on_does_not_affect_true_cold_start():
    """A never-populated key must STILL compute synchronously even with
    the flag on -- disabling that would mean serving nothing at all for a
    genuinely new key, a worse outage than the one this flag fixes."""
    import os
    os.environ["WATCHTOWER_DISABLE_REQUEST_REBUILDS"] = "1"
    try:
        cache = SWRCache(ttl_seconds=60)
        value, meta = cache.get("brand_new_key", lambda: "computed")
        assert value == "computed"
        assert meta["state"] == "fresh"
        assert cache.metrics["cold_computes"] == 1
    finally:
        del os.environ["WATCHTOWER_DISABLE_REQUEST_REBUILDS"]


def test_flag_on_hydrated_stale_entry_served_without_rebuild(monkeypatch):
    """The exact production scenario: a hydrated (from-disk) stale entry
    must be served as-is, with no rebuild triggered, while the flag is on."""
    monkeypatch.setenv("WATCHTOWER_DISABLE_REQUEST_REBUILDS", "1")
    cache = SWRCache(ttl_seconds=60)
    old_computed_at = time.time() - 999999  # far in the past -> definitely stale
    assert cache.hydrate("k", "hydrated_value", old_computed_at) is True

    value, meta = cache.get("k", lambda: "should_never_be_called")
    assert value == "hydrated_value"
    assert meta["state"] in ("stale", "refreshing")
    assert cache.metrics["refreshes_started"] == 0


def test_flag_on_try_get_stale_key_also_suppressed(monkeypatch):
    """try_get() delegates to get() for any already-populated key -- the
    same suppression must apply there too."""
    monkeypatch.setenv("WATCHTOWER_DISABLE_REQUEST_REBUILDS", "1")
    cache = SWRCache(ttl_seconds=60)
    old_computed_at = time.time() - 999999
    cache.hydrate("k", "hydrated_value", old_computed_at)

    value, meta = cache.try_get("k", lambda: "should_never_be_called")
    assert value == "hydrated_value"
    assert cache.metrics["refreshes_started"] == 0


def test_flag_on_try_get_cold_build_still_works(monkeypatch):
    """A never-populated key via try_get() must still kick off its
    background cold build even with the flag on -- same exception as
    get()'s own cold-start path."""
    monkeypatch.setenv("WATCHTOWER_DISABLE_REQUEST_REBUILDS", "1")
    cache = SWRCache(ttl_seconds=60)
    value, meta = cache.try_get("brand_new_key", lambda: "computed")
    assert value is None
    assert meta["state"] == "warming"
    assert cache.metrics["cold_warming_started"] == 1
