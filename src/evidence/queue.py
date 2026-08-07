from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .errors import ComponentDisabled, QueueCorruption, QueueFull
from .metrics import EvidenceMetrics


@dataclass(frozen=True)
class ClaimedMessage:
    message_id: str
    payload: dict[str, Any]
    path: Path


class EvidenceIntakeQueue:
    STATES = ("pending", "processing", "retry", "dead_letter")

    def __init__(self, root: Path, *, enabled: bool = False, max_messages: int = 10_000,
                 max_bytes: int = 256 * 1024 * 1024, max_attempts: int = 5,
                 metrics: EvidenceMetrics | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root)
        self.enabled = enabled
        self.max_messages = max_messages
        self.max_bytes = max_bytes
        self.max_attempts = max_attempts
        self.metrics = metrics or EvidenceMetrics()
        self.clock = clock

    def _require(self) -> None:
        if not self.enabled:
            raise ComponentDisabled("Evidence intake queue is disabled")

    def initialize(self) -> None:
        self._require()
        for state in self.STATES:
            (self.root / state).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _encode(payload: dict[str, Any]) -> bytes:
        return (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _replace_payload(self, path: Path, payload: dict[str, Any]) -> None:
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(self._encode(payload))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
        finally:
            try: temporary.unlink()
            except FileNotFoundError: pass

    def _all_message_paths(self) -> list[Path]:
        return [path for state in self.STATES for path in (self.root / state).glob("*.json")]

    def enqueue(self, envelope: dict[str, Any], *, message_id: str | None = None) -> str:
        self._require()
        self.initialize()
        message_id = message_id or uuid.uuid4().hex
        if not message_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in message_id):
            raise ValueError("message_id must contain only letters, numbers, '-' or '_'")
        existing = [self.root / state / f"{message_id}.json" for state in self.STATES]
        if any(path.exists() for path in existing):
            self.metrics.increment("queue_duplicate")
            return message_id
        paths = self._all_message_paths()
        current_bytes = sum(path.stat().st_size for path in paths)
        payload = {"message_id": message_id, "attempts": 0,
                   "enqueued_at": int(self.clock()), "envelope": envelope}
        encoded = self._encode(payload)
        if len(paths) >= self.max_messages or current_bytes + len(encoded) > self.max_bytes:
            self.metrics.increment("queue_overflow")
            raise QueueFull("Evidence intake queue capacity exceeded")
        target = self.root / "pending" / f"{message_id}.json"
        temporary = self.root / "pending" / f".{message_id}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            self._fsync_directory(target.parent)
        finally:
            try: temporary.unlink()
            except FileNotFoundError: pass
        self.metrics.increment("queue_enqueued")
        return message_id

    def claim(self, limit: int) -> list[ClaimedMessage]:
        self._require()
        self.initialize()
        claimed = []
        for source in sorted((self.root / "pending").glob("*.json"))[:max(0, limit)]:
            target = self.root / "processing" / source.name
            try:
                os.replace(source, target)
                self._fsync_directory(source.parent)
                self._fsync_directory(target.parent)
            except FileNotFoundError:
                continue
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
                claimed.append(ClaimedMessage(str(payload["message_id"]), payload, target))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                os.replace(target, self.root / "dead_letter" / target.name)
                self.metrics.increment("queue_corrupt")
                raise QueueCorruption(f"Invalid intake message: {target.name}") from exc
        self.metrics.increment("queue_claimed", len(claimed))
        return claimed

    def ack(self, claimed: ClaimedMessage) -> None:
        claimed.path.unlink(missing_ok=True)
        self._fsync_directory(claimed.path.parent)
        self.metrics.increment("queue_acked")

    def nack(self, claimed: ClaimedMessage, error: str) -> None:
        payload = dict(claimed.payload)
        payload["attempts"] = int(payload.get("attempts", 0)) + 1
        payload["last_error"] = str(error)[:500]
        payload["last_attempt_at"] = int(self.clock())
        state = "dead_letter" if payload["attempts"] >= self.max_attempts else "retry"
        self._replace_payload(claimed.path, payload)
        target = self.root / state / claimed.path.name
        os.replace(claimed.path, target)
        self._fsync_directory(claimed.path.parent)
        self._fsync_directory(target.parent)
        self.metrics.increment(f"queue_{state}")

    def recover(self) -> int:
        self._require()
        self.initialize()
        recovered = 0
        for state in ("processing", "retry"):
            for source in sorted((self.root / state).glob("*.json")):
                target = self.root / "pending" / source.name
                if target.exists():
                    source.unlink(missing_ok=True)
                else:
                    os.replace(source, target)
                    self._fsync_directory(source.parent)
                    self._fsync_directory(target.parent)
                recovered += 1
        self.metrics.increment("queue_recovered", recovered)
        return recovered

    def depth(self) -> dict[str, int]:
        if not self.enabled:
            return {state: 0 for state in self.STATES}
        return {state: sum(1 for _ in (self.root / state).glob("*.json")) for state in self.STATES}

    def health(self) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "DISABLED", "depth": self.depth()}
        try:
            self.initialize()
            depth = self.depth()
            active = depth["pending"] + depth["processing"] + depth["retry"]
            return {"status": "BACKLOG" if active >= self.max_messages else "HEALTHY",
                    "depth": depth, "max_messages": self.max_messages, "max_bytes": self.max_bytes}
        except OSError as exc:
            return {"status": "DEGRADED", "error": str(exc), "depth": self.depth()}
