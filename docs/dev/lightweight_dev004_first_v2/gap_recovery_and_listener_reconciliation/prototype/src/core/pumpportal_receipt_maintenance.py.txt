"""Explicit, bounded maintenance for compact PumpPortal receipts.

This module is intentionally *not* imported by the listener.  Normal birth
ingestion performs one deterministic bucket operation and no database query;
retirement is an operator-invoked, read-only-dependency check followed by a
same-filesystem rename only when every required dependency check is known.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from src.core.pumpportal_birth_audit import PumpPortalBirthAudit


RETENTION_SECONDS = 7 * 24 * 60 * 60
ACTIVE_STATUSES = {"pending", "queued", "running", "retry", "retry_pending", "unacked", "attempted"}


@dataclass(frozen=True)
class DependencyResult:
    status: str
    reason: str


class ReceiptDependencyChecker:
    """Fail-closed, indexed, read-only dependency checks for maintenance only."""

    # These tables have a mint-leading index and can be checked exactly.
    MINT_REFERENCES = (
        ("creator_resolution_queue", "mint", "status"),
        ("birth_persist_queue", "mint", "status"),
        ("wt_walkback_queue", "mint", "status"),
    )
    # Receipt retirement is allowed to use migration state only after the
    # explicitly qualified composite index exists.  Do not silently accept a
    # differently shaped index: the maintenance contract depends on a direct
    # mint/state lookup, never a queue scan.
    MIGRATION_TABLE = "migration_persist_queue"
    MIGRATION_INDEX = "idx_migration_persist_queue_mint_status"
    MIGRATION_ACTIVE = ("PENDING", "RETRY")
    MIGRATION_TERMINAL = "PROCESSED"

    def __init__(self, *database_paths: str | Path | None):
        self._connections = []
        for raw in database_paths:
            if raw is None:
                continue
            path = Path(raw)
            if not path.is_file():
                raise ValueError("DEPENDENCY_DATABASE_UNAVAILABLE")
            uri = f"file:{path.resolve()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            conn.execute("PRAGMA query_only=ON")
            self._connections.append(conn)

    def close(self):
        for conn in self._connections:
            conn.close()

    @staticmethod
    def _columns(conn, table):
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    @staticmethod
    def _has_index(conn, table, key):
        return ReceiptDependencyChecker._has_index_prefix(conn, table, (key,))

    @staticmethod
    def _has_index_prefix(conn, table, columns):
        for index in conn.execute(f"PRAGMA index_list({table})"):
            name = index[1]
            indexed = [row[2] for row in conn.execute(f"PRAGMA index_info({name})")]
            if indexed[:len(columns)] == list(columns):
                return True
        return False

    def _handoff_reference(self, conn, tables, mint):
        required = {"wb_create_control", "wb_create_delivery"}
        if not required <= tables:
            return DependencyResult("UNKNOWN", "MISSING_DEPENDENCY_TABLE:wb_create_delivery")
        control_columns = self._columns(conn, "wb_create_control")
        delivery_columns = self._columns(conn, "wb_create_delivery")
        if not {"id", "epoch"} <= control_columns or not {"epoch", "mint", "state"} <= delivery_columns:
            return DependencyResult("UNKNOWN", "UNINDEXED_OR_INCOMPATIBLE:wb_create_delivery")
        if not self._has_index_prefix(conn, "wb_create_delivery", ("epoch", "mint")):
            return DependencyResult("UNKNOWN", "UNINDEXED_OR_INCOMPATIBLE:wb_create_delivery")
        plan = " ".join(str(value) for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT state FROM wb_create_delivery WHERE epoch=? AND mint=? LIMIT 1",
            ("epoch", mint),
        ) for value in row)
        if "SCAN wb_create_delivery" in plan.upper():
            return DependencyResult("UNKNOWN", "UNINDEXED_OR_INCOMPATIBLE:wb_create_delivery")
        # id is the control table's primary key.  The delivery query is an
        # exact UNIQUE(epoch,mint) lookup, never a mint scan.
        control = conn.execute("SELECT epoch FROM wb_create_control WHERE id=1").fetchone()
        if control is None:
            return DependencyResult("UNKNOWN", "HANDOFF_CONTROL_UNAVAILABLE")
        row = conn.execute(
            "SELECT state FROM wb_create_delivery WHERE epoch=? AND mint=? LIMIT 1",
            (control[0], mint),
        ).fetchone()
        if row is not None and str(row[0]).lower() in {"pending", "blocked"}:
            return DependencyResult("PROTECTED", "ACTIVE_REFERENCE:wb_create_delivery")
        return DependencyResult("ELIGIBLE", "NO_ACTIVE_HANDOFF_REFERENCE")

    def _migration_reference(self, conn, tables, mint):
        if self.MIGRATION_TABLE not in tables:
            return DependencyResult("UNKNOWN", "MISSING_DEPENDENCY_TABLE:migration_persist_queue")
        columns = self._columns(conn, self.MIGRATION_TABLE)
        if not {"mint", "status"} <= columns:
            return DependencyResult("UNKNOWN", "UNINDEXED_OR_INCOMPATIBLE:migration_persist_queue")
        indexes = {row[1]: [info[2] for info in conn.execute(f"PRAGMA index_info({row[1]})")]
                   for row in conn.execute(f"PRAGMA index_list({self.MIGRATION_TABLE})")}
        if indexes.get(self.MIGRATION_INDEX) != ["mint", "status"]:
            return DependencyResult("UNKNOWN", "MISSING_REQUIRED_INDEX:migration_persist_queue")
        active_plan = " ".join(str(value) for row in conn.execute(
            f"EXPLAIN QUERY PLAN SELECT status FROM {self.MIGRATION_TABLE} "
            "WHERE mint=? AND status IN (?,?) LIMIT 1",
            (mint, *self.MIGRATION_ACTIVE),
        ) for value in row)
        nonterminal_plan = " ".join(str(value) for row in conn.execute(
            f"EXPLAIN QUERY PLAN SELECT status FROM {self.MIGRATION_TABLE} "
            "WHERE mint=? AND status<>? LIMIT 1",
            (mint, self.MIGRATION_TERMINAL),
        ) for value in row)
        plans = f"{active_plan} {nonterminal_plan}".upper()
        if ("SCAN MIGRATION_PERSIST_QUEUE" in plans or
                self.MIGRATION_INDEX.upper() not in plans):
            return DependencyResult("UNKNOWN", "UNINDEXED_OR_INCOMPATIBLE:migration_persist_queue")
        active = conn.execute(
            f"SELECT status FROM {self.MIGRATION_TABLE} WHERE mint=? AND status IN (?,?) LIMIT 1",
            (mint, *self.MIGRATION_ACTIVE),
        ).fetchone()
        if active is not None:
            return DependencyResult("PROTECTED", "ACTIVE_REFERENCE:migration_persist_queue")
        # PROCESSED is the only established terminal state.  Any future or
        # non-terminal value remains protected instead of being reclassified.
        nonterminal = conn.execute(
            f"SELECT status FROM {self.MIGRATION_TABLE} WHERE mint=? AND status<>? LIMIT 1",
            (mint, self.MIGRATION_TERMINAL),
        ).fetchone()
        if nonterminal is not None:
            return DependencyResult("PROTECTED", "NONTERMINAL_REFERENCE:migration_persist_queue")
        return DependencyResult("ELIGIBLE", "NO_ACTIVE_MIGRATION_REFERENCE")

    def check(self, mint: str) -> DependencyResult:
        if not self._connections:
            return DependencyResult("UNKNOWN", "NO_READ_ONLY_DATABASES")
        found_required = set()
        handoff_checked = False
        migration_connections = []
        for conn in self._connections:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, key, status_column in self.MINT_REFERENCES:
                if table not in tables:
                    continue
                found_required.add(table)
                columns = self._columns(conn, table)
                if key not in columns or not self._has_index(conn, table, key):
                    return DependencyResult("UNKNOWN", f"UNINDEXED_OR_INCOMPATIBLE:{table}")
                selected = status_column if status_column in columns else None
                query = f"SELECT {selected or key} FROM {table} WHERE {key}=? LIMIT 1"
                row = conn.execute(query, (mint,)).fetchone()
                if row is None:
                    continue
                if selected is None or str(row[0]).lower() in ACTIVE_STATUSES:
                    return DependencyResult("PROTECTED", f"ACTIVE_REFERENCE:{table}")
            if "wb_create_control" in tables or "wb_create_delivery" in tables:
                handoff = self._handoff_reference(conn, tables, mint)
                if handoff.status != "ELIGIBLE":
                    return handoff
                handoff_checked = True
            if self.MIGRATION_TABLE in tables:
                migration_connections.append((conn, tables))
        missing = {table for table, _key, _status in self.MINT_REFERENCES} - found_required
        if missing:
            # A missing approved dependency surface is not proof that no active
            # reference exists.  This deliberately blocks retirement until the
            # operator supplies the compatible Core/OPS bindings.
            return DependencyResult("UNKNOWN", "MISSING_DEPENDENCY_TABLE:" + ",".join(sorted(missing)))
        if not handoff_checked:
            return DependencyResult("UNKNOWN", "MISSING_DEPENDENCY_TABLE:wb_create_delivery")
        if not migration_connections:
            return DependencyResult("UNKNOWN", "MISSING_DEPENDENCY_TABLE:migration_persist_queue")
        # Check migration only after all other active references.  A missing
        # migration index must not mask a positively active Walkback, creator,
        # birth, or durable-handoff dependency.
        for conn, tables in migration_connections:
            migration = self._migration_reference(conn, tables, mint)
            if migration.status != "ELIGIBLE":
                return migration
        return DependencyResult("ELIGIBLE", "NO_ACTIVE_REFERENCES")


class CompactReceiptMaintenance:
    """One bounded maintenance pass; it never runs as part of receipt writes."""

    def __init__(self, receipt_root: str | Path, archive_root: str | Path | None = None, *, checker: ReceiptDependencyChecker):
        self.root = Path(receipt_root)
        self.archive = Path(archive_root) if archive_root else self.root / ".retired"
        self.checker = checker
        try:
            self.archive.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("ARCHIVE_MUST_BE_WITHIN_RECEIPT_ROOT") from exc

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8192), b""):
                digest.update(block)
        return digest.hexdigest()

    def _archive_target(self, source: Path) -> Path:
        relative = source.relative_to(self.root)
        return self.archive / relative

    def _archive_capacity(self, target: Path):
        # Bound retained reversible evidence to the same 98,304-file envelope.
        bucket = target.parent
        bucket.mkdir(mode=0o700, parents=True, exist_ok=True)
        if bucket.is_symlink():
            raise ValueError("ARCHIVE_SYMLINK")
        count = sum(1 for _ in bucket.glob("*.json"))
        if not target.exists() and count >= PumpPortalBirthAudit.MAX_RECEIPTS_PER_BUCKET:
            raise ValueError("ARCHIVE_CAPACITY")

    def _eligibility(self, row: dict[str, Any], now_ns: int) -> DependencyResult:
        event_ns = row.get("receipt_utc_ns", row.get("handoff_utc_ns"))
        # Retain the exact seven-day boundary; only evidence strictly older
        # than the accepted retention window can become eligible.
        if not isinstance(event_ns, int) or event_ns >= now_ns - RETENTION_SECONDS * 1_000_000_000:
            return DependencyResult("PROTECTED", "YOUNGER_THAN_SEVEN_DAYS")
        if row.get("canonical_conflict"):
            return DependencyResult("PROTECTED", "PROTECTED_AUDIT_CONFLICT")
        if row.get("kind") == "MIGRATION_HANDOFF":
            return DependencyResult("PROTECTED", "DURABLE_HANDOFF_RETAINED")
        if row.get("persistence_outcome") != "COMMITTED":
            return DependencyResult("PROTECTED", "UNRESOLVED_RECEIPT")
        mint = row.get("mint")
        if not isinstance(mint, str) or not mint:
            return DependencyResult("UNKNOWN", "MINT_UNAVAILABLE")
        return self.checker.check(mint)

    def run(self, *, start_bucket: int = 0, max_buckets: int = 1, max_candidates: int = 32, now_ns: int | None = None, include_legacy: bool = False) -> dict[str, Any]:
        if not 0 <= start_bucket < PumpPortalBirthAudit.BUCKET_COUNT:
            raise ValueError("MAINTENANCE_BUCKET")
        if not 1 <= max_buckets <= 16 or not 1 <= max_candidates <= 128:
            raise ValueError("MAINTENANCE_BOUND")
        now_ns = time.time_ns() if now_ns is None else now_ns
        report: dict[str, Any] = {"scanned": 0, "archived": [], "protected": [], "unknown": []}
        buckets = [f"{(start_bucket + offset) % 256:02x}" for offset in range(max_buckets)]
        directories: Iterable[Path] = [self.root / item for item in buckets]
        if include_legacy:
            directories = list(directories) + [self.root]
        for directory in directories:
            if not directory.is_dir() or directory.is_symlink():
                continue
            # Bounded enumeration: maintenance deliberately opens only a fixed
            # number of candidates in fixed bucket order.
            with os.scandir(directory) as entries:
                candidates = sorted(
                    (Path(entry.path) for entry in itertools.islice(
                        (entry for entry in entries if entry.name.endswith(".json") and entry.is_file(follow_symlinks=False)),
                        max_candidates + 1,
                    )),
                    key=lambda item: item.name,
                )[:max_candidates]
            for source in candidates:
                report["scanned"] += 1
                audit = PumpPortalBirthAudit(enabled=False, path=str(self.root / "unused"), compact_path=str(self.root))
                try:
                    row = audit._read_receipt(source)
                    decision = self._eligibility(row, now_ns)
                except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    report["unknown"].append({"receipt": str(source.relative_to(self.root)), "reason": f"UNREADABLE:{type(exc).__name__}"})
                    continue
                relative = str(source.relative_to(self.root))
                if decision.status == "PROTECTED":
                    report["protected"].append({"receipt": relative, "reason": decision.reason})
                    continue
                if decision.status != "ELIGIBLE":
                    report["unknown"].append({"receipt": relative, "reason": decision.reason})
                    continue
                target = self._archive_target(source)
                try:
                    if source.stat().st_dev != self.root.stat().st_dev:
                        raise ValueError("ARCHIVE_NOT_SAME_FILESYSTEM")
                    self._archive_capacity(target)
                    before = self._sha256(source)
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    os.replace(source, target)
                    if self._sha256(target) != before:
                        raise ValueError("ARCHIVE_INTEGRITY")
                    PumpPortalBirthAudit._fsync_directory(target.parent)
                    PumpPortalBirthAudit._fsync_directory(source.parent)
                    report["archived"].append({"receipt": relative, "archive": str(target.relative_to(self.root)), "sha256": before})
                except (OSError, ValueError) as exc:
                    report["unknown"].append({"receipt": relative, "reason": f"ARCHIVE_BLOCKED:{type(exc).__name__}"})
        return report
