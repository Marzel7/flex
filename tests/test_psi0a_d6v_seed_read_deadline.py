from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import src.core.pumpfun_curve_listener as listener_module


class FakeConnection:
    def __init__(self, events, rows=(), mode="success"):
        self.events = events
        self.rows = list(rows)
        self.mode = mode
        self.progress_handler = None

    def set_progress_handler(self, callback, instructions):
        self.events.append(("progress", callback is not None, instructions))
        self.progress_handler = callback

    def execute(self, sql):
        self.events.append(("execute", " ".join(sql.split())))
        if self.mode == "deadline":
            assert self.progress_handler() == 1
            raise listener_module.sqlite3.OperationalError("interrupted")
        if self.mode == "query_exception":
            raise listener_module.sqlite3.OperationalError("fixture query error")
        return self

    def fetchall(self):
        self.events.append(("fetchall",))
        return self.rows


class FakeWebSocket:
    def __init__(self, events):
        self.events = events
        self.payloads = []

    async def send(self, payload):
        self.events.append(("send",))
        self.payloads.append(payload)


async def _run(monkeypatch, *, rows=(), mode="success"):
    events = []
    connection = FakeConnection(events, rows=rows, mode=mode)

    @contextmanager
    def managed(*_args, **kwargs):
        events.append(("open", kwargs))
        try:
            yield connection
        finally:
            events.append(("close",))

    async def immediate(function, *args, **kwargs):
        return function(*args, **kwargs)

    clock_values = iter([0.0, 3.0] if mode == "deadline" else [0.0])
    monkeypatch.setattr(listener_module, "managed_db_connect", managed)
    monkeypatch.setattr(listener_module.asyncio, "to_thread", immediate)
    monkeypatch.setattr(
        listener_module,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock_values)),
    )

    websocket = FakeWebSocket(events)
    tracked = set()
    await listener_module.PumpFunCurveListener._seed_trade_subscriptions(
        object(), websocket, tracked
    )
    return events, websocket, tracked


@pytest.mark.asyncio
async def test_success_preserves_query_order_batching_and_cleanup(monkeypatch):
    rows = [(f"mint-{index}",) for index in range(205)]
    events, websocket, tracked = await _run(monkeypatch, rows=rows)
    names = [event[0] for event in events]
    assert names[:5] == ["open", "progress", "execute", "fetchall", "progress"]
    assert events[1] == ("progress", True, 1000)
    assert events[4] == ("progress", False, 0)
    assert names.index("close") < names.index("send")
    assert len(websocket.payloads) == 3
    assert tracked == {row[0] for row in rows}
    sql = next(event[1] for event in events if event[0] == "execute")
    assert "source_platform = 'pumpfun'" in sql
    assert "lifecycle_stage = 'bonding_curve'" in sql
    assert "LIMIT 200" in sql


@pytest.mark.asyncio
async def test_deadline_is_named_removes_handler_closes_and_sends_nothing(monkeypatch):
    messages = []
    monkeypatch.setattr(listener_module, "log_print", lambda message, **_kwargs: messages.append(message))
    events, websocket, tracked = await _run(monkeypatch, mode="deadline")
    assert ("progress", False, 0) in events
    assert events[-1] == ("close",)
    assert websocket.payloads == []
    assert tracked == set()
    assert any("PSI0A_D6V_SEED_READ_DEADLINE_EXCEEDED" in message for message in messages)


@pytest.mark.asyncio
async def test_query_exception_removes_handler_closes_and_sends_nothing(monkeypatch):
    events, websocket, tracked = await _run(monkeypatch, mode="query_exception")
    assert ("progress", False, 0) in events
    assert events[-1] == ("close",)
    assert websocket.payloads == []
    assert tracked == set()


def test_source_keeps_two_second_deadline_and_single_original_select():
    import inspect

    source = inspect.getsource(
        listener_module.PumpFunCurveListener._seed_trade_subscriptions
    )
    assert "time.monotonic() + 2.0" in source
    assert "set_progress_handler(_progress_handler, 1000)" in source
    assert "set_progress_handler(None, 0)" in source
    assert source.count("SELECT mint FROM token_analysis") == 1
    assert "await asyncio.to_thread(_seed_read)" in source
