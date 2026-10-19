"""Atomic provisioning and read-only validation for ``wt_ops_v2.db``.

This module is deliberately *not* wired into any service startup path yet.
Legacy ``ensure_schema`` wrappers remain in place until a separately approved
deployment switches services to the read-only validation contract below.
"""
from __future__ import annotations

import sqlite3
import os
from dataclasses import dataclass, field
from collections.abc import Callable

from src.core import treasury_bank, walkback_queue, watchtower_attribution, ws_cascade_store
from src.ops import anchor_reconciliation
from src.utils.db_locking import db_connect

CURRENT_VERSION = 1
VERSION_TABLE = "wt_ops_schema_version"
VALID = "VALID"
SCHEMA_MIGRATION_REQUIRED = "SCHEMA_MIGRATION_REQUIRED"
INCOMPATIBLE_SCHEMA = "INCOMPATIBLE_SCHEMA"


class SchemaValidationError(RuntimeError):
    """A deterministic, fail-closed read-only schema validation result."""

    def __init__(self, state: str, detail: str):
        self.state = state
        self.detail = detail
        super().__init__(f"{state}:{detail}")


@dataclass
class MigrationLifecycle:
    """Compact test/diagnostic lifecycle; contains no SQL text or values."""
    connection_count: int = 0
    writer_acquire_count: int = 0
    transaction_begin_count: int = 0
    commit_count: int = 0
    rollback_count: int = 0
    release_count: int = 0
    max_concurrent_owners: int = 0
    _active_owners: int = 0
    events: list[str] = field(default_factory=list)

    def event(self, name: str) -> None:
        self.events.append(name)
        if name == "MIGRATION_ACQUIRE":
            self.writer_acquire_count += 1
            self._active_owners += 1
            self.max_concurrent_owners = max(self.max_concurrent_owners, self._active_owners)
        elif name == "MIGRATION_BEGIN":
            self.transaction_begin_count += 1
        elif name == "MIGRATION_COMMIT":
            self.commit_count += 1
        elif name == "MIGRATION_ROLLBACK":
            self.rollback_count += 1
        elif name == "MIGRATION_RELEASE":
            self.release_count += 1
            self._active_owners -= 1


# The composed walkback step owns the four nested leaves.  The remaining four
# independent roots are explicit here, yielding each of the nine DAG nodes
# exactly once without duplicating any migration logic.
MIGRATION_DAG: tuple[tuple[str, Callable[[sqlite3.Connection], dict]], ...] = (
    ("treasury_bank", treasury_bank.migrate_schema_step),
    ("ws_cascade_store", ws_cascade_store.migrate_schema_step),
    ("walkback_queue", walkback_queue.migrate_schema_step),
    ("anchor_reconciliation", anchor_reconciliation.migrate_schema_step),
    ("watchtower_attribution", watchtower_attribution.migrate_schema_step),
)

_REQUIRED_TABLES = frozenset({
    "wt_confirmed_treasuries",
    "wt_active_subprov_sessions",
    "wt_walkback_queue",
    "wt_attribution_outcomes",
    "wt_walkback_edge_candidates",
    "wt_watchtower_candidates",
    "operation_evidence_priority_requests",
    "wt_anchor_reconciliation_log",
    "watchtower_token_attribution",
})


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _schema_version(conn: sqlite3.Connection) -> int | None:
    if VERSION_TABLE not in _tables(conn):
        return None
    row = conn.execute(f"SELECT version FROM {VERSION_TABLE} WHERE id=1").fetchone()
    return None if row is None else int(row[0])


def validate_read_only(conn: sqlite3.Connection, *, require_version: bool = False) -> str:
    """Validate only via SELECT/PRAGMA; never starts a write transaction.

    ``require_version`` is used after a central migration.  Existing deployed
    schemas that pre-date the metadata table are accepted as compatible during
    the transition, so validation itself never creates a writer-contention
    path.
    """
    tables = _tables(conn)
    missing = sorted(_REQUIRED_TABLES - tables)
    if missing:
        raise SchemaValidationError(SCHEMA_MIGRATION_REQUIRED, f"missing_tables={missing}")
    try:
        walkback_queue.validate_schema(conn)
    except RuntimeError as exc:
        raise SchemaValidationError(SCHEMA_MIGRATION_REQUIRED, str(exc)) from exc
    version = _schema_version(conn)
    if version is None:
        if require_version:
            raise SchemaValidationError(SCHEMA_MIGRATION_REQUIRED, "version_metadata_absent")
        return VALID
    if version > CURRENT_VERSION:
        raise SchemaValidationError(INCOMPATIBLE_SCHEMA, f"future_version={version}")
    if version < CURRENT_VERSION:
        raise SchemaValidationError(SCHEMA_MIGRATION_REQUIRED, f"version={version}")
    return VALID


