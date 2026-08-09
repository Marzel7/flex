"""X78.18 -- Listener Reconnect Isolation.

X78.17 established (docs/audits/x78_17_live_ingestion_rate_collapse.md)
that PumpPortal reconnect failures were frequently caused by database
write-lane contention, not by the provider connection itself. This
milestone's audit (docs/audits/x78_18_reconnect_dependency_audit.md)
found three concrete couplings between the reconnect/startup path and
database write availability:

1. The seed-subscription DB read inside listen_pumpportal_websocket()
   ran inline, before the message-receive loop, on every reconnect.
2. usage_tracker._ensure_started() called ensure_schema() unguarded --
   a failure there (raised synchronously from inside record_wss(), which
   the reconnect loop calls from its message handler) would propagate
   into the reconnect loop's own except block and count as a connection
   failure.
3. main()'s walkback-queue schema write blocked (database_write_service
   .submit() waits on a threading.Event) before listen()/gather() and
   PumpPortal connection could even begin.

These tests verify the fixes for (1) and (2) directly: the seeding work
is isolated as its own coroutine so a failure there can never raise into
the reconnect loop, and usage_tracker's one-time schema bootstrap is
failure-isolated so a CrossProcessDatabaseWriteTimeout there can never
propagate to the caller. (3) is a startup-path change (background
thread), verified by inspection/read rather than a unit test, since it
has no meaningful return-value contract to assert against beyond "does
not block the caller" -- covered narratively in the audit doc.
"""
import asyncio

import pytest

import src.metrics.usage_tracker as usage_tracker


# ---------------------------------------------------------------------------
# (2) usage_tracker._ensure_started() must never raise
# ---------------------------------------------------------------------------

def test_ensure_started_swallows_ensure_schema_failure(monkeypatch):
    """A CrossProcessDatabaseWriteTimeout (or any exception) raised from
    ensure_schema() must not propagate out of _ensure_started() -- this is
    called synchronously from record_wss()/record_webhook(), which the
    PumpPortal reconnect loop calls from inside its message handler. Before
    this fix, this exact call site could turn a transient DB write-lane
    stall into a counted reconnect failure with zero relation to the
    PumpPortal connection itself."""
    monkeypatch.setattr(usage_tracker, "_started", False)
    monkeypatch.setattr(usage_tracker, "_thread", None)

    def _boom():
        raise RuntimeError("CrossProcessDatabaseWriteTimeout (simulated)")

    monkeypatch.setattr(usage_tracker, "ensure_schema", _boom)
    # Prevent the real background flush thread from starting during the test
    monkeypatch.setattr(usage_tracker.threading, "Thread", lambda **kw: _NoopThread())

    usage_tracker._ensure_started()  # must not raise

    assert usage_tracker._started is True


def test_ensure_started_only_attempts_schema_once(monkeypatch):
    """_started is set before the attempt, so a failure doesn't cause
    ensure_schema() to be retried on every subsequent record_wss() call --
    matches the pre-existing once-per-process intent, only the failure mode
    changed."""
    monkeypatch.setattr(usage_tracker, "_started", False)
    monkeypatch.setattr(usage_tracker, "_thread", None)
    monkeypatch.setattr(usage_tracker.threading, "Thread", lambda **kw: _NoopThread())

    calls = {"n": 0}

    def _boom():
        calls["n"] += 1
        raise RuntimeError("simulated write-lane timeout")

    monkeypatch.setattr(usage_tracker, "ensure_schema", _boom)

    usage_tracker._ensure_started()
    usage_tracker._ensure_started()
    usage_tracker._ensure_started()

    assert calls["n"] == 1, "ensure_schema() must only be attempted once per process"


def test_record_wss_does_not_raise_when_schema_bootstrap_fails(monkeypatch):
    """End-to-end: record_wss() -- the exact call the reconnect loop's
    message handler makes -- must not raise even when the underlying
    one-time schema bootstrap fails."""
    monkeypatch.setattr(usage_tracker, "_started", False)
    monkeypatch.setattr(usage_tracker, "_thread", None)
    monkeypatch.setattr(usage_tracker.threading, "Thread", lambda **kw: _NoopThread())
    monkeypatch.setattr(
        usage_tracker, "ensure_schema",
        lambda: (_ for _ in ()).throw(RuntimeError("simulated contention")),
    )

    usage_tracker.record_wss("pumpportal_create", "pumpfun_curve_listener", msg_count=1, est_bytes=10)  # must not raise


class _NoopThread:
    def start(self):
        pass


# ---------------------------------------------------------------------------
# (1) Seed-subscription read must be isolated from the reconnect handler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_seed_trade_subscriptions_failure_does_not_propagate(monkeypatch):
    """_seed_trade_subscriptions() is launched via asyncio.create_task() in
    listen_pumpportal_websocket() specifically so a failure inside it (e.g.
    a DB read stalling/raising under write-lane contention) can never raise
    into the reconnect loop's own except block. This test exercises the
    method directly: it must swallow any internal failure and return
    normally, matching its documented best-effort contract."""
    from src.core.pumpfun_curve_listener import PumpFunCurveListener

    class _FakeWs:
        async def send(self, *_args, **_kwargs):
            raise AssertionError("send() should never be reached if the DB read fails")

    class _FakeListenerForSeed:
        pass

    # Patch the DB access inside the method's closure by patching the module-level
    # managed_db_connect it calls through.
    import src.core.pumpfun_curve_listener as listener_module

    class _BoomingContextManager:
        def __enter__(self):
            raise RuntimeError("simulated CrossProcessDatabaseWriteTimeout")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        listener_module, "managed_db_connect",
        lambda *a, **kw: _BoomingContextManager(),
    )

    # Bind the unbound method to a bare instance-like object (mirrors the
    # convention used in test_x78_10_listener_ensure_db_retry.py) -- the
    # method only touches `self` implicitly via nothing (no self.* reads),
    # so a minimal stand-in works.
    fake_self = _FakeListenerForSeed()
    tracked = set()

    await PumpFunCurveListener._seed_trade_subscriptions(fake_self, _FakeWs(), tracked)  # must not raise

    assert tracked == set(), "no mints should be tracked when the seed read fails"
