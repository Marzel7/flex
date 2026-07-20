"""X24.3 — Bounded RPC tail-latency protection tests.

Covers src/core/rpc_deadline.py (the dedicated deadline-enforcing executor,
capacity bound, circuit breaker, in-flight dedup, and late-result reuse) plus
the wiring into ws_cascade.py's _get_tx()/_get_tx_with_outcome() chokepoint.

Context: urllib.request.urlopen(timeout=N) bounds each individual blocking
socket operation, not the logical HTTP request as a whole (proven in the
X24.2.3 evidence report — observed ~23.6s calls against a configured 12s
budget). Python cannot interrupt a thread blocked in a C-level socket syscall,
so a TRUE cumulative deadline requires running the blocking call on a
dedicated thread and giving up waiting on it once the deadline elapses, while
strictly bounding how many abandoned calls can exist concurrently.
"""
from __future__ import annotations

import threading
import time

import pytest

from src.core.rpc_deadline import RpcDeadlineGuard, Outcome


def _make_guard(**overrides):
    defaults = dict(pool_size=2, max_capacity=3, deadline_s=0.5,
                     breaker_failure_threshold=3, breaker_window_s=10,
                     breaker_open_s=1, breaker_half_open_max_probes=1)
    defaults.update(overrides)
    return RpcDeadlineGuard(**defaults)


@pytest.fixture
def guard():
    g = _make_guard()
    yield g
    g.shutdown(wait=False)


def test_successful_request_under_budget(guard):
    r = guard.call_with_deadline("sig1", lambda: {"ok": True})
    assert r.outcome == Outcome.SUCCESS
    assert r.value == {"ok": True}
    assert r.wall_ms < 500


def test_request_exceeding_deadline_is_abandoned_not_blocking_caller(guard):
    def slow():
        time.sleep(2)
        return {"ok": True}
    t0 = time.time()
    r = guard.call_with_deadline("sig2", slow)
    elapsed = time.time() - t0
    assert r.outcome == Outcome.DEADLINE_EXCEEDED_RUNNING
    # the caller must return at (roughly) the configured deadline, not the
    # full 2s the physical call actually takes -- this IS the cumulative
    # deadline guarantee the whole sprint exists to prove.
    assert elapsed < 1.0


def test_late_result_is_reused_not_refetched(guard):
    calls = {"n": 0}
    def slow_then_succeeds():
        calls["n"] += 1
        time.sleep(1.0)
        return {"ok": True, "call": calls["n"]}
    r1 = guard.call_with_deadline("sig3", slow_then_succeeds)
    assert r1.outcome == Outcome.DEADLINE_EXCEEDED_RUNNING
    # give the abandoned physical request time to actually finish
    time.sleep(1.2)
    r2 = guard.call_with_deadline("sig3", slow_then_succeeds)
    assert r2.reused_late_result is True
    assert r2.outcome == Outcome.SUCCESS
    # the RPC cost must not have been paid twice
    assert calls["n"] == 1


def test_queued_request_cancelled_before_start():
    # pool_size=1 so the second submission is genuinely queued, not running.
    g = _make_guard(pool_size=1, max_capacity=2, deadline_s=0.3)
    try:
        results = {}
        def occupy():
            time.sleep(1.5)
            return {"ok": True}
        def worker(key, sig, fn):
            results[key] = g.call_with_deadline(sig, fn)
        t1 = threading.Thread(target=worker, args=("first", "sigA", occupy))
        t1.start()
        time.sleep(0.05)  # let sigA actually start running on the sole worker
        t2 = threading.Thread(target=worker, args=("second", "sigB", occupy))
        t2.start()
        t1.join()
        t2.join()
        # sigB never got a worker thread within its deadline -> queued, cancellable
        assert results["second"].outcome == Outcome.CANCELLED_BEFORE_START
    finally:
        g.shutdown(wait=False)


def test_capacity_rejection_when_pool_saturated():
    g = _make_guard(pool_size=1, max_capacity=2, deadline_s=5)
    try:
        def slow(sig):
            time.sleep(2)
            return {"ok": True, "sig": sig}
        results = {}
        def worker(sig):
            results[sig] = g.call_with_deadline(sig, lambda: slow(sig))
        threads = [threading.Thread(target=worker, args=(f"sig{i}",)) for i in range(4)]
        for t in threads:
            t.start()
            time.sleep(0.05)
        for t in threads:
            t.join()
        outcomes = [r.outcome for r in results.values()]
        assert Outcome.CAPACITY_REJECTED in outcomes
        # capacity is bounded -- never more than max_capacity=2 physical/queued slots
        rejected = sum(1 for o in outcomes if o == Outcome.CAPACITY_REJECTED)
        assert rejected >= 1
    finally:
        g.shutdown(wait=False)


