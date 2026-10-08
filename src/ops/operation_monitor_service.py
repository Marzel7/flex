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
    _qualified_live_entry,
    _read_only_connection,
    production_queue,
    reconcile_qualified_monitor_fact_queue_projection,
    reconcile_byzantine_assignment_admissions,
    reconcile_watchtower_assignment_admissions,
    reconcile_watchtower_deep_assignment_admissions,
)
from src.ops.token_data_provider_bindings import (
    AUTHORITATIVE_BIRDEYE_CREDENTIAL_LABEL,
    production_provider_bindings,
)
from src.ops.operation_monitor_worker import MonitorBirdeyeTransport

_STOP = False
NO_TRANSPORT_PREFLIGHT_MODE = 'MONITOR_PREFLIGHT_NO_TRANSPORT'


def _sleep_until_next_idle_tick(deadline: float) -> bool:
    """Sleep at most one tick; return false when the deadline has passed."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    time.sleep(min(0.25, remaining))
    return True


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


def run_once(*, worker: MonitorWorker, queue, db_path: str) -> None:
    """One production Monitor iteration, shared verbatim by the loop and tests."""
    if queue.selection_only_mode():
        # A fixed-watermark DEV soak must not reconcile retained source facts
        # into fresh queue work.  Existing messages are still claimed through
        # MonitorQueue.soak_allows(), so only the explicit post-watermark batch
        # can reach the normal worker dispatch path.
        queue.recover_due()
        return worker.process_once()
    reconcile_byzantine_assignment_admissions(db_path, queue)
    reconcile_watchtower_assignment_admissions(db_path, queue)
    reconcile_watchtower_deep_assignment_admissions(db_path, queue)
    reconcile_qualified_monitor_fact_queue_projection(db_path, queue)
    worker.reconcile_stale_watchtower_pending_openings()
    worker.reconcile_exhausted_watchtower_strict_openings()
    worker.reconcile_exhausted_byzantine_provider_failures()
    queue.recover_due()
    worker.reconcile_retained_watchtower_facts()
    worker.reconcile_terminal_ath_jobs()
    worker.process_entry_reference_opening_once()
    worker.process_once()


def preflight_once(*, queue, db_path: str) -> dict[str, object]:
    """Read only the live Monitor's dispatch prerequisites.

    This generic safety mode intentionally never calls reconciliation, queue
    claim/recovery, finalization, opening work, or ``process_once``.  Those
    paths can mutate state or reach a provider.  It is useful for verifying an
    exact Supervisor environment without turning a config check into traffic.
    """
    allowlist = sorted(queue._soak_allowlist() or ())
    with _read_only_connection(db_path, timeout=5) as conn:
        rows = conn.execute(
            "SELECT operation_id,mint,entry_status,entry_timestamp,entry_mc_usd,entry_native_mc_sol,monitor_state,next_observation_at FROM operation_monitor_facts "
            "WHERE mint IN (%s)" % ','.join('?' for _ in allowlist), allowlist
        ).fetchall() if allowlist else []
    facts = [tuple(row) for row in rows]
    # This is deliberately an eligibility projection, not a scheduler or
    # queue claim.  It exercises the same semantic entry predicate as the
    # worker while retaining the no-transport mode's read-only contract.
    eligible_mints = [
        str(row[1]) for row in rows
        if str(row[6]) == 'MONITORING_ACTIVE' and _qualified_live_entry({
            'entry_timestamp': row[3],
            'entry_mc_usd': row[4],
            'entry_native_mc_sol': row[5],
            'entry_reference_state': 'NATIVE_QUALIFIED' if row[5] is not None and str(row[2]) == 'QUALIFIED' else '',
        })
    ]
    identity_counts = {
        mint: len(queue.current_fact_identities(operation_id=str(row[0]), mint=mint))
        for row in rows for mint in [str(row[1])]
        if mint in eligible_mints
    }
    blank_retry_mints = []
    for row in rows:
        mint = str(row[1])
        if mint not in eligible_mints:
            continue
        for state, _path, _payload, envelope in queue.current_fact_identities(operation_id=str(row[0]), mint=mint):
            retryable = state == 'retry' or str(envelope.get('monitor_state') or '') in {'RETRYABLE_DEFERRED', 'NO_USABLE_CANDLE', 'PROVIDER_BACKOFF'}
            deadline = envelope.get('next_eligible_dispatch_at') or envelope.get('recovery_deadline_at') or envelope.get('backoff_until')
            if retryable and not deadline:
                blank_retry_mints.append(mint)
    missing_current_identity_mints = sorted(mint for mint in eligible_mints if identity_counts.get(mint) == 0)
    duplicate_current_identity_mints = sorted(mint for mint in eligible_mints if identity_counts.get(mint, 0) > 1)
    would_dispatch_mints = sorted(mint for mint in eligible_mints if identity_counts.get(mint) == 1)
    return {
        'mode': NO_TRANSPORT_PREFLIGHT_MODE,
        'allowlist': allowlist,
        'facts': facts,
        'would_dispatch_mints': would_dispatch_mints,
        'would_dispatch_count': len(would_dispatch_mints),
        'current_queue_identity_counts': identity_counts,
        'missing_current_identity_mints': missing_current_identity_mints,
        'duplicate_current_identity_mints': duplicate_current_identity_mints,
        'blank_retry_mints': sorted(set(blank_retry_mints)),
        'current_queue_identity_invariant_passed': not missing_current_identity_mints and not duplicate_current_identity_mints and not blank_retry_mints,
        'queue_depth': queue.queue.depth(),
        'transport_calls': 0,
    }


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
    no_transport = os.getenv(NO_TRANSPORT_PREFLIGHT_MODE, '').strip().lower() in {'1', 'true', 'yes', 'on'}
    if no_transport:
        # Deliberately do not construct provider bindings in this mode.
        while not _STOP:
            preflight_once(queue=queue, db_path=db_path)
            deadline = time.monotonic() + interval
            while not _STOP and _sleep_until_next_idle_tick(deadline):
                pass
        return
    # One shared generic registry: this adds no RPC client or dispatch loop.
    # It is used only when durable ENTRY_REFERENCE_OPENING work is present.
    bindings = production_provider_bindings(
        helius_endpoint=os.environ.get('HELIUS_RPC_URL'),
        birdeye_credential_label=os.environ.get(
            'BIRDEYE_CREDENTIAL_LABEL', AUTHORITATIVE_BIRDEYE_CREDENTIAL_LABEL,
        ),
    )
    worker = MonitorWorker(queue, db_path=db_path, provider_bindings=bindings)
    current_price_scheduler = None
    # Watchtower's default monitor contract is historical completed-15m
    # evidence.  The optional faster layer is a distinct trading mode and
    # must be explicitly selected; it can never be reached by the normal
    # DEV Watchtower launcher.
    if (os.getenv('MONITOR_RUNTIME') == 'dev'
            and os.getenv('OPERATION_MONITOR_PRICE_MODE', 'HISTORICAL').upper() == 'TRADING'):
        # This is an isolated DEV-only live layer.  It shares the existing
        # Birdeye binding, compact provider gate and persistent DEV-012 budget.
        from src.ops.dev_current_price_scheduler import DevCurrentPriceScheduler
        transport = MonitorBirdeyeTransport(bindings[('BIRDEYE', 'OHLCV')])
        def fetch_current(mint: str, start: int, _end: int) -> list[dict]:
            response = transport({'mint': mint, 'entry_timestamp': start,
                                  'last_observation_at': start,
                                  'candle_resolution': '1m'})
            return [{'timestamp': row['timestamp'], 'close': row['mc'],
                     'high': row.get('high', row['mc'])}
                    for row in response['candles']]
        current_price_scheduler = DevCurrentPriceScheduler(
            db_path=db_path, queue=queue, fetch=fetch_current)
    while not _STOP:
        run_once(worker=worker, queue=queue, db_path=db_path)
        if current_price_scheduler is not None:
            current_price_scheduler.tick()
        deadline = time.monotonic() + interval
        while not _STOP and _sleep_until_next_idle_tick(deadline):
            pass


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    run_loop()


if __name__ == '__main__':
    main()
