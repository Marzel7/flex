"""DEV-023 bounded retention contract for the canonical RPC cache.

This module is deliberately not scheduled and has no import-time side effects.
Callers must supply the exact canonical database path.  It deletes only expired
``rpc_response_cache`` rows in small committed batches; it never checkpoints,
vacuums, archives payloads, or opens any provider connection.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager

AUTHORIZED_TABLE = "rpc_response_cache"


@dataclass(frozen=True)
class RetentionLimits:
    batch_rows: int = 200
    max_rows_per_run: int = 2_000
    busy_retries: int = 3
    min_free_bytes: int = 4 * 1024**3
    max_wal_bytes: int = 200 * 1024**2


def _limits_valid(limits: RetentionLimits) -> bool:
    """Keep DEV-023's reviewed 200-row mutation ceiling structural."""
    return (
        0 < limits.batch_rows <= 200
        and limits.max_rows_per_run >= 0
        and limits.busy_retries >= 0
    )


def _wal_bytes(path: Path) -> int:
    try:
        return path.with_name(path.name + "-wal").stat().st_size
    except FileNotFoundError:
        return 0


def _schema_ok(conn: sqlite3.Connection) -> bool:
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({AUTHORIZED_TABLE})")]
    return columns == ["cache_key", "response_json", "method", "cached_at", "ttl_seconds", "hit_count"]


def _expired_rowids(conn: sqlite3.Connection, cutoff: float, limit: int) -> list[int]:
    return [
        row[0]
        for row in conn.execute(
            f"SELECT rowid FROM {AUTHORIZED_TABLE} "
            "WHERE cached_at + ttl_seconds <= ? ORDER BY rowid LIMIT ?",
            (cutoff, limit),
        )
    ]


def retain_expired_rpc_cache_batch_in_transaction(
    conn: sqlite3.Connection,
    *,
    cutoff: float,
    limits: RetentionLimits,
) -> dict:
    """Delete at most one reviewed batch through a caller-owned transaction.

    The runtime adapter supplies a ``DatabaseWriteService`` transaction.  This
    function deliberately neither opens a connection nor commits: the shared
    write service owns acquisition, timeout, rollback, commit, and release.
    """
    if not _limits_valid(limits):
        return {"status": "REJECTED_INVALID_LIMITS", "deleted": 0}
    if not _schema_ok(conn):
        return {"status": "REJECTED_SCHEMA", "deleted": 0}
    rowids = _expired_rowids(conn, cutoff, min(limits.batch_rows, limits.max_rows_per_run))
    if not rowids:
        return {"status": "COMPLETE", "deleted": 0}
    placeholders = ",".join("?" for _ in rowids)
    cursor = conn.execute(
        f"DELETE FROM {AUTHORIZED_TABLE} WHERE rowid IN ({placeholders}) "
        "AND cached_at + ttl_seconds <= ?",
        (*rowids, cutoff),
    )
    deleted = max(0, cursor.rowcount or 0)
    return {
        "status": "STOP_ROW_CAP" if deleted == limits.max_rows_per_run else "COMPLETE",
        "deleted": deleted,
    }


def retain_expired_rpc_cache(
    *,
    database_path: str,
    canonical_database_path: str,
    cutoff: float,
    limits: RetentionLimits = RetentionLimits(),
    free_bytes: Callable[[str], int] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    write_lane: Callable[[], ContextManager[object]] | None = None,
    stop_file: str | None = None,
) -> dict:
    """Run one bounded, restart-safe canonical-cache retention pass.

    A result with ``status != 'COMPLETE'`` is fail-closed.  Already committed
    batches remain valid and are simply absent from the next pass, which makes
    restart idempotent without an auxiliary journal.
    """
    path = Path(database_path).resolve()
    canonical = Path(canonical_database_path).resolve()
    if path != canonical:
        return {"status": "REJECTED_NONCANONICAL_DATABASE", "deleted": 0}
    if not _limits_valid(limits):
        return {"status": "REJECTED_INVALID_LIMITS", "deleted": 0}
    if not path.is_file():
        return {"status": "REJECTED_MISSING_DATABASE", "deleted": 0}
    free_bytes = free_bytes or (lambda value: shutil.disk_usage(value).free)
    if free_bytes(str(path.parent)) < limits.min_free_bytes:
        return {"status": "STOP_DISK_FLOOR", "deleted": 0}
    if _wal_bytes(path) > limits.max_wal_bytes:
        return {"status": "STOP_WAL_CEILING", "deleted": 0}
    if stop_file and Path(stop_file).exists():
        return {"status": "STOP_FILE", "deleted": 0}

    deleted = 0
    lane = write_lane or nullcontext
    try:
        with lane():
            # Bypass the application's module-level sqlite3.connect wrapper:
            # this contract supplies its own explicit lane and zero busy wait.
            # Constructing the stdlib connection directly avoids import-time
            # background maintenance side effects as well.
            conn = sqlite3.Connection(str(path), timeout=0)
            try:
                conn.execute("PRAGMA busy_timeout = 0")
                if not _schema_ok(conn):
                    return {"status": "REJECTED_SCHEMA", "deleted": 0}
                while deleted < limits.max_rows_per_run:
                    if free_bytes(str(path.parent)) < limits.min_free_bytes:
                        return {"status": "STOP_DISK_FLOOR", "deleted": deleted}
                    if _wal_bytes(path) > limits.max_wal_bytes:
                        return {"status": "STOP_WAL_CEILING", "deleted": deleted}
                    if stop_file and Path(stop_file).exists():
                        return {"status": "STOP_FILE", "deleted": deleted}
                    rowids = _expired_rowids(
                        conn, cutoff, min(limits.batch_rows, limits.max_rows_per_run - deleted)
                    )
                    if not rowids:
                        return {"status": "COMPLETE", "deleted": deleted}
                    placeholders = ",".join("?" for _ in rowids)
                    try:
                        cursor = conn.execute(
                            f"DELETE FROM {AUTHORIZED_TABLE} WHERE rowid IN ({placeholders}) "
                            "AND cached_at + ttl_seconds <= ?",
                            (*rowids, cutoff),
                        )
                        conn.commit()
                    except sqlite3.OperationalError as exc:
                        conn.rollback()
                        if "busy" not in str(exc).lower() and "locked" not in str(exc).lower():
                            return {"status": "STOP_SQLITE_ERROR", "deleted": deleted}
                        for _ in range(limits.busy_retries):
                            sleep(0)
                            try:
                                cursor = conn.execute(
                                    f"DELETE FROM {AUTHORIZED_TABLE} WHERE rowid IN ({placeholders}) "
                                    "AND cached_at + ttl_seconds <= ?",
                                    (*rowids, cutoff),
                                )
                                conn.commit()
                                break
                            except sqlite3.OperationalError:
                                conn.rollback()
                        else:
                            return {"status": "STOP_PERSISTENT_BUSY", "deleted": deleted}
                    deleted += cursor.rowcount
            finally:
                conn.close()
    except sqlite3.Error:
        return {"status": "STOP_WRITE_LANE_ERROR", "deleted": deleted}
    return {"status": "STOP_ROW_CAP", "deleted": deleted}
