"""EP3.3 orchestration for isolated parallel Operation evaluations.

This module deliberately wraps, rather than changes, the frozen EP3 runtime.
Each job owns its runtime instance and immutable snapshot.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence

from .formalization import CandidateState
from .input_windows import RuntimeEvaluationSnapshot
from .runtime import EvaluationResult, OperationRuntime


@dataclass(frozen=True)
class OperationEvaluationJob:
    operation_key: str
    runtime: OperationRuntime
    snapshot: RuntimeEvaluationSnapshot
    candidate_state: Optional[CandidateState] = None


@dataclass(frozen=True)
class IsolatedOperationResult:
    operation_key: str
    contract_id: str
    contract_version: str
    snapshot_digest: str
    detector_result_id: str
    topology_revision_id: str
    behaviour_observation_ids: tuple[str, ...]
    result: EvaluationResult


class MultiOperationEvaluator:
    """Evaluates independent jobs concurrently with deterministic collection."""

    @staticmethod
    def _evaluate(job: OperationEvaluationJob) -> IsolatedOperationResult:
        store = job.runtime.store
        opened_here = store.connection is None
        if opened_here:
            store.open()
        try:
            result = job.runtime.evaluate_snapshot(
                job.snapshot, current_candidate_state=job.candidate_state
            )
        finally:
            if opened_here:
                store.close()
        if result.contract_id != job.snapshot.contract_id:
            raise ValueError("cross-contract result identity")
        return IsolatedOperationResult(
            operation_key=job.operation_key,
            contract_id=result.contract_id,
            contract_version=result.contract_version,
            snapshot_digest=job.snapshot.input_digest,
            detector_result_id=result.detector_result.result_id,
            topology_revision_id=result.topology.revision_id,
            behaviour_observation_ids=tuple(
                item.observation_id for item in result.behaviours
            ),
            result=result,
        )

    def evaluate(self, jobs: Sequence[OperationEvaluationJob], *, workers: int = 2
                 ) -> Mapping[str, IsolatedOperationResult]:
        keys = [job.operation_key for job in jobs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate operation_key")
        contract_keys = [(job.snapshot.contract_id, job.snapshot.contract_version) for job in jobs]
        if len(contract_keys) != len(set(contract_keys)):
            raise ValueError("duplicate contract version evaluation")
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(jobs) or 1))) as pool:
            results = tuple(pool.map(self._evaluate, jobs))
        return {item.operation_key: item for item in sorted(results, key=lambda item: item.operation_key)}


def replay_identity(results: Mapping[str, IsolatedOperationResult]) -> Mapping[str, tuple[str, str, tuple[str, ...]]]:
    """Return only immutable output identities for isolated replay comparison."""
    return {
        key: (value.detector_result_id, value.topology_revision_id,
              value.behaviour_observation_ids)
        for key, value in sorted(results.items())
    }
