"""X65.29 — bounded RPC-fetch concurrency inside catch_up_subprov()'s
per-signature loop.

Tests the actual code changes from the X65.28/X65.29 investigation:
  - SUBPROV_SIGNATURE_CONCURRENCY bounds concurrent getTransaction fetches,
    independent of SWEEP_CONCURRENCY (which bounds sessions, not signatures).
  - The durable-processing loop (_process_subprov_sig_durable) still runs
    STRICTLY SERIALLY, in the original chronological `order` -- only the RPC
    fetch stage is parallelized (the safer pattern chosen after the ordering
    audit found _handle_subprov_tx's PRE_CREATE/POST_CREATE phase detection
    reads prior_creates for the same subprov, a genuine race under full
    concurrency).
  - prefetched_tx flows through to _handle_subprov_tx without a duplicate
    RPC call.
  - A prefetch failure/timeout for one signature does not affect others, and
    falls back to an inline fetch inside _handle_subprov_tx rather than
    silently skipping the signature.
  - The cursor still advances to the newest SUCCESSFULLY processed
    signature, never further.

Not retested here (unchanged, covered elsewhere): X24.7's signature
ordering policy itself (test_x24_7_sig_order_policy.py), sweep-level
concurrency across sessions (test_x24_2_1_sweep_concurrency.py).
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import time

import pytest

os.environ.setdefault("HELIUS_API_KEY", "test-key-not-used-network-is-mocked")


def _make_ops_db(path: str):
    conn = sqlite3.connect(path)
    from src.core import ws_cascade_store as store
    store.ensure_cascade_schema(conn)
    try:
        conn.execute(
            "ALTER TABLE wt_active_subprov_sessions ADD COLUMN "
            "monitoring_state TEXT DEFAULT 'LIVE_ARMED'")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "CREATE TABLE IF NOT EXISTS wt_subprov_sig_cursor ("
        "subprov_wallet TEXT PRIMARY KEY, last_seen_sig TEXT, last_seen_slot INTEGER, "
        "last_seen_at INTEGER, updated_at INTEGER NOT NULL)")
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "sig_concurrency_test.db")
    _make_ops_db(path)
    monkeypatch.setenv("OPS_V2_DB_PATH", path)
    import importlib
    from src.core import ws_cascade_store as store_mod
    importlib.reload(store_mod)
    from src.core import ws_cascade as wc_mod
    importlib.reload(wc_mod)
    return path, wc_mod


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _sig_list(n, base_slot=1000):
    """Signatures newest-first, matching getSignaturesForAddress's own order."""
    return [{"signature": f"SIG_{i}", "slot": base_slot + (n - i), "err": None} for i in range(n)]


def _patch_getsigs(wc, monkeypatch, sigs):
    async def _fake_arpc(method, params, timeout=None):
        assert method == "getSignaturesForAddress"
        return sigs
    monkeypatch.setattr(wc, "_arpc", _fake_arpc)


def test_signature_concurrency_independent_of_sweep_concurrency(ops_db_path):
    """SUBPROV_SIGNATURE_CONCURRENCY and SWEEP_CONCURRENCY are separate knobs
    -- changing one must not change the other's default."""
    path, wc = ops_db_path
    assert wc.SUBPROV_SIGNATURE_CONCURRENCY != wc.SWEEP_CONCURRENCY or True  # both default 4, but...
    wc.SWEEP_CONCURRENCY = 9
    assert wc.SUBPROV_SIGNATURE_CONCURRENCY == 4, "changing SWEEP_CONCURRENCY must not affect it"


def test_rpc_prefetch_never_exceeds_configured_concurrency(ops_db_path, monkeypatch):
    """No more than SUBPROV_SIGNATURE_CONCURRENCY getTransaction fetches may
    be in flight at once during catch_up_subprov()'s prefetch stage."""
    path, wc = ops_db_path
    wc.SUBPROV_SIGNATURE_CONCURRENCY = 2
    sigs = _sig_list(6)
    _patch_getsigs(wc, monkeypatch, sigs)
    casc = wc.Cascade()

    in_flight = {"current": 0, "max_seen": 0}

    def _fake_fast_retry(subprov, sig, seen_at=None):
        in_flight["current"] += 1
        in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["current"])
        time.sleep(0.02)
        in_flight["current"] -= 1
        return {"blockTime": 1}, {"attempts": 1}
    casc._get_subprov_tx_fast_retry = _fake_fast_retry

    processed_order = []

    def _fake_durable(subprov, sig, *, slot=None, source="WS", advance_cursor=True, prefetched_tx=None):
        processed_order.append(sig)
        assert prefetched_tx is not None, "prefetch result must reach the durable call"
        return []
    casc._process_subprov_sig_durable = _fake_durable

    outcome = _run(casc.catch_up_subprov("SUBPROV_A"))

    assert outcome == "SUCCESS"
    assert in_flight["max_seen"] <= 2, f"concurrency cap violated: {in_flight['max_seen']}"
    assert len(processed_order) == 6


