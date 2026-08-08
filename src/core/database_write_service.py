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
import errno
import fcntl
import json
import logging
import os
import queue
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_log = logging.getLogger("database_write_service")

# X78.9 -- the cross-process flock() bound. Matches the existing in-process
# _DB_WRITE_LOCK timeout (db_locking.py: DB_WRITE_LOCK.acquire(timeout=60),
# db_write_lock(), AsyncDbWriteLock) rather than inventing a new figure --
# a cross-process writer that outlives the in-process contract is already
# outside every timing assumption the rest of the write path makes.
CROSS_PROCESS_LOCK_TIMEOUT_SEC = float(
    os.environ.get("DB_CROSS_PROCESS_LOCK_TIMEOUT_SEC", "60")
)
# Poll interval for the LOCK_NB retry loop. Short enough that legitimate
# short-hold contention (the common case) isn't penalized with added
# latency, long enough not to spin the CPU while waiting out a real hold.
_LOCK_POLL_INTERVAL_SEC = 0.05


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


class CrossProcessDatabaseWriteTimeout(RuntimeError):
    """Raised when the cross-process flock() write lane could not be acquired
    within CROSS_PROCESS_LOCK_TIMEOUT_SEC. Distinct from a generic 'database
    is locked' SQLite error: this fires BEFORE SQLite is ever touched, at the
    advisory-file-lock layer, and always carries whatever owner metadata was
    on disk at the moment of timeout so the caller (and Mission Control) can
    tell who to look at."""

    def __init__(
        self,
        *,
        database: str,
        lock_path: str,
        waiting_pid: int,
        waiting_thread: str,
        command: str,
        wait_seconds: float,
        current_owner: dict[str, Any] | None,
    ) -> None:
        self.database = database
        self.lock_path = lock_path
        self.waiting_pid = waiting_pid
        self.waiting_thread = waiting_thread
        self.command = command
        self.wait_seconds = wait_seconds
        self.current_owner = current_owner
        super().__init__(
            "CrossProcessDatabaseWriteTimeout: "
            f"database={database} lock_path={lock_path} "
            f"waiting_pid={waiting_pid} waiting_thread={waiting_thread} "
            f"command={command} wait_seconds={round(wait_seconds, 3)} "
            f"current_owner={json.dumps(current_owner, sort_keys=True, default=str)}"
        )


# X78.9 Phase 17/18 -- in-memory cross-process lock-lane health, independent
# of the per-command _telemetry deque so a Mission Control read never has to
# scan/filter it. Small bounded history; timestamps only (no PII/large
# payloads) so 24h retention is cheap.
_CROSS_PROCESS_TIMEOUTS: collections.deque[dict[str, Any]] = collections.deque(maxlen=500)
_CROSS_PROCESS_TIMEOUTS_LOCK = threading.Lock()


def _record_cross_process_timeout(path: str, exc: "CrossProcessDatabaseWriteTimeout") -> None:
    with _CROSS_PROCESS_TIMEOUTS_LOCK:
        _CROSS_PROCESS_TIMEOUTS.append({
            "at": time.time(),
            "database_path": path,
            "database": exc.database,
            "waiting_pid": exc.waiting_pid,
            "waiting_thread": exc.waiting_thread,
            "command": exc.command,
            "wait_seconds": round(exc.wait_seconds, 3),
            "current_owner": exc.current_owner,
        })


def cross_process_lock_health(path: str | None = None, *, window_secs: int = 86400) -> dict[str, Any]:
    """Mission Control read: cross-process write-lane health (Phase 17/18).

    State is derived, not stored, from the current flock owner + recent
    timeout history:
      HEALTHY    -- no current holder, or a fresh/short-lived hold, no
                    recent timeouts.
      CONTENDED  -- a hold is approaching the timeout bound (DEGRADED-ish;
                    still succeeding, just slow).
      STALLED    -- at least one cross-process acquisition has actually
                    timed out within `window_secs`.
    """
    now = time.time()
    with _CROSS_PROCESS_TIMEOUTS_LOCK:
        recent = [row for row in _CROSS_PROCESS_TIMEOUTS if row["at"] > now - window_secs]
    if path is not None:
        real_path = os.path.realpath(path)
        recent = [row for row in recent if row["database_path"] == real_path]
        owner_path = f"{real_path}.write.lock.owner"
        current_owner = _read_owner_metadata(owner_path)
    else:
        current_owner = None

    held_seconds = None
    if current_owner and current_owner.get("acquired_at"):
        held_seconds = max(0.0, now - float(current_owner["acquired_at"]))

    if recent:
        state = "STALLED"
    elif held_seconds is not None and held_seconds > CROSS_PROCESS_LOCK_TIMEOUT_SEC * 0.5:
        state = "CONTENDED"
    else:
        state = "HEALTHY"

    last_timeout = recent[-1] if recent else None
    timeouts_1h = sum(1 for row in recent if row["at"] > now - 3600)
    return {
        "state": state,
        "current_owner": current_owner,
        "held_seconds": round(held_seconds, 3) if held_seconds is not None else None,
        "timeout_bound_seconds": CROSS_PROCESS_LOCK_TIMEOUT_SEC,
        "timeouts_1h": timeouts_1h,
        "timeouts_24h": len(recent) if window_secs == 86400 else None,
        "last_timeout": last_timeout,
    }


