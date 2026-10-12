"""Deterministic DEV020 pacing checks against the real DEV budget ledger."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from src.ops.dev_provider_budget import BudgetDenied, DevProviderBudget


def _module():
    path = Path(__file__).parents[1] / "scripts" / "run_dev020_prepeak_session.py"
    spec = importlib.util.spec_from_file_location("dev020_synthetic_clock", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class SyntheticClock:
    def __init__(self, value: float = 1_000) -> None:
        self.value = value

    def wall(self) -> float:
        return self.value

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _ledger_calls(root: Path) -> list[dict]:
    return json.loads((root / "provider_budget.json").read_text())["calls"]


def test_real_ledger_enforces_ten_per_rolling_minute_without_early_debit(tmp_path):
    clock = SyntheticClock()
    budget = DevProviderBudget(tmp_path, now=clock.wall)

    for number in range(10):
        budget.admit(f"mint-{number}", "DEV020_TEST", global_limit=10)

    before = _ledger_calls(tmp_path)
    with pytest.raises(BudgetDenied, match="GLOBAL_PROVIDER_BUDGET_EXHAUSTED"):
        budget.admit("mint-10", "DEV020_TEST", global_limit=10)
    assert _ledger_calls(tmp_path) == before

    clock.advance(59)
    with pytest.raises(BudgetDenied, match="GLOBAL_PROVIDER_BUDGET_EXHAUSTED"):
        budget.admit("mint-10", "DEV020_TEST", global_limit=10)
    assert _ledger_calls(tmp_path) == before

    clock.advance(1)
    admitted = budget.admit("mint-10", "DEV020_TEST", global_limit=10)
    assert admitted["global_calls"] == 1
    assert [call["mint"] for call in _ledger_calls(tmp_path)] == ["mint-10"]


def test_rate_limiter_uses_real_ledger_and_rejected_admission_never_transports(tmp_path):
    module = _module()
    clock = SyntheticClock()
    transported: list[str] = []
    limiter = module.RateLimited(
        lambda request: transported.append(request["request_parameters"]["address"]) or {"ok": True},
        budget_root=tmp_path,
        monotonic_clock=clock.monotonic,
        wall_clock=clock.wall,
        sleep=lambda _seconds: None,
    )

    for number in range(10):
        limiter({"request_parameters": {"address": f"mint-{number}"}})

    with pytest.raises(BudgetDenied, match="GLOBAL_PROVIDER_BUDGET_EXHAUSTED"):
        limiter({"request_parameters": {"address": "mint-10"}})
    assert len(transported) == 10
    assert len(_ledger_calls(tmp_path)) == 10

    clock.advance(60)
    limiter({"request_parameters": {"address": "mint-10"}})
    assert transported[-1] == "mint-10"
    assert len(_ledger_calls(tmp_path)) == 1


def test_fifty_token_cap_and_durable_cooldown_are_deterministic(tmp_path, monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "JOURNAL", tmp_path / "journal.db")
    clock = SyntheticClock()
    calls: list[str] = []
    limiter = module.RateLimited(
        lambda request: calls.append(request["request_parameters"]["address"]) or {"ok": True},
        budget_root=tmp_path / "budget",
        monotonic_clock=clock.monotonic,
        wall_clock=clock.wall,
        sleep=clock.advance,
    )

    for number in range(50):
        limiter({"request_parameters": {"address": f"mint-{number}"}})
    assert len(calls) == 50
    assert limiter.calls == 50
    with pytest.raises(RuntimeError, match="DEV020_PROVIDER_REQUEST_CAP"):
        limiter({"request_parameters": {"address": "mint-51"}})
    assert len(calls) == 50

    module._record_session_cooldown(now=int(clock.wall()))
    with pytest.raises(RuntimeError, match="DEV020_SESSION_COOLDOWN_ACTIVE"):
        module._require_session_ready(now=int(clock.wall()) + 59)
    module._require_session_ready(now=int(clock.wall()) + 60)
