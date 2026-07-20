"""X24.2.1 Sweep Throughput Remediation -- tests for bounded concurrency,
the overlap guard, and health-semantics reporting added to subprov_sweep_pass()/
subprov_sweep_pass_guarded()/sweep_health_report().

Phase 1/2 (measurement) is not re-tested here -- it produced log evidence
against a real running daemon, not a unit-testable artifact. These tests
cover Phase 3's actual code changes: bounded concurrency via a semaphore
(not sequential, not unbounded gather), the no-overlap guard, independent
per-session outcome handling under concurrency, and the DEGRADED/HEALTHY
health report.
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


def _insert_session(path, subprov, expires_at, detected_at=None):
    if detected_at is None:
        detected_at = int(time.time())
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions "
        "(subprov_wallet, state, detected_at, expires_at) VALUES (?, 'ACTIVE', ?, ?)",
        (subprov, detected_at, expires_at))
    conn.commit()
    sid = conn.execute(
        "SELECT id FROM wt_active_subprov_sessions WHERE subprov_wallet=?", (subprov,)
    ).fetchone()[0]
    conn.close()
    return sid


@pytest.fixture
def ops_db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "sweep_concurrency_test.db")
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


def test_sweep_respects_bounded_concurrency_cap(ops_db_path):
    """No more than SWEEP_CONCURRENCY catch_up_subprov() calls may be in
    flight at the same instant -- proves bounded concurrency, not
    unbounded gather() across all selected sessions."""
    path, wc = ops_db_path
    wc.SWEEP_CONCURRENCY = 3
    for i in range(10):
        _insert_session(path, f"SUB_{i}", expires_at=999999)
    casc = wc.Cascade()

    in_flight = {"current": 0, "max_seen": 0}

    async def _fake_catch_up(subprov, limit=None):
        in_flight["current"] += 1
        in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["current"])
        await asyncio.sleep(0.05)
        in_flight["current"] -= 1
        return "SUCCESS"
    casc.catch_up_subprov = _fake_catch_up

    _run(casc.subprov_sweep_pass())

    assert in_flight["max_seen"] <= 3, f"concurrency cap violated: {in_flight['max_seen']}"


def test_sweep_wall_clock_bounded_by_concurrency_not_sequential_sum(ops_db_path):
    """10 sessions x 0.1s each, concurrency=5, must complete in roughly
    2 batches (~0.2s), not 10 sequential steps (~1.0s) -- proves the cycle
    is actually bounded in wall-clock time by the concurrency setting."""
    path, wc = ops_db_path
    wc.SWEEP_CONCURRENCY = 5
    for i in range(10):
        _insert_session(path, f"SUB_{i}", expires_at=999999)
    casc = wc.Cascade()

    async def _fake_catch_up(subprov, limit=None):
        await asyncio.sleep(0.1)
        return "SUCCESS"
    casc.catch_up_subprov = _fake_catch_up

    t0 = time.time()
    _run(casc.subprov_sweep_pass())
    elapsed = time.time() - t0

    assert elapsed < 0.6, f"cycle took {elapsed}s -- looks sequential, not concurrency-bounded"


def test_no_duplicate_concurrent_inspection_of_same_session(ops_db_path):
    """Every selected session must be inspected exactly once per cycle, even
    under concurrent execution."""
    path, wc = ops_db_path
    wc.SWEEP_CONCURRENCY = 4
    for i in range(6):
        _insert_session(path, f"SUB_{i}", expires_at=999999)
    casc = wc.Cascade()

    call_counts: dict[str, int] = {}

    async def _fake_catch_up(subprov, limit=None):
        call_counts[subprov] = call_counts.get(subprov, 0) + 1
        await asyncio.sleep(0.01)
        return "SUCCESS"
    casc.catch_up_subprov = _fake_catch_up

    _run(casc.subprov_sweep_pass())

    assert all(n == 1 for n in call_counts.values()), call_counts
    assert len(call_counts) == 6


def test_mixed_outcomes_handled_independently_under_concurrency(ops_db_path):
    """Concurrent execution must not let one session's failure affect
    another's success -- each outcome is tracked and persisted independently."""
    path, wc = ops_db_path
    wc.SWEEP_CONCURRENCY = 4
    ids = {}
    for name in ("OK_A", "TIMEOUT_A", "OK_B", "ERROR_A"):
        ids[name] = _insert_session(path, name, expires_at=999999)
    outcomes = {"OK_A": "SUCCESS", "TIMEOUT_A": "RPC_TIMEOUT", "OK_B": "SUCCESS", "ERROR_A": "RPC_ERROR"}
    casc = wc.Cascade()

    async def _fake_catch_up(subprov, limit=None):
        await asyncio.sleep(0.01)
        return outcomes[subprov]
    casc.catch_up_subprov = _fake_catch_up

    _run(casc.subprov_sweep_pass())

    conn = sqlite3.connect(path)
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT id, last_swept_at FROM wt_active_subprov_sessions").fetchall()}
    conn.close()
    assert rows[ids["OK_A"]] is not None
    assert rows[ids["OK_B"]] is not None
    assert rows[ids["TIMEOUT_A"]] is None
    assert rows[ids["ERROR_A"]] is None


