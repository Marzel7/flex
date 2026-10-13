"""Provider-free regression tests for DEV035 API liveness and health budgets."""

from contextlib import contextmanager
import sqlite3
import time

import pytest


def _heartbeat_connection(last_seen: int) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE wt_worker_heartbeat(worker_name TEXT, last_seen INTEGER, status TEXT)"
    )
    conn.execute(
        "INSERT INTO wt_worker_heartbeat VALUES ('historical-worker', ?, 'error')",
        (last_seen,),
    )
    return conn


def test_healthz_is_live_when_only_historical_workers_are_stale(monkeypatch):
    import src.core.main as main

    conn = _heartbeat_connection(int(time.time()) - 10_000)

    @contextmanager
    def _read_only_connection(*_args, **_kwargs):
        yield conn

    monkeypatch.setattr(main, "managed_db_connect", _read_only_connection)
    response = main.app.test_client().get("/healthz")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["healthy"] is True
    assert payload["operational_ready"] is False
    assert payload["stale_workers"] == ["historical-worker"]


def test_healthz_rejects_genuine_database_failure(monkeypatch):
    import src.core.main as main

    def _unavailable(*_args, **_kwargs):
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(main, "managed_db_connect", _unavailable)
    response = main.app.test_client().get("/healthz")

    assert response.status_code == 503
    assert response.get_json()["db"] == "error"


def test_elapsed_component_budget_becomes_unknown_without_querying():
    import src.core.main as main

    with pytest.raises(main._HealthComponentDeadlineExceeded):
        main._bounded_health_ro_connect(sqlite3, ":memory:", time.monotonic() - 0.001)

    assert main._health_unknown_from_deadline("intelligence") == {
        "status": "UNKNOWN",
        "evidence": "TIME_BUDGET_EXCEEDED",
        "component": "intelligence",
    }


def test_full_health_returns_partial_unknown_when_component_deadline_expires(monkeypatch):
    import src.core.main as main

    def _deadline(*_args, **_kwargs):
        raise main._HealthComponentDeadlineExceeded("slow component")

    monkeypatch.setattr(main, "_bounded_health_ro_connect", _deadline)
    started = time.monotonic()
    response = main.app.test_client().get("/api/health/full")

    assert response.status_code == 200
    assert time.monotonic() - started < 15.0
    payload = response.get_json()
    assert payload["subsystems"]["ingestion"]["status"] == "UNKNOWN"
    assert payload["subsystems"]["intelligence"]["evidence"] == "TIME_BUDGET_EXCEEDED"
    assert payload["subsystems"]["price_worker"]["status"] == "DECOMMISSIONED"


def test_full_health_marks_unindexed_percentile_evidence_unknown():
    source = (__import__("pathlib").Path(__file__).parents[1] / "src/core/main.py").read_text()
    start = source.index("def api_health_full():")
    end = source.index("@app.route('/api/db-health')", start)
    block = source[start:end]
    assert "UNKNOWN_UNBOUNDED_QUERY" in block
    assert "ORDER BY runtime_secs ASC LIMIT 1 OFFSET" not in block
