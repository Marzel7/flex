"""Offline entrypoint checks: no provider calls or production singleton lock."""

import asyncio
import builtins
import fcntl
import threading
from pathlib import Path

from src.core import pumpfun_curve_listener as listener_module


def test_entrypoint_can_skip_rpc_metrics_side_process(monkeypatch):
    calls = []

    async def fake_main():
        calls.append("main")

    real_run = asyncio.run

    def fake_run(coro):
        real_run(coro)

    monkeypatch.setenv("LISTENER_RPC_METRICS_API_START_ENABLED", "0")
    monkeypatch.setattr(listener_module, "start_rpc_metrics_api", lambda: calls.append("rpc_metrics"))
    monkeypatch.setattr(listener_module, "_start_wal_checkpoint_worker", lambda *a, **k: calls.append("wal"))
    monkeypatch.setattr(listener_module, "main", fake_main)
    monkeypatch.setattr(listener_module.asyncio, "run", fake_run)

    listener_module.run_listener_entrypoint()
    assert calls == ["wal", "main"]


def test_entrypoint_preserves_existing_rpc_metrics_default(monkeypatch):
    calls = []

    async def fake_main():
        calls.append("main")

    real_run = asyncio.run
    monkeypatch.delenv("LISTENER_RPC_METRICS_API_START_ENABLED", raising=False)
    monkeypatch.setattr(listener_module, "start_rpc_metrics_api", lambda: calls.append("rpc_metrics"))
    monkeypatch.setattr(listener_module, "_start_wal_checkpoint_worker", lambda *a, **k: calls.append("wal"))
    monkeypatch.setattr(listener_module, "main", fake_main)
    monkeypatch.setattr(listener_module.asyncio, "run", real_run)

    listener_module.run_listener_entrypoint()
    assert calls == ["rpc_metrics", "wal", "main"]


def test_main_uses_only_test_lock_and_releases_it(monkeypatch, tmp_path):
    events = []
    test_lock = tmp_path / "listener.lock"
    real_open = builtins.open

    def safe_open(path, *args, **kwargs):
        if str(path) == "/tmp/pumpfun_curve_listener.lock":
            return real_open(test_lock, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    class FakeSchemaThread:
        def start(self):
            events.append("schema_thread_not_started")

    real_thread = threading.Thread

    def safe_thread(*args, **kwargs):
        if kwargs.get("name") == "walkback-schema-startup":
            events.append("schema_thread_constructed")
            return FakeSchemaThread()
        return real_thread(*args, **kwargs)

    class FakeListener:
        async def listen(self):
            events.append("listen")

    def fake_flock(handle, operation):
        assert Path(handle.name) == test_lock
        events.append("lock" if operation == fcntl.LOCK_EX | fcntl.LOCK_NB else "unlock")

    monkeypatch.setattr(builtins, "open", safe_open)
    monkeypatch.setattr(fcntl, "flock", fake_flock)
    monkeypatch.setattr(threading, "Thread", safe_thread)
    monkeypatch.setattr(listener_module, "PumpFunCurveListener", FakeListener)
    monkeypatch.setattr(listener_module, "log_print", lambda *a, **k: None)

    asyncio.run(listener_module.main())
    assert events.index("lock") < events.index("listen") < events.index("unlock")
    assert test_lock.read_text().strip().isdigit()
