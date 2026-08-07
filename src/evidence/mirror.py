"""Passive, asynchronous acquisition-response mirror for EP1.2."""

from __future__ import annotations

import hashlib
import json
import os
import queue as thread_queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.acquisition.transaction import AcquisitionResponse

from .artifacts import ArtifactStore
from .config import EvidenceConfig
from .metrics import EvidenceMetrics
from .queue import EvidenceIntakeQueue


MIRROR_SOURCE_VERSION = "ep1.2-mirror-v1"
MIRROR_PARSER_VERSION = "raw-acquisition-v1"


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _sanitize_url(url: str) -> str:
    """Retain request identity without persisting provider credentials."""
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in {"api-key", "apikey", "api_key", "key", "token"}
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _transaction_signatures(method: str, request_payload: Any, response_data: Any) -> list[str]:
    signatures: set[str] = set()
    if method == "getTransaction" and isinstance(request_payload, dict):
        params = request_payload.get("params") or []
        if params and isinstance(params[0], str):
            signatures.add(params[0])
    if isinstance(request_payload, dict):
        transactions = request_payload.get("transactions")
        if isinstance(transactions, list):
            signatures.update(item for item in transactions if isinstance(item, str))
    candidates: list[Any] = []
    if isinstance(response_data, list):
        candidates = response_data
    elif isinstance(response_data, dict):
        result = response_data.get("result")
        if isinstance(result, list):
            candidates = result
        elif isinstance(result, dict):
            candidates = [result]
    for item in candidates:
        if isinstance(item, dict) and isinstance(item.get("signature"), str):
            signatures.add(item["signature"])
    return sorted(signatures)


@dataclass(frozen=True)
class MirrorItem:
    metadata: dict[str, Any]
    http_method: str
    url: str
    request_payload: Any
    response_status: int
    response_data: Any
    response_text: Optional[str]
    response_headers: dict[str, str]
    producer_handoff_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MirrorItem":
        return cls(**dict(value))


