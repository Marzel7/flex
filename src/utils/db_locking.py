#!/usr/bin/env python3
"""
Shared database locking for cross-module consistency.

All SQLite writes must use this lock to prevent "database is locked" errors
in concurrent scenarios (token launches, clustering, extractors, etc.)
"""

import contextlib
import inspect
import logging
import os
import sqlite3
import threading
import time
import weakref
import uuid
from collections import Counter
from typing import Optional

# Capture original sqlite3.connect before any monkey-patching
_sqlite3_connect_orig = sqlite3.connect

# Single lock instance used by all modules
DB_WRITE_LOCK = threading.RLock()

# ── Global lock error tracking (thread-safe) ─────────────────────────────────
_global_lock_errors: list[float] = []
_global_lock_errors_lock = threading.Lock()
_global_failed_writes: int = 0

def record_lock_error(caller: Optional[str] = None) -> None:
    """Called anywhere a 'database is locked' OperationalError is caught."""
    now = time.time()
    with _global_lock_errors_lock:
        _global_lock_errors.append(now)
        # keep 24h for the serializer dashboard's lock_errors_24h metric
        cutoff = now - 86400
        while _global_lock_errors and _global_lock_errors[0] < cutoff:
            _global_lock_errors.pop(0)
    if caller:
        _db_logger.warning(f"[DB_LOCK_ERROR] caller={caller}")


def lock_errors_window(window_secs: int = 86400) -> int:
    """Count of recorded 'database is locked' errors within the window (for the dashboard)."""
    now = time.time()
    with _global_lock_errors_lock:
        return sum(1 for t in _global_lock_errors if t > now - window_secs)


def record_failed_write(caller: Optional[str] = None) -> None:
    global _global_failed_writes
    _global_failed_writes += 1
    if caller:
        _db_logger.error(f"[DB_WRITE_FAILED] caller={caller}")


def get_lock_error_metrics() -> dict:
    """Return lock error counts for /api/db-health."""
    now = time.time()
    with _global_lock_errors_lock:
        errors_1h = len(_global_lock_errors)
        errors_5m = sum(1 for t in _global_lock_errors if t >= now - 300)
    return {
        "lock_errors_1h": errors_1h,
        "lock_errors_5m": errors_5m,
        "failed_writes_total": _global_failed_writes,
    }

_db_logger = logging.getLogger("db_locking")
_open_connections = {}
_open_connections_lock = threading.Lock()

# Process-wide write serializer (the single write lane). Plain Lock (not RLock): releasable from
# any thread, which the async adapter needs; write transactions are short and never nest it.
_DB_WRITE_LOCK = threading.Lock()
_DB_WRITE_STATS = {"acquisitions": 0, "contended": 0, "total_wait_ms": 0.0, "max_wait_ms": 0.0}
_DB_WRITE_STATS_LOCK = threading.Lock()

# ── SERIALIZER OBSERVABILITY (in-memory; no DB writes — measuring must not add write load) ──
from collections import deque as _deque
_DBM_START = time.time()
_DBM_WAIT_SAMPLES = _deque(maxlen=5000)      # recent acquire wait_ms (for P50/P95/P99)
_DBM_COMMIT_SAMPLES = _deque(maxlen=5000)    # recent commit dur_ms
_DBM_QUEUE_DEPTH = 0                          # current writers waiting/holding the lane
_DBM_MAX_QUEUE_DEPTH = 0
_DBM_SLOW = {"gt100": 0, "gt500": 0, "gt1000": 0}
_DBM_BY_WRITER = {}                           # label -> {writes, wait_ms, commit_ms}
_DBM_LOCK = threading.Lock()


def _writer_label(caller) -> str:
    """Map a _db_caller ('file.py:line in func') to a coarse subsystem label for attribution."""
    if not caller:
        return "unknown"
    fname = str(caller).split(":")[0].replace(".py", "")
    _MAP = {
        "pumpfun_curve_listener": "listener",
        "token_prediction_builder": "prediction_builder",
        "rpc_metrics_recorder": "rpc_metrics",
        "risk_scoring_builder": "risk_scoring",
        "realtime_creator_funding_extractor": "creator_resolution",
        "funder_incoming_extractor": "funder_extraction",
        "launch_audit": "launch_audit",
        "operation_forward_walk": "forward_walk",
        "ws_cascade_store": "ws_cascade",
        "ws_cascade": "ws_cascade",
        "treasury_bank": "treasury",
        "watchtower_backfill": "migration_reconciler",
    }
    return _MAP.get(fname, fname)


