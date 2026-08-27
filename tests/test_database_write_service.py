import sqlite3
import threading
import time

import pytest

from src.core.database_write_service import (
    DatabaseWriteService,
    NestedDatabaseWriteError,
    PRIORITY_P0_CRITICAL_INGESTION,
)


def _database(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE values_table(value INTEGER)")


def test_database_selector_owns_independent_write_lanes(tmp_path):
    first, second = tmp_path / "first.db", tmp_path / "second.db"
    _database(first); _database(second)
    service = DatabaseWriteService()
    service.register_database("first", str(first))
    service.register_database("second", str(second))
    barrier = threading.Barrier(2)

    def write(database, value):
        service.submit(database, "parallel-write", lambda conn: (
            barrier.wait(timeout=2),
            conn.execute("INSERT INTO values_table VALUES(?)", (value,)),
        ))

    threads = [
        threading.Thread(target=write, args=("first", 1)),
        threading.Thread(target=write, args=("second", 2)),
    ]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=3)
    with sqlite3.connect(first) as conn:
        assert conn.execute("SELECT value FROM values_table").fetchone()[0] == 1
    with sqlite3.connect(second) as conn:
        assert conn.execute("SELECT value FROM values_table").fetchone()[0] == 2


def test_same_database_serializes_and_exposes_owner_and_waiter(tmp_path):
    path = tmp_path / "ops.db"
    _database(path)
    service = DatabaseWriteService()
    service.register_database("operations", str(path))
    active = threading.Event()
    release = threading.Event()

    def first(conn):
        active.set()
        assert release.wait(timeout=2)
        conn.execute("INSERT INTO values_table VALUES(1)")

    one = threading.Thread(target=lambda: service.submit("operations", "first", first))
    two = threading.Thread(target=lambda: service.submit(
        "operations", "second", lambda conn: conn.execute(
            "INSERT INTO values_table VALUES(2)"
        )
    ))
    one.start(); assert active.wait(timeout=2)
    two.start()
    deadline = time.monotonic() + 2
    try:
        while True:
            diagnostics = service.diagnostics("operations")
            if any(row["command"] == "second"
                   for row in diagnostics["waiting_commands"]):
                break
            assert time.monotonic() < deadline
            threading.Event().wait(0.001)
        assert diagnostics["current_writer"]["command"] == "first"
    finally:
        release.set()
    one.join(timeout=3); two.join(timeout=3)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM values_table ORDER BY value").fetchall() == [(1,), (2,)]


def test_submit_forwards_explicit_priority_to_cross_process_owner(tmp_path):
    path = tmp_path / "ops.db"
    _database(path)
    service = DatabaseWriteService()
    service.register_database("operations", str(path))

    service.submit(
        "operations", "critical-ingest-write",
        lambda conn: conn.execute("INSERT INTO values_table VALUES(1)"),
        priority=PRIORITY_P0_CRITICAL_INGESTION,
    )

    record = service.telemetry(database="operations")[-1]
    assert record["priority"] == PRIORITY_P0_CRITICAL_INGESTION


def test_failure_rolls_back_and_records_transaction(tmp_path):
    path = tmp_path / "ops.db"
    _database(path)
    service = DatabaseWriteService()
    service.register_database("operations", str(path))

    def fail(conn):
        conn.execute("INSERT INTO values_table VALUES(1)")
        raise RuntimeError("stop")

    with pytest.raises(RuntimeError, match="stop"):
        service.submit("operations", "failing-governance-write", fail)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM values_table").fetchone()[0] == 0
    record = service.telemetry(database="operations")[-1]
    assert record["rollback"] is True
    assert record["status"] == "ROLLED_BACK"
    assert record["transaction_id"]
    assert record["duration_ms"] >= 0


def test_callback_cannot_split_service_owned_transaction(tmp_path):
    path = tmp_path / "ops.db"
    _database(path)
    service = DatabaseWriteService()
    service.register_database("operations", str(path))

    def fail_after_legacy_commit(conn):
        conn.execute("INSERT INTO values_table VALUES(1)")
        conn.commit()
        conn.execute("INSERT INTO values_table VALUES(2)")
        raise RuntimeError("rollback-all")

    with pytest.raises(RuntimeError, match="rollback-all"):
        service.submit("operations", "legacy-helper", fail_after_legacy_commit)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM values_table").fetchone()[0] == 0


