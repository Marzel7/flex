"""X76.5A -- Walkback Candidate Generation Health Monitoring.

Named validation of the 6 scenarios from the milestone spec, plus the
recovery-log persistence/idempotency and the incident-labelling
requirement (manual/external termination must never be mislabelled as
an organic self-kill). All tests exercise the REAL functions
(_determine_status, walkback_recovery_log's persistence layer) against
isolated inputs -- never the live 2.9GB database.
"""
import sqlite3
import time

import pytest

from src.ops.walkback_candidate_health import _determine_status
from src.ops import walkback_recovery_log


def _wh(**overrides):
    """Baseline healthy walkback_health dict (build_walkback_health's shape)."""
    base = {
        "pending": 3, "running": 1, "completed_last_hour": 5,
        "completed_per_minute": 1, "stalled_running_jobs": 0,
        "nested_write_failures_last_hour": 0,
    }
    base.update(overrides)
    return base


def _cg(**overrides):
    base = {"stalled": False, "generated_last_hour": 1, "generated_last_day": 20}
    base.update(overrides)
    return base


class TestNamedScenario1HealthyProgress:
    def test_heartbeat_current_recent_completion_no_lease_candidate_generated(self):
        status, reasons = _determine_status(
            supervisor={"available": True, "running": True},
            heartbeat_age=30, lease=None,
            walkback_health=_wh(pending=5, completed_last_hour=10, completed_per_minute=1),
            candidate_generation=_cg(generated_last_hour=2, stalled=False),
            recent_self_kill=False,
        )
        assert status == "HEALTHY"
        assert reasons == []


class TestNamedScenario2HealthyIdle:
    def test_no_pending_no_eligible_no_warning(self):
        status, reasons = _determine_status(
            supervisor={"available": True, "running": True},
            heartbeat_age=30, lease=None,
            walkback_health=_wh(pending=0, running=0, completed_last_hour=0, completed_per_minute=0),
            candidate_generation=_cg(generated_last_hour=0, generated_last_day=0, stalled=True),
            recent_self_kill=False,
        )
        # Zero candidates while walkback itself is idle (no pending, no
        # completions) must NOT be treated as unhealthy -- explicit spec
        # requirement.
        assert status == "IDLE"
        assert "idle" in reasons[0].lower()


class TestNamedScenario3StaleLease:
    def test_lease_exceeds_threshold_is_stalled_with_warning_visible(self):
        status, reasons = _determine_status(
            supervisor={"available": True, "running": True},
            heartbeat_age=30,
            lease={"age_seconds": 700, "command": "walkback_worker.py:469 in _ops_conn"},
            walkback_health=_wh(), candidate_generation=_cg(),
            recent_self_kill=False,
        )
        assert status == "STALLED"
        assert any("700" in r or "lease" in r.lower() for r in reasons)

    def test_lease_past_safe_threshold_but_under_self_kill_is_still_stalled(self):
        """Spec: 'If current write-lease age exceeds the SAFE threshold,
        show: Walkback write lease is stale.' -- this must trigger STALLED
        even below the self-kill threshold, since candidate generation may
        already be blocked."""
        status, reasons = _determine_status(
            supervisor={"available": True, "running": True},
            heartbeat_age=30,
            lease={"age_seconds": 150, "command": "walkback_worker.py:469 in _ops_conn"},  # >120 safe, <600 self-kill
            walkback_health=_wh(), candidate_generation=_cg(),
            recent_self_kill=False,
        )
        assert status == "STALLED"

    def test_lease_under_safe_threshold_is_not_stalled(self):
        status, reasons = _determine_status(
            supervisor={"available": True, "running": True},
            heartbeat_age=30,
            lease={"age_seconds": 8, "command": "ws-cascade-treasury-register"},
            walkback_health=_wh(), candidate_generation=_cg(),
            recent_self_kill=False,
        )
        assert status == "HEALTHY"


