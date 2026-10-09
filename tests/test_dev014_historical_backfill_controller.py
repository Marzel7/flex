import json
from pathlib import Path

from src.ops.watchtower_historical_backfill_controller import ControllerDenied, HistoricalBackfillController, SessionBounds


ROOT = Path(__file__).resolve().parents[1]


def controller(tmp_path):
    reconciliation = json.loads((ROOT / "docs/audits/dev014_watchtower_recent_first_price_forensics_reconciliation_and_batch_5_20261009.v1.json").read_text())
    population = json.loads((ROOT / "docs/audits/dev014_watchtower_forensic_population_v2_20261009.v1.json").read_text())
    return HistoricalBackfillController(tmp_path / "state.json", reconciliation, population)


def test_catchup_precedes_recent_first_and_missing_anchors_are_persisted(tmp_path):
    with controller(tmp_path) as subject:
        assert [item["rank"] for item in subject.work()[:4]] == [22, 23, 24, 26]
        assert subject.next_work()["rank"] == 22
        state = subject._state()
        assert state["deferred"] == []
        state["completed_request_ids"] = [item["request"]["request_identity"] for item in subject.work()[:4]]
        subject._persist(state)
        assert subject.next_work()["rank"] == 41
        assert [item["rank"] for item in subject.work()][-1] == 70


def test_crash_safe_completed_identity_deduplicates_and_pause_resume(tmp_path):
    subject = controller(tmp_path)
    with subject:
        first = subject.next_work(); state = subject._state(); state["completed_request_ids"].append(first["request"]["request_identity"]); subject._persist(state)
        assert subject.next_work()["rank"] == 23
        subject.pause(); assert subject.next_work() is None
        subject.resume(); assert subject.next_work()["rank"] == 23


def test_exclusive_owner_and_live_priority_stop_without_admission_or_transport(tmp_path):
    one, two = controller(tmp_path), controller(tmp_path)
    one.acquire()
    try:
        try: two.acquire()
        except ControllerDenied as exc: assert "ALREADY_OWNED" in str(exc)
        else: raise AssertionError("second controller unexpectedly acquired ownership")
        result = one.run(SessionBounds(1, 1, 1024, 1, 1), health_gate=lambda: None, live_pending=lambda: True,
                         admit=lambda **_: (_ for _ in ()).throw(AssertionError("admit")), transport=lambda _: (_ for _ in ()).throw(AssertionError("transport")))
        assert result["status"] == "LIVE_PRIORITY_PENDING"
    finally: one.release()


def test_health_and_evidence_bounds_fail_closed_without_provider_fixture(tmp_path):
    with controller(tmp_path) as subject:
        denied = subject.run(SessionBounds(1, 1, 1024, 1, 1), health_gate=lambda: (_ for _ in ()).throw(RuntimeError("wal")), live_pending=lambda: False, admit=lambda **_: None, transport=lambda _: ({}, 1))
        assert denied["status"] == "HEALTH_GATE_DENIED"
        bounded = subject.run(SessionBounds(1, 1, 1, 1, 1), health_gate=lambda: None, live_pending=lambda: False, admit=lambda **_: None, transport=lambda _: ({"compact": True}, 2))
        assert bounded["status"] == "EVIDENCE_BOUND_REACHED"


def test_shared_budget_adapter_is_the_only_admission_path(tmp_path):
    calls = []
    class Admission:
        def __init__(self, root): calls.append(("factory", root))
        def admit(self, **kwargs): calls.append(("admit", kwargs))
    with controller(tmp_path) as subject:
        result = subject.run_with_shared_budget(SessionBounds(1, 1, 1024, 1, 1), queue_root=tmp_path / "queue", health_gate=lambda: None,
                                                live_pending=lambda: False, transport=lambda _: ({"compact": True}, 1), admission_factory=Admission)
    assert result["requests"] == 1
    assert calls[0][0] == "factory" and calls[1][0] == "admit"
