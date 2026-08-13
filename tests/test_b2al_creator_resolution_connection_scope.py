import inspect
import sqlite3


def test_hot_connection_open_does_not_mutate_journal_mode(monkeypatch):
    from src.core import creator_resolution_queue as queue

    sentinel = object()
    calls = []

    def fake_db_connect(path, **kwargs):
        calls.append((path, kwargs))
        return sentinel

    monkeypatch.setattr(queue, "db_connect", fake_db_connect)

    assert queue.connect("queue.db", timeout=7) is sentinel
    assert calls == [
        ("queue.db", {"timeout": 7, "row_factory": sqlite3.Row})
    ]
    assert "journal_mode" not in inspect.getsource(queue.connect).lower()


def test_write_and_read_contexts_use_managed_tracked_connections():
    from src.core import creator_resolution_queue as queue

    write_source = inspect.getsource(queue._db)
    read_source = inspect.getsource(queue._read_db)

    assert "managed_db_connect" in write_source
    assert "read_only=True" not in write_source
    assert "managed_db_connect" in read_source
    assert "read_only=True" in read_source


def test_process_queue_selects_read_only_then_claims_in_short_write_scope():
    from src.core import creator_resolution_queue as queue

    source = inspect.getsource(queue.process_queue)
    selection = source.index("with _read_db(db_path) as conn:")
    select_sql = source.index("SELECT mint, attempts, source, priority", selection)
    claim_scope = source.index("with _db(db_path) as conn:", select_sql)
    claim_sql = source.index("SET status='running'", claim_scope)
    eligibility_recheck = source.index("AND status IN ('pending','retry')", claim_sql)

    assert selection < select_sql < claim_scope < claim_sql < eligibility_recheck
    assert "rows = claimed_rows" in source


def test_priority_check_uses_read_only_scope():
    from src.core import creator_resolution_queue as queue

    source = inspect.getsource(queue.process_queue)
    assert "with _read_db(db_path) as _pc:" in source


def test_empty_queue_processes_with_split_connection_scopes(tmp_path):
    from src.core import creator_resolution_queue as queue

    result = queue.process_queue(str(tmp_path / "queue.db"), limit=2)

    assert result == {
        "status": "ok",
        "processed": 0,
        "resolved": 0,
        "failed": 0,
        "skipped": 0,
        "funding_enqueued": 0,
        "errors": [],
    }