def test_overlap_guard_skips_second_concurrent_sweep(ops_db_path):
    """subprov_sweep_pass_guarded() must refuse to start a second sweep
    while a previous one is still running -- report/skip, not queue/stack."""
    path, wc = ops_db_path
    _insert_session(path, "SLOW_SUB", expires_at=999999)
    casc = wc.Cascade()

    async def _slow_catch_up(subprov, limit=None):
        await asyncio.sleep(0.2)
        return "SUCCESS"
    casc.catch_up_subprov = _slow_catch_up

    async def _both():
        t1 = asyncio.ensure_future(casc.subprov_sweep_pass_guarded())
        await asyncio.sleep(0.02)  # let the first cycle actually start
        await casc.subprov_sweep_pass_guarded()  # should be skipped immediately
        await t1

    _run(_both())

    assert casc._sweep_skipped_overlap_count == 1
    assert casc._sweep_in_progress is False


def test_guard_allows_next_cycle_after_previous_completes(ops_db_path):
    """Once a sweep finishes, the guard must allow a subsequent cycle to run
    normally (not permanently locked out)."""
    path, wc = ops_db_path
    _insert_session(path, "SUB_X", expires_at=999999)
    casc = wc.Cascade()
    calls = {"n": 0}

    async def _fake_catch_up(subprov, limit=None):
        calls["n"] += 1
        return "SUCCESS"
    casc.catch_up_subprov = _fake_catch_up

    _run(casc.subprov_sweep_pass_guarded())
    assert casc._sweep_in_progress is False
    _run(casc.subprov_sweep_pass_guarded())

    assert calls["n"] == 2
    assert casc._sweep_skipped_overlap_count == 0


def test_only_successful_sessions_marked_swept_under_concurrency(ops_db_path):
    path, wc = ops_db_path
    wc.SWEEP_CONCURRENCY = 4
    ok_id = _insert_session(path, "OK_ONLY", expires_at=999999)
    fail_id = _insert_session(path, "FAIL_ONLY", expires_at=999999)
    casc = wc.Cascade()

    async def _fake_catch_up(subprov, limit=None):
        return "SUCCESS" if subprov == "OK_ONLY" else "NO_RESULT"
    casc.catch_up_subprov = _fake_catch_up

    _run(casc.subprov_sweep_pass())

    conn = sqlite3.connect(path)
    ok_row = conn.execute("SELECT last_swept_at, sweep_count FROM wt_active_subprov_sessions WHERE id=?", (ok_id,)).fetchone()
    fail_row = conn.execute("SELECT last_swept_at FROM wt_active_subprov_sessions WHERE id=?", (fail_id,)).fetchone()
    conn.close()
    assert ok_row[0] is not None and ok_row[1] == 1
    assert fail_row[0] is None