def migration_required(conn: sqlite3.Connection) -> bool:
    """Read-only preflight for an explicit migration command."""
    try:
        validate_read_only(conn)
    except SchemaValidationError as exc:
        if exc.state == SCHEMA_MIGRATION_REQUIRED:
            return True
        raise
    return False


def run_atomic_migration(
    conn: sqlite3.Connection, *, fail_at: str | None = None,
    lifecycle: MigrationLifecycle | None = None,
    on_event: Callable[[str], None] | None = None,
) -> dict:
    """Run the complete schema DAG under this function's one transaction.

    The supplied connection must be the one tracked writer connection owned by
    the explicit migration command.  No declarative DAG node owns a lease,
    connection, or transaction boundary.
    """
    if conn.in_transaction:
        raise RuntimeError("migration runner requires an idle connection")
    def emit(event: str) -> None:
        if lifecycle is not None:
            lifecycle.event(event)
        if on_event is not None:
            on_event(event)

    conn.execute("BEGIN IMMEDIATE")
    emit("MIGRATION_ACQUIRE")
    emit("MIGRATION_BEGIN")
    executed: list[str] = []
    try:
        # A second runner may have completed after this command's read-only
        # preflight but before we acquired the SQLite write transaction.  The
        # authoritative recheck is deliberately inside that transaction: it
        # prevents duplicate DDL/version writes while keeping the normal
        # already-current path entirely writer-free.
        if not migration_required(conn):
            conn.rollback()
            emit("MIGRATION_ROLLBACK")
            emit("MIGRATION_RELEASE")
            return {"migrated": False, "version": _schema_version(conn), "state": VALID}
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {VERSION_TABLE} "
            "(id INTEGER PRIMARY KEY CHECK (id=1), version INTEGER NOT NULL)"
        )
        if fail_at == "early":
            raise RuntimeError("injected migration failure: early")
        for name, step in MIGRATION_DAG:
            step(conn)
            executed.append(name)
            emit("MIGRATION_PROGRESS")
            if fail_at == name or (fail_at == "mid_dag" and name == "walkback_queue"):
                raise RuntimeError(f"injected migration failure: {name}")
        if fail_at == "pre_version":
            raise RuntimeError("injected migration failure: pre_version")
        conn.execute(
            f"INSERT INTO {VERSION_TABLE}(id, version) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET version=excluded.version",
            (CURRENT_VERSION,),
        )
        if fail_at == "version":
            raise RuntimeError("injected migration failure: version")
        validate_read_only(conn, require_version=True)
        if fail_at == "validation":
            raise RuntimeError("injected migration failure: validation")
        conn.commit()
        emit("MIGRATION_COMMIT")
        emit("MIGRATION_RELEASE")
        return {"migrated": True, "version": CURRENT_VERSION, "steps": tuple(executed)}
    except BaseException:
        emit("MIGRATION_FAILURE")
        if conn.in_transaction:
            conn.rollback()
            emit("MIGRATION_ROLLBACK")
        emit("MIGRATION_RELEASE")
        raise


def migrate_if_required(conn: sqlite3.Connection, *, fail_at: str | None = None) -> dict:
    """Avoid a writer transaction entirely for an already compatible schema."""
    if not migration_required(conn):
        return {"migrated": False, "version": _schema_version(conn), "state": VALID}
    return run_atomic_migration(conn, fail_at=fail_at)


def validate_database(path: str) -> str:
    """Canonical startup validator entrypoint: a read-only connection only."""
    conn = db_connect(path, read_only=True, _caller="operations-schema-validator")
    try:
        return validate_read_only(conn)
    finally:
        conn.close()


def migrate_database(
    path: str, *, fail_at: str | None = None,
    lifecycle: MigrationLifecycle | None = None,
    on_event: Callable[[str], None] | None = None,
) -> dict:
    """The sole explicit migration command entrypoint.

    It preflights through the read-only validator, then opens exactly one
    tracked write connection only when migration is actually required.  That
    connection's first DDL is the single writer-lane acquisition and its
    commit/rollback/close release it on every exit path.
    """
    if os.path.exists(path):
        try:
            state = validate_database(path)
        except SchemaValidationError as exc:
            if exc.state != SCHEMA_MIGRATION_REQUIRED:
                raise
        else:
            return {"migrated": False, "state": state}
    conn = db_connect(path, _caller="operations-schema-migration")
    if lifecycle is not None:
        lifecycle.connection_count += 1
    try:
        return run_atomic_migration(conn, fail_at=fail_at, lifecycle=lifecycle, on_event=on_event)
    finally:
        conn.close()


SERVICE_SCHEMA_SURFACES = {
    "walkback": ("wt_walkback_queue",),
    "cascade": ("wt_active_subprov_sessions",),
    "listener": ("wt_walkback_queue",),
    "scheduler": ("operation_evidence_priority_requests",),
    "monitor": ("watchtower_token_attribution",),
    "funding": ("wt_confirmed_treasuries",),
    "api": ("wt_walkback_queue", "watchtower_token_attribution"),
}
