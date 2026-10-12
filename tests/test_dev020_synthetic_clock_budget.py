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


def _seed_pending(module, root: Path, mint: str = "mint") -> None:
    module.ROOT = root
    module.JOURNAL = root / "journal.db"
    with module._connect() as connection:
        connection.execute(
            "INSERT INTO dev020_prepeak_tokens VALUES(?,?,?,?,?,?)",
            (mint, "{}", "PENDING", None, 1, 1),
        )


def _state(module, mint: str = "mint") -> str:
    with module._connect() as connection:
        return connection.execute("SELECT state FROM dev020_prepeak_tokens WHERE mint=?", (mint,)).fetchone()[0]


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


def test_budget_denial_restores_pending_without_transport_or_debit(tmp_path):
    module = _module()
    _seed_pending(module, tmp_path)
    clock = SyntheticClock()
    budget = DevProviderBudget(tmp_path / "budget", now=clock.wall)
    for number in range(10):
        budget.admit(f"prior-{number}", "DEV020_TEST", global_limit=10)
    calls: list[dict] = []
    limiter = module.RateLimited(
        lambda request: calls.append(request) or {"ok": True},
        budget_root=tmp_path / "budget",
        monotonic_clock=clock.monotonic,
        wall_clock=clock.wall,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(BudgetDenied, match="GLOBAL_PROVIDER_BUDGET_EXHAUSTED"):
        module._admit_then_mark_in_flight(
            mint="mint", request={"request_parameters": {"address": "mint"}}, transport=limiter, now=1,
        )
    assert _state(module) == "PENDING"
    assert calls == []
    assert len(_ledger_calls(tmp_path / "budget")) == 10


def test_successful_admission_marks_in_flight_before_one_transport(tmp_path):
    module = _module()
    _seed_pending(module, tmp_path)
    clock = SyntheticClock()
    calls: list[dict] = []
    limiter = module.RateLimited(
        lambda request: calls.append(request) or {"ok": True},
        budget_root=tmp_path / "budget",
        monotonic_clock=clock.monotonic,
        wall_clock=clock.wall,
        sleep=lambda _seconds: None,
    )

    result = module._admit_then_mark_in_flight(
        mint="mint", request={"request_parameters": {"address": "mint"}}, transport=limiter, now=1,
    )
    assert result == {"ok": True}
    assert _state(module) == "REQUEST_IN_FLIGHT"
    assert len(calls) == 1
    assert len(_ledger_calls(tmp_path / "budget")) == 1


@pytest.mark.parametrize("uncertain", ["ADMISSION_INTENT", "REQUEST_IN_FLIGHT"])
def test_uncertain_attempts_remain_excluded_and_terminal_records_unchanged(tmp_path, monkeypatch, uncertain):
    module = _module()
    _seed_pending(module, tmp_path, mint="terminal")
    with module._connect() as connection:
        connection.execute("UPDATE dev020_prepeak_tokens SET state='OBSERVED_SPARSE',result_json='stable' WHERE mint='terminal'")
        connection.execute("INSERT INTO dev020_prepeak_tokens VALUES(?,?,?,?,?,?)", ("uncertain", "{}", uncertain, None, 1, 1))
    monkeypatch.setattr(module, "eligible_rows", lambda: [])

    with pytest.raises(RuntimeError, match="DEV020_ATTEMPT_OUTCOME_UNPROVEN"):
        module.select()
    with module._connect() as connection:
        assert connection.execute("SELECT state,result_json FROM dev020_prepeak_tokens WHERE mint='terminal'").fetchone() == ("OBSERVED_SPARSE", "stable")


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
