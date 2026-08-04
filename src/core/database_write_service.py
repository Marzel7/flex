"""Shared, database-parameterised SQLite transaction ownership.

Feature code submits a transaction callback.  A database-specific worker owns
the connection, transaction, commit/rollback and telemetry.  Workers in other
processes coordinate through the same advisory lock file, so the database path
-- not the importing process -- defines the write lane.

There are deliberately no retries, sleeps, busy-timeout changes or journal-mode
changes here.  Contention is resolved before SQLite by transaction ownership.
"""
from __future__ import annotations

import collections
import fcntl
import json
import os
import queue
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


Transaction = Callable[[sqlite3.Connection], Any]
try:
    from src.utils.db_locking import _sqlite3_connect_orig as _native_connect
except Exception:
    _native_connect = sqlite3.dbapi2.connect


class _ServiceConnection(sqlite3.Connection):
    """Connection whose transaction boundary belongs exclusively to the service."""

    def commit(self) -> None:
        # Legacy helpers may still call commit after a statement group.  Inside
        # a managed callback that must not split the service-owned transaction.
        return None

    def rollback(self) -> None:
        # The worker performs the single rollback when the callback raises.
        return None

    def service_commit(self) -> None:
        sqlite3.Connection.commit(self)

    def service_rollback(self) -> None:
        sqlite3.Connection.rollback(self)


class DatabaseWriteLockError(RuntimeError):
    def __init__(self, diagnostics: dict[str, Any]):
        self.diagnostics = diagnostics
        super().__init__(
            "Managed database write lock failure: "
            + json.dumps(diagnostics, sort_keys=True, default=str)
        )


class NestedDatabaseWriteError(RuntimeError):
    """Raised before enqueueing work from an active managed transaction."""

    def __init__(
        self,
        *,
        database: str,
        outer_command: str,
        inner_command: str,
        outer_database: str,
    ) -> None:
        self.database = database
        self.outer_command = outer_command
        self.inner_command = inner_command
        self.outer_database = outer_database
        super().__init__(
            "NestedDatabaseWriteError: "
            f"database={database} "
            f"outer_command={outer_command} "
            f"inner_command={inner_command}"
        )


_thread_write_lease = threading.local()


@dataclass
class WriteLease:
    file: Any
    owner_path: str
    owner: dict[str, Any]


def acquire_write_lease(database: str, path: str, transaction_id: str, command: str) -> WriteLease:
    """Acquire the database-wide lane used by services and tracked connections."""
    real_path = os.path.realpath(path)
    outer = getattr(_thread_write_lease, "owner", None)
    if outer is not None:
        # Reject direct tracked-connection acquisition as well as nested
        # DatabaseWriteService.submit().  Allowing a second database here would
        # create an independently committed inner transaction.
        raise NestedDatabaseWriteError(
            database=database.split(":", 1)[0],
            outer_command=outer["command"],
            inner_command=command,
            outer_database=outer["database"],
        )
    lock_path = f"{real_path}.write.lock"
    owner_path = f"{lock_path}.owner"
    Path(lock_path).touch(exist_ok=True)
    lock_file = open(lock_path, "a+")
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    owner = {
        "database": database.split(":", 1)[0],
        "database_selector": database,
        "database_path": real_path,
        "writer_id": f"{os.getpid()}:{threading.current_thread().name}",
        "process_pid": os.getpid(),
        "thread": threading.current_thread().name,
        "transaction_id": transaction_id,
        "command": command,
        "acquired_at": time.time(),
    }
    with open(owner_path, "w", encoding="utf-8") as owner_file:
        json.dump(owner, owner_file, sort_keys=True)
    _thread_write_lease.owner = owner
    return WriteLease(lock_file, owner_path, owner)


def release_write_lease(lease: WriteLease) -> None:
    # The thread-local reentrancy guard must be cleared no matter what happens
    # below -- a failure in unlink/flock/close must never leave this thread
    # permanently unable to acquire a write lease again (observed: walkback_worker
    # stuck for hours after a single release-path OSError left _thread_write_lease
    # .owner set forever, so every later _ops_conn() write raised
    # NestedDatabaseWriteError against itself).
    try:
        try:
            os.unlink(lease.owner_path)
        except FileNotFoundError:
            pass
        finally:
            try:
                fcntl.flock(lease.file.fileno(), fcntl.LOCK_UN)
            finally:
                lease.file.close()
    finally:
        if getattr(_thread_write_lease, "owner", None) is lease.owner:
            del _thread_write_lease.owner


@dataclass
class _Command:
    database: str
    path: str
    command: str
    transaction: Transaction
    transaction_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    submitted_at: float = field(default_factory=time.time)
    submitted_monotonic: float = field(default_factory=time.monotonic)
    submitter_thread: str = field(default_factory=lambda: threading.current_thread().name)
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None


