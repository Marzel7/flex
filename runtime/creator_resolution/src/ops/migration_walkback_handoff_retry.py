"""Durable, bounded retry state for the post-migration Walkback handoff.

This is deliberately separate from migration_persist_queue and from
wt_walkback_queue.  The former records whether a migration was persisted; the
latter is the eventual Walkback work item.  This small main-DB table records
only the boundary between them, so an OPS SQLite failure cannot erase intent.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from typing import Callable, Iterable

from src.core.database_write_service import (
    CrossProcessDatabaseWriteTimeout,
    DatabaseWriteLockError,
)
from src.core.walkback_queue import classify_creator, enqueue_migration

PENDING, RETRY, COMPLETE, TERMINAL = "PENDING", "RETRY", "COMPLETE", "TERMINAL"
STALE_STATE_NOOP = "STALE_STATE_NOOP"
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (10, 60, 300)
RETRY_BATCH_LIMIT = 20
ERROR_MESSAGE_LIMIT = 240
SCHEDULER_CADENCE_SECONDS = 10


def validate_schema(conn: sqlite3.Connection) -> None:
    """Validate the already-provisioned handoff schema without issuing DDL."""
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='migration_walkback_handoff'"
    ).fetchone()
    if table is None:
        raise RuntimeError("MIGRATION_WALKBACK_HANDOFF_SCHEMA_NOT_PROVISIONED")


@contextmanager
def _read_connection(path: str):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        yield conn
    finally:
        conn.close()


@contextmanager
def _write_connection(path: str):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS migration_walkback_handoff (
            mint TEXT PRIMARY KEY,
            creator TEXT,
            migration_tx TEXT,
            migrated_at INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'PENDING',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            next_attempt_at INTEGER NOT NULL DEFAULT 0,
            last_attempt_at INTEGER,
            last_error_class TEXT,
            last_error_message TEXT,
            created_at INTEGER NOT NULL,
            completed_at INTEGER,
            terminal_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_migration_walkback_handoff_due
            ON migration_walkback_handoff(state, next_attempt_at, migrated_at);
    """)


def record_intent(conn: sqlite3.Connection, *, mint: str, creator: str | None,
                  migration_tx: str | None, migrated_at: int, now: int | None = None) -> None:
    now = int(time.time() if now is None else now)
    ensure_schema(conn)
    conn.execute("""
        INSERT INTO migration_walkback_handoff
            (mint,creator,migration_tx,migrated_at,state,attempt_count,next_attempt_at,created_at)
        VALUES (?,?,?,?,?,0,?,?)
        ON CONFLICT(mint) DO UPDATE SET
            creator=COALESCE(migration_walkback_handoff.creator,excluded.creator),
            migration_tx=COALESCE(migration_walkback_handoff.migration_tx,excluded.migration_tx),
            migrated_at=MIN(migration_walkback_handoff.migrated_at,excluded.migrated_at)
    """, (mint, creator, migration_tx, migrated_at, PENDING, now, now))


def classify_enqueue_error(exc: BaseException) -> tuple[bool, str]:
    """Return retryability without turning arbitrary programming failures into retries."""
    message = str(exc).lower()
    if "no such table" in message or "schema" in message:
        return False, type(exc).__name__
    if isinstance(exc, (DatabaseWriteLockError, CrossProcessDatabaseWriteTimeout)):
        return True, type(exc).__name__
    if isinstance(exc, sqlite3.OperationalError):
        code = getattr(exc, "sqlite_errorcode", None)
        if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or "database is locked" in message or "database is busy" in message:
            return True, type(exc).__name__
    return False, type(exc).__name__


def mark_complete(conn: sqlite3.Connection, mint: str, *, now: int | None = None,
                  preserve_failure_history: bool = False) -> None:
    now = int(time.time() if now is None else now)
    if preserve_failure_history:
        # A canonical queue row proves the handoff succeeded after an earlier
        # terminal bookkeeping result.  Keep that forensic evidence intact.
        conn.execute("""UPDATE migration_walkback_handoff
            SET state=?, completed_at=? WHERE mint=?""", (COMPLETE, now, mint))
    else:
        conn.execute("""UPDATE migration_walkback_handoff
            SET state=?, completed_at=?, terminal_at=NULL, last_error_class=NULL, last_error_message=NULL
            WHERE mint=?""", (COMPLETE, now, mint))