def test_rpc_concurrency_never_exceeds_configured_cap_with_many_sessions(ops_db_path):
    """With MAX_ACTIVE_SUBPROVS-worth of sessions selected and a low
    concurrency cap, in-flight RPC calls must never exceed the cap even at
    the full selection size."""
    path, wc = ops_db_path
    wc.SWEEP_CONCURRENCY = 2
    for i in range(wc.MAX_ACTIVE_SUBPROVS):
        _insert_session(path, f"SESS_{i}", expires_at=999999)
    casc = wc.Cascade()
    in_flight = {"current": 0, "max_seen": 0}

    async def _fake_catch_up(subprov, limit=None):
        in_flight["current"] += 1
        in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["current"])
        await asyncio.sleep(0.03)
        in_flight["current"] -= 1
        return "SUCCESS"
    casc.catch_up_subprov = _fake_catch_up

    _run(casc.subprov_sweep_pass())

    assert in_flight["max_seen"] <= 2


def test_backlog_drains_when_capacity_exceeds_arrival_rate(ops_db_path):
    """Simulated sub-capacity arrival: a fixed backlog with no new arrivals
    during the cycle must show a shrinking never-swept count after one
    sweep completes fully (all successes)."""
    path, wc = ops_db_path
    wc.SWEEP_CONCURRENCY = 4
    for i in range(8):
        _insert_session(path, f"DRAIN_{i}", expires_at=999999)
    casc = wc.Cascade()

    async def _fake_catch_up(subprov, limit=None):
        return "SUCCESS"
    casc.catch_up_subprov = _fake_catch_up

    from src.core import ws_cascade_store as store
    conn = casc._ops()
    before = store.sweep_coverage_snapshot(conn, cap=wc.MAX_ACTIVE_SUBPROVS)["never_swept"]
    conn.close()

    _run(casc.subprov_sweep_pass())

    conn = casc._ops()
    after = store.sweep_coverage_snapshot(conn, cap=wc.MAX_ACTIVE_SUBPROVS)["never_swept"]
    conn.close()
    assert after < before


def test_health_report_degraded_when_arrivals_exceed_throughput(ops_db_path):
    """sweep_health_report() must report status=DEGRADED when the measured
    inspection rate is below the measured arrival rate, even though every
    RPC call in the cycle succeeded (no failed outcomes at all) -- this is
    the exact scenario X24.2's live validation actually hit."""
    path, wc = ops_db_path
    wc.SWEEP_CONCURRENCY = 4
    now = int(time.time())
    # 400 arrivals within the last 60s -> ~400/min arrival rate, an order of
    # magnitude above what a 10-per-cycle sweep can sustain even fully
    # successful and reasonably fast, so the DEGRADED comparison is robust
    # regardless of exact cycle timing.
    for i in range(400):
        _insert_session(path, f"ARRIVAL_{i}", expires_at=999999, detected_at=now - 10)
    casc = wc.Cascade()

    async def _fake_catch_up(subprov, limit=None):
        await asyncio.sleep(2.0)  # slow inspection -> low inspections/minute
        return "SUCCESS"
    casc.catch_up_subprov = _fake_catch_up

    _run(casc.subprov_sweep_pass())

    report = casc.sweep_health_report(arrival_window_seconds=60)
    assert report["status"] == "DEGRADED", report
    assert report["arrivals_per_minute"] > report["inspections_per_minute"]
    assert report["backlog_never_swept"] > 0


def test_health_report_healthy_when_throughput_exceeds_arrivals(ops_db_path):
    path, wc = ops_db_path
    wc.SWEEP_CONCURRENCY = 4
    now = int(time.time())
    _insert_session(path, "LONE_ARRIVAL", expires_at=999999, detected_at=now - 10)
    casc = wc.Cascade()

    async def _fake_catch_up(subprov, limit=None):
        return "SUCCESS"  # near-instant -> very high inspections/minute
    casc.catch_up_subprov = _fake_catch_up

    _run(casc.subprov_sweep_pass())

    report = casc.sweep_health_report(arrival_window_seconds=60)
    assert report["status"] == "HEALTHY", report
    assert report["currently_running_sweep"] is False
