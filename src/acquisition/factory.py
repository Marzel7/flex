"""Composition boundary for optional acquisition observers."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Optional

import aiohttp

from .transaction import AcquisitionResponse, SharedTransactionAcquisition


class MirroringTransactionAcquisition(SharedTransactionAcquisition):
    """EP1.1 transport with a passive, non-waiting completion observer."""

    def __init__(self, *args: Any, mirror: Any, retained_store: Any = None, degraded_journal: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._mirror = mirror
        self._retained_store = retained_store
        self._degraded_journal = degraded_journal

    async def request_once(self, **kwargs: Any) -> AcquisitionResponse:
        response = await super().request_once(**kwargs)
        if self._retained_store is not None and response.error is None and response.status is not None:
            try:
                self._retained_store.retain(response, http_method=str(kwargs["http_method"]),
                                            url=str(kwargs["url"]), request_payload=kwargs.get("json_payload"))
                self._retained_store.record_outcome(response, "RETAINED")
            except Exception as exc:
                if self._degraded_journal is not None: _journal_failure(self._degraded_journal.append(response.metadata.__dict__, stage="OBSERVATION_WRITE_FAILED", error=exc))
        self._mirror.publish_nowait(
            response,
            http_method=str(kwargs["http_method"]),
            url=str(kwargs["url"]),
            request_payload=kwargs.get("json_payload"),
        )
        return response


class _ParallelRetainedAcquisitionStore:
    """Run multiple retention writers for shadow/parallel RA4 activation."""

    def __init__(self, stores: list[Any]) -> None:
        self.stores = stores

    def _run(self, operation: str, *args: Any, **kwargs: Any) -> None:
        last_error: Exception | None = None
        all_failed = True
        for store in self.stores:
            try:
                getattr(store, operation)(*args, **kwargs)
                all_failed = False
            except Exception as exc:  # pragma: no cover - defensive path
                last_error = exc
        if all_failed and last_error is not None:
            raise last_error

    def retain(self, *args: Any, **kwargs: Any):
        return self._run("retain", *args, **kwargs)

    def record_outcome(self, *args: Any, **kwargs: Any):
        return self._run("record_outcome", *args, **kwargs)

    def combined_accounting(self, *args: Any, **kwargs: Any):
        for store in self.stores:
            if hasattr(store, "combined_accounting"):
                return store.combined_accounting(*args, **kwargs)
        return {
            "eligible_total": 0,
            "retained_total": 0,
            "failed_with_gap_total": 0,
            "not_retainable_total": 0,
            "failed_gap_write_failed_total": 0,
            "unresolved_durable_total": 0,
            "invalid_journal_event_total": 0,
            "accounting_residual": 0,
        }

    def health(self) -> dict[str, Any]:
        for store in self.stores:
            if hasattr(store, "health"):
                try:
                    return store.health()
                except Exception:
                    continue
        raise RuntimeError("all_retained_stores_unhealthy")

    def record_gap(self, *args: Any, **kwargs: Any):
        return self._run("record_gap", *args, **kwargs)

    def get(self, *args: Any, **kwargs: Any):
        rows: list[Any] = []
        for store in self.stores:
            try:
                rows.extend(store.get(*args, **kwargs))
            except Exception:
                continue
        return rows


class RetainingTransactionAcquisition(SharedTransactionAcquisition):
    """Fail-open prospective retention of completed acquisition context."""
    def __init__(self, *args: Any, retained_store: Any, degraded_journal: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._retained_store = retained_store
        self._degraded_journal = degraded_journal

    async def request_once(self, **kwargs: Any) -> AcquisitionResponse:
        response = await super().request_once(**kwargs)
        if response.error is None and response.status is not None:
            try:
                self._retained_store.retain(response, http_method=str(kwargs["http_method"]),
                                            url=str(kwargs["url"]), request_payload=kwargs.get("json_payload"))
                self._retained_store.record_outcome(response, "RETAINED")
            except Exception as exc:
                # Do not stack gap/outcome writes against a known failed main store.
                if self._degraded_journal is not None: _journal_failure(self._degraded_journal.append(response.metadata.__dict__, stage="OBSERVATION_WRITE_FAILED", error=exc))
        return response


_MIRROR: Any = None
_MIRROR_LOCK = threading.Lock()
_LOGGER = logging.getLogger("acquisition.retention")
_RETENTION_DEGRADED_STATE = {"retention_degraded_accounting_lost_total": 0, "last_degraded_accounting_lost_at": None, "last_degraded_accounting_lost_stage": None, "last_degraded_accounting_lost_error_class": None}

def retention_degraded_health() -> dict[str, Any]:
    return dict(_RETENTION_DEGRADED_STATE)


def retention_health(store: Any = None, journal: Any = None, *, enabled: bool = False) -> dict[str, Any]:
    """Read-only, consolidated health for optional retention infrastructure."""
    base = {"retention_enabled": enabled, "main_retention_store_healthy": False,
            "degraded_journal_healthy": False, "journal_handoff_healthy": False,
            "journal_pending_depth": 0, "journal_oldest_pending_age": None,
            "journal_persist_failures": 0, "eligible_total": 0, "retained_total": 0,
            "failed_with_gap_total": 0, "not_retainable_total": 0,
            "failed_gap_write_failed_total": 0, "unresolved_durable_total": 0,
            "invalid_journal_event_total": 0, "accounting_residual": 0,
            "main_store_busy_timeout_ms": 50, "last_retained_at": None,
            "last_failure_at": None, "last_degraded_journal_event_at": None,
            **retention_degraded_health()}
    if not enabled:
        return {**base, "status": "DISABLED"}
    try:
        if store is not None:
            base.update(store.combined_accounting(journal))
            try:
                base["last_retained_at"] = store.health().get("last_retained_at")
            except Exception:
                pass
            base["main_retention_store_healthy"] = True
    except Exception as exc:
        base["last_failure_at"] = time.time(); base["main_retention_store_healthy"] = False
        base["main_store_error"] = type(exc).__name__
    if journal is not None and hasattr(journal, "health"):
        base.update(journal.health())
    elif journal is not None:
        base["degraded_journal_healthy"] = True; base["journal_handoff_healthy"] = True
    base["last_failure_at"] = base["last_degraded_accounting_lost_at"]
    if base["retention_degraded_accounting_lost_total"]:
        status = "CATASTROPHIC_ACCOUNTING_LOSS"
    elif base["journal_pending_depth"]:
        status = "DEGRADED_PENDING_JOURNAL"
    elif base["failed_gap_write_failed_total"]:
        status = "DURABLE_DEGRADED_ACCOUNTING"
    elif not base["main_retention_store_healthy"] or not base["degraded_journal_healthy"]:
        status = "UNHEALTHY_RETENTION_INFRASTRUCTURE"
    else:
        status = "HEALTHY"
    return {**base, "status": status}

def _journal_failure(result: Any) -> None:
    if result.status != "JOURNAL_PERSIST_FAILED":
        return
    _RETENTION_DEGRADED_STATE["retention_degraded_accounting_lost_total"] += 1
    _RETENTION_DEGRADED_STATE["last_degraded_accounting_lost_at"] = time.time()
    _RETENTION_DEGRADED_STATE["last_degraded_accounting_lost_stage"] = result.failure_stage
    _RETENTION_DEGRADED_STATE["last_degraded_accounting_lost_error_class"] = result.error_class
    _LOGGER.critical(
        "retention_degraded_accounting_lost",
        extra={
            "event": "RETENTION_DEGRADED_ACCOUNTING_LOST",
            "failure_stage": result.failure_stage,
            "error_class": result.error_class,
        },
    )


def _configured_mirror() -> Any:
    global _MIRROR
    if _MIRROR is not None:
        return _MIRROR
    from src.evidence.config import EvidenceConfig

    config = EvidenceConfig.from_env()
    if not (config.platform_enabled and config.mirror_enabled):
        return None
    with _MIRROR_LOCK:
        if _MIRROR is None:
            from src.evidence.mirror import EvidenceMirrorPublisher
            _MIRROR = EvidenceMirrorPublisher(config)
    return _MIRROR


def _configured_retained_store() -> Any:
    from src.evidence.config import EvidenceConfig
    config = EvidenceConfig.from_env()
    if not (config.retained_acquisition_observations_enabled or config.retained_acquisition_v2_shadow_enabled):
        return None
    from src.acquisition.retained_observations import (
        RetainedAcquisitionStore,
        RetainedAcquisitionStoreV2,
    )
    from src.evidence.artifacts import ArtifactStore
    stores = []
    if config.retained_acquisition_observations_enabled:
        stores.append(RetainedAcquisitionStore(
            config.retained_acquisition_database_path,
            ArtifactStore(config.artifact_path, enabled=True),
        ))
    if config.retained_acquisition_v2_shadow_enabled:
        stores.append(RetainedAcquisitionStoreV2(
            config.retained_acquisition_v2_shadow_database_path,
            ArtifactStore(config.retained_acquisition_v2_shadow_artifact_path, enabled=True),
            daily_payload_cap_bytes=config.retained_acquisition_v2_daily_payload_cap_bytes,
        ))
    if not stores:
        return None
    store = stores[0] if len(stores) == 1 else _ParallelRetainedAcquisitionStore(stores)
    from src.acquisition.degraded_accounting import BoundedDegradedJournalHandoff, DegradedAccountingJournal
    return store, BoundedDegradedJournalHandoff(
        DegradedAccountingJournal(config.retained_acquisition_degraded_path),
        max_pending=config.retained_acquisition_journal_buffer_size,
        on_persist_failure=_journal_failure,
    )


def build_transaction_acquisition(
    session: aiohttp.ClientSession,
    *,
    semaphore: Optional[asyncio.Semaphore] = None,
    telemetry_sink: Any = None,
) -> SharedTransactionAcquisition:
    """Return byte-compatible EP1.1 transport unless mirror is explicitly on."""
    mirror = _configured_mirror()
    retained = _configured_retained_store(); retained_store, degraded_journal = retained if retained else (None, None)
    cls: Any = MirroringTransactionAcquisition if mirror is not None else (RetainingTransactionAcquisition if retained_store is not None else SharedTransactionAcquisition)
    kwargs = {"semaphore": semaphore, "telemetry_sink": telemetry_sink}
    if mirror is not None:
        kwargs["mirror"] = mirror
    if retained_store is not None:
        kwargs["retained_store"] = retained_store
        kwargs["degraded_journal"] = degraded_journal
    return cls(session, **kwargs)


def reset_mirror_for_tests() -> None:
    global _MIRROR
    with _MIRROR_LOCK:
        if _MIRROR is not None:
            _MIRROR.stop()
        _MIRROR = None