def _dbm_record_acquire(caller, wait_ms: float):
    label = _writer_label(caller)
    with _DBM_LOCK:
        _DBM_WAIT_SAMPLES.append(wait_ms)
        w = _DBM_BY_WRITER.setdefault(label, {"writes": 0, "wait_ms": 0.0, "commit_ms": 0.0})
        w["writes"] += 1
        w["wait_ms"] += wait_ms


def _dbm_record_commit(caller, dur_ms: float):
    label = _writer_label(caller)
    with _DBM_LOCK:
        _DBM_COMMIT_SAMPLES.append(dur_ms)
        if dur_ms > 1000: _DBM_SLOW["gt1000"] += 1
        if dur_ms > 500:  _DBM_SLOW["gt500"] += 1
        if dur_ms > 100:  _DBM_SLOW["gt100"] += 1
        w = _DBM_BY_WRITER.setdefault(label, {"writes": 0, "wait_ms": 0.0, "commit_ms": 0.0})
        w["commit_ms"] += dur_ms


def _dbm_queue(delta: int):
    global _DBM_QUEUE_DEPTH, _DBM_MAX_QUEUE_DEPTH
    with _DBM_LOCK:
        _DBM_QUEUE_DEPTH += delta
        if _DBM_QUEUE_DEPTH > _DBM_MAX_QUEUE_DEPTH:
            _DBM_MAX_QUEUE_DEPTH = _DBM_QUEUE_DEPTH


def _pct(samples, p):
    if not samples:
        return 0.0
    s = sorted(samples)
    k = int(round((p / 100.0) * (len(s) - 1)))
    return round(s[k], 2)


def serializer_metrics() -> dict:
    """Full DB-serializer observability snapshot for the dashboard (in-memory, no DB read)."""
    now = time.time()
    with _DBM_LOCK:
        waits = list(_DBM_WAIT_SAMPLES)
        commits = list(_DBM_COMMIT_SAMPLES)
        slow = dict(_DBM_SLOW)
        by_writer = {k: dict(v) for k, v in _DBM_BY_WRITER.items()}
        qd, mqd = _DBM_QUEUE_DEPTH, _DBM_MAX_QUEUE_DEPTH
    with _DB_WRITE_STATS_LOCK:
        total = _DB_WRITE_STATS["acquisitions"]
    uptime_min = max((now - _DBM_START) / 60.0, 0.001)
    lock_err_24h = lock_errors_window(86400)
    # top writers by count
    top = sorted(by_writer.items(), key=lambda kv: -kv[1]["writes"])
    top_writers = []
    for label, w in top:
        c = w["writes"] or 1
        top_writers.append({
            "writer": label, "write_count": w["writes"],
            "avg_wait_ms": round(w["wait_ms"] / c, 2),
            "avg_commit_ms": round(w["commit_ms"] / c, 2),
            "total_time_waiting_ms": round(w["wait_ms"], 1),
        })
    return {
        "total_writes": total,
        "writes_per_min": round(total / uptime_min, 1),
        "uptime_min": round(uptime_min, 1),
        "avg_wait_ms": round(sum(waits) / len(waits), 2) if waits else 0.0,
        "p50_wait_ms": _pct(waits, 50), "p95_wait_ms": _pct(waits, 95), "p99_wait_ms": _pct(waits, 99),
        "avg_commit_ms": round(sum(commits) / len(commits), 2) if commits else 0.0,
        "p95_commit_ms": _pct(commits, 95), "p99_commit_ms": _pct(commits, 99),
        "queue_depth": qd, "max_queue_depth": mqd,
        "slow_commits_gt100": slow["gt100"], "slow_commits_gt500": slow["gt500"], "slow_commits_gt1000": slow["gt1000"],
        "lock_errors_24h": lock_err_24h,
        "top_writers": top_writers,
        "serialize_enabled": _DB_WRITE_SERIALIZE,
    }


