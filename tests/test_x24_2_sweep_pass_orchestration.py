"""X24.2 deployment-readiness fix — orchestration-level tests for
subprov_sweep_pass(), the exact function containing the confirmed defect:
mark_swept() previously fired unconditionally regardless of whether
catch_up_subprov() actually succeeded.

These tests exercise subprov_sweep_pass() itself (not fair_sweep_candidates/
mark_swept in isolation, which the original X24.2 suite already covered but
which never caught this bug because they never called the orchestration
function). catch_up_subprov is monkeypatched to return each of its four
explicit outcomes deterministically — no real RPC, no real WS, real
Cascade instance against an isolated on-disk SQLite copy so schema init,
wallet-profile build, etc. all run as genuine code, just never touching
production data.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile

import pytest

os.environ.setdefault("HELIUS_API_KEY", "test-key-not-used-network-is-mocked")


def _make_ops_db(path: str):
    conn = sqlite3.connect(path)
    from src.core import ws_cascade_store as store
    store.ensure_cascade_schema(conn)
    # NOTE (unrelated pre-existing gap, out of X24.2's authorized scope, not fixed
    # here): ensure_cascade_schema's initial CREATE TABLE for
    # wt_active_subprov_sessions does not include monitoring_state, and no
    # ALTER TABLE anywhere in this module adds it either -- the column only
    # exists in production because the live DB predates this code path. A
    # from-scratch schema build (as this test fixture needs) is missing it.
    # Added explicitly here so this test can exercise the real scheduler code
    # against a schema shaped like production's, without touching
    # ensure_cascade_schema itself (out of scope for this review's authorized
    # fixes).
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


def _insert_session(path, subprov, expires_at, detected_at=0):
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
    path = str(tmp_path / "sweep_orchestration_test.db")
    _make_ops_db(path)
    # Point BOTH modules' module-level OPS_DB_PATH at the isolated copy before
    # Cascade() is constructed, matching the X24.1 replay's safe-isolation pattern.
    monkeypatch.setenv("OPS_V2_DB_PATH", path)
    import importlib
    from src.core import ws_cascade_store as store_mod
    importlib.reload(store_mod)
    from src.core import ws_cascade as wc_mod
    importlib.reload(wc_mod)
    return path, wc_mod


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_successful_inspection_marks_session_swept(ops_db_path):
    path, wc = ops_db_path
    sid = _insert_session(path, "SUCCESS_SUBPROV", expires_at=999999)
    casc = wc.Cascade()

    async def _fake_catch_up(subprov, limit=None):
        return "SUCCESS"
    casc.catch_up_subprov = _fake_catch_up

    _run(casc.subprov_sweep_pass())

    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT last_swept_at, sweep_count FROM wt_active_subprov_sessions WHERE id=?", (sid,)
    ).fetchone()
    conn.close()
    assert row[0] is not None, "a SUCCESS outcome must mark the session swept"
    assert row[1] == 1


def test_rpc_timeout_does_not_mark_session_swept(ops_db_path):
    path, wc = ops_db_path
    sid = _insert_session(path, "TIMEOUT_SUBPROV", expires_at=999999)
    casc = wc.Cascade()

    async def _fake_catch_up(subprov, limit=None):
        return "RPC_TIMEOUT"
    casc.catch_up_subprov = _fake_catch_up

    _run(casc.subprov_sweep_pass())

    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT last_swept_at, sweep_count FROM wt_active_subprov_sessions WHERE id=?", (sid,)
    ).fetchone()
    conn.close()
    assert row[0] is None, "RPC_TIMEOUT must NOT advance fairness -- this is the confirmed defect"
    assert row[1] == 0


def test_rpc_error_does_not_mark_session_swept(ops_db_path):
    path, wc = ops_db_path
    sid = _insert_session(path, "ERROR_SUBPROV", expires_at=999999)
    casc = wc.Cascade()

    async def _fake_catch_up(subprov, limit=None):
        return "RPC_ERROR"
    casc.catch_up_subprov = _fake_catch_up

    _run(casc.subprov_sweep_pass())

    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT last_swept_at FROM wt_active_subprov_sessions WHERE id=?", (sid,)
    ).fetchone()
    conn.close()
    assert row[0] is None, "RPC_ERROR must NOT advance fairness"


def test_null_rpc_result_does_not_mark_session_swept(ops_db_path):
    path, wc = ops_db_path
    sid = _insert_session(path, "NULL_RESULT_SUBPROV", expires_at=999999)
    casc = wc.Cascade()

    async def _fake_catch_up(subprov, limit=None):
        return "NO_RESULT"
    casc.catch_up_subprov = _fake_catch_up

    _run(casc.subprov_sweep_pass())

    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT last_swept_at FROM wt_active_subprov_sessions WHERE id=?", (sid,)
    ).fetchone()
    conn.close()
    assert row[0] is None, "NO_RESULT must NOT advance fairness"


def test_mixed_success_and_failure_across_one_cycle(ops_db_path):
    """The realistic case: some sessions inspect successfully, others fail,
    within the SAME sweep cycle. Only the successes should advance fairness;
    the failures must remain un-swept so they are retried on a future cycle
    (prioritised ahead of the successes, which are now legitimately swept)."""
    path, wc = ops_db_path
    sid_ok1 = _insert_session(path, "OK_1", expires_at=999999)
    sid_timeout = _insert_session(path, "TIMEOUT_1", expires_at=999999)
    sid_ok2 = _insert_session(path, "OK_2", expires_at=999999)
    sid_error = _insert_session(path, "ERROR_1", expires_at=999999)
    sid_null = _insert_session(path, "NULL_1", expires_at=999999)

    outcomes = {
        "OK_1": "SUCCESS", "TIMEOUT_1": "RPC_TIMEOUT",
        "OK_2": "SUCCESS", "ERROR_1": "RPC_ERROR", "NULL_1": "NO_RESULT",
    }
    casc = wc.Cascade()

    async def _fake_catch_up(subprov, limit=None):
        return outcomes[subprov]
    casc.catch_up_subprov = _fake_catch_up

    _run(casc.subprov_sweep_pass())

    conn = sqlite3.connect(path)
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT id, last_swept_at FROM wt_active_subprov_sessions"
    ).fetchall()}
    conn.close()

    assert rows[sid_ok1] is not None
    assert rows[sid_ok2] is not None
    assert rows[sid_timeout] is None
    assert rows[sid_error] is None
    assert rows[sid_null] is None

    # The failed sessions must now be prioritised ahead of the successfully-swept
    # ones on the NEXT cycle's fair_sweep_candidates() ordering.
    from src.core import ws_cascade_store as store
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    next_cycle = store.fair_sweep_candidates(conn, limit=10)
    conn.close()
    ids_in_order = [r["id"] for r in next_cycle]
    failed_ids = {sid_timeout, sid_error, sid_null}
    succeeded_ids = {sid_ok1, sid_ok2}
    first_two_failed = set(ids_in_order[:3]) >= failed_ids
    assert first_two_failed, (
        "failed inspections must be prioritised ahead of successfully-swept "
        f"sessions on the next cycle; got order {ids_in_order}"
    )
    assert not (set(ids_in_order[:3]) & succeeded_ids), (
        "a successfully-swept session must not preempt a failed (never truly "
        "inspected) session in the next cycle's ordering"
    )


def test_failed_outcomes_are_metered_distinctly(ops_db_path):
    path, wc = ops_db_path
    _insert_session(path, "METER_TIMEOUT", expires_at=999999)
    casc = wc.Cascade()

    async def _fake_catch_up(subprov, limit=None):
        return "RPC_TIMEOUT"
    casc.catch_up_subprov = _fake_catch_up

    _run(casc.subprov_sweep_pass())

    assert casc._subprov_sig_metrics.get("sweep_inspection_failed_rpc_timeout", 0) == 1
    assert casc._subprov_sig_metrics.get("sweep_failed_inspections_last_cycle") == 1


def test_catch_up_subprov_returns_success_for_a_genuinely_empty_result(monkeypatch):
    """An empty signature list is a genuine, successful inspection (nothing new
    to report), not a failure -- must return SUCCESS, not be conflated with
    RPC_TIMEOUT/RPC_ERROR/NO_RESULT."""
    import os
    os.environ.setdefault("HELIUS_API_KEY", "test-key")
    from src.core import ws_cascade as wc

    casc = wc.Cascade.__new__(wc.Cascade)  # bypass __init__ (no DB/network needed for this unit)

    # A single shared in-memory connection (not ":memory:" per-call, which would
    # give each call a SEPARATE empty database) so the table created up front is
    # actually visible to catch_up_subprov's own _ops() calls.
    _shared_conn = sqlite3.connect(":memory:")
    _shared_conn.execute(
        "CREATE TABLE wt_subprov_sig_cursor (subprov_wallet TEXT PRIMARY KEY, "
        "last_seen_sig TEXT, last_seen_slot INTEGER, last_seen_at INTEGER, "
        "updated_at INTEGER NOT NULL)")
    _shared_conn.commit()
    casc._ops = lambda: _shared_conn

    async def _fake_arpc(method, params):
        return []  # genuinely empty, successful RPC result
    monkeypatch.setattr(wc, "_arpc", _fake_arpc)

    outcome = asyncio.get_event_loop().run_until_complete(
        casc.catch_up_subprov("SOME_SUBPROV", limit=5)
    )
    assert outcome == "SUCCESS"
