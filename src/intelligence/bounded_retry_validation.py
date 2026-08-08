"""Crash-safe mechanics for the OIP v2.1C acquisition-policy experiment."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from src.acquisition.transaction import AcquisitionResponse


EXPERIMENT_ID = "OIP_V2_1C"
PHYSICAL_ATTEMPT_LIMIT = 1_000
POLICIES = ("NO_RETRY", "DELAYED_RETRY", "EXISTING_FAILOVER")
RETRYABLE_FAILURES = frozenset({
    "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "RATE_LIMITED",
    "TRANSPORT_ERROR", "RPC_ERROR",
})


@dataclass(frozen=True)
class ExperimentTarget:
    signature: str
    launch: str
    dependency_type: str
    launch_reason: str
    launch_timestamp: int | None
    policy_cohort: str
    ordinal: int


def _stable_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def construct_matched_cohorts(
    failures: Iterable[dict[str, Any]], coverage_by_mint: dict[str, Any], *,
    migration_only_launches: int = 30, both_missing_launches: int = 120,
) -> tuple[list[ExperimentTarget], dict[str, Any]]:
    """Select comparable launch groups without intelligence/identity labels."""
    by_launch: dict[str, list[dict[str, Any]]] = {}
    for row in failures:
        by_launch.setdefault(row["launch"], []).append(row)

    migration_only = []
    both_missing = []
    for mint, rows in by_launch.items():
        coverage = coverage_by_mint.get(mint)
        if coverage is None:
            continue
        purposes = {row["purpose"] for row in rows}
        if (coverage.reason == "MISSING_MIGRATION_TRANSACTION"
                and "eligible_migrated_migration" in purposes):
            migration_only.append(mint)
        elif (coverage.reason == "MISSING_CREATION_AND_MIGRATION_TRANSACTION"
              and {"eligible_migrated_creation", "eligible_migrated_migration"} <= purposes):
            both_missing.append(mint)

    migration_only.sort(key=_stable_key)
    both_missing.sort(key=_stable_key)
    chosen_classes = (
        ("MISSING_MIGRATION_TRANSACTION", migration_only[:migration_only_launches]),
        ("MISSING_CREATION_AND_MIGRATION_TRANSACTION", both_missing[:both_missing_launches]),
    )
    assignments: dict[str, str] = {}
    class_counts: dict[str, dict[str, int]] = {}
    for class_name, mints in chosen_classes:
        counts = {policy: 0 for policy in POLICIES}
        for index, mint in enumerate(mints):
            policy = POLICIES[index % len(POLICIES)]
            assignments[mint] = policy
            counts[policy] += 1
        class_counts[class_name] = counts

    selected: list[ExperimentTarget] = []
    ordinal = 0
    for policy in POLICIES:
        policy_rows: list[ExperimentTarget] = []
        for mint, assigned in assignments.items():
            if assigned != policy:
                continue
            coverage = coverage_by_mint[mint]
            rows = {row["purpose"]: row for row in by_launch[mint]}
            # Migration is always attempted before creation within each cohort.
            purposes = ["eligible_migrated_migration", "eligible_migrated_creation"]
            for purpose in purposes:
                if purpose not in rows:
                    continue
                ordinal += 1
                policy_rows.append(ExperimentTarget(
                    signature=rows[purpose]["signature"], launch=mint,
                    dependency_type="MIGRATION" if purpose.endswith("migration") else "CREATION",
                    launch_reason=coverage.reason,
                    launch_timestamp=coverage.launch_timestamp,
                    policy_cohort=policy, ordinal=ordinal,
                ))
        policy_rows.sort(key=lambda row: (
            0 if row.dependency_type == "MIGRATION" else 1,
            row.launch_timestamp if row.launch_timestamp is not None else -1,
            _stable_key(row.launch), row.signature,
        ))
        selected.extend(policy_rows)

    target_counts = {policy: sum(row.policy_cohort == policy for row in selected)
                     for policy in POLICIES}
    launch_counts = {policy: len({row.launch for row in selected if row.policy_cohort == policy})
                     for policy in POLICIES}
    max_attempts = {
        "NO_RETRY": target_counts["NO_RETRY"],
        "DELAYED_RETRY": target_counts["DELAYED_RETRY"] * 2,
        "EXISTING_FAILOVER": target_counts["EXISTING_FAILOVER"] * 2,
    }
    manifest = {
        "experiment_id": EXPERIMENT_ID,
        "selection_method": "MIGRATION_FIRST_STABLE_SHA256_LAUNCH_GROUPS_V1",
        "selection_inputs": {
            "migration_only_launches": migration_only_launches,
            "both_missing_launches": both_missing_launches,
        },
        "policy_order": list(POLICIES),
        "dependency_order": ["MIGRATION", "CREATION"],
        "target_counts": target_counts,
        "launch_counts": launch_counts,
        "class_launch_counts": class_counts,
        "maximum_physical_attempts_by_cohort": max_attempts,
        "maximum_physical_attempts": sum(max_attempts.values()),
        "physical_attempt_limit": PHYSICAL_ATTEMPT_LIMIT,
        "targets": [asdict(row) for row in selected],
    }
    if manifest["maximum_physical_attempts"] > PHYSICAL_ATTEMPT_LIMIT:
        raise ValueError("cohort plan can exceed the physical-attempt ceiling")
    return selected, manifest


def classify_attempt(response: AcquisitionResponse) -> tuple[str, str | None, int | None]:
    """Return one evidence-backed primary class, diagnostic code, RPC code."""
    if response.error is not None:
        name = type(response.error).__name__
        if name in {"TimeoutError", "ServerTimeoutError"}:
            return "PROVIDER_TIMEOUT", name, None
        return "TRANSPORT_ERROR", name, None
    status = response.status
    if status == 429:
        return "RATE_LIMITED", "HTTP_429", None
    if status is not None and status >= 500:
        return "PROVIDER_UNAVAILABLE", f"HTTP_{status}", None
    if status in {400, 404, 405, 415, 422}:
        return "MALFORMED_REQUEST", f"HTTP_{status}", None
    if status != 200:
        return "UNKNOWN_WITH_RAW_TELEMETRY", None if status is None else f"HTTP_{status}", None
    if not isinstance(response.data, dict):
        return "UNKNOWN_WITH_RAW_TELEMETRY", "NON_OBJECT_JSON", None
    error = response.data.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        if code in {-32600, -32602}:
            return "MALFORMED_REQUEST", f"RPC_{code}", code
        return "RPC_ERROR", f"RPC_{code}", code
    if "result" not in response.data:
        return "UNKNOWN_WITH_RAW_TELEMETRY", "RESULT_KEY_ABSENT", None
    if response.data["result"] is None:
        return "TRANSACTION_NOT_FOUND", "NULL_RESULT", None
    return "SUCCESS", None, None


def diagnostic_bytes(response: AcquisitionResponse) -> bytes:
    if response.raw_body is not None:
        return response.raw_body
    if response.text is not None:
        return response.text.encode("utf-8", errors="replace")
    if response.data is not None:
        return json.dumps(response.data, sort_keys=True, default=str).encode("utf-8")
    if response.error is not None:
        return repr(response.error).encode("utf-8", errors="replace")
    return b""


class PhysicalAttemptBudget:
    """Crash-safe monotonic attempt allocator; reservations count as spent."""

    def __init__(self, checkpoint_path: Path, *, limit: int = PHYSICAL_ATTEMPT_LIMIT):
        self.path = checkpoint_path
        self.limit = limit
        self._lock = threading.Lock()
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"experiment_id": EXPERIMENT_ID, "physical_attempt_count": 0,
                    "in_flight": None, "completed_target_keys": [], "target_progress": {}}
        state = json.loads(self.path.read_text())
        if state.get("experiment_id") != EXPERIMENT_ID:
            raise RuntimeError("checkpoint belongs to a different experiment")
        return state

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(self._state, handle, sort_keys=True, indent=2)
            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, self.path)

    @property
    def count(self) -> int:
        return int(self._state["physical_attempt_count"])

    @property
    def completed_target_keys(self) -> set[str]:
        return set(self._state.get("completed_target_keys", []))

    @property
    def in_flight(self) -> dict[str, Any] | None:
        return self._state.get("in_flight")

    def target_progress(self, key: str) -> dict[str, Any] | None:
        return self._state.get("target_progress", {}).get(key)

    def reserve(self, attempt_context: dict[str, Any]) -> int:
        with self._lock:
            if self.count >= self.limit:
                raise RuntimeError("physical-attempt ceiling reached")
            number = self.count + 1
            self._state["physical_attempt_count"] = number
            self._state["in_flight"] = {**attempt_context, "physical_attempt_number": number,
                                         "reserved_at": time.time()}
            self._persist()
            return number

    def record_target_attempt(self, key: str, *, attempt_number: int,
                              result_class: str, attempt_id: str) -> None:
        with self._lock:
            progress = self._state.setdefault("target_progress", {})
            progress[key] = {"attempts": attempt_number, "previous_class": result_class,
                             "previous_attempt_id": attempt_id}
            self._state["in_flight"] = None
            self._persist()

    def complete_target(self, key: str) -> None:
        with self._lock:
            completed = set(self._state.get("completed_target_keys", []))
            completed.add(key)
            self._state["completed_target_keys"] = sorted(completed)
            self._state.setdefault("target_progress", {}).pop(key, None)
            self._state["in_flight"] = None
            self._persist()


class DurablePhysicalAttemptLedger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, row: dict[str, Any]) -> None:
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            handle.flush(); os.fsync(handle.fileno())

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]


def target_key(target: ExperimentTarget) -> str:
    return f"{target.policy_cohort}:{target.signature}"
