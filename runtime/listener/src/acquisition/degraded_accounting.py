"""Independent append-only journal for catastrophic retention accounting gaps."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any
from dataclasses import dataclass

JOURNAL_SCHEMA_VERSION = 1
@dataclass(frozen=True)
class JournalResult:
    status: str
    event_id: str
    failure_stage: str
    error_class: str | None = None


class DegradedAccountingJournal:
    """One bounded append attempt; intentionally independent of retention SQLite."""
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lost_total = 0
        self.last_lost: dict[str, Any] | None = None

    def append(self, metadata: dict[str, Any], *, stage: str, error: Exception) -> JournalResult:
        identity = {"acquisition_id": metadata.get("acquisition_id"), "correlation_id": metadata.get("correlation_id"), "stage": stage, "provider": metadata.get("provider"), "method": metadata.get("method")}
        event = {"event_id": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(), "schema_version": JOURNAL_SCHEMA_VERSION, "observed_at": int(time.time()), "acquisition_identity": metadata.get("acquisition_id"), "mint": metadata.get("launch"), "creator": metadata.get("creator"), "correlation_id": metadata.get("correlation_id"), "purpose": metadata.get("purpose"), "provider": metadata.get("provider"), "method": metadata.get("method"), "failure_stage": stage, "main_store_error_class": type(error).__name__, "main_store_error_message_sanitized": str(error)[:300], "source_component": "shared_transaction_acquisition", "source_version": "e1c1"}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # O_EXCL makes duplicate logical degraded events idempotent.
            target = self.path.parent / f"{event['event_id']}.json"
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump(event, handle, sort_keys=True, separators=(",", ":")); handle.flush(); os.fsync(handle.fileno())
            return JournalResult("JOURNAL_PERSISTED", event["event_id"], stage)
        except FileExistsError:
            return JournalResult("JOURNAL_DUPLICATE_ALREADY_PRESENT", event["event_id"], stage)
        except Exception:
            self.lost_total += 1; self.last_lost = event
            return JournalResult("JOURNAL_PERSIST_FAILED", event["event_id"], stage, type(error).__name__)

    def events(self) -> list[dict[str, Any]]:
        if not self.path.parent.exists(): return []
        return [json.loads(path.read_text()) for path in sorted(self.path.parent.glob("*.json"))]


class BoundedDegradedJournalHandoff:
    """A bounded, non-waiting factory boundary for degraded journal writes.

    Persistence still uses the immutable journal above, but its filesystem
    flush is owned by one background worker.  ``ACCEPTED`` is deliberately not
    a durability claim: only the worker's ``JOURNAL_PERSISTED`` result is.
    """
    def __init__(self, journal: DegradedAccountingJournal, *, max_pending: int = 128,
                 on_persist_failure: Any = None) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be positive")
        self.journal = journal
        self.path = journal.path
        self._queue: queue.Queue[tuple[dict[str, Any], str, Exception]] = queue.Queue(maxsize=max_pending)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._on_persist_failure = on_persist_failure
        self._accepting = True
        self.persist_failures = 0
        self.last_result: JournalResult | None = None
        self.last_persisted_at: float | None = None
        self.last_error: str | None = None
        self._pending_since: list[float] = []

    def _ensure_started(self) -> None:
        if self._thread and self._thread.is_alive(): return
        with self._lock:
            if self._thread and self._thread.is_alive(): return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="retention-degraded-journal", daemon=True)
            self._thread.start()

    def append(self, metadata: dict[str, Any], *, stage: str, error: Exception) -> JournalResult:
        if not self._accepting:
            identity = {"acquisition_id": metadata.get("acquisition_id"), "correlation_id": metadata.get("correlation_id"), "stage": stage, "provider": metadata.get("provider"), "method": metadata.get("method")}
            event_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            return JournalResult("JOURNAL_PERSIST_FAILED", event_id, stage, "JournalHandoffStopped")
        self._ensure_started()
        identity = {"acquisition_id": metadata.get("acquisition_id"), "correlation_id": metadata.get("correlation_id"), "stage": stage, "provider": metadata.get("provider"), "method": metadata.get("method")}
        event_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        try:
            self._queue.put_nowait((dict(metadata), stage, error))
            self._pending_since.append(time.monotonic())
            return JournalResult("JOURNAL_ACCEPTED_FOR_PERSISTENCE", event_id, stage)
        except queue.Full:
            result = JournalResult("JOURNAL_PERSIST_FAILED", event_id, stage, "JournalHandoffFull")
            self.persist_failures += 1; self.last_result = result; self.last_error = result.error_class
            if self._on_persist_failure: self._on_persist_failure(result)
            return result

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try: metadata, stage, error = self._queue.get(timeout=0.05)
            except queue.Empty: continue
            try:
                result = self.journal.append(metadata, stage=stage, error=error)
                self.last_result = result
                if result.status in {"JOURNAL_PERSISTED", "JOURNAL_DUPLICATE_ALREADY_PRESENT"}:
                    self.last_persisted_at = time.time()
                else:
                    self.persist_failures += 1; self.last_error = result.error_class
                    if self._on_persist_failure: self._on_persist_failure(result)
            finally:
                if self._pending_since: self._pending_since.pop(0)
                self._queue.task_done()

    def drain(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)
        return not self._queue.unfinished_tasks

    def stop(self, timeout: float = 1.0) -> dict[str, Any]:
        """Bounded clean shutdown: drain if possible, report any residual."""
        self._accepting = False
        drained = self.drain(timeout)
        self._stop.set()
        if self._thread: self._thread.join(timeout)
        return {"drained": drained, "pending_remaining": len(self._pending_since),
                "worker_alive": bool(self._thread and self._thread.is_alive())}

    def events(self) -> list[dict[str, Any]]:
        return self.journal.events()

    def health(self) -> dict[str, Any]:
        # Includes an item already claimed by the writer but not yet fsync'd.
        depth = len(self._pending_since)
        oldest = (time.monotonic() - self._pending_since[0]) if self._pending_since else None
        return {"journal_handoff_healthy": bool(self._thread and self._thread.is_alive()) or depth == 0,
                "journal_pending_depth": depth, "journal_oldest_pending_age": oldest,
                "journal_persist_failures": self.persist_failures,
                "degraded_journal_healthy": self.last_error is None,
                "last_degraded_journal_event_at": self.last_persisted_at}