_thread_write_lease = threading.local()

# X78.11b -- shared, thread-independent lease-identity registry.
#
# Problem: _thread_write_lease is a threading.local(), so only the thread
# that acquired a lease can ever see/clear ITS OWN slot. But
# release_write_lease() can legitimately be called from a DIFFERENT thread
# than the one that acquired the lease -- specifically, db_locking.py's
# background db-conn-reaper thread force-closes long-running connections
# that belong to other threads (the WAL-hang mitigation), and
# TrackedConnection.close() -> _release_write_lane() -> release_write_lease()
# runs on whichever thread calls close(). When that's the reaper thread,
# the OLD code's `if _thread_write_lease.owner is lease.owner: del` only
# ever inspected the REAPER's own (irrelevant, always-empty) thread-local --
# never touching the actual owning thread's slot -- so the owning thread's
# reentrancy guard survived forever, self-colliding on every later write
# from that same thread (reproduced live: creator_resolution_worker's
# MainThread, whose RPC-bound resolution work routinely exceeds the
# reaper's 45s threshold).
#
# Fix: give every acquired lease a unique identity token. Record, in a
# shared (lock-protected, not thread-local) map keyed by the OWNING
# thread's ident, which token is currently "the active lease for that
# thread." release_write_lease() -- regardless of which thread calls it --
# invalidates that shared record for the lease's actual owning thread.
# acquire_write_lease()'s reentrancy check then compares the calling
# thread's local `owner` against the shared record for ITS OWN thread
# ident: if the shared record no longer matches (or is gone), the local
# reference is stale -- self-heal by clearing it and proceeding normally,
# rather than raising a false NestedDatabaseWriteError. A thread's own
# thread-local storage is never written to from another thread; only the
# shared registry is touched cross-thread, which is safe under its lock.
_active_lease_lock = threading.Lock()
_active_lease_by_thread_ident: dict[int, Any] = {}  # thread ident -> lease token (opaque object)


@dataclass
class WriteLease:
    file: Any
    owner_path: str
    owner: dict[str, Any]
    owner_thread_ident: int = 0
    token: object = None