# Central write serialization toggle: when on, every TrackedConnection acquires the one global
# write lock for the duration of a write TRANSACTION (first write stmt → commit/rollback/close),
# turning N independent writers (listener, prediction_builder, rpc_metrics, symbol_fetch, …) into
# one orderly write lane. Reads (SELECT-only conns) never acquire → read concurrency preserved.
# Env-flagged so it can be rolled out / backed out without code changes.
_DB_WRITE_SERIALIZE = os.environ.get("DB_WRITE_SERIALIZE", "1") == "1"
_WRITE_SQL_PREFIXES = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "UPSERT")


def _is_write_sql(sql) -> bool:
    try:
        s = sql.lstrip().upper()
    except Exception:
        return False
    return s.startswith(_WRITE_SQL_PREFIXES)


class TrackedCursor(sqlite3.Cursor):
    """Cursor that enters the same managed write lane before its first mutation."""

    def execute(self, sql, parameters=()):
        if _DB_WRITE_SERIALIZE and _is_write_sql(sql):
            self.connection._acquire_write_lane()
        return super().execute(sql, parameters)

    def executemany(self, sql, parameters):
        if _DB_WRITE_SERIALIZE and _is_write_sql(sql):
            self.connection._acquire_write_lane()
        return super().executemany(sql, parameters)


