"""Composition boundary for optional acquisition observers."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Optional

import aiohttp

from .transaction import AcquisitionResponse, SharedTransactionAcquisition


class MirroringTransactionAcquisition(SharedTransactionAcquisition):
    """EP1.1 transport with a passive, non-waiting completion observer."""

    def __init__(self, *args: Any, mirror: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._mirror = mirror

    async def request_once(self, **kwargs: Any) -> AcquisitionResponse:
        response = await super().request_once(**kwargs)
        self._mirror.publish_nowait(
            response,
            http_method=str(kwargs["http_method"]),
            url=str(kwargs["url"]),
            request_payload=kwargs.get("json_payload"),
        )
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


def build_transaction_acquisition(
    session: aiohttp.ClientSession,
    *,
    semaphore: Optional[asyncio.Semaphore] = None,
    telemetry_sink: Any = None,
) -> SharedTransactionAcquisition:
    """Return byte-compatible EP1.1 transport unless mirror is explicitly on."""
    mirror = _configured_mirror()
    cls: Any = MirroringTransactionAcquisition if mirror is not None else SharedTransactionAcquisition
    kwargs = {"semaphore": semaphore, "telemetry_sink": telemetry_sink}
    if mirror is not None:
        kwargs["mirror"] = mirror
    return cls(session, **kwargs)


def reset_mirror_for_tests() -> None:
    global _MIRROR
    with _MIRROR_LOCK:
        if _MIRROR is not None:
            _MIRROR.stop()
        _MIRROR = None