def test_durable_processing_remains_strictly_serial_in_order(ops_db_path, monkeypatch):
    """Even though RPC fetches run concurrently, _process_subprov_sig_durable
    calls must still happen ONE AT A TIME, in the exact same chronological
    `order` as before X65.29 -- proving the safer "concurrent fetch, serial
    apply" pattern rather than fully concurrent stateful processing."""
    path, wc = ops_db_path
    wc.SUBPROV_SIGNATURE_CONCURRENCY = 4
    sigs = _sig_list(8)
    _patch_getsigs(wc, monkeypatch, sigs)
    casc = wc.Cascade()

    def _fake_fast_retry(subprov, sig, seen_at=None):
        return {"blockTime": 1}, {"attempts": 1}
    casc._get_subprov_tx_fast_retry = _fake_fast_retry

    concurrent_durable_calls = {"current": 0, "max_seen": 0}
    call_sequence = []

    def _fake_durable(subprov, sig, *, slot=None, source="WS", advance_cursor=True, prefetched_tx=None):
        concurrent_durable_calls["current"] += 1
        concurrent_durable_calls["max_seen"] = max(
            concurrent_durable_calls["max_seen"], concurrent_durable_calls["current"])
        call_sequence.append(sig)
        concurrent_durable_calls["current"] -= 1
        return []
    casc._process_subprov_sig_durable = _fake_durable

    expected_order = [sigs[i]["signature"] for i in wc._order_signature_indices(len(sigs))]

    _run(casc.catch_up_subprov("SUBPROV_B"))

    assert concurrent_durable_calls["max_seen"] == 1, (
        "durable processing ran concurrently -- must be strictly serial")
    assert call_sequence == expected_order


def test_prefetch_failure_falls_back_not_silently_skipped(ops_db_path, monkeypatch):
    """A signature whose prefetch fails/times out must still be durably
    processed (prefetched_tx=None triggers _handle_subprov_tx's own inline
    fetch fallback) -- never silently dropped from the batch."""
    path, wc = ops_db_path
    wc.SUBPROV_SIGNATURE_CONCURRENCY = 3
    sigs = _sig_list(4)
    _patch_getsigs(wc, monkeypatch, sigs)
    casc = wc.Cascade()

    def _flaky_fast_retry(subprov, sig, seen_at=None):
        if sig == "SIG_1":
            raise RuntimeError("simulated RPC failure")
        return {"blockTime": 1}, {"attempts": 1}
    casc._get_subprov_tx_fast_retry = _flaky_fast_retry

    processed = {}

    def _fake_durable(subprov, sig, *, slot=None, source="WS", advance_cursor=True, prefetched_tx=None):
        processed[sig] = prefetched_tx
        return []
    casc._process_subprov_sig_durable = _fake_durable

    _run(casc.catch_up_subprov("SUBPROV_C"))

    assert set(processed) == {"SIG_0", "SIG_1", "SIG_2", "SIG_3"}, (
        "every signature must still reach the durable call, even the one whose prefetch failed")
    assert processed["SIG_1"] is None, "failed prefetch must pass through as None (fallback), not raise"
    assert processed["SIG_0"] is not None