def test_circuit_breaker_opens_after_threshold_failures():
    g = _make_guard(pool_size=2, max_capacity=4, deadline_s=0.2,
                     breaker_failure_threshold=2, breaker_window_s=30,
                     breaker_open_s=0.5, breaker_half_open_max_probes=1)
    try:
        def always_slow():
            time.sleep(2)
            return {"ok": True}
        assert g.breaker_state() == "CLOSED"
        r1 = g.call_with_deadline("b1", always_slow)
        r2 = g.call_with_deadline("b2", always_slow)
        assert r1.outcome == Outcome.DEADLINE_EXCEEDED_RUNNING
        assert r2.outcome == Outcome.DEADLINE_EXCEEDED_RUNNING
        assert g.breaker_state() == "OPEN"
        # while OPEN, a request must be rejected WITHOUT ever touching the executor
        r3 = g.call_with_deadline("b3", always_slow)
        assert r3.outcome == Outcome.CIRCUIT_OPEN_REJECTED
        assert r3.wall_ms == 0.0
    finally:
        g.shutdown(wait=False)


def test_circuit_breaker_half_open_recovers_on_success():
    g = _make_guard(pool_size=2, max_capacity=4, deadline_s=0.2,
                     breaker_failure_threshold=1, breaker_window_s=30,
                     breaker_open_s=0.3, breaker_half_open_max_probes=1)
    try:
        def always_slow():
            time.sleep(2)
            return {"ok": True}
        r1 = g.call_with_deadline("c1", always_slow)
        assert r1.outcome == Outcome.DEADLINE_EXCEEDED_RUNNING
        assert g.breaker_state() == "OPEN"
        time.sleep(0.4)  # let the OPEN window elapse
        assert g.breaker_state() == "HALF_OPEN"
        # a fast success as the HALF_OPEN probe must close the breaker
        r2 = g.call_with_deadline("c2", lambda: {"ok": True})
        assert r2.outcome == Outcome.SUCCESS
        assert g.breaker_state() == "CLOSED"
    finally:
        g.shutdown(wait=False)


def test_circuit_breaker_half_open_reopens_on_failure():
    g = _make_guard(pool_size=2, max_capacity=4, deadline_s=0.2,
                     breaker_failure_threshold=1, breaker_window_s=30,
                     breaker_open_s=0.3, breaker_half_open_max_probes=1)
    try:
        def always_slow():
            time.sleep(2)
            return {"ok": True}
        g.call_with_deadline("d1", always_slow)
        assert g.breaker_state() == "OPEN"
        time.sleep(0.4)
        assert g.breaker_state() == "HALF_OPEN"
        g.call_with_deadline("d2", always_slow)  # probe fails too
        assert g.breaker_state() == "OPEN"
    finally:
        g.shutdown(wait=False)


def test_duplicate_concurrent_requests_share_one_physical_call():
    g = _make_guard(pool_size=2, max_capacity=4, deadline_s=2)
    try:
        call_count = {"n": 0}
        lock = threading.Lock()
        def counted():
            with lock:
                call_count["n"] += 1
            time.sleep(0.3)
            return {"ok": True}
        results = []
        def worker():
            results.append(g.call_with_deadline("dup_sig", counted))
        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
            time.sleep(0.02)
        for t in threads:
            t.join()
        assert all(r.outcome == Outcome.SUCCESS for r in results)
        # at most one PHYSICAL request for the same signature (design requirement 5)
        assert call_count["n"] == 1
        assert sum(1 for r in results if r.reused_late_result is False) == 1 or \
               g.metrics["duplicate_suppressed"] >= 1
    finally:
        g.shutdown(wait=False)


def test_rpc_error_outcome_on_exception():
    g = _make_guard(pool_size=2, max_capacity=4, deadline_s=1)
    try:
        def raises():
            raise ValueError("boom")
        r = g.call_with_deadline("err_sig", raises)
        assert r.outcome == Outcome.RPC_ERROR
    finally:
        g.shutdown(wait=False)


def test_not_found_outcome_on_none_result():
    g = _make_guard(pool_size=2, max_capacity=4, deadline_s=1)
    try:
        r = g.call_with_deadline("none_sig", lambda: None)
        assert r.outcome == Outcome.NOT_FOUND
        assert r.value is None
    finally:
        g.shutdown(wait=False)


