"""Composition boundary for optional acquisition observers."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Optional

import aiohttp

from .transaction import AcquisitionResponse, SharedTransactionAcquisition


class MirroringTransactionAcquisition(SharedTransactionAcquisition):
    """EP1.1 transport with a passive, non-waiting completion observer."""

    def __init__(self, *args: Any, mirror: Any, retained_store: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._mirror = mirror
        self._retained_store = retained_store

    async def request_once(self, **kwargs: Any) -> AcquisitionResponse:
        response = await super().request_once(**kwargs)
        if self._retained_store is not None and response.error is None and response.status is not None:
            try:
                self._retained_store.retain(response, http_method=str(kwargs["http_method"]),
                                            url=str(kwargs["url"]), request_payload=kwargs.get("json_payload"))
                self._retained_store.record_outcome(response, "RETAINED")
            except Exception as exc:
                try:
                    self._retained_store.record_gap(response, str(exc))
                    self._retained_store.record_outcome(response, "FAILED_WITH_GAP")
                except Exception:
                    try: self._retained_store.record_outcome(response, "FAILED_GAP_WRITE_FAILED")
                    except Exception: pass
        self._mirror.publish_nowait(
            response,
            http_method=str(kwargs["http_method"]),
            url=str(kwargs["url"]),
            request_payload=kwargs.get("json_payload"),
        )
        return response


class RetainingTransactionAcquisition(SharedTransactionAcquisition):
    """Fail-open prospective retention of completed acquisition context."""
    def __init__(self, *args: Any, retained_store: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._retained_store = retained_store

    async def request_once(self, **kwargs: Any) -> AcquisitionResponse:
        response = await super().request_once(**kwargs)
        if response.error is None and response.status is not None:
            try:
                self._retained_store.retain(response, http_method=str(kwargs["http_method"]),
                                            url=str(kwargs["url"]), request_payload=kwargs.get("json_payload"))
                self._retained_store.record_outcome(response, "RETAINED")
            except Exception as exc:
                # Retention is observability only: never alter acquisition semantics.
                try:
                    self._retained_store.record_gap(response, str(exc))
                    self._retained_store.record_outcome(response, "FAILED_WITH_GAP")
                except Exception:
                    try: self._retained_store.record_outcome(response, "FAILED_GAP_WRITE_FAILED")
                    except Exception: pass
        return response


_MIRROR: Any = None
_MIRROR_LOCK = threading.Lock()


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
    if not config.retained_acquisition_observations_enabled:
        return None
    from src.acquisition.retained_observations import RetainedAcquisitionStore
    from src.evidence.artifacts import ArtifactStore
    return RetainedAcquisitionStore(
        config.retained_acquisition_database_path,
        ArtifactStore(config.artifact_path, enabled=True),
    )


def build_transaction_acquisition(
    session: aiohttp.ClientSession,
    *,
    semaphore: Optional[asyncio.Semaphore] = None,
    telemetry_sink: Any = None,
) -> SharedTransactionAcquisition:
    """Return byte-compatible EP1.1 transport unless mirror is explicitly on."""
    mirror = _configured_mirror()
    retained_store = _configured_retained_store()
    cls: Any = MirroringTransactionAcquisition if mirror is not None else (RetainingTransactionAcquisition if retained_store is not None else SharedTransactionAcquisition)
    kwargs = {"semaphore": semaphore, "telemetry_sink": telemetry_sink}
    if mirror is not None:
        kwargs["mirror"] = mirror
    if retained_store is not None:
        kwargs["retained_store"] = retained_store
    return cls(session, **kwargs)


def reset_mirror_for_tests() -> None:
    global _MIRROR
    with _MIRROR_LOCK:
        if _MIRROR is not None:
            _MIRROR.stop()
        _MIRROR = None
