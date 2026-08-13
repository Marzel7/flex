import sqlite3


def test_ready_schema_skips_write_connection(tmp_path, monkeypatch):
    from src.core import creator_resolution_queue as queue

    db_path = str(tmp_path / "ready.db")
    queue.initialize_schema(db_path)

    def fail_write_connection(*args, **kwargs):
        raise AssertionError("ready schema must not enter the write lane")

    monkeypatch.setattr(queue, "_db", fail_write_connection)

    assert queue.schema_ready(db_path) is True
    queue.initialize_schema(db_path)


def test_incomplete_schema_falls_through_to_migration(tmp_path, monkeypatch):
    from src.core import creator_resolution_queue as queue

    db_path = str(tmp_path / "incomplete.db")
    queue.initialize_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP INDEX idx_creator_resolution_queue_updated")

    calls = 0
    original = queue.ensure_schema

    def counted(conn):
        nonlocal calls
        calls += 1
        return original(conn)

    monkeypatch.setattr(queue, "ensure_schema", counted)

    assert queue.schema_ready(db_path) is False
    queue.initialize_schema(db_path)
    assert calls == 1
    assert queue.schema_ready(db_path) is True


def test_ready_shape_with_pending_backfill_is_not_ready(tmp_path):
    from src.core import creator_resolution_queue as queue

    db_path = str(tmp_path / "backfill.db")
    queue.initialize_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO creator_resolution_queue
                (mint, next_attempt_at, created_at, updated_at)
            VALUES ('mint-1', 0, 0, 0)
            """
        )

    assert queue.schema_ready(db_path) is False
    queue.initialize_schema(db_path)
    assert queue.schema_ready(db_path) is True


def test_missing_database_fails_closed_to_migration(tmp_path):
    from src.core import creator_resolution_queue as queue

    db_path = str(tmp_path / "missing.db")

    assert queue.schema_ready(db_path) is False
    queue.initialize_schema(db_path)
    assert queue.schema_ready(db_path) is True