class TrackedConnection(sqlite3.Connection):
    """SQLite connection that unregisters itself when closed, tracks lock errors, and (when
    DB_WRITE_SERIALIZE=1) holds the global write lock for the duration of a write transaction so
    concurrent writers across all modules serialize instead of colliding on the file."""

    def _acquire_write_lane(self):
        """Acquire the global write lock once per write transaction (idempotent within the conn)."""
        if not _DB_WRITE_SERIALIZE or getattr(self, "_holds_write_lock", False):
            return
        caller = getattr(self, "_db_caller", None)
        _dbm_queue(+1)                      # this writer is now WAITING for the lane
        t0 = time.monotonic()
        acquired = _DB_WRITE_LOCK.acquire(timeout=60)
        wait_ms = (time.monotonic() - t0) * 1000.0
        _dbm_queue(-1)                      # no longer waiting (acquired OR timed out) — symmetric,
                                            # so an abandoned-after-acquire conn can't leak the counter
        if not acquired:
            record_lock_error(caller)
            return  # fall through to SQLite's own busy_timeout rather than deadlock
        self._holds_write_lock = True
        try:
            from src.core.database_write_service import acquire_write_lease
            path = getattr(self, "_db_path", None)
            if path:
                txid = str(uuid.uuid4())
                command = getattr(self, "_db_caller", None) or "tracked-connection-write"
                self._cross_process_lease = acquire_write_lease(
                    f"tracked:{os.path.realpath(path)}", path, txid, command
                )
                self._write_transaction_id = txid
                self._write_started_at = time.time()
                self._write_started_monotonic = time.monotonic()
                self._write_total_changes_before = self.total_changes
                self._write_rolled_back = False
        except Exception:
            self._holds_write_lock = False
            _DB_WRITE_LOCK.release()
            raise
        try:
            with _DB_WRITE_STATS_LOCK:
                _DB_WRITE_STATS["acquisitions"] += 1
                if wait_ms > 1.0:
                    _DB_WRITE_STATS["contended"] += 1
                _DB_WRITE_STATS["total_wait_ms"] += wait_ms
                if wait_ms > _DB_WRITE_STATS["max_wait_ms"]:
                    _DB_WRITE_STATS["max_wait_ms"] = wait_ms
        except Exception:
            pass
        _dbm_record_acquire(caller, wait_ms)   # percentile + per-writer attribution

    def _release_write_lane(self):
        try:
            self._release_write_lane_inner()
        finally:
            # Belt-and-suspenders: even if the lease-release path above raised
            # before reaching its own cleanup, this thread's process-local write
            # lock must never stay held -- that would starve every future writer
            # on this connection's thread rather than just this one transaction.
            if getattr(self, "_holds_write_lock", False):
                self._holds_write_lock = False
                try:
                    _DB_WRITE_LOCK.release()
                except RuntimeError:
                    pass

    def _release_write_lane_inner(self):
        lease = getattr(self, "_cross_process_lease", None)
        if lease is not None:
            # release_write_lease() below (which unlocks the cross-process
            # file lock AND clears the thread-local re-entrancy guard,
            # _thread_write_lease.owner in database_write_service.py) must
            # run no matter what happens in the telemetry block below. The
            # import itself is hoisted above both try blocks so an
            # ImportError can't leave release_write_lease unbound going into
            # the finally. Before this guard, an exception raised by
            # record_external() (or anything else in the telemetry block)
            # skipped release_write_lease() entirely while the OLD code's
            # single finally still cleared self._cross_process_lease —
            # silently leaking both the file lock and the thread-local
            # forever, so every subsequent write on that thread raised
            # NestedDatabaseWriteError permanently (observed: walkback_worker
            # stuck ~26h after a single mid-batch RPC timeout).
            from src.core.database_write_service import (
                database_write_service, release_write_lease,
            )
            try:
                try:
                    started = getattr(self, "_write_started_monotonic", time.monotonic())
                    rolled_back = bool(getattr(self, "_write_rolled_back", False))
                    database_write_service.record_external({
                        "database": lease.owner["database"],
                        "database_selector": lease.owner.get("database_selector"),
                        "database_path": lease.owner["database_path"],
                        "writer_id": lease.owner["writer_id"],
                        "process_pid": lease.owner["process_pid"],
                        "thread": lease.owner["thread"],
                        "transaction_id": lease.owner["transaction_id"],
                        "command": lease.owner["command"],
                        "queue_wait_ms": None,
                        "begin_timestamp": getattr(self, "_write_started_at", None),
                        "commit_timestamp": None if rolled_back else time.time(),
                        "rollback": rolled_back,
                        "duration_ms": round((time.monotonic() - started) * 1000.0, 3),
                        "rows_modified": self.total_changes - getattr(
                            self, "_write_total_changes_before", self.total_changes
                        ),
                        "status": "ROLLED_BACK" if rolled_back else "COMMITTED",
                    })
                except Exception:
                    _db_logger.exception(
                        "[DB_WRITE_LEASE] record_external failed; releasing lease anyway "
                        f"command={lease.owner.get('command')}"
                    )
            finally:
                release_write_lease(lease)
                self._cross_process_lease = None
        if getattr(self, "_holds_write_lock", False):
            self._holds_write_lock = False
            try:
                _DB_WRITE_LOCK.release()
            except RuntimeError:
                pass

    def execute(self, sql, parameters=()):
        if _DB_WRITE_SERIALIZE and _is_write_sql(sql):
            self._acquire_write_lane()
        try:
            return super().execute(sql, parameters)
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                record_lock_error(getattr(self, "_db_caller", None))
            raise

    def cursor(self, factory=TrackedCursor):
        return super().cursor(factory)

    def executescript(self, sql_script):
        if _DB_WRITE_SERIALIZE and any(
            _is_write_sql(statement) for statement in sql_script.split(";")
        ):
            self._acquire_write_lane()
        return super().executescript(sql_script)

    def executemany(self, sql, parameters):
        if _DB_WRITE_SERIALIZE and _is_write_sql(sql):
            self._acquire_write_lane()
        try:
            return super().executemany(sql, parameters)
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                record_lock_error(getattr(self, "_db_caller", None))
            raise

    def commit(self):
        # Acquire the lane at commit too: writes done via cursor.execute() (not conn.execute)
        # bypass the execute-level acquire, but they ALL funnel through conn.commit(). In WAL mode
        # the commit is where the write lock is held longest, so serializing commits is the
        # high-value catch for cursor-based writers (price_service, etc.). No-op if already held.
        if _DB_WRITE_SERIALIZE and self.in_transaction and not getattr(self, "_holds_write_lock", False):
            self._acquire_write_lane()
        t0 = time.monotonic()
        committed = False
        try:
            result = super().commit()
            committed = True
            return result
        finally:
            self._write_rolled_back = not committed
            dur_ms = (time.monotonic() - t0) * 1000.0
            caller = getattr(self, "_db_caller", None)
            if dur_ms > 50.0:
                _db_logger.warning(f"[DB_COMMIT_SLOW] {dur_ms:.0f}ms caller={caller}")
            _dbm_record_commit(caller, dur_ms)   # commit-time percentiles + slow buckets + attribution
            self._release_write_lane()

    def rollback(self):
        self._write_rolled_back = True
        try:
            return super().rollback()
        finally:
            self._release_write_lane()

    def close(self):
        # release the write lane if a transaction was abandoned without commit/rollback
        self._release_write_lane()
        tracking_id = getattr(self, "_db_tracking_id", None)
        if tracking_id is not None:
            with _open_connections_lock:
                _open_connections.pop(tracking_id, None)
            try:
                self._db_tracking_id = None
            except Exception:
                pass
        return super().close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def _register_connection(conn: sqlite3.Connection, path: str, caller: str) -> None:
    global _reaper_db_path
    tracking_id = id(conn)
    try:
        conn._db_tracking_id = tracking_id
    except Exception:
        return
    if _reaper_db_path is None:
        _reaper_db_path = path
    with _open_connections_lock:
        _open_connections[tracking_id] = {
            "path": path,
            "caller": caller,
            "opened_at": time.time(),
            "thread": threading.current_thread().name,
            "conn_ref": weakref.ref(conn),
        }


