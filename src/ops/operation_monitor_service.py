"""Supervisor entrypoint for the existing durable MonitorWorker.

This module owns process lifecycle only.  Queue, transport, reduction, shared
writer persistence, and ACK semantics remain in ``operation_monitor_worker``.
"""
from __future__ import annotations

import os
import signal
import sqlite3
import time
from pathlib import Path

from src.ops.operation_monitor_worker import (
    MonitorWorker,
    _read_only_connection,
    production_queue,
    reconcile_byzantine_assignment_admissions,
)

_STOP = False


def _stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True


def validate_monitor_schema(path: str) -> None:
    """Read-only startup validation; this worker never runs DDL."""
    with _read_only_connection(path, timeout=5) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    required = {'operation_monitor_facts', 'operation_monitor_observations'}
    missing = required - tables
    if missing:
        raise RuntimeError(f"MONITOR_SCHEMA_NOT_PROVISIONED:{','.join(sorted(missing))}")


def run_loop(*, idle_seconds: float | None = None) -> None:
    """Single-concurrency, bounded-idle Supervisor loop."""
    global _STOP
    _STOP = False
    if os.getenv('OPERATIONS_MODE', 'OFF').upper() != 'MONITOR':
        raise RuntimeError('MONITOR_MODE_NOT_ENABLED')
    db_path = os.environ.get('WT_OPS_DB_PATH')
    if not db_path:
        raise RuntimeError('WT_OPS_DB_PATH_REQUIRED')
    validate_monitor_schema(db_path)
    queue = production_queue()
    if not queue.enabled:
        raise RuntimeError('MONITOR_QUEUE_DISABLED')
    interval = float(idle_seconds or os.getenv('OPERATION_MONITOR_IDLE_SECONDS', '5'))
    interval = max(1.0, min(interval, 60.0))
    worker = MonitorWorker(queue, db_path=db_path)
    while not _STOP:
        # Assignment-first Byzantine visibility uses the existing Monitor queue.
        # This idempotent reconciliation is read-only against membership and
        # writes only a missing/repaired logical queue message after activation.
        reconcile_byzantine_assignment_admissions(db_path, queue)
        # Shared Birdeye backoff is timer-gated in the durable queue.  This only
        # moves an already-due identity; it performs no provider or DB work.
        queue.recover_due()
        worker.reconcile_retained_watchtower_facts()
        # Terminal ATH work is reconciled from already-committed terminal facts.
        # It shares the same queue, provider gate, and single worker.
        worker.reconcile_terminal_ath_jobs()
        # process_once owns claim/ACK/release.  It holds no DB handle while idle.
        worker.process_once()
        deadline = time.monotonic() + interval
        while not _STOP and time.monotonic() < deadline:
            time.sleep(min(0.25, deadline - time.monotonic()))


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    run_loop()


if __name__ == '__main__':
    main()