def mark_attempt_failure(conn: sqlite3.Connection, mint: str, exc: BaseException,
                         *, now: int | None = None) -> str:
    now = int(time.time() if now is None else now)
    retryable, error_class = classify_enqueue_error(exc)
    row = conn.execute("SELECT state,attempt_count FROM migration_walkback_handoff WHERE mint=?", (mint,)).fetchone()
    if row is None:
        raise KeyError(f"handoff intent missing for {mint}")
    previous_state, previous_attempts = row[0], int(row[1])
    if previous_state not in (PENDING, RETRY):
        return STALE_STATE_NOOP
    attempts = previous_attempts + 1
    message = str(exc)[:ERROR_MESSAGE_LIMIT]
    if not retryable or attempts >= MAX_ATTEMPTS:
        state, next_due, terminal_at = TERMINAL, 0, now
    else:
        state, next_due, terminal_at = RETRY, now + BACKOFF_SECONDS[attempts - 1], None
    result = conn.execute("""UPDATE migration_walkback_handoff
        SET state=?, attempt_count=?, next_attempt_at=?, last_attempt_at=?,
            last_error_class=?, last_error_message=?, terminal_at=?
        WHERE mint=? AND state=? AND attempt_count=?""",
        (state, attempts, next_due, now, error_class, message, terminal_at,
         mint, previous_state, previous_attempts))
    if result.rowcount != 1:
        return STALE_STATE_NOOP
    return state


def reconcile_missing_intents(live_conn: sqlite3.Connection, ops_conn: sqlite3.Connection,
                              *, since: int, now: int | None = None,
                              limit: int = RETRY_BATCH_LIMIT) -> list[str]:
    """Bounded crash-gap repair from committed migrations; never scans history unbounded."""
    now = int(time.time() if now is None else now)
    rows = live_conn.execute("""
        SELECT mint,migrated_at,migration_tx,earliest_tx_creator FROM token_analysis
        WHERE migrated_at IS NOT NULL AND migrated_at>=? AND migration_tx IS NOT NULL
        ORDER BY migrated_at ASC LIMIT ?
    """, (since, limit)).fetchall()
    created: list[str] = []
    for mint, migrated_at, migration_tx, creator in rows:
        if classify_creator(creator, ops_conn, live_conn)[0] != "FULL_WALKBACK":
            continue
        existing = live_conn.execute("SELECT 1 FROM migration_walkback_handoff WHERE mint=?", (mint,)).fetchone()
        if existing:
            continue
        record_intent(live_conn, mint=mint, creator=creator, migration_tx=migration_tx,
                      migrated_at=migrated_at, now=now)
        created.append(mint)
    return created


def converge_terminal_queue_successes(live_conn: sqlite3.Connection, ops_conn: sqlite3.Connection,
                                      *, since: int, now: int | None = None,
                                      limit: int = RETRY_BATCH_LIMIT) -> list[str]:
    """Bounded prospective bookkeeping convergence; never retries TERMINAL rows."""
    now = int(time.time() if now is None else now)
    rows = live_conn.execute("""SELECT mint FROM migration_walkback_handoff
        WHERE state=? AND migrated_at>=? ORDER BY terminal_at,migrated_at LIMIT ?""",
        (TERMINAL, since, limit)).fetchall()
    converged: list[str] = []
    for row in rows:
        mint = row[0]
        if ops_conn.execute("SELECT 1 FROM wt_walkback_queue WHERE mint=?", (mint,)).fetchone():
            mark_complete(live_conn, mint, now=now, preserve_failure_history=True)
            converged.append(mint)
    return converged


def due_intents(live_conn: sqlite3.Connection, *, now: int | None = None,
                limit: int = RETRY_BATCH_LIMIT) -> list[sqlite3.Row | tuple]:
    now = int(time.time() if now is None else now)
    return live_conn.execute("""SELECT mint,creator,migration_tx,migrated_at,state,attempt_count
        FROM migration_walkback_handoff
        WHERE state IN (?,?) AND next_attempt_at<=?
        ORDER BY next_attempt_at,migrated_at LIMIT ?""", (PENDING, RETRY, now, limit)).fetchall()


def process_due_intents(live_conn: sqlite3.Connection, ops_conn: sqlite3.Connection,
                        *, now: int | None = None, limit: int = RETRY_BATCH_LIMIT,
                        before_complete: Callable[[str], None] | None = None,
                        enqueue: Callable[..., object] = enqueue_migration) -> list[tuple[str, str]]:
    """Single-threaded bounded processor. Callers own connection/write lanes; no sleeps occur here."""
    now = int(time.time() if now is None else now)
    outcomes: list[tuple[str, str]] = []
    for row in due_intents(live_conn, now=now, limit=limit):
        mint, creator = row[0], row[1]
        if ops_conn.execute("SELECT 1 FROM wt_walkback_queue WHERE mint=?", (mint,)).fetchone():
            mark_complete(live_conn, mint, now=now)
            outcomes.append((mint, COMPLETE))
            continue
        try:
            enqueue(ops_conn, mint=mint, creator=creator, live_conn=live_conn)
            if before_complete:
                before_complete(mint)
            if not ops_conn.execute("SELECT 1 FROM wt_walkback_queue WHERE mint=?", (mint,)).fetchone():
                raise RuntimeError("enqueue_migration returned without durable queue row")
            mark_complete(live_conn, mint, now=now)
            outcomes.append((mint, COMPLETE))
        except Exception as exc:
            outcomes.append((mint, mark_attempt_failure(live_conn, mint, exc, now=now)))
    return outcomes


