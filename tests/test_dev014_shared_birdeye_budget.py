"""Provider-free qualification of DEV-014's shared Birdeye admission."""
from __future__ import annotations

import json
import threading

import pytest

from src.ops.dev_provider_budget import BudgetDenied
from src.ops.operation_monitor_worker import MonitorQueue
from src.ops.watchtower_historical_budget import HistoricalForensicsBudgetAdmission


def _calls(root):
    return json.loads((root / "provider_budget.json").read_text())["calls"]


def test_live_and_historical_share_one_atomic_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_RUNTIME", "dev")
    root = tmp_path / "queue"
    live = MonitorQueue(root, enabled=True)
    historical = HistoricalForensicsBudgetAdmission(root)

    live.admit_provider_dispatch("live-mint", "CURRENT_MC_OVERLAY", now=100)
    historical.admit(mint="research-mint", request_identity="research-1", now=100)

    assert [call["class"] for call in _calls(root)] == ["CURRENT_MC_OVERLAY", "WATCHTOWER_HISTORICAL_FORENSICS_1M"]
    assert {path.name for path in root.iterdir()} == {"provider_budget.json", "provider_budget.lock"}


def test_mixed_concurrent_callers_cannot_oversubscribe_global_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_RUNTIME", "dev")
    monkeypatch.setenv("MONITOR_PROVIDER_GLOBAL_LIMIT", "3")
    monkeypatch.setenv("MONITOR_PROVIDER_TOKEN_LIMIT", "4")
    root = tmp_path / "queue"
    barrier = threading.Barrier(8)
    accepted: list[str] = []
    denied: list[str] = []
    guard = threading.Lock()

    def attempt(index):
        queue = MonitorQueue(root, enabled=True)
        history = HistoricalForensicsBudgetAdmission(root)
        barrier.wait()
        try:
            if index % 2:
                history.admit(mint=f"mint-{index}", request_identity=f"request-{index}", now=100)
            else:
                queue.admit_provider_dispatch(f"mint-{index}", "LIVE_CURRENT", now=100)
        except BudgetDenied:
            with guard: denied.append(str(index))
        else:
            with guard: accepted.append(str(index))

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(8)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()

    assert len(accepted) == 3
    assert len(denied) == 5
    assert len(_calls(root)) == 3


def test_per_mint_limit_and_duplicate_historical_admission_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_RUNTIME", "dev")
    monkeypatch.setenv("MONITOR_PROVIDER_GLOBAL_LIMIT", "20")
    monkeypatch.setenv("MONITOR_PROVIDER_TOKEN_LIMIT", "2")
    root = tmp_path / "queue"
    live = MonitorQueue(root, enabled=True)
    historical = HistoricalForensicsBudgetAdmission(root)
    live.admit_provider_dispatch("same-mint", "LIVE_CURRENT", now=100)
    historical.admit(mint="same-mint", request_identity="request-1", now=100)

    with pytest.raises(BudgetDenied, match="DUPLICATE_HISTORICAL_PROVIDER_ADMISSION"):
        historical.admit(mint="other-mint", request_identity="request-1", now=100)
    with pytest.raises(BudgetDenied, match="TOKEN_PROVIDER_BUDGET_EXHAUSTED"):
        HistoricalForensicsBudgetAdmission(root).admit(mint="same-mint", request_identity="request-2", now=100)
    assert len(_calls(root)) == 2


def test_invalid_existing_ledger_fails_closed_without_transport_admission(tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_RUNTIME", "dev")
    root = tmp_path / "queue"; root.mkdir()
    (root / "provider_budget.json").write_text("not-json")
    with pytest.raises(BudgetDenied, match="PROVIDER_BUDGET_LEDGER_INVALID"):
        HistoricalForensicsBudgetAdmission(root).admit(mint="mint", request_identity="request", now=100)
    assert (root / "provider_budget.json").read_text() == "not-json"


def test_denial_prevents_transport_and_no_background_service_is_started(tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_RUNTIME", "dev")
    monkeypatch.setenv("MONITOR_PROVIDER_GLOBAL_LIMIT", "0")
    before = {thread.ident for thread in threading.enumerate()}
    with pytest.raises(BudgetDenied, match="GLOBAL_PROVIDER_BUDGET_EXHAUSTED"):
        HistoricalForensicsBudgetAdmission(tmp_path / "queue").admit(mint="mint", request_identity="request", now=100)
    assert {thread.ident for thread in threading.enumerate()} == before
    assert not (tmp_path / "queue" / "provider_budget.json").exists()
