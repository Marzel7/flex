import asyncio
import json
from pathlib import Path

from src.acquisition.transaction import AcquisitionMetadata, AcquisitionResponse
from src.intelligence.bounded_retry_validation import (
    PHYSICAL_ATTEMPT_LIMIT, DurablePhysicalAttemptLedger, PhysicalAttemptBudget,
    classify_attempt, construct_matched_cohorts,
)


class Coverage:
    def __init__(self, reason, timestamp):
        self.reason = reason
        self.launch_timestamp = timestamp


def response(*, status=200, data=None, error=None):
    metadata = AcquisitionMetadata(
        "a", "c", "test", None, "mint", "json_rpc", "helius_rpc",
        "getTransaction", None, None, 1.0, "none", 0,
    )
    return AcquisitionResponse(status, data, None, {}, metadata, 12.5, error=error)


def failure_rows():
    rows = []
    coverage = {}
    for index in range(30):
        mint = f"migration-only-{index}"
        rows.append({"launch": mint, "signature": f"m-{index}",
                     "purpose": "eligible_migrated_migration"})
        coverage[mint] = Coverage("MISSING_MIGRATION_TRANSACTION", index)
    for index in range(120):
        mint = f"both-{index}"
        rows.extend((
            {"launch": mint, "signature": f"bm-{index}",
             "purpose": "eligible_migrated_migration"},
            {"launch": mint, "signature": f"bc-{index}",
             "purpose": "eligible_migrated_creation"},
        ))
        coverage[mint] = Coverage("MISSING_CREATION_AND_MIGRATION_TRANSACTION", index)
    return rows, coverage


def test_migration_first_matched_plan_is_deterministic_and_bounded():
    failures, coverage = failure_rows()
    first, manifest = construct_matched_cohorts(failures, coverage)
    second, second_manifest = construct_matched_cohorts(reversed(failures), coverage)
    assert first == second
    assert manifest == second_manifest
    assert manifest["launch_counts"] == {
        "NO_RETRY": 50, "DELAYED_RETRY": 50, "EXISTING_FAILOVER": 50,
    }
    assert manifest["target_counts"] == {
        "NO_RETRY": 90, "DELAYED_RETRY": 90, "EXISTING_FAILOVER": 90,
    }
    assert manifest["maximum_physical_attempts"] == 450
    assert manifest["maximum_physical_attempts"] < PHYSICAL_ATTEMPT_LIMIT
    for policy in manifest["policy_order"]:
        rows = [row for row in first if row.policy_cohort == policy]
        dependencies = [row.dependency_type for row in rows]
        assert dependencies == sorted(dependencies, key=lambda value: value != "MIGRATION")


def test_failure_taxonomy_has_no_telemetry_not_retained_bucket():
    cases = (
        (response(data={"result": {"slot": 1}}), "SUCCESS"),
        (response(data={"result": None}), "TRANSACTION_NOT_FOUND"),
        (response(status=429), "RATE_LIMITED"),
        (response(status=503), "PROVIDER_UNAVAILABLE"),
        (response(status=400), "MALFORMED_REQUEST"),
        (response(data={"error": {"code": -32005}}), "RPC_ERROR"),
        (response(data=[]), "UNKNOWN_WITH_RAW_TELEMETRY"),
        (response(status=None, error=asyncio.TimeoutError()), "PROVIDER_TIMEOUT"),
        (response(status=None, error=OSError()), "TRANSPORT_ERROR"),
    )
    assert [classify_attempt(item)[0] for item, _ in cases] == [name for _, name in cases]


def test_attempt_reservation_and_progress_survive_restart_without_reuse(tmp_path: Path):
    checkpoint = tmp_path / "checkpoint.json"
    ledger = DurablePhysicalAttemptLedger(tmp_path / "attempts.jsonl")
    budget = PhysicalAttemptBudget(checkpoint)
    number = budget.reserve({
        "target_key": "DELAYED_RETRY:sig", "attempt_id": "attempt-1",
        "attempt_number_for_target": 1, "provider": "helius_rpc",
        "policy_cohort": "DELAYED_RETRY",
    })
    assert number == 1
    ledger.append({"physical_attempt_number": number, "result_class": "PROVIDER_TIMEOUT"})
    budget.record_target_attempt(
        "DELAYED_RETRY:sig", attempt_number=1,
        result_class="PROVIDER_TIMEOUT", attempt_id="attempt-1",
    )

    resumed = PhysicalAttemptBudget(checkpoint)
    assert resumed.count == 1
    assert resumed.in_flight is None
    assert resumed.target_progress("DELAYED_RETRY:sig") == {
        "attempts": 1, "previous_class": "PROVIDER_TIMEOUT",
        "previous_attempt_id": "attempt-1",
    }
    assert len(ledger.rows()) == 1
    assert resumed.reserve({"target_key": "DELAYED_RETRY:sig", "attempt_id": "attempt-2"}) == 2


def test_hard_budget_rejects_attempt_1001(tmp_path: Path):
    budget = PhysicalAttemptBudget(tmp_path / "checkpoint.json", limit=2)
    budget.reserve({"target_key": "a"}); budget.record_target_attempt(
        "a", attempt_number=1, result_class="PROVIDER_TIMEOUT", attempt_id="1")
    budget.reserve({"target_key": "b"}); budget.record_target_attempt(
        "b", attempt_number=1, result_class="PROVIDER_TIMEOUT", attempt_id="2")
    try:
        budget.reserve({"target_key": "c"})
    except RuntimeError as exc:
        assert "ceiling" in str(exc)
    else:
        raise AssertionError("budget allowed a physical request beyond its ceiling")
