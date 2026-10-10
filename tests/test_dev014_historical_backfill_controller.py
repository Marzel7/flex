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
        assert [item["rank"] for item in subject.work()][-1] == 90


def test_qualified_entry_anchor_carries_matching_retained_entry_mc(tmp_path):
    rank_41 = next(item for item in controller(tmp_path).work() if item["rank"] == 41)
    assert rank_41["anchor"]["class"] == "QUALIFIED_ENTRY_ANCHOR"
    assert rank_41["entry_mc_usd"] == 166294.66234
    rank_53 = next(item for item in controller(tmp_path).work() if item["rank"] == 53)
    assert rank_53["anchor"]["class"] == "QUALIFIED_ENTRY_ANCHOR"
    assert rank_53["entry_mc_usd"] > 0


def test_qualified_entry_anchor_without_retained_entry_mc_fails_closed(tmp_path):
    subject = controller(tmp_path)
    launch = next(x for x in subject.population["launches"] if x["mint"] == "6RufsEXSv9Nd4YUmUGah8MeMXZT2rxn3kriGiBcSpump")
    launch["evidence"]["opening"].pop("entry_mc_usd")
    try: subject.work()
    except ControllerDenied as exc: assert str(exc) == "QUALIFIED_ENTRY_MC_UNAVAILABLE"
    else: raise AssertionError("missing retained Entry MC was accepted")


def test_later_qualified_entry_anchor_rejects_nonfinite_entry_mc(tmp_path):
    subject = controller(tmp_path)
    launch = next(x for x in subject.population["launches"] if x["mint"] == "HPHkPvCdGjBV5kaYT4ZeV1Rc27fHMN4Qr4YuG2vzpump")
    launch["evidence"]["opening"]["entry_mc_usd"] = float("nan")
    rank_53 = next(item for item in subject.work() if item["rank"] == 53)
    assert rank_53["request"] is None
    assert rank_53["anchor"]["class"] == "NO_QUALIFIED_ENTRY_OR_OBSERVED_PRICE_ANCHOR"


def test_later_qualified_entry_anchor_rejects_mismatched_provenance(tmp_path):
    subject = controller(tmp_path)
    launch = next(x for x in subject.population["launches"] if x["mint"] == "6RufsEXSv9Nd4YUmUGah8MeMXZT2rxn3kriGiBcSpump")
    launch["evidence"]["opening"]["provenance"] = "mismatched"
    try: subject.work()
    except ControllerDenied as exc: assert str(exc) == "QUALIFIED_ENTRY_MC_UNAVAILABLE"
    else: raise AssertionError("mismatched retained Entry MC provenance was accepted")


def test_frozen_rank_71_90_extension_preserves_order_mints_and_anchor_projection(tmp_path):
    subject = controller(tmp_path)
    work = {item["rank"]: item for item in subject.work()}
    assert [work[rank]["mint"] for rank in range(71, 91)] == [
        "8mnDxKCJUS59RzvesoYEk2ivtVhn5mBS2tmKj32ppump", "9Q2DMkmqkFPHAQHNgkRLUN7XYoSp87GqUAFVoV71pump",
        "59F95Wkn5LRpiKmG2FE3g87Lzkz4redvNJNKSi1Npump", "3KdoU8X1DJBvmmxYYttJWQ1KiMfarg3bFAdeCAmspump",
        "8xySnQGLve5959cr4QiJxpNGW5wkBWPh4VxgJVycpump", "2n3bPZcfbcUNgaP6Ktw8E1wYoycAhuR1sSHa7uB6pump",
        "A8MBwPZwR6msXrDBRzg8b1W7BpjjPch1WsTELQJzpump", "9DPUFzAMZfbh4ie5RLJ1C7Zhes5eEayPsTU91tSzpump",
        "8n1Qyjo7LrQMFaZsTJ3K1qPkNRMxDmS6R4eDjkvjpump", "FCoXFnRQsHtb2rPReJUrtkPD8qBm8UVbXNdHx49spump",
        "7mqDrCDrw7zxqQs3KiRWmx3bns7uy3mMSiAKgDotpump", "7rnZdzwZMkrviU1S6r74e1gGfMnnDzmMSXTgrXKSpump",
        "5PmsmHC6aqCuBzECBLX5Mv9FA8Pgd9rfrX9bT4kzpump", "B5yWXR3PZeRuEDs7P55SBgyZEiVu3Ak7k3uWmYLdpump",
        "4Z9eH1BCuq2Y6FFRXkZft9LoxwMqQWo7bJsEbcd7pump", "BvnqdYwuFrvi9ds63EtwQLYME89ikJkULnJPAd83pump",
        "FvYjAkbhhzpp3Rk3BoSLVKjYpi2zJ4baAS26R9hupump", "7KG3FFuwqs21v2CHL2UEZtrcZpH3tm676as6SEMdpump",
        "BULbQ4j9WU6r63idgdQVsjt6i3nKSTXgyj7Qqcdpump", "AoMGtae9dteY2XteudmTa9W4uZLfuDVHXxcduJE1pump",
    ]
    qualified = [71, 72, 74, 75, 77, 78, 79, 80, 82, 84, 86]
    deferred = [73, 76, 81, 83, 85, 87, 88, 89, 90]
    assert all(work[rank]["request"] and work[rank]["anchor"]["class"] == "QUALIFIED_ENTRY_ANCHOR" and work[rank]["entry_mc_usd"] > 0 for rank in qualified)
    assert all(work[rank]["request"] is None and work[rank]["anchor"]["class"] == "NO_QUALIFIED_ENTRY_OR_OBSERVED_PRICE_ANCHOR" for rank in deferred)
    assert work[71]["request"]["params"]["time_to"] - work[71]["request"]["params"]["time_from"] == 3600
    assert work[71]["request"]["request_identity"] == next(item for item in subject.work() if item["rank"] == 71)["request"]["request_identity"]


def test_rank_71_invalid_entry_mc_is_deferred_before_any_admission(tmp_path):
    subject = controller(tmp_path)
    launch = next(x for x in subject.population["launches"] if x["mint"] == "8mnDxKCJUS59RzvesoYEk2ivtVhn5mBS2tmKj32ppump")
    launch["evidence"]["opening"]["entry_mc_usd"] = 0
    rank_71 = next(item for item in subject.work() if item["rank"] == 71)
    assert rank_71["request"] is None
    assert rank_71["anchor"]["class"] == "NO_QUALIFIED_ENTRY_OR_OBSERVED_PRICE_ANCHOR"


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
