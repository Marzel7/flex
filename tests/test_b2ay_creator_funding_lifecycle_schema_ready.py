import asyncio
import inspect
import sqlite3


def test_worker_initializes_lifecycle_schema_before_concurrent_loop():
    from src.core import creator_funding_worker as worker

    source = inspect.getsource(worker._run_loop_async)
    initialize_at = source.index("initialize_schema, DB_PATH")
    loop_at = source.index("while not _STOP:")

    assert initialize_at < loop_at


def test_worker_steady_state_wrapper_forces_schema_ready(monkeypatch):
    from src.core import creator_funding_worker as worker

    captured = {}

    def fake(*args, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(worker, "_record_event_fail_open", fake)
    monkeypatch.setattr(worker, "_LIFECYCLE_SCHEMA_READY", True)

    assert worker.record_event_fail_open("db", creator="c", mint="m", source=None, event="E")
    assert captured["schema_ready"] is True


def test_schema_ready_event_and_gap_paths_never_run_ddl(tmp_path, monkeypatch):
    from src.core import creator_funding_lifecycle as lifecycle

    db_path = str(tmp_path / "lifecycle.db")
    lifecycle.initialize_schema(db_path)

    def fail_schema(conn):
        raise AssertionError("steady-state path must not run lifecycle DDL")

    monkeypatch.setattr(lifecycle, "ensure_schema", fail_schema)

    assert lifecycle.record_event_fail_open(
        db_path,
        creator="creator",
        mint="mint-1",
        source="crq_worker",
        event="STARTED",
        schema_ready=True,
    )

    lifecycle._record_gap_best_effort(
        db_path,
        creator="creator",
        mint="mint-2",
        event="FAILED",
        occurred_at=1,
        error=RuntimeError("synthetic"),
        schema_ready=True,
    )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM creator_funding_lifecycle_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM creator_funding_lifecycle_gaps").fetchone()[0] == 1


def test_external_callers_retain_schema_assurance(tmp_path):
    from src.core import creator_funding_lifecycle as lifecycle

    db_path = str(tmp_path / "external.db")

    assert lifecycle.record_event_fail_open(
        db_path,
        creator="creator",
        mint="mint",
        source=None,
        event="STARTED",
    )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM creator_funding_lifecycle_events").fetchone()[0] == 1


def test_concurrent_schema_ready_events_do_not_invoke_schema(tmp_path, monkeypatch):
    from src.core import creator_funding_lifecycle as lifecycle

    db_path = str(tmp_path / "concurrent.db")
    lifecycle.initialize_schema(db_path)

    monkeypatch.setattr(
        lifecycle,
        "ensure_schema",
        lambda conn: (_ for _ in ()).throw(AssertionError("unexpected DDL")),
    )

    async def run():
        results = await asyncio.gather(*(
            asyncio.to_thread(
                lifecycle.record_event_fail_open,
                db_path,
                creator=f"creator-{i}",
                mint=f"mint-{i}",
                source="crq_worker",
                event="STARTED",
                schema_ready=True,
            )
            for i in range(8)
        ))
        assert all(results)

    asyncio.run(run())
