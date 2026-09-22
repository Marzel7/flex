import sqlite3

import pytest

from src.ops import operation_fingerprint_drift as drift


def _operation():
    return {"operator_id": "op-1", "display_name": "Byzantine"}


def test_observer_releases_transaction_before_each_analytical_phase(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE marker(value TEXT)")
    phases = []

    def read_rows(current, _mint):
        phases.append(("rows", current.in_transaction))
        return []

    monkeypatch.setattr(drift, "_rows", read_rows)
    monkeypatch.setattr(drift, "_route", lambda _rows: ())
    monkeypatch.setattr(drift, "_exact_profiles", lambda _conn, _mint: set())
    monkeypatch.setattr(drift, "_active_operations", lambda _conn: [_operation()])
    monkeypatch.setattr(drift, "_expected_route", lambda _conn, _name: ())
    monkeypatch.setattr(drift, "compare_route", lambda _expected, _observed: (
        "EXACT_MATCH", ["topology"], []
    ))

    def write_phase(current, *_args):
        assert not current.in_transaction
        current.execute("INSERT INTO marker VALUES ('evidence')")

    def health_phase(current, *_args):
        phases.append(("health", current.in_transaction))
        current.execute("INSERT INTO marker VALUES ('health')")

    def cluster_phase(current, *_args):
        phases.append(("cluster", current.in_transaction))
        current.execute("INSERT INTO marker VALUES ('cluster')")

    monkeypatch.setattr(drift, "_upsert_evidence", write_phase)
    monkeypatch.setattr(drift, "_refresh_health", health_phase)
    monkeypatch.setattr(drift, "_refresh_clusters", cluster_phase)

    assert drift.observe_completed_walkback(conn, "mint-1", now=1) == {"EXACT_MATCH": 1}
    assert phases == [("rows", False), ("health", False), ("cluster", False)]
    assert conn.in_transaction is False
    assert conn.execute("SELECT COUNT(*) FROM marker").fetchone()[0] == 3


def test_observer_rolls_back_failed_optional_write(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE marker(value TEXT)")
    conn.commit()

    monkeypatch.setattr(drift, "_rows", lambda _conn, _mint: [])
    monkeypatch.setattr(drift, "_route", lambda _rows: ())
    monkeypatch.setattr(drift, "_exact_profiles", lambda _conn, _mint: set())
    monkeypatch.setattr(drift, "_active_operations", lambda _conn: [_operation()])
    monkeypatch.setattr(drift, "_expected_route", lambda _conn, _name: ())
    monkeypatch.setattr(drift, "compare_route", lambda _expected, _observed: (
        "EXACT_MATCH", ["topology"], []
    ))
    monkeypatch.setattr(drift, "_upsert_evidence", lambda current, *_args: current.execute(
        "INSERT INTO marker VALUES ('evidence')"
    ))

    def fail_health(current, *_args):
        current.execute("INSERT INTO marker VALUES ('uncommitted-health')")
        raise sqlite3.OperationalError("offline injected failure")

    monkeypatch.setattr(drift, "_refresh_health", fail_health)

    with pytest.raises(sqlite3.OperationalError, match="offline injected failure"):
        drift.observe_completed_walkback(conn, "mint-1", now=1)

    assert conn.in_transaction is False
    assert conn.execute("SELECT value FROM marker ORDER BY rowid").fetchall() == [
        ("evidence",)
    ]
