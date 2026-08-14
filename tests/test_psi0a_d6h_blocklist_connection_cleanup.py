import pytest

import src.core.pumpfun_curve_listener as listener


class FakeConnection:
    def __init__(self, *, row=None, fail_phase=None):
        self.row = row
        self.fail_phase = fail_phase
        self.closed = 0
        self.commits = 0
        self.statements = []

    def execute(self, sql, parameters=()):
        normalized = " ".join(sql.split())
        self.statements.append((normalized, parameters))
        if normalized.startswith("SELECT") and self.fail_phase == "SELECT":
            raise RuntimeError("injected SELECT failure")
        if normalized.startswith("UPDATE") and self.fail_phase == "UPDATE":
            raise RuntimeError("injected UPDATE failure")
        if normalized.startswith("INSERT") and self.fail_phase == "INSERT":
            raise RuntimeError("injected INSERT failure")
        return self

    def fetchone(self):
        return self.row

    def commit(self):
        self.commits += 1
        if self.fail_phase == "COMMIT":
            raise RuntimeError("injected COMMIT failure")

    def close(self):
        self.closed += 1


async def _run(monkeypatch, connection, *, fail_json=False):
    instance = object.__new__(listener.PumpFunCurveListener)

    async def immediate(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(listener, "db_connect", lambda *_args, **_kwargs: connection)
    monkeypatch.setattr(listener.asyncio, "to_thread", immediate)
    if fail_json:
        monkeypatch.setattr(
            listener.json,
            "dumps",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("injected JSON failure")
            ),
        )
    await instance._add_rug_creator_to_blocklist("mint-1", "creator-1")


@pytest.mark.asyncio
async def test_success_update_preserves_behavior_and_closes_once(monkeypatch):
    connection = FakeConnection(row=(1, '["mint-0"]'))
    await _run(monkeypatch, connection)
    assert connection.closed == 1
    assert connection.commits == 1
    update = next(item for item in connection.statements if item[0].startswith("UPDATE"))
    assert update[1][0] == 2
    assert update[1][2] == "MALICIOUS"
    assert "mint-1" in update[1][1]


@pytest.mark.asyncio
async def test_success_insert_preserves_behavior_and_closes_once(monkeypatch):
    connection = FakeConnection(row=None)
    await _run(monkeypatch, connection)
    assert connection.closed == 1
    assert connection.commits == 1
    insert = next(item for item in connection.statements if item[0].startswith("INSERT"))
    assert insert[1][0] == "creator-1"
    assert "mint-1" in insert[1][1]


@pytest.mark.asyncio
async def test_malformed_stored_json_falls_back_and_still_closes(monkeypatch):
    connection = FakeConnection(row=(1, "not-json"))
    await _run(monkeypatch, connection)
    assert connection.closed == 1
    assert connection.commits == 1
    update = next(item for item in connection.statements if item[0].startswith("UPDATE"))
    assert "mint-1" in update[1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "row", "fail_json"),
    [
        ("SELECT", None, False),
        (None, (1, "[]"), True),
        ("UPDATE", (1, "[]"), False),
        ("INSERT", None, False),
        ("COMMIT", (1, "[]"), False),
    ],
)
async def test_every_named_exception_path_closes_once(
    monkeypatch, phase, row, fail_json
):
    connection = FakeConnection(row=row, fail_phase=phase)
    await _run(monkeypatch, connection, fail_json=fail_json)
    assert connection.closed == 1
    assert connection.commits == (1 if phase == "COMMIT" else 0)


@pytest.mark.asyncio
async def test_missing_creator_remains_noop_without_connection(monkeypatch):
    instance = object.__new__(listener.PumpFunCurveListener)
    monkeypatch.setattr(
        listener,
        "db_connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("opened")),
    )
    assert await instance._add_rug_creator_to_blocklist("mint-1", None) is None