def test_nested_same_database_write_fails_immediately_and_rolls_back(tmp_path):
    path = tmp_path / "ops.db"
    _database(path)
    service = DatabaseWriteService()
    service.register_database("operations", str(path))

    def outer(conn):
        conn.execute("INSERT INTO values_table VALUES(1)")
        service.submit(
            "operations", "inner-governance-write",
            lambda inner: inner.execute("INSERT INTO values_table VALUES(2)"),
        )

    started = time.monotonic()
    with pytest.raises(NestedDatabaseWriteError) as exc:
        service.submit("operations", "operator-promotion-approve", outer)
    assert time.monotonic() - started < 1
    assert exc.value.database == "operations"
    assert exc.value.outer_command == "operator-promotion-approve"
    assert exc.value.inner_command == "inner-governance-write"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM values_table").fetchone()[0] == 0


def test_nested_cross_database_write_is_rejected_without_partial_commit(tmp_path):
    operations, live = tmp_path / "operations.db", tmp_path / "live.db"
    _database(operations); _database(live)
    service = DatabaseWriteService()
    service.register_database("operations", str(operations))
    service.register_database("live", str(live))

    def outer(conn):
        conn.execute("INSERT INTO values_table VALUES(1)")
        service.submit(
            "live", "nested-live-write",
            lambda inner: inner.execute("INSERT INTO values_table VALUES(2)"),
        )

    with pytest.raises(NestedDatabaseWriteError) as exc:
        service.submit("operations", "outer-operations-write", outer)
    assert exc.value.database == "live"
    assert exc.value.outer_database == "operations"
    for path in (operations, live):
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM values_table").fetchone()[0] == 0


def test_tracked_connection_cannot_reacquire_lane_inside_callback(tmp_path):
    from src.utils.db_locking import db_connect

    path = tmp_path / "ops.db"
    _database(path)
    service = DatabaseWriteService()
    service.register_database("operations", str(path))

    def outer(conn):
        conn.execute("INSERT INTO values_table VALUES(1)")
        with db_connect(str(path)) as nested:
            nested.execute("INSERT INTO values_table VALUES(2)")

    with pytest.raises(NestedDatabaseWriteError) as exc:
        service.submit("operations", "operator-promotion-approve", outer)
    assert exc.value.outer_command == "operator-promotion-approve"
    assert "test_database_write_service.py" in exc.value.inner_command
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM values_table").fetchone()[0] == 0


def test_release_write_lane_survives_telemetry_failure(tmp_path, monkeypatch):
    # Regression for a leak found live in walkback_worker: a single
    # exception inside TrackedConnection._release_write_lane()'s telemetry
    # block (database_write_service.record_external) used to skip
    # release_write_lease() entirely while still clearing
    # self._cross_process_lease, permanently leaking both the cross-process
    # file lock and the thread-local re-entrancy guard
    # (database_write_service._thread_write_lease.owner). Every subsequent
    # write on that thread then raised NestedDatabaseWriteError forever,
    # even across brand-new connection objects, until process restart.
    from src.utils import db_locking
    from src.core import database_write_service as write_module

    monkeypatch.setattr(db_locking, "_DB_WRITE_SERIALIZE", True)

    path = tmp_path / "ops.db"
    _database(path)

    monkeypatch.setattr(
        write_module.database_write_service, "record_external",
        lambda record: (_ for _ in ()).throw(RuntimeError("telemetry boom")),
    )

    conn = db_locking.db_connect(str(path))
    conn.execute("INSERT INTO values_table VALUES(1)")
    conn.commit()  # must not raise, and must fully release the lease
    conn.close()

    assert getattr(write_module._thread_write_lease, "owner", None) is None

    # A second, brand-new connection on the same thread must be able to
    # acquire the write lane immediately -- proving nothing was leaked.
    conn2 = db_locking.db_connect(str(path))
    conn2.execute("INSERT INTO values_table VALUES(2)")
    conn2.commit()
    conn2.close()

    with sqlite3.connect(path) as check:
        assert check.execute("SELECT COUNT(*) FROM values_table").fetchone()[0] == 2


def test_cascade_operations_write_uses_database_write_service(tmp_path, monkeypatch):
    from src.core import database_write_service as write_module
    from src.core import ws_cascade_store

    path = tmp_path / "ops.db"
    _database(path)
    managed = DatabaseWriteService()
    calls = []

    class AuditService:
        def register_database(self, database, database_path):
            calls.append(("register", database, database_path))
            managed.register_database(database, database_path)

        def submit(self, database, command, transaction):
            calls.append(("submit", database, command))
            return managed.submit(database, command, transaction)

    monkeypatch.setattr(write_module, "database_write_service", AuditService())
    monkeypatch.setattr(ws_cascade_store, "OPS_DB_PATH", str(path))
    ws_cascade_store.operations_write(
        "ws-cascade-test",
        lambda conn: conn.execute("INSERT INTO values_table VALUES(7)"),
    )

    assert [call[0] for call in calls] == ["register", "submit"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM values_table").fetchone()[0] == 7