def test_executor_shutdown_rejects_further_calls_safely():
    g = _make_guard(pool_size=2, max_capacity=4, deadline_s=1)
    g.shutdown(wait=False)
    assert g.is_running() is False
    r = g.call_with_deadline("after_shutdown", lambda: {"ok": True})
    assert r.outcome == Outcome.RPC_ERROR  # fail-closed, no crash


def test_executor_restart_creates_a_working_pool_again():
    g = _make_guard(pool_size=2, max_capacity=4, deadline_s=1)
    try:
        g.shutdown(wait=False)
        assert g.is_running() is False
        g.restart()
        assert g.is_running() is True
        r = g.call_with_deadline("post_restart", lambda: {"ok": True})
        assert r.outcome == Outcome.SUCCESS
    finally:
        g.shutdown(wait=False)


def test_capacity_counter_returns_to_zero_after_calls_complete(guard):
    for i in range(5):
        guard.call_with_deadline(f"sig_seq_{i}", lambda: {"ok": True})
    time.sleep(0.1)
    assert guard.current_capacity_used() == 0


# ── ws_cascade.py wiring: unchanged retry semantics / detection behaviour ────

def test_get_tx_contract_unchanged_for_existing_call_sites(monkeypatch):
    """Every pre-existing call site of _get_tx() (treasury tx, CDC tx, sibling
    classification, launch audit) expects a plain dict-or-None return. The
    X24.3 wiring must preserve that contract exactly -- only the fast-retry
    path sees the richer DeadlineResult via _get_tx_with_outcome()."""
    import os
    os.environ.setdefault("HELIUS_API_KEY", "test-key")
    from src.core import ws_cascade as wc

    monkeypatch.setattr(wc, "_get_tx_raw", lambda sig: {"result": sig})
    # Force a fresh guard for isolation from any other test's singleton state.
    wc._get_tx_guard_instance = None
    try:
        result = wc._get_tx("some_signature")
        assert result == {"result": "some_signature"}
    finally:
        if wc._get_tx_guard_instance is not None:
            wc._get_tx_guard_instance.shutdown(wait=False)
        wc._get_tx_guard_instance = None


def test_get_tx_contract_returns_none_on_deadline_exceeded(monkeypatch):
    import os
    os.environ.setdefault("HELIUS_API_KEY", "test-key")
    from src.core import ws_cascade as wc
    from src.core.rpc_deadline import RpcDeadlineGuard

    def slow_raw(sig):
        time.sleep(1)
        return {"result": sig}
    monkeypatch.setattr(wc, "_get_tx_raw", slow_raw)
    wc._get_tx_guard_instance = RpcDeadlineGuard(pool_size=2, max_capacity=4, deadline_s=0.2)
    try:
        result = wc._get_tx("slow_sig")
        assert result is None  # collapsed to None, matching the pre-X24.3 contract
    finally:
        wc._get_tx_guard_instance.shutdown(wait=False)
        wc._get_tx_guard_instance = None


def test_get_tx_with_outcome_exposes_explicit_outcome(monkeypatch):
    import os
    os.environ.setdefault("HELIUS_API_KEY", "test-key")
    from src.core import ws_cascade as wc
    from src.core.rpc_deadline import RpcDeadlineGuard, Outcome

    def slow_raw(sig):
        time.sleep(1)
        return {"result": sig}
    monkeypatch.setattr(wc, "_get_tx_raw", slow_raw)
    wc._get_tx_guard_instance = RpcDeadlineGuard(pool_size=2, max_capacity=4, deadline_s=0.2)
    try:
        result = wc._get_tx_with_outcome("slow_sig2")
        assert result.outcome == Outcome.DEADLINE_EXCEEDED_RUNNING
    finally:
        wc._get_tx_guard_instance.shutdown(wait=False)
        wc._get_tx_guard_instance = None


# ── X24.3 final hardening: bounded LRU cache + production metrics ───────────

def test_late_result_cache_is_bounded_by_max_entries():
    """A burst of many distinct abandoned signatures within one TTL window
    must not grow the cache without limit -- LRU eviction keeps it at
    max_entries, bounding memory as well as time."""
    from src.core.rpc_deadline import RpcDeadlineGuard, _InFlightRegistry

    g = _make_guard(pool_size=4, max_capacity=20, deadline_s=0.1)
    try:
        # Directly exercise the registry's bounded cache without waiting on
        # real thread timing for every one of many entries.
        registry = g._registry
        registry._max_entries = 5
        for i in range(10):
            fut_holder = {}
            def make_fn(i=i):
                return {"ok": True, "i": i}
            # Simulate a completed physical request directly via the callback
            # path (what _on_physical_request_done does), bypassing the
            # executor entirely for a fast, deterministic test.
            import concurrent.futures as cf
            fut = cf.Future()
            fut.set_result(make_fn())
            registry._on_physical_request_done(f"sig{i}", fut)
        assert len(registry._late_results) == 5
        # the OLDEST entries (sig0..sig4) must have been evicted, newest kept
        assert "sig9" in registry._late_results
        assert "sig0" not in registry._late_results
    finally:
        g.shutdown(wait=False)