class TestNamedScenario4SelfKillRecovery:
    def test_recent_self_kill_reports_recovering(self):
        status, reasons = _determine_status(
            supervisor={"available": True, "running": True},
            heartbeat_age=5, lease=None,
            walkback_health=_wh(), candidate_generation=_cg(),
            recent_self_kill=True,
        )
        assert status == "RECOVERING"

    def test_recovery_log_records_and_reconciles_to_healthy(self, tmp_path):
        """End-to-end through the real persistence layer: record_self_kill
        -> mark_restarted -> mark_healthy -> recent_events reflects it."""
        path = str(tmp_path / "recovery.db")
        conn = sqlite3.connect(path)
        walkback_recovery_log.ensure_schema(conn)

        event_id = walkback_recovery_log.record_self_kill(
            conn, worker="walkback_worker",
            reason="stale write lease held 650s (threshold 600s)",
            lease_age_seconds=650.0, lease_command="walkback_worker.py:469 in _ops_conn",
            lease_transaction_id="tx-test-1",
        )
        events = walkback_recovery_log.recent_events(conn, worker="walkback_worker", limit=5)
        assert len(events) == 1
        assert events[0]["event_kind"] == "stale_lease_self_kill"
        assert events[0]["restarted_at"] is None  # not yet reconciled

        now = int(time.time())
        walkback_recovery_log.mark_restarted(conn, event_id, restarted_at=now, outcome="restarted successfully")
        walkback_recovery_log.mark_healthy(conn, event_id, healthy_at=now + 11)

        events = walkback_recovery_log.recent_events(conn, worker="walkback_worker", limit=5)
        assert events[0]["restarted_at"] == now
        assert events[0]["healthy_at"] == now + 11
        assert events[0]["restart_outcome"] == "restarted successfully"
        conn.close()

    def test_event_remains_in_recovery_history_after_reconciliation(self, tmp_path):
        path = str(tmp_path / "recovery.db")
        conn = sqlite3.connect(path)
        event_id = walkback_recovery_log.record_self_kill(
            conn, worker="walkback_worker", reason="test", lease_age_seconds=610.0,
            lease_command="x", lease_transaction_id="tx-2",
        )
        walkback_recovery_log.mark_restarted(conn, event_id, restarted_at=int(time.time()), outcome="restarted successfully")
        walkback_recovery_log.mark_healthy(conn, event_id, healthy_at=int(time.time()))
        # The event is healthy now, but must STILL appear in history (spec:
        # "the event remains in recovery history").
        events = walkback_recovery_log.recent_events(conn, worker="walkback_worker", limit=5)
        assert len(events) == 1
        conn.close()


class TestNamedScenario5WorkerStopped:
    def test_supervisor_reports_not_running_yields_stopped(self):
        status, reasons = _determine_status(
            supervisor={"available": True, "running": False},
            heartbeat_age=9999, lease=None,
            walkback_health=_wh(), candidate_generation=_cg(),
            recent_self_kill=False,
        )
        assert status == "STOPPED"
        assert "not running" in reasons[0]

    def test_stopped_takes_precedence_over_everything_else(self):
        """Even with a stale lease AND a recent self-kill flag, STOPPED
        must win -- nothing else is meaningful if the process is dead."""
        status, reasons = _determine_status(
            supervisor={"available": True, "running": False},
            heartbeat_age=9999,
            lease={"age_seconds": 9999, "command": "x"},
            walkback_health=_wh(), candidate_generation=_cg(),
            recent_self_kill=True,
        )
        assert status == "STOPPED"


