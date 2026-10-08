import json
import sqlite3

import pytest

from src.ops import operation_monitor_worker
from src.ops.operation_monitor_worker import MonitorQueue, MonitorWorker, canonical_birth_enrichment, policy_c_enrichment_decision

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


def _canonical_birth_db(path, *, mint, create_time=1_791_490_876, duplicate=False):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE wt_watchtower_launches(mint TEXT, creator_wallet TEXT, create_signature TEXT, create_slot INTEGER, create_time INTEGER, creator_extraction_method TEXT, confidence TEXT)")
    rows = [(mint, "creator", "create-signature", 44, create_time, "WALKBACK_RECOVERED", "WALKBACK")]
    if duplicate:
        rows.append((mint, "other", "other-signature", 45, create_time + 1, "WALKBACK_RECOVERED", "WALKBACK"))
    con.executemany("INSERT INTO wt_watchtower_launches VALUES(?,?,?,?,?,?,?)", rows)
    con.commit(); con.close()


def test_cattok_legacy_envelope_enriches_only_from_canonical_birth_authority(tmp_path):
    mint = "DNtZMNDZ65hAJbJXRiP9NnV98ZTrwLTEJZ78fWKCpump"
    authority = tmp_path / "canonical.db"; _canonical_birth_db(authority, mint=mint)
    legacy = {"mint": mint, "operation_id": "watchtower", "birth": {"mint": mint, "monitor_admission_event_id": "admission-1"}, "assignment": {"event_id": "assignment-1", "assigned_at": 1_791_490_900}}
    enriched = canonical_birth_enrichment(legacy, db_path=str(authority))
    assert enriched["state"] == "CANONICAL_BIRTH_ENRICHED"
    assert enriched["birth"]["create_time"] == 1_791_490_876
    assert enriched["birth"]["mint"] == mint
    assert legacy["birth"] == {"mint": mint, "monitor_admission_event_id": "admission-1"}


@pytest.mark.parametrize("birth_time, now, expected", [
    (1_000_000, 1_000_000, 1_000_900),
    (1_000_000, 1_007_200, 1_009_000),
    (1_000_000, 1_021_600, 1_025_200),
    (1_000_000, 1_043_200, 1_050_400),
])
def test_legacy_enrichment_uses_all_policy_c_boundaries(tmp_path, birth_time, now, expected):
    authority = tmp_path / "canonical.db"; _canonical_birth_db(authority, mint="mint", create_time=birth_time)
    result = policy_c_enrichment_decision({"mint":"mint", "operation_id":"watchtower", "assignment":{"event_id":"a"}}, now=now, db_path=str(authority))
    assert result["state"] == "SCHEDULED"
    assert result["next_check_at"] == expected


def test_missing_or_conflicting_canonical_birth_never_yields_schedule(tmp_path):
    missing = policy_c_enrichment_decision({"mint":"none", "operation_id":"watchtower", "assignment":{"event_id":"a"}}, now=1_000_000, db_path=str(tmp_path / "absent.db"))
    assert missing["state"] == "INSUFFICIENT_CANONICAL_BIRTH"
    authority = tmp_path / "conflict.db"; _canonical_birth_db(authority, mint="mint", duplicate=True)
    conflict = policy_c_enrichment_decision({"mint":"mint", "operation_id":"watchtower", "assignment":{"event_id":"a"}}, now=1_000_000, db_path=str(authority))
    assert conflict["state"] == "CONFLICTING_CANONICAL_BIRTH"


def test_legacy_schedule_transition_preserves_payload_identity_and_birth(tmp_path):
    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    envelope = {"mint":"mint", "operation_id":"watchtower", "assignment":{"event_id":"a", "assigned_at":1}, "birth":{"mint":"mint", "monitor_admission_event_id":"admission"}, "retry_count":2}
    message_id = queue.queue.enqueue(envelope, message_id="legacy")
    claimed = queue.claim(1)[0]
    queue.defer_policy_c_schedule(claimed, next_eligible_at=1_010_800, classification="CANONICAL_POLICY_C_SCHEDULE", birth_evidence_id="birth-proof")
    stored = json.loads((tmp_path / "queue" / "pending" / f"{message_id}.json").read_text())["envelope"]
    assert stored["birth"] == envelope["birth"]
    assert stored["assignment"] == envelope["assignment"]
    assert stored["retry_count"] == 2
    assert stored["next_eligible_dispatch_at"] == stored["policy_c_scheduled_at"] == 1_010_800


def test_legacy_active_envelope_is_deferred_before_provider_dispatch(tmp_path, monkeypatch):
    now = 2_000_000; mint = "legacy-active"
    authority = tmp_path / "canonical.db"; _canonical_birth_db(authority, mint=mint, create_time=now - 3 * 3600)
    monkeypatch.setenv("OPERATION_MONITOR_CANONICAL_BIRTH_DB_PATH", str(authority))
    monkeypatch.setattr(operation_monitor_worker.time, "time", lambda: now)
    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    queue.queue.enqueue({
        "mint": mint, "operation_id": "watchtower", "cohort": "PROSPECTIVE_MONITOR_COHORT",
        "assignment": {"event_id":"assignment", "assigned_at":now - 100},
        "birth": {"mint":mint, "monitor_admission_event_id":"admission"},
        "entry_timestamp":now - 100, "entry_mc_usd":10.0, "entry_method":"fixture",
        "entry_reference_state":"QUALIFIED", "monitor_state":"ENTRY_REFERENCE_QUALIFIED",
        "candle_resolution":"15m", "retry_count":2,
    }, message_id="legacy-active")
    calls=[]
    worker=MonitorWorker(queue, transport=lambda _p: calls.append("provider"), persist=lambda _item: True, db_path=str(tmp_path / "monitor.db"))
    monkeypatch.setattr(worker, "_activate_from_qualified_opening", lambda _payload: None)
    assert worker.process_once() == 1
    assert calls == []
    stored=json.loads((tmp_path / "queue" / "pending" / "legacy-active.json").read_text())["envelope"]
    assert stored["birth"] == {"mint":mint, "monitor_admission_event_id":"admission"}
    assert stored["assignment"]["event_id"] == "assignment"
    assert stored["retry_count"] == 2
    assert stored["policy_c_scheduled_at"] > now
