"""Provider-free terminal coverage and monitor-store binding contracts."""
from __future__ import annotations

import sqlite3
import json
from types import SimpleNamespace

import pytest

from src.ops.operator_lifecycle_projection import ensure_schema
from src.ops.operation_monitor_worker import MonitorQueue, MonitorWorker


def _terminal_fact(state: str) -> dict:
    return {
        "operation_id": "watchtower", "mint": "coverage-mint",
        "cohort_class": "PROSPECTIVE_MONITOR_COHORT",
        "monitor_state": "PRICE_MONITOR_COMPLETE_COLLAPSED",
        "next_observation_at": None, "final_proven_ath_mc": None,
        "entry_method": "FIRST_FULL_POST_MIGRATION_SECOND_MC",
        "entry_timestamp": 100, "entry_mc_usd": 10.0,
        "monitor_completed_at": 200, "terminal_coverage_state": state,
        "terminal_coverage_provenance_digest": "coverage-proof",
        "terminal_coverage_gap_digest": "gap-proof",
        "provenance_digest": "fact-proof",
    }


def _terminal_envelope(state: str) -> dict:
    return {
        "work_type": "WATCHTOWER_TERMINAL_ATH_FINALIZATION", "operation_id": "watchtower",
        "mint": "coverage-mint", "logical_identity": "fixture-identity",
        "terminal_coverage_state": state,
        "terminal_coverage_provenance_digest": "coverage-proof",
        "terminal_coverage_gap_digest": "gap-proof",
    }


def test_legacy_schema_gains_explicit_terminal_coverage_contract(tmp_path):
    database = tmp_path / "legacy.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute("""CREATE TABLE operation_monitor_facts(
            operation_id TEXT,mint TEXT,cohort_class TEXT,entry_method TEXT,
            monitor_state TEXT,next_observation_at INTEGER,provenance_digest TEXT,created_at INTEGER,updated_at INTEGER,
            PRIMARY KEY(operation_id,mint))""")
        conn.execute("""CREATE TABLE operation_monitor_observations(
            operation_id TEXT,mint TEXT,observation_timestamp INTEGER,mc_usd REAL,
            resolution TEXT,source TEXT,request_identity TEXT,provenance_digest TEXT,
            created_at INTEGER,PRIMARY KEY(operation_id,mint,observation_timestamp,resolution))""")
        ensure_schema(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(operation_monitor_facts)")}
    assert {"terminal_coverage_state", "terminal_coverage_provenance_digest", "terminal_coverage_gap_digest"}.issubset(columns)


@pytest.mark.parametrize("state", ["PARTIAL", "UNKNOWN", "malformed"])
def test_incomplete_terminal_fact_is_not_enqueued_for_provider_finalization(tmp_path, state):
    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    result = queue.enqueue_terminal_ath_finalization(_terminal_fact(state))
    assert result["status"] == f"DEFER_TERMINAL_COVERAGE_{state.upper() if state in {'PARTIAL', 'UNKNOWN'} else 'UNKNOWN'}"
    assert queue.queue.depth()["pending"] == 0


def test_incomplete_terminal_envelope_is_sealed_without_finalizer_or_provider(tmp_path):
    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    queue.queue.enqueue(_terminal_envelope("PARTIAL"), message_id="partial")
    finalizer_calls: list[str] = []

    class Finalizer:
        def __init__(self, *_args, **_kwargs):
            finalizer_calls.append("constructed")

    worker = MonitorWorker(
        queue, db_path=str(tmp_path / "unused.sqlite"),
        transport=lambda _payload: (_ for _ in ()).throw(AssertionError("provider dispatch")),
        terminal_finalizer_factory=Finalizer,
    )
    assert worker.process_once() == 1
    assert finalizer_calls == []
    payload = json.loads((tmp_path / "queue" / "dead_letter" / "partial.json").read_text())
    assert payload["envelope"]["provider_outcome"] == "TERMINAL_COVERAGE_BLOCKED"
    assert payload["envelope"]["next_eligible_dispatch_at"] is None
    assert queue.queue.depth()["retry"] == 0


def test_complete_terminal_coverage_reaches_finalizer(tmp_path):
    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    queue.queue.enqueue(_terminal_envelope("COMPLETE"), message_id="complete")
    calls: list[str] = []

    class Finalizer:
        def __init__(self, *_args, **_kwargs):
            calls.append("constructed")
        def freeze(self, _mint):
            return {"mint": "coverage-mint", "terminal_coverage_state": "COMPLETE"}
        def logical_job_identity(self, _fact):
            return "fixture-identity"
        def finalize(self, _mint, **kwargs):
            calls.append(str(kwargs["watchtower_price_fact_contract"]))
            return {"state": "FINALIZED"}

    worker = MonitorWorker(queue, db_path=str(tmp_path / "unused.sqlite"), transport=lambda _payload: None, terminal_finalizer_factory=Finalizer)
    assert worker.process_once() == 1
    assert calls == ["constructed", "False"]
    assert queue.queue.depth()["pending"] == 0


def test_live_projection_uses_only_configured_monitor_store(monkeypatch, tmp_path):
    database = tmp_path / "monitor.sqlite"
    recovered_entries = {
        "9JtPfLbN32CszHmstoXYdWaazFLaoxhfqf9mKgQMpump": 105600.45900012992,
        "7JVDPbS8iYHoUD4yWYxQH9yHcop5oPeBnfBvXkiqpump": 128917.09988926353,
        "6KuKphbVWZoz4kagf5QGaJEsUxJBY99LdBetfvwtpump": 125378.29249620523,
        "6LdjC13zbAxmruKrqsAQ4znUrjNe7gXXVeCHDBMYpump": 162775.9385584885,
    }
    with sqlite3.connect(database) as conn:
        ensure_schema(conn)
        conn.executemany("""INSERT INTO operation_monitor_facts(
            operation_id,mint,cohort_class,entry_method,entry_status,entry_exactness,
            monitor_state,entry_timestamp,entry_mc_usd,provenance_digest,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", [(
            "watchtower", mint, "PROSPECTIVE_MONITOR_COHORT",
            "FIRST_FULL_POST_MIGRATION_SECOND_MC", "QUALIFIED", "EXACT",
            "PRICE_MONITOR_COMPLETE_COLLAPSED", 100, entry_mc, f"proof-{mint}", 100, 100,
        ) for mint, entry_mc in recovered_entries.items()])
    monkeypatch.setenv("WATCHTOWER_MONITOR_UI_DB_PATH", str(database))
    import src.ops.operator_routes as routes
    import src.ops.operation_monitor_worker as monitor_worker
    disabled_queue = SimpleNamespace(enabled=False, depth=lambda: {"pending": 0, "retry": 0, "processing": 0, "dead_letter": 0})
    monkeypatch.setattr(monitor_worker, "production_queue", lambda: SimpleNamespace(queue=disabled_queue))
    projection = routes._monitor_live_projection()
    rows = {row["mint"]: row for row in projection["rows"]}
    assert {mint: row["entry_mc_usd"] for mint, row in rows.items()} == recovered_entries
    assert {row["lifecycle_class"] for row in rows.values()} == {"TERMINAL_COVERAGE_UNKNOWN"}
    monkeypatch.delenv("WATCHTOWER_MONITOR_UI_DB_PATH")
    with pytest.raises(routes.MonitorStoreUnavailable, match="WATCHTOWER_MONITOR_UI_DB_PATH_REQUIRED"):
        routes._monitor_live_projection()
