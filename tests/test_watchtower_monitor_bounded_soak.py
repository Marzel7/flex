"""Provider-free regression coverage for the opt-in bounded Monitor soak."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.ops import operation_monitor_service as service
from src.ops.monitor_live_admission import ensure_schema, record_member
from src.ops.operation_monitor_bridge_service import config as bridge_config, run_once as bridge_once
from src.ops.operation_monitor_worker import MonitorQueue, MonitorWorker
from src.ops.dev_provider_budget import BudgetDenied


def _selection(path, watermark=100):
    path.write_text(json.dumps({
        "mode": "DEV_005_ISOLATED_SOAK",
        "allowlist": [],
        "minimum_assignment_timestamp": watermark,
    }))
    return path


def _envelope(mint, assigned_at):
    return {
        "operation_id": "watchtower", "mint": mint,
        "assignment": {"event_id": f"event-{mint}", "assigned_at": assigned_at},
    }


def test_fixed_watermark_preserves_retained_queue_messages(tmp_path):
    selection = _selection(tmp_path / "selection.json")
    queue = MonitorQueue(tmp_path / "queue", enabled=True, soak_selection_path=selection)
    queue.queue.enqueue(_envelope("old", 99), message_id="old")
    queue.queue.enqueue(_envelope("new", 100), message_id="new")

    claimed = queue.claim(10)

    assert [item.message_id for item in claimed] == ["new"]
    assert (queue.queue.root / "pending" / "old.json").exists()
    assert (queue.queue.root / "processing" / "new.json").exists()


def test_committed_continuous_watermark_excludes_historical_and_allows_forward_work(tmp_path):
    selection = Path(__file__).resolve().parents[1] / "docs" / "audits" / "watchtower_continuous_monitor_watermark.v1.json"
    payload = json.loads(selection.read_text())
    assert payload == {
        "allowlist": [],
        "minimum_assignment_timestamp": 1791446333,
        "mode": "DEV_005_ISOLATED_SOAK",
    }
    queue = MonitorQueue(tmp_path / "queue", enabled=True, soak_selection_path=selection)

    assert not queue.soak_allows(_envelope("historical", 1791446332))
    assert queue.soak_allows(_envelope("forward", 1791446333))


@pytest.mark.parametrize(
    "kind,assigned_at",
    (pytest.param("missing", None, id="missing"), pytest.param("null", None, id="null"),
     pytest.param("malformed", "not-a-timestamp", id="malformed"), pytest.param("pre", 99, id="pre-watermark")),
)
def test_unproven_assignment_time_never_reaches_provider_boundary(tmp_path, monkeypatch, kind, assigned_at):
    """The installed claim loop rejects unproven assignments before dispatch.

    This exercises the worker loop, not just the predicate: neither its
    Birdeye transport nor its immediate provider-budget admission may run.
    """
    selection = _selection(tmp_path / "selection.json", watermark=100)
    queue = MonitorQueue(tmp_path / "queue", enabled=True, soak_selection_path=selection)
    envelope = _envelope("target", assigned_at)
    if kind == "missing":
        envelope["assignment"].pop("assigned_at")
    envelope["work_type"] = "WATCHTOWER_TERMINAL_ATH_FINALIZATION"
    queue.queue.enqueue(envelope, message_id="unproven")

    provider_admissions = []
    transport_calls = []

    def provider_spy(*args, **kwargs):
        provider_admissions.append((args, kwargs))
        raise AssertionError("unproven assignment reached provider admission")

    def transport_spy(*args, **kwargs):
        transport_calls.append((args, kwargs))
        raise AssertionError("unproven assignment reached provider transport")

    monkeypatch.setattr(queue, "admit_provider_dispatch", provider_spy)
    worker = MonitorWorker(queue, transport=transport_spy, persist=lambda _item: True)

    assert worker.process_once() == 0
    assert provider_admissions == []
    assert transport_calls == []
    assert (queue.queue.root / "pending" / "unproven.json").exists()


def test_selection_only_service_skips_global_reconciliation(tmp_path, monkeypatch):
    selection = _selection(tmp_path / "selection.json")
    queue = MonitorQueue(tmp_path / "queue", enabled=True, soak_selection_path=selection)

    class Worker:
        def process_once(self):
            return "bounded-dispatch"

    for name in (
        "reconcile_byzantine_assignment_admissions",
        "reconcile_watchtower_assignment_admissions",
        "reconcile_watchtower_deep_assignment_admissions",
        "reconcile_qualified_monitor_fact_queue_projection",
    ):
        monkeypatch.setattr(service, name, lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError(name)))

    assert service.run_once(worker=Worker(), queue=queue, db_path="unused") == "bounded-dispatch"


def test_bridge_delivers_only_post_watermark_outbox_row_once(tmp_path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE operator_launch_membership (mint TEXT, operator_id TEXT, source_population_id TEXT, assigned_at INTEGER, event_id TEXT)")
        ensure_schema(connection)
        record_member(connection, operation_id="watchtower", mint="old", membership_id="old-member", committed_at=99)
        record_member(connection, operation_id="watchtower", mint="new", membership_id="new-member", committed_at=100)
        connection.commit()
    selection = _selection(tmp_path / "selection.json")
    cfg = {"source_db": str(source), "monitor_db": str(tmp_path / "monitor.db"), "queue_path": str(tmp_path / "queue"), "selection_path": str(selection), "health_path": ""}

    first = bridge_once(cfg, now=200)
    second = bridge_once(cfg, now=201)

    assert first and first["state"] == "DELIVERED"
    assert second is None
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT state FROM monitor_admission_outbox WHERE mint='old'").fetchone()[0] == "PENDING"
        assert connection.execute("SELECT state FROM monitor_admission_outbox WHERE mint='new'").fetchone()[0] == "DELIVERED"


def test_zero_provider_budget_fails_closed_before_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_RUNTIME", "dev")
    monkeypatch.setenv("MONITOR_PROVIDER_GLOBAL_LIMIT", "0")
    monkeypatch.setenv("MONITOR_PROVIDER_TOKEN_LIMIT", "0")
    queue = MonitorQueue(tmp_path / "queue", enabled=True)

    with pytest.raises(BudgetDenied, match="GLOBAL_PROVIDER_BUDGET_EXHAUSTED"):
        queue.admit_provider_dispatch("mint", "15M_EVIDENCE", now=100)
    assert not (queue.queue.root / "provider_budget.json").exists()


def test_continuous_provider_limits_are_explicit_and_bounded(tmp_path, monkeypatch):
    """The continuous contract must use an explicit finite 20/4 budget."""
    monkeypatch.setenv("MONITOR_RUNTIME", "dev")
    monkeypatch.setenv("MONITOR_PROVIDER_GLOBAL_LIMIT", "20")
    monkeypatch.setenv("MONITOR_PROVIDER_TOKEN_LIMIT", "4")
    queue = MonitorQueue(tmp_path / "queue", enabled=True)

    for index in range(4):
        assert queue.admit_provider_dispatch("mint", "15M_EVIDENCE", now=100 + index)
    with pytest.raises(BudgetDenied, match="TOKEN_PROVIDER_BUDGET_EXHAUSTED"):
        queue.admit_provider_dispatch("mint", "15M_EVIDENCE", now=104)

    for index in range(16):
        assert queue.admit_provider_dispatch(f"other-{index}", "15M_EVIDENCE", now=105 + index)
    with pytest.raises(BudgetDenied, match="GLOBAL_PROVIDER_BUDGET_EXHAUSTED"):
        queue.admit_provider_dispatch("other-20", "15M_EVIDENCE", now=121)


@pytest.mark.parametrize("value", ("", "bad", "-1"))
def test_invalid_provider_limits_fail_closed(tmp_path, monkeypatch, value):
    monkeypatch.setenv("MONITOR_RUNTIME", "dev")
    monkeypatch.setenv("MONITOR_PROVIDER_GLOBAL_LIMIT", value)
    monkeypatch.setenv("MONITOR_PROVIDER_TOKEN_LIMIT", "4")
    queue = MonitorQueue(tmp_path / "queue", enabled=True)

    with pytest.raises(BudgetDenied, match="INVALID_PROVIDER_BUDGET_LIMIT"):
        queue.admit_provider_dispatch("mint", "15M_EVIDENCE", now=100)


def test_iteration_caps_are_explicit_positive_opt_in(monkeypatch):
    monkeypatch.setenv("MONITOR_MAX_ITERATIONS", "1")
    assert service._bounded_iteration_limit() == 1
    assert bridge_config({"MONITOR_BRIDGE_SOURCE_DB": "source", "MONITOR_BRIDGE_MONITOR_DB": "monitor", "MONITOR_BRIDGE_QUEUE_PATH": "queue", "MONITOR_BRIDGE_MAX_ITERATIONS": "1"})["max_iterations"] == 1
    monkeypatch.setenv("MONITOR_MAX_ITERATIONS", "0")
    with pytest.raises(RuntimeError, match="INVALID_MONITOR_MAX_ITERATIONS"):
        service._bounded_iteration_limit()


def test_launcher_preserves_bounded_soak_controls_across_env_reset():
    """The hardened env -i launcher must not discard explicit soak limits."""
    launcher = (Path(__file__).resolve().parents[1] / "scripts" / "run_dev_005a_monitor.sh").read_text()

    for name in (
        "MONITOR_MAX_ITERATIONS",
        "MONITOR_PROVIDER_GLOBAL_LIMIT",
        "MONITOR_PROVIDER_TOKEN_LIMIT",
    ):
        assert f'{name}_VALUE="${{{name}:-}}"' in launcher
        assert f'{name}="${name}_VALUE"' in launcher