def run_maintenance(live_db_path: str, ops_db_path: str, *, t0: int | None,
                    now: int | None = None, limit: int = RETRY_BATCH_LIMIT) -> dict[str, object]:
    """One bounded maintenance tick; the listener supplies the periodic lifecycle.

    ``t0`` is deliberately required for prospective reconciliation.  Passing
    None processes only already-durable intents and therefore cannot seed a
    historical outage population.
    """
    now = int(time.time() if now is None else now)

    # Discovery is deliberately completed on query-only connections before a
    # write-capable connection exists.  In particular, classification can scan
    # both databases but can never retain either database's writer lane.
    missing: list[tuple[str, str | None, str, int]] = []
    terminal_mints: list[str] = []
    with _read_connection(live_db_path) as live_read, _read_connection(ops_db_path) as ops_read:
        validate_schema(live_read)
        if t0 is not None:
            rows = live_read.execute("""
                SELECT mint,migrated_at,migration_tx,earliest_tx_creator
                FROM token_analysis
                WHERE migrated_at IS NOT NULL AND migrated_at>=? AND migration_tx IS NOT NULL
                ORDER BY migrated_at ASC LIMIT ?
            """, (t0, limit)).fetchall()
            for mint, migrated_at, migration_tx, creator in rows:
                if classify_creator(creator, ops_read, live_read)[0] != "FULL_WALKBACK":
                    continue
                if live_read.execute(
                    "SELECT 1 FROM migration_walkback_handoff WHERE mint=?", (mint,)
                ).fetchone() is None:
                    missing.append((mint, creator, migration_tx, migrated_at))
            terminal_mints = [row[0] for row in live_read.execute("""
                SELECT mint FROM migration_walkback_handoff
                WHERE state=? AND migrated_at>=?
                ORDER BY terminal_at,migrated_at LIMIT ?
            """, (TERMINAL, t0, limit)).fetchall()]

    # Persist crash-gap intents in one short live-DB-only transaction.
    reconciled: list[str] = []
    if missing:
        with _write_connection(live_db_path) as live_write:
            for mint, creator, migration_tx, migrated_at in missing:
                if live_write.execute(
                    "SELECT 1 FROM migration_walkback_handoff WHERE mint=?", (mint,)
                ).fetchone() is not None:
                    continue
                record_intent(live_write, mint=mint, creator=creator,
                              migration_tx=migration_tx, migrated_at=migrated_at, now=now)
                reconciled.append(mint)

    # Materialize cross-database convergence decisions before opening the live
    # writer.  This prevents an OPS read or wait from extending its transaction.
    converged_plan: list[str] = []
    if terminal_mints:
        with _read_connection(ops_db_path) as ops_read:
            converged_plan = [
                mint for mint in terminal_mints
                if ops_read.execute(
                    "SELECT 1 FROM wt_walkback_queue WHERE mint=?", (mint,)
                ).fetchone()
            ]
    converged: list[str] = []
    if converged_plan:
        with _write_connection(live_db_path) as live_write:
            for mint in converged_plan:
                mark_complete(live_write, mint, now=now, preserve_failure_history=True)
                converged.append(mint)

    # Snapshot due work without retaining a live transaction.  Each OPS write
    # completes before its separate live bookkeeping transaction begins.
    with _read_connection(live_db_path) as live_read:
        due = [tuple(row) for row in due_intents(live_read, now=now, limit=limit)]

    outcomes: list[tuple[str, str]] = []
    for row in due:
        mint, creator = row[0], row[1]
        try:
            with _read_connection(ops_db_path) as ops_read:
                already_queued = ops_read.execute(
                    "SELECT 1 FROM wt_walkback_queue WHERE mint=?", (mint,)
                ).fetchone() is not None
            if not already_queued:
                # enqueue_migration may need committed live facts for
                # classification/anchor selection.  The connection is strictly
                # query-only and Python/SQLite report no active write transaction.
                with _read_connection(live_db_path) as live_read:
                    with _write_connection(ops_db_path) as ops_write:
                        enqueue_migration(ops_write, mint=mint, creator=creator,
                                          live_conn=live_read)
                        if not ops_write.execute(
                            "SELECT 1 FROM wt_walkback_queue WHERE mint=?", (mint,)
                        ).fetchone():
                            raise RuntimeError("enqueue_migration returned without durable queue row")
            with _write_connection(live_db_path) as live_write:
                mark_complete(live_write, mint, now=now)
            outcomes.append((mint, COMPLETE))
        except Exception as exc:
            with _write_connection(live_db_path) as live_write:
                outcome = mark_attempt_failure(live_write, mint, exc, now=now)
            outcomes.append((mint, outcome))

    return {"reconciled": reconciled, "converged": converged, "outcomes": outcomes}
