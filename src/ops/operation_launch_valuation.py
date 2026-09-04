"""Post-commit, bounded launch-valuation enrichment (Stage 0).

This module deliberately has no imports from the listener, Walkback, detector,
or UI layers.  A caller may invoke :func:`enqueue_after_assignment_commit`
*only after* its authoritative operation-assignment transaction committed.  The
worker is deliberately opt-in and does no network work unless both feature
flags are explicitly enabled.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from src.evidence.errors import ComponentDisabled, QueueFull
from src.evidence.metrics import EvidenceMetrics
from src.evidence.queue import ClaimedMessage, EvidenceIntakeQueue


VALUATION_CONTRACT_VERSION = "operation-launch-valuation.v1"
VALUATION_JOB_SCHEMA_VERSION = 1
VALUATION_RPC_CONTRACT_VERSION = "operation-launch-valuation-rpc.v1"
MAX_CREATION_LOOKUPS = 1
MAX_FIRST_SECOND_BLOCKS = 3
MAX_TOTAL_ALCHEMY_CALLS_PER_LAUNCH = 4
ADDRESS_PAGINATION_ALLOWED = False
HISTORICAL_DISCOVERY_ALLOWED = False
AUTO_EXPAND_WINDOW = False
WORKER_CONCURRENCY = 1
QUEUE_CAPACITY = 500
QUEUE_MAX_BYTES = 8 * 1024 * 1024
MAX_RETRY_COUNT = 3
RETRY_BACKOFF_POLICY = "exponential_seconds:1,2,4"
NON_RETRYABLE_STATES = frozenset({
    "MISSING_CANONICAL_BIRTH", "INVALID_CREATION_SIGNATURE", "MINT_MISMATCH",
    "UNSUPPORTED_TRANSACTION_VARIANT", "INSUFFICIENT_EVIDENCE",
    "CONTRADICTORY_EVIDENCE",
})
QUALITY_STATES = frozenset({"PROVEN", "QUALIFIED", "APPROXIMATE", "INSUFFICIENT_EVIDENCE"})
OVERALL_STATES = frozenset({"COMPLETE", "PARTIAL", "INSUFFICIENT_EVIDENCE", "ACQUISITION_FAILED", "CONTRADICTORY_EVIDENCE"})


def _flag(env: dict[str, str], name: str) -> bool:
    return env.get(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class ValuationConfig:
    enabled: bool = False
    rpc_enabled: bool = False
    queue_path: Path = Path("database/evidence_platform/operation_launch_valuation_jobs")
    result_path: Path = Path("database/evidence_platform/operation_launch_valuations")
    queue_capacity: int = QUEUE_CAPACITY
    queue_max_bytes: int = QUEUE_MAX_BYTES
    max_retries: int = MAX_RETRY_COUNT

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "ValuationConfig":
        values = dict(os.environ if env is None else env)
        return cls(
            enabled=_flag(values, "OPERATION_LAUNCH_VALUATION_ENABLED"),
            rpc_enabled=_flag(values, "OPERATION_LAUNCH_VALUATION_RPC_ENABLED"),
            queue_path=Path(values.get("OPERATION_LAUNCH_VALUATION_QUEUE_PATH", str(cls.queue_path))),
            result_path=Path(values.get("OPERATION_LAUNCH_VALUATION_RESULT_PATH", str(cls.result_path))),
            queue_capacity=max(1, int(values.get("OPERATION_LAUNCH_VALUATION_QUEUE_CAPACITY", QUEUE_CAPACITY))),
            queue_max_bytes=max(1024, int(values.get("OPERATION_LAUNCH_VALUATION_QUEUE_MAX_BYTES", QUEUE_MAX_BYTES))),
            max_retries=max(1, min(MAX_RETRY_COUNT, int(values.get("OPERATION_LAUNCH_VALUATION_MAX_RETRIES", MAX_RETRY_COUNT)))),
        )


@dataclass(frozen=True)
class ValuationJob:
    mint: str
    operation_id: str
    operation_assignment_digest: str
    canonical_birth_digest: str
    creation_signature: str
    valuation_contract_version: str = VALUATION_CONTRACT_VERSION
    enqueued_at: int = 0

    @property
    def valuation_job_id(self) -> str:
        # Excludes timestamp: delivery is immutable and idempotent.
        return hashlib.sha256(
            f"{self.mint}|{self.operation_id}|{self.canonical_birth_digest}|{self.valuation_contract_version}".encode()
        ).hexdigest()

    def envelope(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["valuation_job_id"] = self.valuation_job_id
        payload["schema_version"] = VALUATION_JOB_SCHEMA_VERSION
        return payload


def logical_valuation_id(job: ValuationJob) -> str:
    return hashlib.sha256(f"{job.mint}|{job.operation_id}|{job.valuation_contract_version}".encode()).hexdigest()


def build_valuation_job(*, mint: str, operation_id: str, operation_assignment: dict[str, Any],
                        canonical_birth: dict[str, Any], now: int | None = None) -> ValuationJob:
    """Construct a compact job only from already-durable assignment/birth facts."""
    creation_signature = str(canonical_birth.get("signature") or "")
    birth_mint = str(canonical_birth.get("mint") or "")
    if not mint or not operation_id or not creation_signature or birth_mint != mint:
        raise ValueError("valuation requires a mint-bound canonical birth signature")
    return ValuationJob(
        mint=mint, operation_id=operation_id,
        operation_assignment_digest=sha256(operation_assignment),
        canonical_birth_digest=str(canonical_birth.get("raw_payload_sha256") or sha256(canonical_birth)),
        creation_signature=creation_signature, enqueued_at=int(time.time() if now is None else now),
    )


class ValuationJobQueue:
    """Finite, durable queue; queue failure never rolls back operation assignment."""
    def __init__(self, config: ValuationConfig, *, enabled: bool | None = None,
                 metrics: EvidenceMetrics | None = None) -> None:
        self.config = config
        self.metrics = metrics or EvidenceMetrics()
        self.queue = EvidenceIntakeQueue(config.queue_path, enabled=config.enabled if enabled is None else enabled,
                                         max_messages=config.queue_capacity, max_bytes=config.queue_max_bytes,
                                         max_attempts=config.max_retries, metrics=self.metrics)

    def enqueue(self, job: ValuationJob) -> str:
        return self.queue.enqueue(job.envelope(), message_id=job.valuation_job_id)

    def enqueue_non_blocking(self, job: ValuationJob) -> dict[str, Any]:
        try:
            return {"status": "ENQUEUED", "job_id": self.enqueue(job), "pending": False}
        except (QueueFull, ComponentDisabled, OSError) as exc:
            self.metrics.increment("valuation_enqueue_backlogged")
            return {"status": "BACKLOGGED", "job_id": job.valuation_job_id, "pending": True,
                    "error": type(exc).__name__}


def enqueue_after_assignment_commit(*, assignment_committed: bool, mint: str, operation_id: str,
                                    operation_assignment: dict[str, Any], canonical_birth: dict[str, Any] | None,
                                    queue: ValuationJobQueue, now: int | None = None) -> dict[str, Any]:
    """Post-commit bridge.  It intentionally cannot perform RPC or decoding."""
    if not assignment_committed:
        return {"status": "NOT_ENQUEUED_PRECOMMIT"}
    if not canonical_birth:
        return {"status": "NOT_ENQUEUED_MISSING_CANONICAL_BIRTH"}
    try:
        job = build_valuation_job(mint=mint, operation_id=operation_id,
                                  operation_assignment=operation_assignment,
                                  canonical_birth=canonical_birth, now=now)
    except ValueError:
        return {"status": "NOT_ENQUEUED_INVALID_CANONICAL_BIRTH"}
    return queue.enqueue_non_blocking(job)


class ValuationWorker:
    """Single-concurrency worker. Provider callbacks run after all DB handles close."""
    def __init__(self, config: ValuationConfig, queue: ValuationJobQueue, *,
                 retained_lookup: Callable[[ValuationJob], dict[str, Any] | None],
                 acquire_creation: Callable[[str], dict[str, Any]] | None = None,
                 persist: Callable[[dict[str, Any]], None] | None = None,
                 metrics: EvidenceMetrics | None = None) -> None:
        self.config, self.queue = config, queue
        self.retained_lookup, self.acquire_creation, self.persist = retained_lookup, acquire_creation, persist
        self.metrics = metrics or queue.metrics
        self.in_flight = 0

    @staticmethod
    def _job(claimed: ClaimedMessage) -> ValuationJob:
        p = dict(claimed.payload["envelope"])
        p.pop("valuation_job_id", None); p.pop("schema_version", None)
        return ValuationJob(**p)

    def _result(self, job: ValuationJob, *, status: str, quality: str,
                creation: dict[str, Any] | None = None, reason: str | None = None) -> dict[str, Any]:
        assert quality in QUALITY_STATES
        assert status in OVERALL_STATES
        result = {
            "schema_version": 1, "record_type": "operation_launch_valuation.v1",
            "logical_valuation_id": logical_valuation_id(job), "mint": job.mint,
            "operation_id": job.operation_id, "valuation_contract_version": job.valuation_contract_version,
            "canonical_birth_digest": job.canonical_birth_digest,
            "creation_signature": job.creation_signature, "overall_status": status,
            "pumpfun_create_event_mc_sol": None, "pumpfun_create_event_mc_sol_quality": "INSUFFICIENT_EVIDENCE",
            "pumpfun_opening_curve_mc_sol": None, "pumpfun_curve_completion_mc_sol": None,
            "pumpswap_first_successful_swap_signature": None,
            "pumpswap_first_successful_swap_slot": None,
            "pumpswap_first_successful_swap_tx_index": None,
            "pumpswap_first_successful_swap_mc_sol": None,
            "pumpswap_first_second_peak_mc_sol": None,
            "pumpswap_quality": quality, "reason": reason,
            "source_artifact_digests": [job.canonical_birth_digest],
            "parser_version": "operation_launch_valuation.stage0.v1",
            "rpc_contract_version": VALUATION_RPC_CONTRACT_VERSION,
        }
        if creation:
            result["creation_slot"] = creation.get("slot")
            result["creation_transaction_quality"] = "QUALIFIED"
        result["observation_id"] = sha256(result)
        return result

    def process_once(self) -> int:
        if not self.config.enabled:
            return 0
        claimed_items = self.queue.queue.claim(WORKER_CONCURRENCY)
        for claimed in claimed_items:
            self.in_flight += 1
            assert self.in_flight <= WORKER_CONCURRENCY
            try:
                job = self._job(claimed)
                # No database connection/lease is held here.  Retained lookup must
                # return a detached value before external acquisition begins.
                retained = self.retained_lookup(job)
                if retained is None:
                    result = self._result(job, status="INSUFFICIENT_EVIDENCE", quality="INSUFFICIENT_EVIDENCE",
                                          reason="MISSING_RETAINED_CREATION_EVIDENCE")
                elif retained.get("creation_tx"):
                    result = self._result(job, status="PARTIAL", quality="QUALIFIED", creation=retained["creation_tx"],
                                          reason="STAGE0_RETAINED_ONLY")
                elif not self.config.rpc_enabled:
                    result = self._result(job, status="INSUFFICIENT_EVIDENCE", quality="INSUFFICIENT_EVIDENCE",
                                          reason="RPC_FEATURE_DISABLED")
                elif self.acquire_creation is None:
                    result = self._result(job, status="ACQUISITION_FAILED", quality="INSUFFICIENT_EVIDENCE",
                                          reason="NO_ACQUISITION_ADAPTER")
                else:
                    # Exactly one allowed creation lookup; no pagination/discovery
                    # or block expansion exists in this Stage-0 worker.
                    creation = self.acquire_creation(job.creation_signature)
                    self.metrics.increment("valuation_alchemy_calls")
                    result = self._result(job, status="PARTIAL", quality="QUALIFIED", creation=creation,
                                          reason="STAGE0_EXACT_CREATION_ONLY")
                if self.persist is not None:
                    started = time.monotonic(); self.persist(result)
                    self.metrics.observe("db_write_duration_ms", (time.monotonic() - started) * 1000)
                self.queue.queue.ack(claimed)  # commit/persist before ack
            except (TimeoutError, ConnectionError) as exc:
                self.queue.queue.nack(claimed, str(exc))
            except ValueError as exc:
                # Evidence/decoder incompatibility is terminal; do not waste
                # provider budget retrying an invariant failure.
                terminal = self._result(job, status="INSUFFICIENT_EVIDENCE", quality="INSUFFICIENT_EVIDENCE",
                                        reason=f"UNSUPPORTED_TRANSACTION_VARIANT:{str(exc)[:120]}")
                if self.persist is not None:
                    self.persist(terminal)
                self.queue.queue.ack(claimed)
            except Exception as exc:  # deterministic terminal decode/evidence failure
                self.queue.queue.nack(claimed, str(exc))
            finally:
                self.in_flight -= 1
        return len(claimed_items)


def retained_ui_state(record: dict[str, Any] | None) -> str:
    """Pure reader contract: UI gets no acquisition capability."""
    if record is None:
        return "PENDING"
    return {"COMPLETE": "AVAILABLE", "PARTIAL": "PARTIAL", "INSUFFICIENT_EVIDENCE": "INSUFFICIENT_EVIDENCE",
            "ACQUISITION_FAILED": "FAILED", "CONTRADICTORY_EVIDENCE": "FAILED"}.get(record.get("overall_status"), "FAILED")


class AppendOnlyValuationStore:
    """Content-addressed derived results; never alters attribution/source tables."""
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def append(self, record: dict[str, Any]) -> str:
        encoded = canonical_json(record)
        digest = hashlib.sha256(encoded).hexdigest()
        target = self.root / f"{digest}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as handle:
                handle.write(encoded)
                handle.flush(); os.fsync(handle.fileno())
        except FileExistsError:
            if target.read_bytes() != encoded:
                raise RuntimeError("valuation artifact digest collision")
        return digest