class DatabaseWriteService:
    """One local queue per database, backed by a cross-process write lane."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: dict[str, queue.Queue[_Command]] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._paths: dict[str, str] = {}
        self._telemetry: collections.deque[dict[str, Any]] = collections.deque(maxlen=2000)
        self._current: dict[str, dict[str, Any]] = {}
        self._waiting: dict[str, list[dict[str, Any]]] = {}
        self._active_transaction = threading.local()

    @staticmethod
    def _key(path: str) -> str:
        return os.path.realpath(path)

    def register_database(self, database: str, path: str) -> None:
        key = self._key(path)
        with self._lock:
            existing = self._paths.get(database)
            if existing and existing != key:
                raise ValueError(f"Database {database!r} is already registered at {existing}")
            self._paths[database] = key
            if key in self._workers and self._workers[key].is_alive():
                return
            work: queue.Queue[_Command] = queue.Queue()
            worker = threading.Thread(
                target=self._worker_loop,
                args=(key, work),
                daemon=True,
                name=f"db-writer-{database}",
            )
            self._queues[key] = work
            self._workers[key] = worker
            worker.start()

    def submit(
        self,
        database: str,
        command: str,
        transaction: Transaction,
        *,
        path: str | None = None,
    ) -> Any:
        if path is not None:
            self.register_database(database, path)
        with self._lock:
            registered = self._paths.get(database)
            if registered is None:
                raise KeyError(f"Database {database!r} has not been registered")
            work = self._queues[registered]
        outer = getattr(self._active_transaction, "value", None)
        if outer is not None:
            # Cross-database nesting is also rejected: an inner database could
            # commit before the outer callback rolls back, which would falsely
            # imply atomicity across two independent SQLite files.
            raise NestedDatabaseWriteError(
                database=database.split(":", 1)[0],
                outer_command=outer["command"],
                inner_command=command,
                outer_database=outer["database"],
            )
        item = _Command(database, registered, command, transaction)
        with self._lock:
            self._waiting.setdefault(registered, []).append({
                "transaction_id": item.transaction_id,
                "command": command,
                "submitted_at": item.submitted_at,
                "submitter_thread": item.submitter_thread,
            })
        work.put(item)
        item.event.wait()
        if item.error is not None:
            raise item.error
        return item.result

    def _worker_loop(self, path: str, work: queue.Queue[_Command]) -> None:
        while True:
            item = work.get()
            try:
                self._execute(item)
            except BaseException as exc:  # returned to the submitting request
                item.error = exc
            finally:
                with self._lock:
                    waiting = self._waiting.get(item.path, [])
                    self._waiting[item.path] = [
                        row for row in waiting
                        if row["transaction_id"] != item.transaction_id
                    ]
                item.event.set()
                work.task_done()

    def _execute(self, item: _Command) -> None:
        queue_wait_ms = (time.monotonic() - item.submitted_monotonic) * 1000.0
        writer_id = f"{os.getpid()}:{threading.current_thread().name}"
        record: dict[str, Any] = {
            "database": item.database.split(":", 1)[0],
            "database_selector": item.database,
            "database_path": item.path,
            "writer_id": writer_id,
            "process_pid": os.getpid(),
            "thread": threading.current_thread().name,
            "submitter_thread": item.submitter_thread,
            "transaction_id": item.transaction_id,
            "command": item.command,
            "queue_wait_ms": round(queue_wait_ms, 3),
            "begin_timestamp": None,
            "commit_timestamp": None,
            "rollback": False,
            "duration_ms": None,
            "rows_modified": 0,
            "status": "WAITING",
            "phase": "write-lane-acquired",
            "phase_elapsed_ms": 0.0,
            "phases": [],
        }
        started = time.monotonic()
        conn: sqlite3.Connection | None = None
        lease = acquire_write_lease(
            item.database, item.path, item.transaction_id, item.command
        )
        try:
            record["queue_wait_ms"] = round(
                (time.monotonic() - item.submitted_monotonic) * 1000.0, 3
            )
            owner = lease.owner
            with self._lock:
                waiting = self._waiting.get(item.path, [])
                self._waiting[item.path] = [
                    row for row in waiting
                    if row["transaction_id"] != item.transaction_id
                ]
                self._current[item.path] = dict(owner)
            try:
                conn = _native_connect(
                    item.path, timeout=10, check_same_thread=False,
                    factory=_ServiceConnection,
                )
                self._set_phase(record, started, "native-connection-opened")
                conn.row_factory = sqlite3.Row
                record["begin_timestamp"] = time.time()
                record["status"] = "ACTIVE"
                before = conn.total_changes
                self._set_phase(record, started, "begin-attempted")
                conn.execute("BEGIN")
                self._set_phase(record, started, "begin-acquired")
                self._active_transaction.value = {
                    "database": item.database.split(":", 1)[0],
                    "database_selector": item.database,
                    "database_path": item.path,
                    "command": item.command,
                    "transaction_id": item.transaction_id,
                    "record": record,
                    "started": started,
                }
                try:
                    item.result = item.transaction(conn)
                finally:
                    del self._active_transaction.value
                self._set_phase(record, started, "commit-attempted")
                conn.service_commit()
                self._set_phase(record, started, "commit-completed")
                record["rows_modified"] = conn.total_changes - before
                record["commit_timestamp"] = time.time()
                record["status"] = "COMMITTED"
            except BaseException as exc:
                record["rollback"] = True
                record["status"] = "ROLLED_BACK"
                record["error_type"] = type(exc).__name__
                record["error"] = str(exc)
                record["sqlite_error_code"] = getattr(exc, "sqlite_errorcode", None)
                record["sqlite_error_name"] = getattr(exc, "sqlite_errorname", None)
                record["phase_elapsed_ms"] = round(
                    (time.monotonic() - started) * 1000.0, 3
                )
                if conn is not None:
                    conn.service_rollback()
                if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower():
                    diagnostics = {
                        "database": record["database"],
                        "database_path": item.path,
                        "current_writer": dict(owner),
                        # This exception arose inside the current callback.  It
                        # is not evidence that the callback is waiting on itself;
                        # true managed nesting is rejected by submit() above.
                        "waiting_command": None,
                        "failed_command": item.command,
                        "managed_reentrancy_detected": False,
                        "phase": record["phase"],
                        "phase_elapsed_ms": record["phase_elapsed_ms"],
                        "sqlite_error_code": record["sqlite_error_code"],
                        "sqlite_error_name": record["sqlite_error_name"],
                        "transaction_id": item.transaction_id,
                        "transaction_age_seconds": round(
                            time.monotonic() - started, 3
                        ),
                    }
                    record["lock_diagnostics"] = diagnostics
                    record["error_type"] = "DatabaseWriteLockError"
                    record["error"] = json.dumps(diagnostics, sort_keys=True)
                    raise DatabaseWriteLockError(diagnostics) from exc
                raise
            finally:
                if conn is not None:
                    conn.close()
                record["duration_ms"] = round((time.monotonic() - started) * 1000.0, 3)
                with self._lock:
                    self._current.pop(item.path, None)
                    self._telemetry.append(dict(record))
        finally:
            release_write_lease(lease)

    @staticmethod
    def _set_phase(record: dict[str, Any], started: float, phase: str) -> None:
        elapsed = round((time.monotonic() - started) * 1000.0, 3)
        record["phase"] = phase
        record["phase_elapsed_ms"] = elapsed
        record["phases"].append({"phase": phase, "elapsed_ms": elapsed, "at": time.time()})

    def mark_phase(self, phase: str) -> None:
        """Mark a feature-level boundary on the active managed transaction."""
        active = getattr(self._active_transaction, "value", None)
        if active is None:
            raise RuntimeError(f"No active managed transaction for phase {phase!r}")
        self._set_phase(active["record"], active["started"], phase)

    def record_external(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._telemetry.append(dict(record))

    def telemetry(self, *, database: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._telemetry)
        if database is not None:
            rows = [row for row in rows if row["database"] == database
                    or row.get("database_selector") == database]
        return rows[-max(0, limit):]

    def diagnostics(self, database: str, waiting_command: str | None = None) -> dict[str, Any]:
        with self._lock:
            path = self._paths.get(database)
            if path is None:
                matches = {
                    candidate_path
                    for selector, candidate_path in self._paths.items()
                    if selector.split(":", 1)[0] == database
                }
                if len(matches) == 1:
                    path = matches.pop()
            current = dict(self._current.get(path, {})) if path else {}
            waiting = list(self._waiting.get(path, [])) if path else []
        if path and not current:
            try:
                with open(f"{path}.write.lock.owner", encoding="utf-8") as owner_file:
                    current = json.load(owner_file)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                current = {}
        age = None
        if current.get("acquired_at"):
            age = max(0.0, time.time() - float(current["acquired_at"]))
        return {
            "database": database,
            "database_path": path,
            "current_writer": current or None,
            "waiting_command": waiting_command,
            "waiting_commands": waiting,
            "transaction_age_seconds": round(age, 3) if age is not None else None,
        }


database_write_service = DatabaseWriteService()


def execute_script(conn: sqlite3.Connection, script: str) -> None:
    """Execute a DDL script without ``executescript``'s implicit commit."""
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            sql = statement.strip()
            if sql:
                conn.execute(sql)
            statement = ""
    if statement.strip():
        raise ValueError("Incomplete SQL statement in schema script")
