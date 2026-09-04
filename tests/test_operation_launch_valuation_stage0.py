import json
from pathlib import Path

from src.ops.operation_launch_valuation import (
    ADDRESS_PAGINATION_ALLOWED, AUTO_EXPAND_WINDOW, HISTORICAL_DISCOVERY_ALLOWED,
    MAX_CREATION_LOOKUPS, MAX_FIRST_SECOND_BLOCKS, MAX_TOTAL_ALCHEMY_CALLS_PER_LAUNCH,
    AppendOnlyValuationStore, ValuationConfig, ValuationJobQueue, ValuationWorker,
    build_valuation_job, enqueue_after_assignment_commit, retained_ui_state,
)


MINT = "MINT"; OP = "operation-1"
BIRTH = {"mint": MINT, "signature": "create-signature", "raw_payload_sha256": "b" * 64}
ASSIGNMENT = {"operation_id": OP, "state": "CONFIRMED"}


def _queue(tmp_path, *, enabled=True, capacity=500):
    config = ValuationConfig(enabled=enabled, queue_path=tmp_path / "jobs",
                             result_path=tmp_path / "results", queue_capacity=capacity)
    return config, ValuationJobQueue(config)


def test_hard_rpc_contract_is_frozen():
    assert (MAX_CREATION_LOOKUPS, MAX_FIRST_SECOND_BLOCKS, MAX_TOTAL_ALCHEMY_CALLS_PER_LAUNCH) == (1, 3, 4)
    assert not ADDRESS_PAGINATION_ALLOWED
    assert not HISTORICAL_DISCOVERY_ALLOWED
    assert not AUTO_EXPAND_WINDOW


def test_flags_default_off():
    c = ValuationConfig.from_env({})
    assert not c.enabled and not c.rpc_enabled


def test_post_commit_only_and_canonical_birth_required(tmp_path):
    _, queue = _queue(tmp_path)
    assert enqueue_after_assignment_commit(assignment_committed=False, mint=MINT, operation_id=OP,
        operation_assignment=ASSIGNMENT, canonical_birth=BIRTH, queue=queue)["status"] == "NOT_ENQUEUED_PRECOMMIT"
    assert enqueue_after_assignment_commit(assignment_committed=True, mint=MINT, operation_id=OP,
        operation_assignment=ASSIGNMENT, canonical_birth=None, queue=queue)["status"] == "NOT_ENQUEUED_MISSING_CANONICAL_BIRTH"


def test_compact_idempotent_job_envelope(tmp_path):
    config, queue = _queue(tmp_path)
    a = enqueue_after_assignment_commit(assignment_committed=True, mint=MINT, operation_id=OP,
        operation_assignment=ASSIGNMENT, canonical_birth=BIRTH, queue=queue, now=5)
    b = enqueue_after_assignment_commit(assignment_committed=True, mint=MINT, operation_id=OP,
        operation_assignment=ASSIGNMENT, canonical_birth=BIRTH, queue=queue, now=99)
    assert a["job_id"] == b["job_id"]
    assert queue.queue.depth()["pending"] == 1
    stored = json.loads(next((config.queue_path / "pending").glob("*.json")).read_text())
    assert "raw_payload" not in stored and stored["envelope"]["mint"] == MINT


def test_bounded_queue_overflow_is_nonblocking(tmp_path):
    _, queue = _queue(tmp_path, capacity=1)
    first = enqueue_after_assignment_commit(assignment_committed=True, mint=MINT, operation_id=OP,
        operation_assignment=ASSIGNMENT, canonical_birth=BIRTH, queue=queue)
    second = enqueue_after_assignment_commit(assignment_committed=True, mint="MINT2", operation_id=OP,
        operation_assignment=ASSIGNMENT, canonical_birth={**BIRTH, "mint": "MINT2", "signature": "two"}, queue=queue)
    assert first["status"] == "ENQUEUED" and second["status"] == "BACKLOGGED"


def test_feature_off_prevents_worker_and_rpc(tmp_path):
    config, queue = _queue(tmp_path, enabled=False)
    calls = []
    worker = ValuationWorker(config, queue, retained_lookup=lambda _: None,
                             acquire_creation=lambda _: calls.append(1))
    assert worker.process_once() == 0 and calls == []


def test_retained_evidence_preferred_and_append_only_replay(tmp_path):
    config, queue = _queue(tmp_path)
    enqueue_after_assignment_commit(assignment_committed=True, mint=MINT, operation_id=OP,
        operation_assignment=ASSIGNMENT, canonical_birth=BIRTH, queue=queue)
    calls, results = [], []
    store = AppendOnlyValuationStore(config.result_path)
    worker = ValuationWorker(config, queue, retained_lookup=lambda _: {"creation_tx": {"slot": 1}},
        acquire_creation=lambda _: calls.append(1), persist=lambda r: results.append((r, store.append(r))))
    assert worker.process_once() == 1 and calls == [] and len(results) == 1
    assert len(list(config.result_path.glob("*.json"))) == 1
    assert retained_ui_state(results[0][0]) == "PARTIAL"


def test_rpc_gate_returns_insufficient_without_provider_call(tmp_path):
    config, queue = _queue(tmp_path)
    enqueue_after_assignment_commit(assignment_committed=True, mint=MINT, operation_id=OP,
        operation_assignment=ASSIGNMENT, canonical_birth=BIRTH, queue=queue)
    calls, result = [], []
    ValuationWorker(config, queue, retained_lookup=lambda _: {}, acquire_creation=lambda _: calls.append(1),
                    persist=result.append).process_once()
    assert calls == [] and result[0]["reason"] == "RPC_FEATURE_DISABLED"


def test_single_rpc_creation_lookup_when_explicitly_enabled(tmp_path):
    config, queue = _queue(tmp_path)
    config = ValuationConfig(**{**config.__dict__, "rpc_enabled": True})
    queue = ValuationJobQueue(config)
    enqueue_after_assignment_commit(assignment_committed=True, mint=MINT, operation_id=OP,
        operation_assignment=ASSIGNMENT, canonical_birth=BIRTH, queue=queue)
    calls, result = [], []
    ValuationWorker(config, queue, retained_lookup=lambda _: {}, acquire_creation=lambda s: calls.append(s) or {"slot": 3},
                    persist=result.append).process_once()
    assert calls == ["create-signature"] and result[0]["creation_slot"] == 3


def test_no_membership_mutation_or_ui_rpc_contract(tmp_path):
    assert retained_ui_state(None) == "PENDING"
    assert retained_ui_state({"overall_status": "COMPLETE"}) == "AVAILABLE"
    assert retained_ui_state({"overall_status": "INSUFFICIENT_EVIDENCE"}) == "INSUFFICIENT_EVIDENCE"
