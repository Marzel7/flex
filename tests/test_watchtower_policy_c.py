import pytest

from src.ops.operation_monitor_worker import MonitorQueue

from src.ops.watchtower_policy_c import (
    POLICY_C_CHECKPOINTS,
    policy_c_checkpoint_offsets,
    policy_c_schedule,
)


def test_policy_c_has_exact_24_hour_checkpoint_sequence():
    offsets = policy_c_checkpoint_offsets()
    assert POLICY_C_CHECKPOINTS == ((0, 7200, 900), (7200, 21600, 1800), (21600, 43200, 3600), (43200, 86400, 7200))
    assert len(offsets) == 28
    assert [sum(start < item <= end for item in offsets) for start, end, _ in POLICY_C_CHECKPOINTS] == [8, 8, 6, 6]
    assert offsets[-1] == 86400


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0, 900), (2 * 3600 - 1, 7200), (2 * 3600, 9000),
        (6 * 3600 - 1, 21600), (6 * 3600, 25200),
        (12 * 3600 - 1, 43200), (12 * 3600, 50400),
        (24 * 3600 - 1, 86400),
    ],
)
def test_policy_c_uses_age_anchored_next_checkpoint(age, expected):
    result = policy_c_schedule(birth_timestamp=1_000_000, now=1_000_000 + age)
    assert result == {"state": "SCHEDULED", "next_check_at": 1_000_000 + expected}


def test_policy_c_stops_routine_monitoring_at_24_hours():
    assert policy_c_schedule(birth_timestamp=1_000_000, now=1_086_400) == {
        "state": "NO_FURTHER_CHECK", "next_check_at": None,
    }


def test_policy_c_fails_closed_without_canonical_birth():
    assert policy_c_schedule(birth_timestamp=None, now=1_000_000) == {
        "state": "INSUFFICIENT_CANONICAL_BIRTH", "next_check_at": None,
    }


def test_cattok_successor_uses_policy_c_deadline_and_deduplicates(tmp_path):
    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    fact = {
        "operation_id": "watchtower", "mint": "DNtZMNDZ65hAJbJXRiP9NnV98ZTrwLTEJZ78fWKCpump",
        "monitor_state": "MONITORING_ACTIVE", "entry_status": "QUALIFIED",
        "entry_timestamp": 1_000_100, "entry_mc_usd": 10.0,
        "entry_method": "FIRST_FULL_POST_MIGRATION_SECOND_MC", "entry_exactness": "EXACT",
        "provenance_digest": "entry-proof", "last_observation_at": 1_009_000,
        "next_observation_at": 1_009_060,
    }
    envelope = {"birth": {"create_time": 1_000_000}, "cohort": "PROSPECTIVE_MONITOR_COHORT", "candle_resolution": "15m"}
    first = queue.enqueue_active_successor(predecessor_id="first", fact=fact, envelope=envelope, now=1_009_000)
    second = queue.enqueue_active_successor(predecessor_id="first", fact=fact, envelope=envelope, now=1_009_000)
    assert first["status"] == second["status"] == "ENQUEUED_ACTIVE_SUCCESSOR"
    assert first["job_id"] == second["job_id"]
    assert queue.queue.depth()["pending"] == 1
    stored = next((tmp_path / "queue" / "pending").glob("*.json")).read_text()
    assert '"next_eligible_dispatch_at":1010800' in stored
