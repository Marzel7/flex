"""Shared, operation-neutral transaction acquisition.

EP1.1 extracts transport concerns from existing consumers without changing
their interpretation or publishing Evidence.  Correlation metadata emitted by
this module is operational telemetry only.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping, Optional, Protocol, Sequence

import aiohttp


class CacheInterface(Protocol):
    """Minimal cache boundary; implementations retain their existing policy."""

    def get(self, key: str) -> Any: ...

    def set(self, key: str, value: Any, method: str) -> None: ...


@dataclass(frozen=True)
class AcquisitionContext:
    purpose: str = "unspecified"
    creator: Optional[str] = None
    launch: Optional[str] = None
    correlation_id: Optional[str] = None


@dataclass(frozen=True)
class AcquisitionMetadata:
    acquisition_id: str
    correlation_id: str
    purpose: str
    creator: Optional[str]
    launch: Optional[str]
    request_type: str
    provider: str
    method: str
    page_number: Optional[int]
    cursor: Optional[str]
    timestamp: float
    cache_state: str
    retry_count: int


@dataclass(frozen=True)
class AcquisitionResponse:
    status: Optional[int]
    data: Any
    text: Optional[str]
    headers: Mapping[str, str]
    metadata: AcquisitionMetadata
    latency_ms: float
    raw_body: Optional[bytes] = None
    artifact_representation: str = "RAW_BYTES_UNAVAILABLE"
    error: Optional[BaseException] = None


_CONTEXT: contextvars.ContextVar[AcquisitionContext] = contextvars.ContextVar(
    "transaction_acquisition_context", default=AcquisitionContext()
)


@contextmanager
def acquisition_scope(
    *, purpose: str, creator: Optional[str] = None, launch: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> Iterator[AcquisitionContext]:
    """Attach immutable correlation context to requests in this async task."""
    context = AcquisitionContext(
        purpose=purpose,
        creator=creator,
        launch=launch,
        correlation_id=correlation_id or str(uuid.uuid4()),
    )
    token = _CONTEXT.set(context)
    try:
        yield context
    finally:
        _CONTEXT.reset(token)


TelemetrySink = Callable[[AcquisitionMetadata], None]
MetricsSink = Callable[..., None]


def _provider_name(url: str) -> str:
    if "helius" in url:
        return "helius_rpc"
    if "solana.com" in url:
        return "solana_public_rpc"
    return "transaction_provider"


class SharedTransactionAcquisition:
    """Single transport owner for transaction requests.

    Callers retain parsing and business semantics. The compatibility methods
    intentionally accept legacy retry, timeout and metric settings so EP1.1
    can preserve production behaviour request-for-request.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        semaphore: Optional[asyncio.Semaphore] = None,
        telemetry_sink: Optional[TelemetrySink] = None,
    ) -> None:
        self._session = session
        self._semaphore = semaphore
        self._telemetry_sink = telemetry_sink

    @staticmethod
    def cache_get(cache: Optional[CacheInterface], key: str) -> Any:
        return None if cache is None else cache.get(key)

    @staticmethod
    def cache_set(
        cache: Optional[CacheInterface], key: str, value: Any, method: str
    ) -> None:
        if cache is not None:
            cache.set(key, value, method)

    def _metadata(
        self,
        *,
        acquisition_id: str,
        request_type: str,
        provider: str,
        method: str,
        page_number: Optional[int],
        cursor: Optional[str],
        cache_state: str,
        retry_count: int,
    ) -> AcquisitionMetadata:
        context = _CONTEXT.get()
        metadata = AcquisitionMetadata(
            acquisition_id=acquisition_id,
            correlation_id=context.correlation_id or acquisition_id,
            purpose=context.purpose,
            creator=context.creator,
            launch=context.launch,
            request_type=request_type,
            provider=provider,
            method=method,
            page_number=page_number,
            cursor=cursor,
            timestamp=time.time(),
            cache_state=cache_state,
            retry_count=retry_count,
        )
        if self._telemetry_sink is not None:
            self._telemetry_sink(metadata)
        return metadata

    async def request_once(
        self,
        *,
        http_method: str,
        url: str,
        timeout_seconds: float,
        request_type: str,
        method: str,
        json_payload: Any = None,
        page_number: Optional[int] = None,
        cursor: Optional[str] = None,
        cache_state: str = "none",
        retry_count: int = 0,
        acquisition_id: Optional[str] = None,
        metrics_sink: Optional[MetricsSink] = None,
        metric_fields: Optional[Mapping[str, Any]] = None,
    ) -> AcquisitionResponse:
        """Execute one HTTP attempt and normalize its transport response."""
        request_id = acquisition_id or str(uuid.uuid4())
        metadata = self._metadata(
            acquisition_id=request_id,
            request_type=request_type,
            provider=_provider_name(url),
            method=method,
            page_number=page_number,
            cursor=cursor,
            cache_state=cache_state,
            retry_count=retry_count,
        )
        start = time.time()
        try:
            request = self._session.get if http_method.upper() == "GET" else self._session.post
            kwargs: dict[str, Any] = {
                "timeout": aiohttp.ClientTimeout(total=timeout_seconds)
            }
            if http_method.upper() != "GET":
                kwargs["json"] = json_payload
            async with request(url, **kwargs) as response:
                latency_ms = (time.time() - start) * 1000
                text: Optional[str] = None
                data: Any = None
                raw_body = await response.read()
                try:
                    data = await response.json()
                except Exception:
                    text = await response.text()
                result = AcquisitionResponse(
                    status=response.status,
                    data=data,
                    text=text,
                    headers=dict(response.headers),
                    metadata=metadata,
                    latency_ms=latency_ms,
                    raw_body=raw_body,
                    artifact_representation="EXACT_PROVIDER_ARTIFACT",
                )
                if metrics_sink is not None:
                    metrics_sink(
                        status_code=response.status,
                        latency_ms=latency_ms,
                        **dict(metric_fields or {}),
                    )
                return result
        except Exception as error:
            return AcquisitionResponse(
                status=None,
                data=None,
                text=None,
                headers={},
                metadata=metadata,
                latency_ms=(time.time() - start) * 1000,
                error=error,
            )

    async def json_rpc_legacy(
        self,
        payload: dict[str, Any],
        *,
        rpc_urls: Sequence[str],
        max_retries: int,
        timeout_seconds: float,
        metrics_sink: MetricsSink,
        cache_action: str,
        credits_saved: int,
        page_number: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Compatibility implementation of creator funding's RPC policy."""
        request_id = str(uuid.uuid4())

        async def execute() -> Optional[dict[str, Any]]:
            for attempt in range(max_retries):
                for rpc_url in rpc_urls:
                    response = await self.request_once(
                        http_method="POST",
                        url=rpc_url,
                        json_payload=payload,
                        timeout_seconds=timeout_seconds,
                        request_type="json_rpc",
                        method=payload.get("method", "unknown"),
                        page_number=page_number,
                        cursor=cursor,
                        cache_state=cache_action,
                        retry_count=attempt,
                        acquisition_id=request_id,
                    )
                    if isinstance(response.error, asyncio.TimeoutError):
                        continue
                    if response.error is not None:
                        continue

                    status = int(response.status or 0)
                    if status != 200:
                        metrics_sink(
                            section="creator_funding", provider="helius_rpc",
                            method=payload.get("method", "unknown"), status_code=status,
                            latency_ms=response.latency_ms, mode="realtime", retries=attempt,
                            source_file="realtime_creator_funding_extractor",
                            cache_action=cache_action, credits_saved=credits_saved,
                            error=f"HTTP {status}",
                        )
                        if status == 429:
                            retry_after = response.headers.get("Retry-After")
                            try:
                                retry_delay = float(retry_after) if retry_after else None
                            except (ValueError, TypeError):
                                retry_delay = None
                            await asyncio.sleep(min(30.0, retry_delay or (0.5 * (2 ** attempt))))
                            continue
                        if status >= 500:
                            continue
                        return None

                    data = response.data
                    if not isinstance(data, dict):
                        continue
                    if "error" in data:
                        error_code = data["error"].get("code", -1)
                        metrics_sink(
                            section="creator_funding", provider="helius_rpc",
                            method=payload.get("method", "unknown"), status_code=200,
                            latency_ms=response.latency_ms, mode="realtime", retries=attempt,
                            source_file="realtime_creator_funding_extractor",
                            cache_action=cache_action, credits_saved=credits_saved,
                            error=f"RPC error {error_code}",
                        )
                        if error_code in {-32008, -32000, -32003, -32009}:
                            continue
                        return None
                    if "result" in data:
                        metrics_sink(
                            section="creator_funding", provider="helius_rpc",
                            method=payload.get("method", "unknown"), status_code=status,
                            latency_ms=response.latency_ms, mode="realtime", retries=attempt,
                            source_file="realtime_creator_funding_extractor",
                            cache_action=cache_action, credits_saved=credits_saved,
                        )
                        return data
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))
            return None

        if self._semaphore is None:
            return await execute()
        async with self._semaphore:
            return await execute()
