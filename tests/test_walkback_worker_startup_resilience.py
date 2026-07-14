"""walkback_worker.run_loop() startup resilience.

Root cause fixed here: recover_stalled_running_jobs()/finalize_exhausted_pending()
at startup had no exception handling at all, so a transient sqlite3 "database is
locked" error during either call crashed the ENTIRE worker process before it ever
reached the main loop — a genuine crash-loop observed in production (11 restarts,
zero queue completions). The fix isolates each non-essential startup maintenance
call: a lock-contention OperationalError is logged and skipped (the next boot or
a later scheduled pass still catches the cleanup); any OTHER exception still
propagates, since that would indicate a real defect, not transient contention.
This must never change attribution/capture/queue-decision logic — only startup
robustness.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

import src.core.walkback_worker as walkback_worker
from src.core.walkback_worker import _is_lock_error, run_loop


def test_is_lock_error_identifies_locked_database():
    assert _is_lock_error(sqlite3.OperationalError("database is locked")) is True


def test_is_lock_error_rejects_other_operational_errors():
    assert _is_lock_error(sqlite3.OperationalError("no such table: foo")) is False


def test_is_lock_error_rejects_non_operational_exceptions():
    assert _is_lock_error(ValueError("database is locked")) is False


@pytest.fixture
def stub_run_loop_dependencies(monkeypatch, tmp_path):
    """Stub every run_loop() dependency except the two startup-maintenance calls
    under test, and make the main while-loop exit after one iteration so the test
    doesn't hang. _ops_conn() opens a FRESH connection each call (as it does in
    production) rather than reusing one closed connection object."""
    db_path = str(tmp_path / "ops.db")

    def _fresh_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE IF NOT EXISTS wt_walkback_queue "
            "(mint TEXT, status TEXT, attempts INTEGER)"
        )
        return conn

    monkeypatch.setattr(walkback_worker, "_ops_conn", _fresh_conn)
    monkeypatch.setattr("src.core.walkback_queue.ensure_schema", lambda conn: None)
    monkeypatch.setattr("src.core.treasury_bank.initialize_schema", lambda conn: None)
    monkeypatch.setattr("src.ops.attribution_outcome.ensure_schema", lambda conn: None)
    monkeypatch.setattr(walkback_worker, "_write_heartbeat", lambda conn: None)

    def fake_sleep(seconds):
        raise KeyboardInterrupt("stop the loop after one iteration")

    monkeypatch.setattr(walkback_worker.time, "sleep", fake_sleep)
    return db_path


def test_lock_error_in_recover_stalled_jobs_does_not_crash_worker(stub_run_loop_dependencies, capsys):
    with patch(
        "src.ops.walkback_health.recover_stalled_running_jobs",
        side_effect=sqlite3.OperationalError("database is locked"),
    ), patch.object(walkback_worker, "finalize_exhausted_pending", return_value=0):
        with pytest.raises(KeyboardInterrupt):
            run_loop()
    out = capsys.readouterr().out
    assert "startup maintenance skipped (recover_stalled_running_jobs)" in out
    assert "worker starting" in out  # proves the main loop was reached


def test_lock_error_in_finalize_exhausted_pending_does_not_crash_worker(stub_run_loop_dependencies, capsys):
    with patch(
        "src.ops.walkback_health.recover_stalled_running_jobs",
        return_value={"requeued": 0, "failed": 0},
    ), patch.object(
        walkback_worker, "finalize_exhausted_pending",
        side_effect=sqlite3.OperationalError("database is locked"),
    ):
        with pytest.raises(KeyboardInterrupt):
            run_loop()
    out = capsys.readouterr().out
    assert "startup maintenance skipped (finalize_exhausted_pending)" in out
    assert "worker starting" in out


def test_non_lock_exception_in_recover_stalled_jobs_still_fails_loudly(stub_run_loop_dependencies):
    with patch(
        "src.ops.walkback_health.recover_stalled_running_jobs",
        side_effect=RuntimeError("a genuine defect, not lock contention"),
    ):
        with pytest.raises(RuntimeError, match="a genuine defect"):
            run_loop()


def test_non_lock_exception_in_finalize_exhausted_pending_still_fails_loudly(stub_run_loop_dependencies):
    with patch(
        "src.ops.walkback_health.recover_stalled_running_jobs",
        return_value={"requeued": 0, "failed": 0},
    ), patch.object(
        walkback_worker, "finalize_exhausted_pending",
        side_effect=RuntimeError("a genuine defect, not lock contention"),
    ):
        with pytest.raises(RuntimeError, match="a genuine defect"):
            run_loop()


def test_main_loop_starts_after_skipped_maintenance_task(stub_run_loop_dependencies, capsys):
    """The main loop must actually be entered (heartbeat/queue-check reached), not
    just print "worker starting" and hang — proven by observing the queue-empty
    branch execute before the stubbed sleep raises KeyboardInterrupt."""
    with patch(
        "src.ops.walkback_health.recover_stalled_running_jobs",
        side_effect=sqlite3.OperationalError("database is locked"),
    ), patch.object(walkback_worker, "finalize_exhausted_pending", return_value=0):
        with pytest.raises(KeyboardInterrupt):
            run_loop()
    out = capsys.readouterr().out
    assert "queue empty (pending=0)" in out


def test_skipped_maintenance_task_can_succeed_on_a_later_pass():
    """recover_stalled_running_jobs itself is unmodified — a later call (e.g. the
    next process boot, or a manual invocation) with a free lock succeeds normally.
    This is a direct regression check that we did not change that function's
    actual behavior, only run_loop()'s handling of its failure."""
    from src.ops.walkback_health import recover_stalled_running_jobs

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE wt_walkback_queue (mint TEXT PRIMARY KEY, status TEXT, "
        "attempts INTEGER, started_at INTEGER, updated_at INTEGER, enqueued_at INTEGER, last_error TEXT)"
    )
    conn.commit()
    result = recover_stalled_running_jobs(conn, max_attempts=3, stalled_after_seconds=180)
    assert result == {"requeued": 0, "failed": 0}


def test_no_attribution_or_capture_semantics_changed():
    """Sanity check: the startup-resilience fix touches only run_loop()'s startup
    sequence's OWN exception handling — it must not change what
    recover_stalled_running_jobs() itself does or returns. A later sprint (X21D.3)
    added diagnostic-only tracing inside this function (trace_boundary/trace_failure
    calls, wrapped in a try/except that unconditionally re-raises) to prove the
    exact failure boundary — that tracing must never swallow an exception or alter
    the return value, so this test checks BEHAVIOR (still raises, still returns the
    same shape), not literal absence of a try/except keyword."""
    import sqlite3

    from src.ops.walkback_health import recover_stalled_running_jobs as rsj

    # Behavior check 1: on success, same return shape as always.
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE wt_walkback_queue (mint TEXT PRIMARY KEY, status TEXT, "
        "attempts INTEGER, started_at INTEGER, updated_at INTEGER, enqueued_at INTEGER, last_error TEXT)"
    )
    conn.commit()
    assert rsj(conn, max_attempts=3, stalled_after_seconds=180) == {"requeued": 0, "failed": 0}
    conn.close()

    # Behavior check 2: a genuine failure still propagates (tracing must re-raise,
    # never swallow) — confirms the diagnostic wrapper added for X21D.3 is transparent.
    conn2 = sqlite3.connect(":memory:")  # no table created — first SELECT will raise
    with pytest.raises(sqlite3.OperationalError):
        rsj(conn2, max_attempts=3, stalled_after_seconds=180)
    conn2.close()