def get_open_connection_summary(limit: int = 25) -> dict:
    """Return lightweight diagnostics for currently open tracked DB connections."""
    now = time.time()
    with _open_connections_lock:
        records = list(_open_connections.values())
    by_caller = Counter(record["caller"] for record in records)
    by_thread = Counter(record["thread"] for record in records)
    oldest = sorted(records, key=lambda record: record["opened_at"])[:limit]
    return {
        "open_count": len(records),
        "by_caller": by_caller.most_common(limit),
        "by_thread": by_thread.most_common(limit),
        "oldest": [
            {
                **record,
                "age_seconds": round(now - record["opened_at"], 1),
            }
            for record in oldest
        ],
    }


def db_connect(path: str, timeout: int = 30, row_factory=None,
               read_only: bool = False) -> sqlite3.Connection:
    """
    Open a SQLite connection with safe defaults for concurrent access.

    - timeout=30: waits up to 30s for a write lock (Python-level)
    - busy_timeout=30000: mirrors at the SQLite C level (handles WAL checkpoints)
    - never mutates persistent database settings at connection time

    read_only=True: open the file with URI mode=ro. In WAL this reads the last
    committed snapshot WITHOUT taking the database write lock, so it can NEVER be
    blocked by a write storm. Use it for dashboard/read endpoints. A read-only
    connection cannot run write PRAGMAs, so we skip synchronous setup.

    Use this instead of sqlite3.connect() everywhere to avoid "database is locked".
    Logs caller location and elapsed time when acquisition is slow (>1s).
    """
    # Identify caller for lock contention diagnostics
    frame = inspect.stack()[1]
    caller = f"{frame.filename.split('/')[-1]}:{frame.lineno} in {frame.function}"

    t0 = time.monotonic()
    try:
        if read_only:
            # mode=ro URI connection — pure read, bypasses the write lane entirely.
            conn = _sqlite3_connect_orig(f"file:{path}?mode=ro", timeout=timeout,
                                         uri=True, factory=TrackedConnection)
            conn.execute("PRAGMA busy_timeout=%d" % int(timeout * 1000))
            if row_factory is not None:
                conn.row_factory = row_factory
            _register_connection(conn, path, caller)
            try:
                conn._db_caller = caller
                conn._read_only = True   # lets callers skip write-only setup
            except Exception:
                pass
            return conn
        conn = _sqlite3_connect_orig(path, timeout=timeout, factory=TrackedConnection)
    except Exception as e:
        elapsed = time.monotonic() - t0
        _db_logger.error(f"[DB_CONNECT_FAIL] caller={caller} elapsed={elapsed:.2f}s error={e}")
        raise

    elapsed = time.monotonic() - t0
    if elapsed > 1.0:
        _db_logger.warning(f"[DB_CONNECT_SLOW] caller={caller} elapsed={elapsed:.2f}s path={path}")

    _register_connection(conn, path, caller)
    try:
        conn._db_caller = caller
        conn._db_path = path
    except Exception:
        pass
    if row_factory is not None:
        conn.row_factory = row_factory
    # busy_timeout & synchronous are CONNECTION-level (cheap, no write lock) — set always.
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextlib.contextmanager
def managed_db_connect(path: str, timeout: int = 30, row_factory=None,
                       read_only: bool = False):
    """Context manager wrapper around db_connect — guarantees conn.close() on exit.

    Use this in Flask routes and anywhere a connection must be closed even if an
    exception or early return occurs:

        with managed_db_connect(DB_PATH, read_only=True) as conn:
            ...
    """
    conn = db_connect(path, timeout=timeout, row_factory=row_factory, read_only=read_only)
    try:
        yield conn
    finally:
        conn.close()


