"""Provider-free regression coverage for the opt-in bounded Monitor soak."""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.ops import operation_monitor_service as service
from src.ops.monitor_live_admission import ensure_schema, record_member
from src.ops.operation_monitor_bridge_service import config as bridge_config, run_once as bridge_once
from src.ops.operation_monitor_worker import MonitorQueue
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


def test_iteration_caps_are_explicit_positive_opt_in(monkeypatch):
    monkeypatch.setenv("MONITOR_MAX_ITERATIONS", "1")
    assert service._bounded_iteration_limit() == 1
    assert bridge_config({"MONITOR_BRIDGE_SOURCE_DB": "source", "MONITOR_BRIDGE_MONITOR_DB": "monitor", "MONITOR_BRIDGE_QUEUE_PATH": "queue", "MONITOR_BRIDGE_MAX_ITERATIONS": "1"})["max_iterations"] == 1
    monkeypatch.setenv("MONITOR_MAX_ITERATIONS", "0")
    with pytest.raises(RuntimeError, match="INVALID_MONITOR_MAX_ITERATIONS"):
        service._bounded_iteration_limit()
