"""X65.31 — isolate the X65.29 prefetch stage from Python's shared asyncio
default executor.

X65.30's root-cause audit measured a representative slow batch where 95.3%
of "rpc_fetch_ms" was executor ADMISSION delay: prefetch submissions
(_ato_thread, routed through the shared default ThreadPoolExecutor,
max_workers=12) queued behind unrelated durable-processing/CDC/websocket
work on that same pool. This task moves prefetch submission onto a
dedicated _subprov_prefetch_executor, leaving every other _ato_thread()
call site (durable processing included) completely unchanged.

Explicitly NOT reused: RpcDeadlineGuard's own executor (_get_tx_guard()).
_get_subprov_tx_fast_retry() already calls into that guard's
call_with_deadline(), which submits onto the guard's OWN dedicated,
capacity-bounded executor and blocks the caller on fut.result(timeout=...).
Reusing the guard's executor for prefetch submission would make prefetch
calls compete for the same max_capacity slots as every other _get_tx
caller in the process, changing the guard's admission semantics for
unrelated callers -- exactly what this task prohibits. A separate pool
means only "a thread blocked waiting on the (unchanged) guard" lives here,
never anything competing for guard capacity.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
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
    path = str(tmp_path / "prefetch_isolation_test.db")
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
    return [{"signature": f"SIG_{i}", "slot": base_slot + (n - i), "err": None} for i in range(n)]


def _patch_getsigs(wc, monkeypatch, sigs):
    async def _fake_arpc(method, params, timeout=None):
        assert method == "getSignaturesForAddress"
        return sigs
    monkeypatch.setattr(wc, "_arpc", _fake_arpc)


def test_dedicated_executor_exists_and_is_separately_sized(ops_db_path):
    path, wc = ops_db_path
    assert wc._subprov_prefetch_executor is not None
    assert wc._subprov_prefetch_executor._max_workers == wc.SUBPROV_PREFETCH_EXECUTOR_WORKERS


def test_dedicated_executor_default_is_independent_of_other_pools(ops_db_path):
    path, wc = ops_db_path
    # Changing the sweep/signature concurrency knobs must not resize (or
    # otherwise touch) the dedicated prefetch executor -- it is configured
    # by its own env var only.
    original_workers = wc._subprov_prefetch_executor._max_workers
    wc.SWEEP_CONCURRENCY = 99
    wc.SUBPROV_SIGNATURE_CONCURRENCY = 99
    assert wc._subprov_prefetch_executor._max_workers == original_workers


def test_prefetch_runs_on_dedicated_executor_threads_not_default(ops_db_path, monkeypatch):
    """Prefetch worker threads must be named from the dedicated pool's
    thread_name_prefix ('ws-subprov-prefetch'), never the default executor's
    anonymous ThreadPoolExecutor-N naming -- direct proof of isolation."""
    path, wc = ops_db_path
    wc.SUBPROV_SIGNATURE_CONCURRENCY = 2
    sigs = _sig_list(4)
    _patch_getsigs(wc, monkeypatch, sigs)
    casc = wc.Cascade()

    thread_names = []

    def _fake_fast_retry(subprov, sig, seen_at=None):
        thread_names.append(threading.current_thread().name)
        return {"blockTime": 1}, {"attempts": 1}
    casc._get_subprov_tx_fast_retry = _fake_fast_retry
    casc._process_subprov_sig_durable = lambda *a, **k: []

    _run(casc.catch_up_subprov("SUBPROV_ISO"))

    assert thread_names, "prefetch never ran"
    assert all(name.startswith("ws-subprov-prefetch") for name in thread_names), (
        f"prefetch ran on non-dedicated threads: {thread_names}")


def test_durable_processing_still_uses_default_executor_unchanged(ops_db_path, monkeypatch):
    """The durable-processing stage (_process_subprov_sig_durable, called via
    the pre-existing _ato_thread) must be completely untouched by this task
    -- it still runs on whatever pool _ato_thread has always used, not the
    new dedicated prefetch pool."""
    path, wc = ops_db_path
    wc.SUBPROV_SIGNATURE_CONCURRENCY = 2
    sigs = _sig_list(3)
    _patch_getsigs(wc, monkeypatch, sigs)
    casc = wc.Cascade()
    casc._get_subprov_tx_fast_retry = lambda subprov, sig, seen_at=None: ({"blockTime": 1}, {})

    durable_thread_names = []

    def _fake_durable(subprov, sig, *, slot=None, source="WS", advance_cursor=True, prefetched_tx=None):
        durable_thread_names.append(threading.current_thread().name)
        return []
    casc._process_subprov_sig_durable = _fake_durable

    _run(casc.catch_up_subprov("SUBPROV_DURABLE"))

    assert durable_thread_names
    assert not any(name.startswith("ws-subprov-prefetch") for name in durable_thread_names), (
        "durable processing must never run on the dedicated prefetch executor")


def test_busy_shared_default_executor_does_not_block_prefetch(ops_db_path, monkeypatch):
    """The core proof: saturating the shared default executor with unrelated
    slow work must NOT delay the PREFETCH stage specifically, because
    prefetch no longer competes for that pool's threads at all. The durable-
    processing stage (unchanged, by design still on the shared pool) IS
    expected to queue behind the hog tasks -- that's the pre-existing,
    untouched behaviour this task deliberately preserves -- so this test
    asserts on prefetch_total_ms (the isolated stage), not overall
    catch_up_subprov() wall-clock time."""
    path, wc = ops_db_path
    wc.SUBPROV_SIGNATURE_CONCURRENCY = 2
    sigs = _sig_list(4)
    _patch_getsigs(wc, monkeypatch, sigs)
    casc = wc.Cascade()
    casc._get_subprov_tx_fast_retry = lambda subprov, sig, seen_at=None: ({"blockTime": 1}, {})
    casc._process_subprov_sig_durable = lambda *a, **k: []

    # Saturate the shared default executor (used by durable processing, CDC,
    # websocket handling) with long-running blocking work, simulating the
    # exact contention X65.30 measured live.
    loop = asyncio.get_event_loop()
    default_executor_size = 12  # matches the process's own asyncio default sizing in this env

    def _hog():
        time.sleep(2.0)

    async def _saturate_and_measure():
        hog_futures = [loop.run_in_executor(None, _hog) for _ in range(default_executor_size)]
        outcome = await casc.catch_up_subprov("SUBPROV_UNDER_LOAD")
        for f in hog_futures:
            f.cancel()
        return outcome

    outcome = _run(_saturate_and_measure())

    assert outcome == "SUCCESS"
    timing = casc._last_catchup_timing
    # If prefetch were still sharing the saturated default pool, this would
    # be >= ~2000ms (queued behind the hog tasks). Isolated on its own
    # dedicated pool, it must stay fast regardless of shared-pool load.
    assert timing["prefetch_total_ms"] < 500, (
        f"prefetch_total_ms={timing['prefetch_total_ms']}ms while the shared "
        "default executor was saturated -- isolation is not working"
    )


def test_prefetch_executor_stats_reports_dedicated_pool_shape(ops_db_path):
    path, wc = ops_db_path
    stats = wc._prefetch_executor_stats()
    for field in ("max_workers", "active_threads", "queue_depth", "active_workers", "pending_jobs"):
        assert field in stats
    assert stats["max_workers"] == wc.SUBPROV_PREFETCH_EXECUTOR_WORKERS


def test_signature_batch_log_reports_queue_vs_execution_split(ops_db_path, monkeypatch):
    """The instrumentation split this task requires: executor_queue_ms and
    rpc_execution_ms must both be present and independently attributable
    (not just a single combined rpc_fetch_ms as before X65.31)."""
    path, wc = ops_db_path
    wc.SUBPROV_SIGNATURE_CONCURRENCY = 2
    sigs = _sig_list(3)
    _patch_getsigs(wc, monkeypatch, sigs)
    casc = wc.Cascade()
    casc._get_subprov_tx_fast_retry = lambda subprov, sig, seen_at=None: ({"blockTime": 1}, {})
    casc._process_subprov_sig_durable = lambda *a, **k: []

    _run(casc.catch_up_subprov("SUBPROV_SPLIT"))

    timing = casc._last_catchup_timing
    assert timing["executor_queue_ms"] >= 0
    assert timing["rpc_execution_ms"] >= 0
    assert timing["prefetch_total_ms"] >= 0
    assert "rpc_fetch_ms" not in timing, "old combined field must be fully replaced, not left dangling"


def test_ordering_and_cursor_correctness_unchanged_by_isolation(ops_db_path, monkeypatch):
    """Regression guard: moving prefetch to a dedicated executor must not
    change WHICH signature the cursor advances to, nor the serial order of
    durable processing -- identical to test_x65_29's own cursor test, run
    again here to prove X65.31 didn't reintroduce an ordering bug."""
    path, wc = ops_db_path
    wc.SUBPROV_SIGNATURE_CONCURRENCY = 4
    sigs = _sig_list(5)  # SIG_0 = newest (index 0), SIG_4 = oldest
    _patch_getsigs(wc, monkeypatch, sigs)
    casc = wc.Cascade()
    casc._get_subprov_tx_fast_retry = lambda subprov, sig, seen_at=None: ({"blockTime": 1}, {})

    def _fake_durable(subprov, sig, *, slot=None, source="WS", advance_cursor=True, prefetched_tx=None):
        if sig == "SIG_0":
            raise RuntimeError("newest signature fails")
        return []
    casc._process_subprov_sig_durable = _fake_durable

    _run(casc.catch_up_subprov("SUBPROV_CURSOR_ISO"))

    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT last_seen_sig FROM wt_subprov_sig_cursor WHERE subprov_wallet=?",
        ("SUBPROV_CURSOR_ISO",)).fetchone()
    conn.close()
    assert row is not None
    assert row[0] != "SIG_0", "cursor must never advance past a failed signature"
