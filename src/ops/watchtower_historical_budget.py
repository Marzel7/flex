"""Shared Birdeye admission for bounded non-canonical historical research.

This is deliberately a very small adapter.  It does not create a historical
ledger or scheduler: every admission reaches ``MonitorQueue``'s established
atomic ``provider_budget.json`` gate under its existing lock.  Callers must
invoke it immediately before one transport attempt and must not retry after a
denial.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.ops.dev_provider_budget import BudgetDenied
from src.ops.operation_monitor_worker import MonitorQueue


HISTORICAL_FORENSICS_REQUEST_CLASS = "WATCHTOWER_HISTORICAL_FORENSICS_1M"


class HistoricalForensicsBudgetAdmission:
    """One-process duplicate guard around the canonical worker budget gate."""

    def __init__(self, queue_root: str | Path, *, queue_factory: Callable[..., MonitorQueue] = MonitorQueue):
        self.queue_root = Path(queue_root)
        self._queue = queue_factory(self.queue_root, enabled=True)
        self._admitted_request_ids: set[str] = set()

    def admit(self, *, mint: str, request_identity: str, now: int | None = None) -> None:
        if not mint or not request_identity:
            raise BudgetDenied("HISTORICAL_REQUEST_IDENTITY_REQUIRED")
        if request_identity in self._admitted_request_ids:
            raise BudgetDenied("DUPLICATE_HISTORICAL_PROVIDER_ADMISSION")
        # force_dev is intentional: a historical research invocation must not
        # silently bypass accounting merely because an operator shell omitted
        # MONITOR_RUNTIME=dev.  The ledger, limits, lock and backoff check are
        # still exactly those used by the live worker.
        self._queue.admit_provider_dispatch(
            mint,
            HISTORICAL_FORENSICS_REQUEST_CLASS,
            now=now,
            force_dev=True,
        )
        self._admitted_request_ids.add(request_identity)