def _read_owner_metadata(owner_path: str) -> dict[str, Any] | None:
    """Best-effort diagnostic read of the .write.lock.owner sidecar. Metadata
    is diagnostic ONLY (Phase 5/6) -- it is never used to decide lock
    ownership; that question is answered exclusively by the kernel via
    flock(). A missing/corrupt/stale file here just means diagnostics are
    unavailable, not that the lock is free."""
    try:
        with open(owner_path, encoding="utf-8") as owner_file:
            return json.load(owner_file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def acquire_write_lease(
    database: str,
    path: str,
    transaction_id: str,
    command: str,
    *,
    timeout: float = CROSS_PROCESS_LOCK_TIMEOUT_SEC,
) -> WriteLease:
    """Acquire the database-wide lane used by services and tracked connections.

    Bounded: a non-blocking LOCK_NB retry loop against a monotonic deadline,
    NOT a blocking flock(LOCK_EX). A single wedged holder can therefore only
    ever cost every other writer up to `timeout` seconds, never an unbounded
    hang (X78.9 -- previously a live-but-hung holder such as
    creator_funding_worker blocked the whole platform for ~7.5h because this
    call had no timeout at all).
    """
    real_path = os.path.realpath(path)
    this_thread_ident = threading.get_ident()
    outer = getattr(_thread_write_lease, "owner", None)
    if outer is not None:
        # X78.11b -- before treating this as a genuine same-thread nested
        # acquisition, confirm the cached local `owner` still corresponds
        # to the CURRENTLY active lease for this thread in the shared
        # registry. If a different thread (the reaper) released this
        # thread's lease on its behalf, the shared registry entry for this
        # thread ident is gone/changed -- self-heal by clearing the stale
        # local reference and falling through to a normal acquisition,
        # rather than raising a false-positive NestedDatabaseWriteError
        # against a lease that no longer really exists. The token is kept
        # in a SEPARATE thread-local (not embedded in `owner`, which is
        # copied verbatim into API-facing telemetry/diagnostics elsewhere
        # and must stay plain-JSON-serializable) so it never leaks out.
        local_token = getattr(_thread_write_lease, "token", None)
        with _active_lease_lock:
            still_active = _active_lease_by_thread_ident.get(this_thread_ident) is local_token
        if not still_active:
            del _thread_write_lease.owner
            if hasattr(_thread_write_lease, "token"):
                del _thread_write_lease.token
            outer = None
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

    deadline = time.monotonic() + timeout
    waiting_pid = os.getpid()
    waiting_thread = threading.current_thread().name
    acquired = False
    while True:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            break
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                lock_file.close()
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(_LOCK_POLL_INTERVAL_SEC, remaining))

    if not acquired:
        wait_seconds = time.monotonic() - (deadline - timeout)
        current_owner = _read_owner_metadata(owner_path)
        lock_file.close()
        _log.warning(
            "[CROSS_PROCESS_LOCK] timeout after %.1fs database=%s command=%s "
            "waiting_pid=%s current_owner=%s",
            wait_seconds, database, command, waiting_pid, current_owner,
        )
        exc = CrossProcessDatabaseWriteTimeout(
            database=database.split(":", 1)[0],
            lock_path=lock_path,
            waiting_pid=waiting_pid,
            waiting_thread=waiting_thread,
            command=command,
            wait_seconds=wait_seconds,
            current_owner=current_owner,
        )
        # Recorded here (the single acquisition chokepoint used by both
        # TrackedConnection and DatabaseWriteService) so Mission Control sees
        # every cross-process timeout regardless of which caller hit it.
        _record_cross_process_timeout(real_path, exc)
        raise exc

    token = object()  # unique identity for THIS acquisition; never persisted/serialized anywhere
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
    with _active_lease_lock:
        _active_lease_by_thread_ident[this_thread_ident] = token
    _thread_write_lease.owner = owner
    _thread_write_lease.token = token
    return WriteLease(lock_file, owner_path, owner, owner_thread_ident=this_thread_ident, token=token)


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
        # X78.11b -- this call may run on a DIFFERENT thread than the one
        # that acquired the lease (the db-conn-reaper force-closing a
        # long-running connection on another thread's behalf). The shared
        # registry invalidation below works correctly regardless of which
        # thread calls it; only the owning thread's own threading.local()
        # slot is ever mutated, and only BY that owning thread itself (see
        # acquire_write_lease's self-healing check above) -- we never
        # attempt to write into another thread's local storage here.
        with _active_lease_lock:
            if _active_lease_by_thread_ident.get(lease.owner_thread_ident) is lease.token:
                del _active_lease_by_thread_ident[lease.owner_thread_ident]
        # If we happen to be running ON the owning thread (the common case:
        # normal same-thread commit/rollback/close), clear its local
        # reference immediately too -- purely an optimization so the
        # common path doesn't need to go through the self-heal branch on
        # its very next acquisition; correctness does not depend on this
        # succeeding, since the shared-registry invalidation above is what
        # acquire_write_lease actually checks.
        if threading.get_ident() == lease.owner_thread_ident:
            if getattr(_thread_write_lease, "owner", None) is lease.owner:
                del _thread_write_lease.owner
            if getattr(_thread_write_lease, "token", None) is lease.token:
                del _thread_write_lease.token


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
        try:
            lease = acquire_write_lease(
                item.database, item.path, item.transaction_id, item.command
            )
        except CrossProcessDatabaseWriteTimeout as exc:
            # Cross-process health tracking already happened inside
            # acquire_write_lease() itself (the single chokepoint shared with
            # TrackedConnection); this just adds the per-command telemetry
            # row for the DatabaseWriteService queue view.
            record["status"] = "LOCK_TIMEOUT"
            record["error_type"] = "CrossProcessDatabaseWriteTimeout"
            record["error"] = str(exc)
            record["duration_ms"] = round((time.monotonic() - started) * 1000.0, 3)
            with self._lock:
                self._telemetry.append(dict(record))
            raise
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