# ── Connection reaper ─────────────────────────────────────────────────────────
# Flask routes that open db_connect() without a try/finally will leak connections
# and cause the WAL to grow unbounded. The reaper closes any connection that has
# been open longer than MAX_CONNECTION_AGE_SECS.

_REAPER_INTERVAL_SECS = 10
_MAX_CONNECTION_AGE_SECS = 20  # idle connections held longer than this are leaks
# Connections stuck in_transaction are normally left alone (active write). But a
# connection that has been in_transaction for this long is abandoned, not active —
# this is the failure mode behind the recurring WAL-hang: under lock contention an
# operation raises "database is locked" mid-transaction, the conn is left
# in_transaction, the reaper's in_transaction guard then protects it FOREVER, and
# such connections pile up (145 observed) and starve the WAL checkpoint. We rollback
# and close them past this threshold. A legitimate write never holds a txn this long.
_MAX_TXN_CONNECTION_AGE_SECS = 45   # was 120 — abandoned txns pin the WAL; reap sooner
_reaper_db_path: Optional[str] = None  # set from first registered connection


def _reaper_loop() -> None:
    while True:
        time.sleep(_REAPER_INTERVAL_SECS)
        try:
            reaped = _reap_stale_connections()
            if reaped and _reaper_db_path:
                try:
                    conn = sqlite3.connect(_reaper_db_path, timeout=2)
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                    conn.close()
                except Exception:
                    pass
        except Exception:
            pass


# ── WAL watchdog ──────────────────────────────────────────────────────────────
# PASSIVE autocheckpoint can't truncate the WAL while any reader holds it open.
# This watchdog runs RESTART checkpoint every 5 minutes when WAL exceeds 500 MB,
# which resets the write position to the start of the file so it stops growing.
# RESTART waits for all readers to finish (up to busy_timeout) then truncates.

_WAL_WATCHDOG_INTERVAL = 30        # seconds between checks (was 60)
_WAL_SIZE_THRESHOLD    = 32 * 1024 * 1024   # 32 MB (was 200 MB — too loose; let it bloat)


def _wal_watchdog_loop() -> None:
    import os
    while True:
        time.sleep(_WAL_WATCHDOG_INTERVAL)
        if not _reaper_db_path:
            continue
        try:
            wal_path = _reaper_db_path + "-wal"
            if not os.path.exists(wal_path):
                continue
            wal_size = os.path.getsize(wal_path)
            if wal_size < _WAL_SIZE_THRESHOLD:
                continue
            _db_logger.info(f"[WAL_WATCHDOG] WAL is {wal_size/1e6:.1f} MB — running TRUNCATE checkpoint")
            conn = sqlite3.connect(_reaper_db_path, timeout=10)
            # TRUNCATE actually shrinks the -wal file (RESTART only resets the write
            # position). Falls back to RESTART semantics if readers block truncation.
            result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            conn.close()
            wal_after = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
            _db_logger.info(f"[WAL_WATCHDOG] checkpoint result={result}  WAL after={wal_after/1e6:.1f} MB")
        except Exception as e:
            _db_logger.warning(f"[WAL_WATCHDOG] checkpoint failed: {e}")