def test_late_result_cache_lru_touch_protects_frequently_read_entries():
    """An entry that keeps being read (move_to_end on hit) must survive
    eviction longer than one nobody has asked about, even if the read one
    was inserted first -- proving this is LRU, not FIFO-by-insertion."""
    import concurrent.futures as cf
    g = _make_guard(pool_size=4, max_capacity=20, deadline_s=0.1)
    try:
        registry = g._registry
        registry._max_entries = 3

        def complete(sig, value):
            fut = cf.Future()
            fut.set_result(value)
            registry._on_physical_request_done(sig, fut)

        complete("keep_me", {"ok": True})   # inserted first
        complete("filler1", {"ok": True})
        complete("filler2", {"ok": True})
        # touch "keep_me" so it becomes most-recently-used
        assert registry.take_late_result("keep_me") is not None
        # now insert enough new entries to force eviction of the LEAST recently used
        complete("filler3", {"ok": True})
        complete("filler4", {"ok": True})
        assert "keep_me" in registry._late_results, "LRU-touched entry evicted too early"
    finally:
        g.shutdown(wait=False)


def test_production_metrics_deadline_exceeded_and_abandoned_running():
    g = _make_guard(pool_size=1, max_capacity=2, deadline_s=0.2)
    try:
        def slow():
            time.sleep(1)
            return {"ok": True}
        r = g.call_with_deadline("m1", slow)
        assert r.outcome == Outcome.DEADLINE_EXCEEDED_RUNNING
        snap = g.production_metrics_snapshot()
        assert snap["deadline_exceeded"] == 1
        assert snap["abandoned_running"] == 1
        assert snap["cancelled_before_start"] == 0
    finally:
        g.shutdown(wait=False)


def test_production_metrics_capacity_rejected():
    g = _make_guard(pool_size=1, max_capacity=1, deadline_s=5)
    try:
        def slow():
            time.sleep(1.5)
            return {"ok": True}
        results = []
        def worker(sig):
            results.append(g.call_with_deadline(sig, slow))
        t1 = threading.Thread(target=worker, args=("cap1",))
        t2 = threading.Thread(target=worker, args=("cap2",))
        t1.start(); time.sleep(0.05); t2.start()
        t1.join(); t2.join()
        snap = g.production_metrics_snapshot()
        assert snap["capacity_rejected"] >= 1
    finally:
        g.shutdown(wait=False)


def test_production_metrics_breaker_transitions():
    g = _make_guard(pool_size=2, max_capacity=4, deadline_s=0.2,
                     breaker_failure_threshold=1, breaker_window_s=30,
                     breaker_open_s=0.3, breaker_half_open_max_probes=1)
    try:
        def always_slow():
            time.sleep(2)
            return {"ok": True}
        g.call_with_deadline("bm1", always_slow)
        snap = g.production_metrics_snapshot()
        assert snap["breaker_open"] == 1
        time.sleep(0.4)
        assert g.breaker_state() == "HALF_OPEN"
        snap = g.production_metrics_snapshot()
        assert snap["breaker_half_open"] == 1
        g.call_with_deadline("bm2", lambda: {"ok": True})  # probe succeeds
        snap = g.production_metrics_snapshot()
        assert snap["breaker_closed"] == 1
    finally:
        g.shutdown(wait=False)


def test_production_metrics_cache_hit_miss_eviction():
    import concurrent.futures as cf
    g = _make_guard(pool_size=2, max_capacity=4, deadline_s=0.1)
    try:
        registry = g._registry
        registry._max_entries = 1

        def complete(sig):
            fut = cf.Future()
            fut.set_result({"ok": True})
            registry._on_physical_request_done(sig, fut)

        # miss: nothing cached yet
        assert registry.take_late_result("never_seen") is None
        complete("a")
        # hit
        assert registry.take_late_result("a") is not None
        complete("b")  # forces eviction of "a" since max_entries=1 and "b" is newer
        snap = g.production_metrics_snapshot()
        assert snap["late_result_cache_miss"] >= 1
        assert snap["late_result_cache_hit"] >= 1
        assert snap["late_result_cache_eviction"] >= 1
    finally:
        g.shutdown(wait=False)
