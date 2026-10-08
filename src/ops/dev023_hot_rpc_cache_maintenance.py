"""Disabled-by-default runtime adapter for the DEV-023 cache contract.

This module is a bounded *tick*, not a scheduler or daemon.  A future
approved scheduler may call :func:`run_retention_tick`; absent an explicit
``DEV023_HOT_RPC_RETENTION_ENABLED=1`` it returns ``DISABLED`` before opening
or registering a database.  The existing ``DatabaseWriteService`` retains
exclusive ownership of the canonical write lane and receives P3 housekeeping
priority, so normal ingestion and operational writes outrank retention.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.database_write_service import (
    CrossProcessDatabaseWriteTimeout,
    DatabaseWriteLockError,
    PRIORITY_P3_HOUSEKEEPING,
    database_write_service,
)
from src.ops.dev023_hot_rpc_cache_retention import (
    RetentionLimits,
    retain_expired_rpc_cache_batch_in_transaction,
)


DEFAULT_CADENCE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class RuntimeRetentionConfig:
    """Explicit runtime authority; scheduling remains opt-in and external."""

    enabled: bool
    canonical_database_path: str
    cutoff: float
    stop_file: str
    cadence_seconds: int = DEFAULT_CADENCE_SECONDS
    limits: RetentionLimits = RetentionLimits()


def config_from_environment(*, canonical_database_path: str, cutoff: float) -> RuntimeRetentionConfig:
    """Read only explicit opt-in values; default is permanently disabled."""
    return RuntimeRetentionConfig(
        enabled=os.environ.get("DEV023_HOT_RPC_RETENTION_ENABLED", "0") == "1",
        canonical_database_path=canonical_database_path,
        cutoff=cutoff,
        stop_file=os.environ.get("DEV023_HOT_RPC_RETENTION_STOP_FILE", ""),
        cadence_seconds=int(os.environ.get("DEV023_HOT_RPC_RETENTION_CADENCE_SECONDS", DEFAULT_CADENCE_SECONDS)),
    )


def _preflight(config: RuntimeRetentionConfig) -> str | None:
    path = Path(config.canonical_database_path).resolve()
    if config.cadence_seconds <= 0:
        return "REJECTED_INVALID_SCHEDULE"
    if not path.is_file():
        return "REJECTED_MISSING_DATABASE"
    if config.stop_file and Path(config.stop_file).exists():
        return "STOP_FILE"
    if shutil.disk_usage(path.parent).free < config.limits.min_free_bytes:
        return "STOP_DISK_FLOOR"
    try:
        wal_size = path.with_name(path.name + "-wal").stat().st_size
    except FileNotFoundError:
        wal_size = 0
    if wal_size > config.limits.max_wal_bytes:
        return "STOP_WAL_CEILING"
    return None


def run_retention_tick(config: RuntimeRetentionConfig, *, write_service: Any = None) -> dict:
    """Submit one 200-row maximum maintenance transaction, or fail closed.

    There is deliberately no loop: one scheduler invocation can never retain
    more than a single reviewed batch.  The service owns lease acquisition,
    timeout, transaction release and priority ordering on every result path.
    """
    if not config.enabled:
        return {"status": "DISABLED", "deleted": 0}
    if config.limits.batch_rows > 200 or config.limits.max_rows_per_run > 200:
        return {"status": "REJECTED_INVALID_LIMITS", "deleted": 0}
    stopped = _preflight(config)
    if stopped:
        return {"status": stopped, "deleted": 0}

    path = str(Path(config.canonical_database_path).resolve())
    selector = f"dev023-hot-rpc-cache:{path}"
    service = write_service or database_write_service
    service.register_database(selector, path)

    def transaction(conn):
        # Re-check immediately inside the service-owned lease; a scheduler
        # preflight can never authorize a later mutation by itself.
        current_stop = _preflight(config)
        if current_stop:
            return {"status": current_stop, "deleted": 0}
        return retain_expired_rpc_cache_batch_in_transaction(
            conn, cutoff=config.cutoff, limits=config.limits
        )

    try:
        return service.submit(
            selector,
            "dev023-hot-rpc-cache-retention",
            transaction,
            priority=PRIORITY_P3_HOUSEKEEPING,
        )
    except (CrossProcessDatabaseWriteTimeout, DatabaseWriteLockError):
        return {"status": "STOP_WRITE_LANE_TIMEOUT", "deleted": 0}
    except Exception:
        # Do not treat an unknown runner/service failure as permission to
        # retry or use a direct SQLite connection.
        return {"status": "STOP_WRITE_LANE_ERROR", "deleted": 0}