# Threads that handle live data — never reap their connections mid-flight
_REAPER_SAFE_THREADS = {
    # wt-* webhook/websocket threads: mid-flight reads must not be interrupted
    "wt-infra-processor",
    "wt-candidate-processor",
    "wt-corridor-monitor",
    # MainThread intentionally NOT exempt — asyncio opens idle connections that
    # must be reaped. Active transactions are protected by the in_transaction check.
}


def _reap_stale_connections() -> int:
    now = time.time()
    cutoff = now - _MAX_CONNECTION_AGE_SECS
    to_reap = []

    with _open_connections_lock:
        for tracking_id, record in list(_open_connections.items()):
            if record["opened_at"] >= cutoff:
                continue
            thread_name = record.get("thread", "")
            # Skip live-data threads — they may be mid-read on a webhook/websocket
            if thread_name in _REAPER_SAFE_THREADS:
                continue
            to_reap.append((tracking_id, record))

    reaped = 0
    for tracking_id, record in to_reap:
        conn = record.get("conn_ref") and record["conn_ref"]()
        age = round(now - record["opened_at"])
        caller = record.get("caller", "unknown")
        if conn is not None:
            try:
                if conn.in_transaction:
                    # Active write — normally leave it alone. But a transaction held
                    # this long is abandoned (the WAL-hang failure mode), not active:
                    # rollback to release the WAL read-lock, then close.
                    if age < _MAX_TXN_CONNECTION_AGE_SECS:
                        continue
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    conn.close()
                    reaped += 1
                    _db_logger.warning(
                        f"[DB_REAPER] force-closed ABANDONED in_transaction connection "
                        f"age={age}s caller={caller} (was starving WAL checkpoint)"
                    )
                    continue
                conn.close()
                reaped += 1
                _db_logger.warning(
                    f"[DB_REAPER] closed idle connection age={age}s caller={caller}"
                )
            except Exception:
                pass
        else:
            # Already GC'd but not unregistered — clean up the tracking entry
            with _open_connections_lock:
                _open_connections.pop(tracking_id, None)
    return reaped


def _start_connection_reaper() -> None:
    threading.Thread(target=_reaper_loop, daemon=True, name="db-conn-reaper").start()
    threading.Thread(target=_wal_watchdog_loop, daemon=True, name="db-wal-watchdog").start()


_start_connection_reaper()


# ── sqlite3.connect monkey-patch ─────────────────────────────────────────────
# Redirect all bare sqlite3.connect() calls to db_connect() so every connection
# gets busy_timeout=30000, WAL mode, and reaper tracking — without editing every
# caller. Only patches connections to the main flex DB; other paths pass through.
import os as _os

_FLEX_DB_PATH = _os.getenv(
    "DB_PATH",
    _os.path.join(_os.path.dirname(__file__), "../../database/flex_complete_database.db"),
)
_FLEX_DB_ABS = _os.path.realpath(_FLEX_DB_PATH) if _FLEX_DB_PATH else None


def _patched_connect(database, timeout=5, *args, **kwargs):
    # Only intercept the main flex DB; let everything else (in-memory, other paths) through
    try:
        db_abs = _os.path.realpath(str(database)) if database and database != ":memory:" else None
    except Exception:
        db_abs = None

    if db_abs and _FLEX_DB_ABS and db_abs == _FLEX_DB_ABS:
        # Use the higher timeout if caller asked for more
        effective_timeout = max(timeout, 30)
        # factory kwarg would conflict — db_connect uses TrackedConnection internally
        kwargs.pop("factory", None)
        row_factory = kwargs.pop("row_factory", None)
        conn = db_connect(database, timeout=effective_timeout, row_factory=row_factory)
        return conn

    return _sqlite3_connect_orig(database, timeout, *args, **kwargs)


sqlite3.connect = _patched_connect


