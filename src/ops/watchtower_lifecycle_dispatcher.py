"""Finite dispatcher composed solely from frozen lifecycle authorizations and probes."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from src.ops.dev_provider_budget import BudgetDenied
from src.ops.watchtower_lifecycle_cohort import MAX_SESSION, digest
from src.ops.watchtower_lifecycle_probe import request_identity, validate_authorization


TERMINAL_OR_PROTECTED = frozenset({"PENDING", "ADMITTED", "ATTEMPTED", "COMPLETED", "PARTIAL_VALID_CANDLES", "NO_VALID_CANDLES", "INVALID_CANDLE_VALUES", "MALFORMED_RESPONSE", "PROVIDER_API_ERROR", "HTTP_FAILURE", "OUTCOME_UNKNOWN"})
RETRYABLE_PRE_ADMISSION = frozenset({"GLOBAL_PROVIDER_BUDGET_EXHAUSTED", "TOKEN_PROVIDER_BUDGET_EXHAUSTED"})


def validate_lifecycle_cohort_authorization(value: dict[str, Any]) -> dict[str, Any]:
    supplied = value.get("content_hash") if isinstance(value, dict) else None
    candidate = dict(value) if isinstance(value, dict) else {}
    candidate.pop("content_hash", None)
    if supplied != digest(candidate) or candidate.get("schema") != "dev014.watchtower-lifecycle-cohort-authorization.v1":
        raise ValueError("LIFECYCLE_COHORT_AUTHORIZATION_INVALID")
    records = candidate.get("records")
    if not isinstance(records, list) or not records or candidate.get("max_total_paid_requests") != len(records) or candidate.get("max_requests_per_session") != MAX_SESSION:
        raise ValueError("LIFECYCLE_COHORT_AUTHORIZATION_INVALID")
    seen_mints: set[str] = set(); seen_ids: set[str] = set()
    for record in records:
        auth = {key: record.get(key) for key in ("rank", "mint", "creation_timestamp", "time_from", "time_to", "interval", "content_hash")}
        validate_authorization(auth)
        identity = request_identity(auth)
        if record.get("request_identity") != identity or identity in seen_ids or auth["mint"] in seen_mints:
            raise ValueError("LIFECYCLE_COHORT_MEMBERSHIP_INVALID")
        seen_ids.add(identity); seen_mints.add(auth["mint"])
    return value


def journal_outcome(path: Path, record: dict[str, Any]) -> str:
    if not path.exists():
        return "NEW"
    if path.is_symlink():
        raise RuntimeError("LIFECYCLE_JOURNAL_SYMLINK_DENIED")
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError("LIFECYCLE_JOURNAL_UNREADABLE") from exc
    if value.get("request_identity") != record["request_identity"] or value.get("mint") != record["mint"]:
        raise RuntimeError("LIFECYCLE_JOURNAL_IDENTITY_CONFLICT")
    state = value.get("state")
    if state not in TERMINAL_OR_PROTECTED:
        raise RuntimeError("LIFECYCLE_JOURNAL_STATE_INVALID")
    return state


class FiniteLifecycleDispatcher:
    """A caller-owned finite loop; per-request durability remains OneRequestLifecycleProbe."""
    def __init__(self, authorization: dict[str, Any], journal_root: Path, *, health: Callable[[], Any], probe_factory: Callable[[dict[str, Any], Path], Any], storage_guard: Any, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep, cancelled: Callable[[], bool] = lambda: False, progress: Callable[[dict[str, Any]], None] | None = None):
        self.authorization = validate_lifecycle_cohort_authorization(authorization)
        self.journal_root = Path(journal_root)
        self.health, self.probe_factory, self.storage_guard = health, probe_factory, storage_guard
        self.clock, self.sleep, self.cancelled = clock, sleep, cancelled
        self.progress = progress or (lambda _value: None)

    def _remaining(self) -> list[dict[str, Any]]:
        remaining = []
        for record in self.authorization["records"]:
            state = journal_outcome(self.journal_root / record["request_identity"] / "journal.json", record)
            if state == "NEW":
                remaining.append(record)
        return remaining

    def run(self) -> dict[str, Any]:
        completed: list[str] = []; sessions: list[list[str]] = []; admitted_at: list[float] = []
        while True:
            remaining = self._remaining()
            if not remaining:
                return {"status": "EXHAUSTED", "completed": completed, "sessions": sessions}
            session: list[str] = []
            for record in remaining[:MAX_SESSION]:
                while len([stamp for stamp in admitted_at if stamp > self.clock() - 60]) >= 10:
                    if self.cancelled(): return {"status": "CANCELLED", "completed": completed, "sessions": sessions}
                    self.health(); self.storage_guard.check(); self.sleep(1.0)
                if self.cancelled(): return {"status": "CANCELLED", "completed": completed, "sessions": sessions}
                self.health(); self.storage_guard.check()
                path = self.journal_root / record["request_identity"] / "journal.json"
                probe = self.probe_factory(record, path)
                while True:
                    try:
                        probe.execute()
                        admitted_at.append(self.clock()); completed.append(record["request_identity"]); session.append(record["request_identity"])
                        self.progress({"completed": len(completed), "completed_sessions": len(sessions), "current_session": len(sessions) + 1})
                        break
                    except BudgetDenied as exc:
                        if str(exc) not in RETRYABLE_PRE_ADMISSION:
                            return {"status": "BUDGET_HOLD", "completed": completed, "sessions": sessions, "reason": str(exc)}
                        if self.cancelled(): return {"status": "CANCELLED", "completed": completed, "sessions": sessions}
                        self.health(); self.storage_guard.check(); self.sleep(1.0)
            sessions.append(session)
            if not self._remaining():
                return {"status": "EXHAUSTED", "completed": completed, "sessions": sessions}
            deadline = self.clock() + 60.0
            while True:
                if self.cancelled(): return {"status": "CANCELLED", "completed": completed, "sessions": sessions}
                wait = max(0.0, deadline - self.clock())
                if wait == 0.0: break
                self.health(); self.storage_guard.check(); self.sleep(min(1.0, wait))