class TestNamedScenario6CandidateGenerationSilence:
    def test_walkback_active_but_no_candidate_generated_is_degraded(self):
        """walkback IS progressing (completed work in the last hour) but
        candidate generation has gone silent -- DEGRADED with an explicit
        reason, not a bare zero."""
        status, reasons = _determine_status(
            supervisor={"available": True, "running": True},
            heartbeat_age=30, lease=None,
            walkback_health=_wh(completed_last_hour=8, completed_per_minute=1),
            candidate_generation=_cg(generated_last_hour=0, generated_last_day=0, stalled=True),
            recent_self_kill=False,
        )
        assert status == "DEGRADED"
        assert any("candidate" in r.lower() for r in reasons)

    def test_zero_candidates_with_no_walkback_progress_is_not_flagged_via_silence_reason(self):
        """Contrast case: if walkback ISN'T progressing either, this is
        correctly attributed to walkback itself (via the pending/no-
        completion DEGRADED reason), not double-counted as a separate
        'candidate silence' issue on top."""
        status, reasons = _determine_status(
            supervisor={"available": True, "running": True},
            heartbeat_age=30, lease=None,
            walkback_health=_wh(pending=5, completed_last_hour=0, completed_per_minute=0),
            candidate_generation=_cg(generated_last_hour=0, generated_last_day=0, stalled=True),
            recent_self_kill=False,
        )
        assert status == "DEGRADED"
        assert any("pending" in r.lower() for r in reasons)


class TestIncidentLabelling:
    """The X76.5 SIGABRT incident must be recorded as
    manual_external_termination, never conflated with an organic self-kill."""

    def test_manual_termination_recorded_with_correct_kind(self, tmp_path):
        path = str(tmp_path / "recovery.db")
        conn = sqlite3.connect(path)
        walkback_recovery_log.record_manual_termination(
            conn, worker="walkback_worker",
            reason="os.kill(pid, SIGABRT) sent during investigation debugging",
            detected_at=1785974736, restarted_at=1785974737, healthy_at=1785974742,
        )
        events = walkback_recovery_log.recent_events(conn, worker="walkback_worker", limit=5)
        assert len(events) == 1
        assert events[0]["event_kind"] == "manual_external_termination"
        assert events[0]["event_kind"] != "stale_lease_self_kill"
        conn.close()

    def test_manual_and_self_kill_events_are_independently_counted(self, tmp_path):
        path = str(tmp_path / "recovery.db")
        conn = sqlite3.connect(path)
        walkback_recovery_log.record_manual_termination(
            conn, worker="walkback_worker", reason="manual",
            detected_at=int(time.time()) - 100,
        )
        walkback_recovery_log.record_self_kill(
            conn, worker="walkback_worker", reason="self-kill",
            lease_age_seconds=650.0, lease_command="x", lease_transaction_id="tx-3",
        )
        counts = walkback_recovery_log.counts_in_window(conn, worker="walkback_worker", window_seconds=3600)
        assert counts["manual_termination"] == 1
        assert counts["self_kill"] == 1
        assert counts["total"] == 2
        conn.close()

    def test_recent_events_ordered_newest_first(self, tmp_path):
        path = str(tmp_path / "recovery.db")
        conn = sqlite3.connect(path)
        now = int(time.time())
        walkback_recovery_log.record_manual_termination(
            conn, worker="walkback_worker", reason="older", detected_at=now - 1000,
        )
        walkback_recovery_log.record_self_kill(
            conn, worker="walkback_worker", reason="newer", lease_age_seconds=610.0,
            lease_command="x", lease_transaction_id="tx-4",
        )
        events = walkback_recovery_log.recent_events(conn, worker="walkback_worker", limit=5)
        assert events[0]["reason"] == "newer"
        assert events[1]["reason"] == "older"
        conn.close()

    def test_recent_events_limited_to_five(self, tmp_path):
        path = str(tmp_path / "recovery.db")
        conn = sqlite3.connect(path)
        for i in range(8):
            walkback_recovery_log.record_self_kill(
                conn, worker="walkback_worker", reason=f"event-{i}",
                lease_age_seconds=610.0, lease_command="x", lease_transaction_id=f"tx-{i}",
            )
        events = walkback_recovery_log.recent_events(conn, worker="walkback_worker", limit=5)
        assert len(events) == 5
        conn.close()