class EvidenceMirrorPublisher:
    """Bounded non-waiting handoff with durable failure/back-pressure spool."""

    def __init__(
        self,
        config: EvidenceConfig,
        *,
        artifacts: ArtifactStore | None = None,
        intake: EvidenceIntakeQueue | None = None,
        metrics: EvidenceMetrics | None = None,
        clock: Any = time.time,
    ) -> None:
        self.config = config
        self.metrics = metrics or EvidenceMetrics()
        self.clock = clock
        enabled = self.enabled
        self.artifacts = artifacts or ArtifactStore(
            config.artifact_path,
            enabled=enabled and config.artifact_store_enabled,
            metrics=self.metrics,
        )
        self.intake = intake or EvidenceIntakeQueue(
            config.queue_path,
            enabled=enabled and config.queue_enabled,
            max_messages=config.queue_max_messages,
            max_bytes=config.queue_max_bytes,
            max_attempts=config.max_attempts,
            metrics=self.metrics,
        )
        self._handoff: thread_queue.Queue[MirrorItem] = thread_queue.Queue(
            maxsize=config.mirror_buffer_size
        )
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._replay_lock = threading.Lock()
        self._stop = threading.Event()
        self._last_published_at: float | None = None
        self._last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.config.platform_enabled and self.config.mirror_enabled)

    def _ensure_started(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        with self._thread_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="evidence-mirror", daemon=True
            )
            self._thread.start()
            self.metrics.increment("mirror_started")

    @staticmethod
    def item_from_response(
        response: AcquisitionResponse,
        *,
        http_method: str,
        url: str,
        request_payload: Any,
        handoff_at: float,
    ) -> MirrorItem:
        return MirrorItem(
            metadata=asdict(response.metadata),
            http_method=http_method.upper(),
            url=_sanitize_url(url),
            request_payload=request_payload,
            response_status=int(response.status or 0),
            response_data=response.data,
            response_text=response.text,
            response_headers=dict(response.headers),
            producer_handoff_at=handoff_at,
        )

    def publish_nowait(
        self,
        response: AcquisitionResponse,
        *,
        http_method: str,
        url: str,
        request_payload: Any,
    ) -> bool:
        if not self.enabled or response.error is not None or response.status is None:
            return False
        started = time.perf_counter()
        item = self.item_from_response(
            response,
            http_method=http_method,
            url=url,
            request_payload=request_payload,
            handoff_at=self.clock(),
        )
        self._ensure_started()
        try:
            self._handoff.put_nowait(item)
            self.metrics.increment("mirror_handoff")
            accepted = True
        except thread_queue.Full:
            self.metrics.increment("mirror_backpressure")
            accepted = self._spool(item, reason="handoff_full")
        self.metrics.observe(
            "mirror_producer_handoff_ms", (time.perf_counter() - started) * 1000
        )
        return accepted

    def _spool_path(self, item: MirrorItem) -> Path:
        identity = hashlib.sha256(_canonical({
            "acquisition_id": item.metadata["acquisition_id"],
            "provider": item.metadata["provider"],
            "retry_count": item.metadata["retry_count"],
            "response_status": item.response_status,
            "request": item.request_payload,
        })).hexdigest()
        return self.config.mirror_spool_path / f"{identity}.json"

    def _spool(self, item: MirrorItem, *, reason: str) -> bool:
        target = self._spool_path(item)
        temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = {"reason": reason, "spooled_at": int(self.clock()), "item": item.to_dict()}
            with temporary.open("xb") as handle:
                handle.write(_canonical(payload))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self.metrics.increment("mirror_spooled")
            return True
        except Exception as exc:
            self._last_error = str(exc)
            self.metrics.increment("mirror_dropped")
            return False
        finally:
            temporary.unlink(missing_ok=True)

    def _artifact_payload(self, item: MirrorItem) -> bytes:
        return _canonical({
            "status": item.response_status,
            "data": item.response_data,
            "text": item.response_text,
            "headers": item.response_headers,
        })

    def _acquisition_envelope(self, item: MirrorItem, artifact: Any) -> dict[str, Any]:
        request = {
            "http_method": item.http_method,
            "url": item.url,
            "payload": item.request_payload,
        }
        request_digest = hashlib.sha256(_canonical(request)).hexdigest()
        response_digest = artifact.digest
        metadata = item.metadata
        signatures = _transaction_signatures(
            str(metadata["method"]), item.request_payload, item.response_data
        )
        envelope_identity = _canonical({
            "acquisition_id": metadata["acquisition_id"],
            "provider": metadata["provider"],
            "retry_count": metadata["retry_count"],
            "response_digest": response_digest,
        })
        envelope_id = f"acq-{hashlib.sha256(envelope_identity).hexdigest()}"
        return {
            "envelope_id": envelope_id,
            "observed_at": int(metadata["timestamp"]),
            "acquired_at": int(metadata["timestamp"]),
            "source": "shared_transaction_acquisition",
            "source_version": MIRROR_SOURCE_VERSION,
            "provider": metadata["provider"],
            "evidence_digest": response_digest,
            "replay_version": "1",
            "parser_version": MIRROR_PARSER_VERSION,
            "payload_type": "acquisition/response",
            "artifact": {
                "digest": artifact.digest,
                "size_bytes": artifact.size_bytes,
                "compressed_bytes": artifact.compressed_bytes,
                "content_type": artifact.content_type,
                "compression": artifact.compression,
            },
            "provenance": {
                "provider_request_id": metadata["acquisition_id"],
                "rpc_verification_state": "ACQUIRED_RESPONSE",
                "acquisition_method": metadata["method"],
                "source_metadata": {
                    "request_digest": request_digest,
                    "response_digest": response_digest,
                    "http_status": item.response_status,
                },
            },
            "acquisition": {
                "acquisition_id": metadata["acquisition_id"],
                "correlation_id": metadata["correlation_id"],
                "provider": metadata["provider"],
                "method": metadata["method"],
                "purpose": metadata["purpose"],
                "creator": metadata["creator"],
                "launch": metadata["launch"],
                "transaction_signatures": signatures,
                "cursor": metadata["cursor"],
                "request_digest": request_digest,
                "response_digest": response_digest,
                "timestamp": metadata["timestamp"],
                "parser_version": MIRROR_PARSER_VERSION,
                "retry_count": metadata["retry_count"],
                "cache_state": metadata["cache_state"],
                "artifact_reference": artifact.digest,
            },
        }

    def _publish(self, item: MirrorItem) -> None:
        raw = self._artifact_payload(item)
        artifact = self.artifacts.put(
            raw,
            metadata={
                "acquisition_id": item.metadata["acquisition_id"],
                "correlation_id": item.metadata["correlation_id"],
                "source": "evidence_mirror",
            },
        )
        envelope = self._acquisition_envelope(item, artifact)
        message_id = envelope["envelope_id"].replace("acq-", "mirror-")
        self.intake.enqueue(envelope, message_id=message_id)
        now = self.clock()
        self._last_published_at = now
        self._last_error = None
        self.metrics.increment("mirror_published")
        self.metrics.observe("mirror_publish_latency_ms", (now - item.producer_handoff_at) * 1000)
        self.metrics.observe("mirror_freshness_ms", (now - item.metadata["timestamp"]) * 1000)

    def _handle(self, item: MirrorItem) -> None:
        try:
            self._publish(item)
        except Exception as exc:
            self._last_error = str(exc)
            self.metrics.increment("mirror_failures")
            self.metrics.increment("mirror_retries")
            self._spool(item, reason=str(exc))

    def replay_spool(self, *, limit: int | None = None) -> int:
        if not self.enabled or not self.config.mirror_spool_path.exists():
            return 0
        with self._replay_lock:
            recovered = 0
            paths = sorted(self.config.mirror_spool_path.glob("*.json"))
            for path in paths[:limit]:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    self._publish(MirrorItem.from_dict(payload["item"]))
                    path.unlink()
                    recovered += 1
                    self.metrics.increment("mirror_recovered")
                except Exception as exc:
                    self._last_error = str(exc)
                    self.metrics.increment("mirror_replay_failures")
                    break
            return recovered

    def _run(self) -> None:
        while not self._stop.is_set():
            self.replay_spool(limit=100)
            try:
                item = self._handoff.get(timeout=self.config.mirror_retry_seconds)
            except thread_queue.Empty:
                continue
            try:
                self._handle(item)
            finally:
                self._handoff.task_done()

    def drain(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while self._handoff.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)
        return self._handoff.unfinished_tasks == 0

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def health(self) -> dict[str, Any]:
        if not self.enabled:
            return {
                "status": "DISABLED", "queue_depth": 0, "spool_depth": 0,
                "publish_latency_ms": None, "failures": 0, "retry_count": 0,
                "dropped_mirrors": 0, "recovered_mirrors": 0,
                "producer_handoff_latency_ms": None, "mirror_freshness_ms": None,
            }
        spool_depth = (
            len(list(self.config.mirror_spool_path.glob("*.json")))
            if self.config.mirror_spool_path.exists() else 0
        )
        status = "DEGRADED" if self._last_error or spool_depth else "HEALTHY"
        snapshot = self.metrics.snapshot()
        counters = snapshot["counters"]
        distributions = snapshot["distributions"]
        return {
            "status": status,
            "queue_depth": self._handoff.qsize(),
            "queue_capacity": self.config.mirror_buffer_size,
            "spool_depth": spool_depth,
            "last_published_at": self._last_published_at,
            "last_error": self._last_error,
            "publish_latency_ms": distributions.get("mirror_publish_latency_ms"),
            "failures": counters.get("mirror_failures", 0),
            "retry_count": counters.get("mirror_retries", 0),
            "dropped_mirrors": counters.get("mirror_dropped", 0),
            "recovered_mirrors": counters.get("mirror_recovered", 0),
            "producer_handoff_latency_ms": distributions.get("mirror_producer_handoff_ms"),
            "mirror_freshness_ms": distributions.get("mirror_freshness_ms"),
            "metrics": snapshot,
        }
