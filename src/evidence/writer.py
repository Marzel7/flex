from __future__ import annotations

import fcntl
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any, Callable

from .artifacts import ArtifactStore
from .config import EvidenceConfig
from .database import EvidenceDatabase
from .errors import ArtifactCorruption, ComponentDisabled, WriterOwnershipError
from .logging import log_event
from .metrics import EvidenceMetrics
from .queue import ClaimedMessage, EvidenceIntakeQueue


REQUIRED_ENVELOPE_FIELDS = {
    "envelope_id", "observed_at", "acquired_at", "source", "source_version",
    "provider", "evidence_digest", "replay_version", "parser_version",
    "payload_type", "artifact", "provenance",
}


class EvidenceWriter:
    def __init__(self, config: EvidenceConfig, queue: EvidenceIntakeQueue,
                 artifacts: ArtifactStore, *, metrics: EvidenceMetrics | None = None,
                 database_factory: Callable[[Path], EvidenceDatabase] = EvidenceDatabase,
                 after_commit: Callable[[], None] | None = None) -> None:
        self.config = config
        self.queue = queue
        self.artifacts = artifacts
        self.metrics = metrics or EvidenceMetrics()
        self.database = database_factory(config.database_path)
        self.after_commit = after_commit
        self._lease: Any = None
        self._started = False
        self._stopping = False
        self.logger = logging.getLogger("evidence.writer")

    def _acquire_ownership(self) -> None:
        lock_path = Path(f"{self.config.database_path}.writer.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise WriterOwnershipError("Another Evidence writer owns the database") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        self._lease = handle

    def start(self) -> None:
        if not (self.config.platform_enabled and self.config.writer_enabled):
            raise ComponentDisabled("Evidence writer is disabled")
        self.config.validate_isolation()
        self._acquire_ownership()
        try:
            self.queue.initialize()
            recovered = self.queue.recover()
            self.database.open_writer()
            self._started = True
            self.metrics.increment("writer_started")
            log_event(self.logger, logging.INFO, "writer_started", recovered=recovered,
                      database=str(self.config.database_path))
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        self.database.close()
        if self._lease is not None:
            try: fcntl.flock(self._lease.fileno(), fcntl.LOCK_UN)
            finally: self._lease.close()
            self._lease = None
        self._started = False
        self.metrics.increment("writer_stopped")

    @staticmethod
    def _validate(claimed: ClaimedMessage) -> None:
        envelope = claimed.payload.get("envelope")
        if not isinstance(envelope, dict) or not REQUIRED_ENVELOPE_FIELDS <= set(envelope):
            raise ValueError("Intake message does not contain a complete Evidence envelope")
        if not isinstance(envelope.get("artifact"), dict) or "digest" not in envelope["artifact"]:
            raise ValueError("Evidence envelope has no artifact digest")
        if not isinstance(envelope.get("provenance"), dict):
            raise ValueError("Evidence envelope has no provenance")

    def run_once(self) -> dict[str, int]:
        if not self._started:
            raise RuntimeError("Evidence writer is not started")
        started = time.monotonic()
        claimed = self.queue.claim(self.config.writer_batch_size)
        valid: list[ClaimedMessage] = []
        for item in claimed:
            try:
                self._validate(item)
                self.artifacts.verify(str(item.payload["envelope"]["artifact"]["digest"]))
                valid.append(item)
            except (ValueError, ArtifactCorruption, OSError) as exc:
                self.queue.nack(item, str(exc))
                self.metrics.increment("writer_rejected")
        if not valid:
            return {"claimed": len(claimed), "inserted": 0, "duplicates": 0, "failed": len(claimed)}
        try:
            result = self.database.append_batch([item.payload for item in valid])
            if self.after_commit:
                self.after_commit()
            for item in valid:
                self.queue.ack(item)
            self.metrics.increment("writer_inserted", result["inserted"])
            self.metrics.increment("writer_duplicates", result["duplicates"])
            self.metrics.observe("writer_batch_ms", (time.monotonic() - started) * 1000)
            return {"claimed": len(claimed), **result, "failed": len(claimed) - len(valid)}
        except BaseException as exc:
            for item in valid:
                if item.path.exists():
                    self.queue.nack(item, str(exc))
            self.metrics.increment("writer_failed", len(valid))
            log_event(self.logger, logging.ERROR, "batch_failed", count=len(valid), error=str(exc))
            raise

    def run_forever(self) -> None:
        self.start()
        self._stopping = False
        try:
            while not self._stopping:
                result = self.run_once()
                if result["claimed"] == 0:
                    time.sleep(self.config.writer_poll_seconds)
        finally:
            self.stop()

    def request_stop(self, *_: Any) -> None:
        self._stopping = True

    def health(self) -> dict[str, Any]:
        if not self.config.writer_enabled:
            return {"status": "DISABLED"}
        return {"status": "HEALTHY" if self._started else "STOPPED",
                "started": self._started, "pid": os.getpid() if self._started else None}


def main() -> int:
    from .service import EvidencePlatform
    config = EvidenceConfig.from_env()
    if not (config.platform_enabled and config.writer_enabled):
        return 0
    platform = EvidencePlatform(config)
    writer = platform.writer
    signal.signal(signal.SIGTERM, writer.request_stop)
    signal.signal(signal.SIGINT, writer.request_stop)
    writer.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
