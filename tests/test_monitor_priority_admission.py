import json
import os
import threading
import time

import pytest

from src.ops.dev_provider_budget import BudgetDenied
from src.ops.operation_monitor_worker import MonitorQueue


def _queue(tmp_path):
    return MonitorQueue(tmp_path / "queue", enabled=True)


def _live(mint="live", **extra):
    return {"mint": mint, "operation_id": "watchtower", **extra}


def test_empty_queue_admits_historical_once(tmp_path):
    q = _queue(tmp_path)
    result = q.admit_historical_provider_dispatch(
        request_identity="historical-1", mint="history", request_class="HISTORICAL_1M", now=100
    )
    assert result["admitted"] is True
    assert result["priority_classification"] == "LIVE_CLEAR"
    assert len(json.loads((q.queue.root / "provider_budget.json").read_text())["calls"]) == 1


@pytest.mark.parametrize("state", ["pending", "retry", "processing"])
def test_due_live_work_in_every_active_state_blocks_historical(tmp_path, state):
    q = _queue(tmp_path)
    message = q.queue.enqueue(_live(), message_id="live")
    source = q.queue.root / "pending" / f"{message}.json"
    if state != "pending": os.replace(source, q.queue.root / state / source.name)
    with pytest.raises(BudgetDenied, match="HISTORICAL_DENIED_LIVE_PENDING"):
        q.admit_historical_provider_dispatch(request_identity="h", mint="history", request_class="HIST", now=100)
    assert not (q.queue.root / "provider_budget.json").exists()


def test_future_and_provider_free_work_are_clear_but_unknown_is_closed(tmp_path):
    q = _queue(tmp_path)
    q.queue.enqueue(_live(next_eligible_dispatch_at=101), message_id="future")
    q.queue.enqueue({"provider_bound": False, "work_type": "REPORT_ONLY"}, message_id="free")
    assert q.classify_live_provider_demand(now=100) == "LIVE_CLEAR"
    (q.queue.root / "pending" / "free.json").unlink()
    q.queue.enqueue({"work_type": "NEW_UNQUALIFIED_PROVIDER_WORK"}, message_id="unknown")
    assert q.classify_live_provider_demand(now=100) == "UNKNOWN"


def test_malformed_entry_fails_closed_without_budget_debit(tmp_path):
    q = _queue(tmp_path)
    q.queue.initialize()
    (q.queue.root / "pending" / "bad.json").write_text("not-json")
    with pytest.raises(BudgetDenied, match="HISTORICAL_DENIED_UNKNOWN"):
        q.admit_historical_provider_dispatch(request_identity="h", mint="history", request_class="HIST", now=100)
    assert not (q.queue.root / "provider_budget.json").exists()


def test_live_publication_and_historical_admission_share_ordering_lock(tmp_path):
    q = _queue(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    published = threading.Event()

    def historical():
        from src.ops.dev_provider_budget import DevProviderBudget
        budget = DevProviderBudget(q.queue.root)
        with budget.locked():
            assert q.classify_live_provider_demand(now=100) == "LIVE_CLEAR"
            entered.set()
            assert release.wait(2)
            budget.admit_locked("history", "HIST", now=100)

    def live_publish():
        assert entered.wait(2)
        q.queue.enqueue(_live(), message_id="live")
        published.set()

    first = threading.Thread(target=historical)
    second = threading.Thread(target=live_publish)
    first.start(); second.start()
    assert entered.wait(2)
    assert not published.wait(.05)
    release.set(); first.join(2); second.join(2)
    assert published.is_set()
    assert q.classify_live_provider_demand(now=100) == "LIVE_PENDING"
    with pytest.raises(BudgetDenied, match="HISTORICAL_DENIED_LIVE_PENDING"):
        q.admit_historical_provider_dispatch(request_identity="later", mint="history", request_class="HIST", now=100)


def test_existing_live_admission_and_budget_limits_are_unchanged(tmp_path, monkeypatch):
    q = _queue(tmp_path)
    monkeypatch.setenv("MONITOR_RUNTIME", "dev")
    for _ in range(4): q.admit_provider_dispatch("live", "LIVE", now=100)
    with pytest.raises(BudgetDenied, match="TOKEN_PROVIDER_BUDGET_EXHAUSTED"):
        q.admit_provider_dispatch("live", "LIVE", now=100)
