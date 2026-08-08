"""X78.10 -- pumpfun_curve_listener._ensure_db() retry on cross-process lock
timeout.

Caught live during X78.10 production validation, ~10 minutes into the
authoritative soak: the listener's one-shot, unretried startup DDL
(_ensure_db) hit CrossProcessDatabaseWriteTimeout and crashed the whole
process; supervisord's immediate restart landed in the same contention,
producing a ~2min-cadence crash-loop (13 restarts in 21 minutes). X78.9
correctly turned what used to be an indefinite blocking flock() hang into a
bounded 60s timeout -- but nothing at the caller level absorbed that bounded
failure, so a single transient contention event became a fatal startup
error instead of something worth waiting out.

Fix: _ensure_db() is now a thin retry wrapper (bounded exponential backoff
+ jitter, same convention as creator_funding_worker.py's
_retry_on_nested_write) around the renamed _ensure_db_once() (the original,
unchanged DDL body). Retries ONLY CrossProcessDatabaseWriteTimeout -- any
other exception (schema error, programming error, etc.) must still fail
immediately with no retry, exactly as before this change.

These tests exercise the retry wrapper directly against a lightweight stand-
in object (SimpleNamespace-like) rather than constructing a real
PumpFunCurveListener, whose __init__ has heavy DB/websocket/RPC side
effects unrelated to this retry logic -- the same approach used by
test_x78_9_price_worker_singleton.py and test_x78_10_price_service_singleton.py
for their respective heavy constructors.
"""
import time

import pytest

from src.core.database_write_service import CrossProcessDatabaseWriteTimeout
from src.core.pumpfun_curve_listener import PumpFunCurveListener


class _FakeListener:
    """Bare object carrying just what _ensure_db (unbound method, called
    against self) needs: the retry-budget class attributes and an
    _ensure_db_once to stand in for the real DDL body."""
    _ENSURE_DB_RETRY_MAX_ATTEMPTS = PumpFunCurveListener._ENSURE_DB_RETRY_MAX_ATTEMPTS
    _ENSURE_DB_RETRY_BASE_SECONDS = 0.01  # fast for tests; shape under test is retry COUNT/exception handling, not real timing

    def __init__(self, ensure_db_once_fn):
        self._ensure_db_once = ensure_db_once_fn


def _make_timeout(wait_seconds=60.0, owner_command="price_service.py:339 in _get_conn"):
    return CrossProcessDatabaseWriteTimeout(
        database="tracked", lock_path="/fake/path", waiting_pid=1,
        waiting_thread="MainThread", command="pumpfun_curve_listener.py:_ensure_db",
        wait_seconds=wait_seconds,
        current_owner={"command": owner_command, "process_pid": 999},
    )


def test_transient_timeouts_then_success_within_budget(monkeypatch):
    """First N acquisitions time out (N < retry budget), a later attempt
    succeeds -- startup must complete, not crash."""
    monkeypatch.setattr(time, "sleep", lambda s: None)  # don't actually wait in tests

    calls = {"n": 0}

    def flaky_ensure_db_once():
        calls["n"] += 1
        if calls["n"] <= 3:
            raise _make_timeout()
        return "schema-ready"

    fake = _FakeListener(flaky_ensure_db_once)
    PumpFunCurveListener._ensure_db(fake)  # must not raise

    assert calls["n"] == 4, "expected 3 failed attempts + 1 success"


def test_retry_budget_exhausted_raises_the_timeout(monkeypatch):
    """If contention never clears within the retry budget, the ORIGINAL
    CrossProcessDatabaseWriteTimeout must still propagate (startup fails
    loudly) -- not swallowed, not converted to a different error, not
    retried forever."""
    monkeypatch.setattr(time, "sleep", lambda s: None)

    calls = {"n": 0}

    def always_timing_out():
        calls["n"] += 1
        raise _make_timeout(wait_seconds=60.0 + calls["n"])

    fake = _FakeListener(always_timing_out)

    with pytest.raises(CrossProcessDatabaseWriteTimeout):
        PumpFunCurveListener._ensure_db(fake)

    assert calls["n"] == fake._ENSURE_DB_RETRY_MAX_ATTEMPTS + 1, (
        "expected exactly max_attempts+1 calls (initial + all retries) before giving up"
    )


def test_non_timeout_exception_fails_immediately_no_retry():
    """A genuine schema/programming error (anything that is NOT
    CrossProcessDatabaseWriteTimeout) must fail on the FIRST attempt with
    zero retries -- retrying schema errors or arbitrary exceptions would be
    wrong (they are not proven-transient contention, unlike the lock
    timeout class)."""
    calls = {"n": 0}

    def broken_schema():
        calls["n"] += 1
        raise sqlite3_operational_error()

    def sqlite3_operational_error():
        import sqlite3
        return sqlite3.OperationalError("near \"CRAETE\": syntax error")

    fake = _FakeListener(broken_schema)

    with pytest.raises(Exception):
        PumpFunCurveListener._ensure_db(fake)

    assert calls["n"] == 1, "a non-timeout exception must not be retried"


def test_zero_retries_needed_on_first_success(monkeypatch):
    """The common case: no contention at all, _ensure_db_once succeeds
    immediately, no sleep/backoff invoked."""
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    calls = {"n": 0}

    def succeeds_immediately():
        calls["n"] += 1
        return "ok"

    fake = _FakeListener(succeeds_immediately)
    PumpFunCurveListener._ensure_db(fake)

    assert calls["n"] == 1
    assert slept == [], "no retry/backoff should occur when there's no contention"
