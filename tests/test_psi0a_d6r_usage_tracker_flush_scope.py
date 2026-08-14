import copy

import pytest

import src.metrics.usage_tracker as usage_tracker


class EndFlush(RuntimeError):
    pass


class FakeConnection:
    def __init__(self, events, fail_on=None):
        self.events = events
        self.fail_on = fail_on
        self.execute_count = 0

    def execute(self, sql, parameters=()):
        self.execute_count += 1
        self.events.append(("execute", " ".join(sql.split()), list(parameters)))
        if self.fail_on in {"insert", "insert_and_rollback"}:
            raise RuntimeError("injected insert failure")

    def commit(self):
        self.events.append(("commit",))
        if self.fail_on == "commit":
            raise RuntimeError("injected commit failure")

    def rollback(self):
        self.events.append(("rollback",))
        if self.fail_on == "insert_and_rollback":
            raise RuntimeError("injected rollback failure")

    def close(self):
        self.events.append(("close",))


def _item(table="wss_metrics"):
    return {
        "_table": table,
        "ts": 1.0,
        "subscription": "pumpportal",
        "source_file": "fixture",
        "msg_count": 1,
        "est_bytes": 2,
        "note": None,
    }


def _one_cycle(monkeypatch, batch, fail_on=None, events=None):
    events = [] if events is None else events
    connection = FakeConnection(events, fail_on=fail_on)
    sleeps = 0

    def sleep(_seconds):
        nonlocal sleeps
        sleeps += 1
        events.append(("sleep",))
        if sleeps > 1:
            raise EndFlush()

    def connect(*_args, **kwargs):
        events.append(("connect", kwargs))
        return connection

    monkeypatch.setattr(usage_tracker.time, "sleep", sleep)
    monkeypatch.setattr(usage_tracker.sqlite3, "connect", connect)
    monkeypatch.setattr(usage_tracker, "_queue", copy.deepcopy(batch))

    with pytest.raises(EndFlush):
        usage_tracker._flush()
    return events


def test_success_preserves_insert_commit_and_close_order(monkeypatch):
    events = _one_cycle(monkeypatch, [_item()])
    names = [event[0] for event in events]
    assert names == ["sleep", "connect", "execute", "commit", "close", "sleep"]
    assert events[1][1] == {"timeout": 10, "priority": 3}
    assert events[2][1].startswith("INSERT INTO wss_metrics")


def test_empty_batch_does_not_open_connection(monkeypatch):
    events = _one_cycle(monkeypatch, [])
    assert [event[0] for event in events] == ["sleep", "sleep"]


@pytest.mark.parametrize("fail_on", ["insert", "commit"])
def test_write_or_commit_exception_rolls_back_then_closes(monkeypatch, fail_on):
    events = _one_cycle(monkeypatch, [_item()], fail_on=fail_on)
    names = [event[0] for event in events]
    assert "rollback" in names
    assert names.index("rollback") < names.index("close")
    assert names.count("close") == 1


def test_rollback_exception_still_closes(monkeypatch):
    events = _one_cycle(monkeypatch, [_item()], fail_on="insert_and_rollback")
    names = [event[0] for event in events]
    assert names.index("rollback") < names.index("close")
    assert names.count("close") == 1


def test_batch_is_removed_under_lock_before_connection_open(monkeypatch):
    events = []

    class ObservedLock:
        def __enter__(self):
            events.append(("lock_enter",))

        def __exit__(self, *_args):
            events.append(("lock_exit",))

    monkeypatch.setattr(usage_tracker, "_lock", ObservedLock())
    _one_cycle(monkeypatch, [_item()], events=events)
    names = [event[0] for event in events]
    assert names.index("lock_exit") < names.index("connect")


def test_steady_state_flush_contains_no_journal_or_synchronous_pragma():
    import inspect

    source = inspect.getsource(usage_tracker._flush)
    assert "journal_mode" not in source
    assert "synchronous" not in source
    assert "time.sleep(5)" in source
    assert "priority=3" in source