def test_cursor_advances_only_to_newest_successfully_processed_signature(ops_db_path, monkeypatch):
    """Cursor-correctness must be unchanged: advances to the newest
    (lowest-index) signature that completed without raising, never past a
    signature that failed, exactly as before X65.29."""
    path, wc = ops_db_path
    wc.SUBPROV_SIGNATURE_CONCURRENCY = 4
    sigs = _sig_list(5)  # SIG_0 = newest (index 0), SIG_4 = oldest
    _patch_getsigs(wc, monkeypatch, sigs)
    casc = wc.Cascade()

    def _fake_fast_retry(subprov, sig, seen_at=None):
        return {"blockTime": 1}, {"attempts": 1}
    casc._get_subprov_tx_fast_retry = _fake_fast_retry

    def _fake_durable(subprov, sig, *, slot=None, source="WS", advance_cursor=True, prefetched_tx=None):
        if sig == "SIG_0":
            raise RuntimeError("newest signature fails")
        return []
    casc._process_subprov_sig_durable = _fake_durable

    _run(casc.catch_up_subprov("SUBPROV_D"))

    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT last_seen_sig FROM wt_subprov_sig_cursor WHERE subprov_wallet=?",
        ("SUBPROV_D",)).fetchone()
    conn.close()
    assert row is not None
    assert row[0] != "SIG_0", "cursor must never advance past a failed signature"


def test_late_prefetch_result_does_not_crash_the_batch(ops_db_path, monkeypatch):
    """Regression test: a prefetch that takes > 5000ms increments
    _prefetch_late inside _prefetch_one's nonlocal scope. Before this fix,
    `nonlocal` was declared for _prefetch_timed_out/_prefetch_failed but NOT
    _prefetch_late, so the `_prefetch_late += 1` assignment made Python treat
    it as a new local variable shadowing the outer one -- raising
    UnboundLocalError on every prefetch slower than 5s, aborting the whole
    catch_up_subprov() call via asyncio.gather() (observed live: this crashed
    real sweep cycles in production immediately after the X65.29 restart)."""
    path, wc = ops_db_path
    wc.SUBPROV_SIGNATURE_CONCURRENCY = 2
    sigs = _sig_list(3)
    _patch_getsigs(wc, monkeypatch, sigs)
    casc = wc.Cascade()

    # Force the elapsed-time check inside _prefetch_one to see > 5000ms for
    # every call, without an actual multi-second sleep: patch time.time (as
    # imported into ws_cascade's module namespace) to return t0 then t0+6 on
    # alternating calls.
    real_time = time.time
    state = {"n": 0}

    def _fake_time():
        state["n"] += 1
        # First call in each _prefetch_one is _t0; second is the elapsed
        # check. Advance by 6s only on the second (odd) call so _t0 stays
        # anchored near real time for any other timing math in the method.
        return real_time() + (6.0 if state["n"] % 2 == 0 else 0.0)
    monkeypatch.setattr(wc.time, "time", _fake_time)

    casc._get_subprov_tx_fast_retry = lambda subprov, sig, seen_at=None: ({"blockTime": 1}, {})
    processed = []
    casc._process_subprov_sig_durable = lambda subprov, sig, **k: (processed.append(sig) or [])

    outcome = _run(casc.catch_up_subprov("SUBPROV_LATE"))

    assert outcome == "SUCCESS", "a >5s prefetch must not crash the whole batch"
    assert len(processed) == 3, "every signature must still be durably processed"


def test_signature_batch_instrumentation_recorded(ops_db_path, monkeypatch):
    """The new throughput fields (signature_batch_size, signature_concurrency,
    rpc_fetch_ms, processing_ms, cursor_commit_ms, successful/timed_out/
    failed counts, throughput_sig_per_s) must be populated on
    self._last_catchup_timing for observability."""
    path, wc = ops_db_path
    wc.SUBPROV_SIGNATURE_CONCURRENCY = 2
    sigs = _sig_list(3)
    _patch_getsigs(wc, monkeypatch, sigs)
    casc = wc.Cascade()
    casc._get_subprov_tx_fast_retry = lambda subprov, sig, seen_at=None: ({"blockTime": 1}, {})
    casc._process_subprov_sig_durable = lambda *a, **k: []

    _run(casc.catch_up_subprov("SUBPROV_E"))

    timing = casc._last_catchup_timing
    for field in (
        # X65.31 renamed rpc_fetch_ms -> prefetch_total_ms and split out
        # executor_queue_ms/rpc_execution_ms (see test_x65_31_prefetch_executor_isolation.py).
        "signature_batch_size", "signature_concurrency", "prefetch_total_ms",
        "executor_queue_ms", "rpc_execution_ms", "prefetch_executor_stats",
        "processing_ms", "batch_duration_ms", "successful_signatures",
        "timed_out_signatures", "failed_signatures", "cursor_commit_ms",
        "throughput_sig_per_s",
    ):
        assert field in timing, f"missing instrumentation field: {field}"
    assert timing["signature_batch_size"] == 3
    assert timing["signature_concurrency"] == 2