# ── WRITE SERIALIZER ─────────────────────────────────────────────────────────
# The recurring lock-storm root: many subsystems in the listener process write the live DB
# CONCURRENTLY (births, creator resolution, funding, prediction, reconciler — 19 unguarded write
# sites). busy_timeout + retry don't help when N writers collide simultaneously; SQLite still
# serializes at the file level and the losers exhaust their timeout → birth writes fail (1699
# observed), the WAL gets pinned, everything chokes. self.db_lock (asyncio.Lock) only covers async
# tasks — it does nothing for the executor-thread/pool writers, which is why it was applied to only
# ~12 of 31 sites.
#
# Fix: a process-wide threading.Lock that EVERY write path acquires — works from BOTH async tasks
# AND threads. Serializing writes turns N-way file contention into an orderly queue, so no writer
# starves another. Reads stay lock-free (RO connections).
#
# Use as a context manager around the write transaction:
#     with db_write_lock():
#         with db_connect(DB_PATH) as conn:
#             conn.execute(...); conn.commit()
# (_DB_WRITE_LOCK / _DB_WRITE_STATS defined near the top, before TrackedConnection.)


@contextlib.contextmanager
def db_write_lock(label: str = ""):
    """Serialize a DB write across async tasks AND threads. Records contention metrics so the
    before/after impact of serialization is measurable. Cheap when uncontended."""
    t0 = time.monotonic()
    acquired = _DB_WRITE_LOCK.acquire(timeout=60)
    wait_ms = (time.monotonic() - t0) * 1000.0
    if not acquired:
        _db_logger.warning(f"[DB_WRITE_LOCK] timeout acquiring after 60s label={label}")
        raise sqlite3.OperationalError("db_write_lock acquire timeout")
    try:
        with _DB_WRITE_STATS_LOCK:
            _DB_WRITE_STATS["acquisitions"] += 1
            if wait_ms > 1.0:
                _DB_WRITE_STATS["contended"] += 1
            _DB_WRITE_STATS["total_wait_ms"] += wait_ms
            if wait_ms > _DB_WRITE_STATS["max_wait_ms"]:
                _DB_WRITE_STATS["max_wait_ms"] = wait_ms
        yield
    finally:
        _DB_WRITE_LOCK.release()


class AsyncDbWriteLock:
    """`async with` adapter over the SAME process-wide _DB_WRITE_LOCK used by db_write_lock().

    This is the unification: async-task writers (`async with self.db_lock`) and thread/pool writers
    (`with db_write_lock()`) acquire ONE lock, so they actually serialize against each other. The
    acquire runs in a thread executor so it never blocks the event loop while waiting. Drop-in for
    the old `asyncio.Lock()` (same `async with` interface)."""
    async def __aenter__(self):
        import asyncio
        loop = asyncio.get_event_loop()
        # acquire off the loop so a contended write can't stall the event loop
        ok = await loop.run_in_executor(None, lambda: _DB_WRITE_LOCK.acquire(timeout=60))
        if not ok:
            _db_logger.warning("[DB_WRITE_LOCK] async acquire timeout after 60s")
            raise sqlite3.OperationalError("async db_write_lock acquire timeout")
        with _DB_WRITE_STATS_LOCK:
            _DB_WRITE_STATS["acquisitions"] += 1
        return self

    async def __aexit__(self, *exc):
        try:
            _DB_WRITE_LOCK.release()
        except RuntimeError:
            pass
        return False


def db_write_stats() -> dict:
    """Snapshot of write-serializer contention metrics (for before/after measurement)."""
    with _DB_WRITE_STATS_LOCK:
        s = dict(_DB_WRITE_STATS)
    acq = s["acquisitions"] or 1
    s["avg_wait_ms"] = round(s["total_wait_ms"] / acq, 2)
    s["contended_pct"] = round(100.0 * s["contended"] / acq, 1)
    s["max_wait_ms"] = round(s["max_wait_ms"], 1)
    return s


@contextlib.contextmanager
def db_retry(path: str, retries: int = 5, base_delay: float = 0.5, **kwargs):
    """
    db_connect with exponential-backoff retry on OperationalError (locked).
    Use for write-heavy paths that still see lock contention despite busy_timeout.

        with db_retry(DB_PATH) as conn:
            conn.execute(...)
            conn.commit()
    """
    last_exc = None
    for attempt in range(retries):
        conn = None
        try:
            conn = db_connect(path, **kwargs)
            yield conn
            conn.close()
            return
        except sqlite3.OperationalError as e:
            last_exc = e
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            if "locked" not in str(e).lower() or attempt == retries - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))
    raise last_exc
