import threading

import pytest

from src.ops.strict_migration_window import (
    BOUNDED_POST_MIGRATION_METHOD,
    MIGRATION_SECOND_FALLBACK_METHOD,
    PLUS1_ENTRY_METHOD,
)
from src.ops.watchtower_shadow_evaluation import (
    MAX_SHADOW_RESULTS,
    SHADOW_MODE,
    evaluate_retained_record,
    evaluate_retained_records,
)


def _record(offsets, *, duplicates=()):
    return {
        "mint": "natural-token", "migration_timestamp": 100,
        "http_status": 200, "normalization_state": "COMPLETE",
        "offsets": {str(key): value for key, value in offsets.items()},
        "duplicate_offsets": list(duplicates),
    }


def test_shadow_reuses_strict_reducer_in_required_order():
    assert evaluate_retained_record(_record({1: 11, 0: 10, 2: 12}))["entry_method"] == PLUS1_ENTRY_METHOD
    assert evaluate_retained_record(_record({0: 10, 2: 12}))["entry_method"] == MIGRATION_SECOND_FALLBACK_METHOD
    result = evaluate_retained_record(_record({2: 12}))
    assert result["entry_method"] == BOUNDED_POST_MIGRATION_METHOD
    assert result["entry_offset_seconds"] == 2


@pytest.mark.parametrize("offsets", [{3: 13}, {4: 14}, {3: 13, 4: 14}, {}])
def test_late_or_absent_evidence_fails_closed(offsets):
    result = evaluate_retained_record(_record(offsets))
    assert result["state"] == "INSUFFICIENT_EVIDENCE"
    assert "entry_timestamp" not in result


def test_ambiguous_early_evidence_fails_closed_without_falling_later():
    result = evaluate_retained_record(_record({2: 12}, duplicates=(1,)))
    assert result["state"] == "INSUFFICIENT_EVIDENCE"
    assert result["reason"] == "STRICT_MIGRATION_AMBIGUOUS_EARLY_OFFSET"


def test_evaluation_is_pure_bounded_and_deterministic():
    record = _record({2: 12})
    before_threads = {thread.name for thread in threading.enumerate()}
    first = evaluate_retained_records([record])
    second = evaluate_retained_records([record])
    assert first == second
    assert record["offsets"] == {"2": 12}
    assert {thread.name for thread in threading.enumerate()} == before_threads
    result = first[0]
    assert result["shadow_mode"] == SHADOW_MODE
    assert all(result[key] == 0 for key in ("provider_calls", "database_writes", "queue_writes", "background_workers_started"))
    assert result["authoritative_mutation"] is False
    with pytest.raises(ValueError, match="WATCHTOWER_SHADOW_RESULT_BOUND"):
        evaluate_retained_records([record] * (MAX_SHADOW_RESULTS + 1))


def test_raw_provider_evidence_is_rejected():
    assert evaluate_retained_record({**_record({1: 11}), "payload": {}})["state"] == "INSUFFICIENT_EVIDENCE"


def test_invalid_identity_fails_closed_without_raising():
    assert evaluate_retained_record({"mint": "natural-token", "migration_timestamp": "not-a-time"})["state"] == "INSUFFICIENT_EVIDENCE"
