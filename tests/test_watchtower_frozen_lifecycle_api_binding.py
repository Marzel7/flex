"""Provider-free compatibility checks for the frozen Watchtower lifecycle."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from src.ops.operator_lifecycle_projection import ensure_schema
from src.ops.watchtower_terminal_ath_finalizer import WatchtowerTerminalAthFinalizer


TARGETS = {
    "9JtPfLbN32CszHmstoXYdWaazFLaoxhfqf9mKgQMpump": (105600.45900012992, 222174.89682065282),
    "7JVDPbS8iYHoUD4yWYxQH9yHcop5oPeBnfBvXkiqpump": (128917.09988926353, 168300.03364945002),
    "6KuKphbVWZoz4kagf5QGaJEsUxJBY99LdBetfvwtpump": (125378.29249620523, 125378.29249620523),
    "6LdjC13zbAxmruKrqsAQ4znUrjNe7gXXVeCHDBMYpump": (162775.9385584885, 274476.6056229458),
}


def _monitor_store(path):
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
        conn.executemany(
            """INSERT INTO operation_monitor_facts(
                operation_id,mint,cohort_class,entry_method,entry_status,entry_exactness,
                monitor_state,entry_timestamp,entry_mc_usd,running_peak_mc_usd,
                running_peak_timestamp,running_peak_multiple,drawdown_percent,
                provenance_digest,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [("watchtower", mint, "PROSPECTIVE_MONITOR_COHORT",
              "FIRST_FULL_POST_MIGRATION_SECOND_MC", "QUALIFIED", "EXACT",
              "PRICE_MONITOR_COMPLETE_COLLAPSED", 100, entry, peak, 150,
              peak / entry, 90.0, f"proof-{mint}", 100, 100)
             for mint, (entry, peak) in TARGETS.items()],
        )


def test_frozen_schema_has_one_finalization_authority_and_no_coverage_columns(tmp_path):
    database = tmp_path / "monitor.sqlite"
    _monitor_store(database)
    with sqlite3.connect(database) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(operation_monitor_facts)")}
    assert {
        "retained_monitor_peak_mc_usd", "retained_monitor_peak_evidence",
        "final_proven_ath_mc", "final_ath_multiple", "final_ath_evidence",
        "final_ath_resolution", "ath_finalization_request_id",
    }.issubset(columns)
    assert not {"terminal_coverage_state", "terminal_coverage_provenance_digest", "terminal_coverage_gap_digest", "terminal_coverage_updated_at"}.intersection(columns)


def test_monitor_projection_reads_configured_store_and_labels_observed_peaks(monkeypatch, tmp_path):
    database = tmp_path / "monitor.sqlite"
    _monitor_store(database)
    monkeypatch.setenv("WATCHTOWER_MONITOR_UI_DB_PATH", str(database))
    import src.ops.operation_monitor_worker as worker
    import src.ops.operator_routes as routes
    disabled = SimpleNamespace(enabled=False, depth=lambda: {"pending": 0, "retry": 0, "processing": 0, "dead_letter": 0})
    monkeypatch.setattr(worker, "production_queue", lambda: SimpleNamespace(queue=disabled))
    rows = {row["mint"]: row for row in routes._monitor_live_projection()["rows"]}
    assert set(rows) == set(TARGETS)
    for mint, (entry, peak) in TARGETS.items():
        assert rows[mint]["entry_mc_usd"] == entry
        assert rows[mint]["running_peak_mc_usd"] == peak
        assert rows[mint]["final_proven_ath_mc"] is None
        assert rows[mint]["coverage_class"] == "OBSERVED_LOWER_BOUND"
        assert rows[mint]["history_visible"] is False
    monkeypatch.delenv("WATCHTOWER_MONITOR_UI_DB_PATH")
    with pytest.raises(routes.MonitorStoreUnavailable, match="WATCHTOWER_MONITOR_UI_DB_PATH_REQUIRED"):
        routes._monitor_live_projection()


def test_strict_finalizer_rejects_incomplete_ohlcv_before_final_persistence(tmp_path):
    database = tmp_path / "terminal.sqlite"
    with sqlite3.connect(database) as conn:
        ensure_schema(conn)
        conn.execute("""INSERT INTO operation_monitor_facts(
            operation_id,mint,cohort_class,entry_method,entry_status,entry_exactness,
            monitor_state,entry_timestamp,entry_mc_usd,latest_mc_usd,
            monitor_completed_at,provenance_digest,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            "watchtower", "coverage-mint", "PROSPECTIVE_MONITOR_COHORT",
            "FIRST_FULL_POST_MIGRATION_SECOND_MC", "QUALIFIED", "EXACT",
            "PRICE_MONITOR_COMPLETE_COLLAPSED", 100, 10.0, 1.0, 1901,
            "proof", 1901, 1901,
        ))

    class Binding:
        def __call__(self, _request):
            return SimpleNamespace(status_code=200, payload={"data": {"items": [
                {"timestamp": 100, "o": 10, "h": 12, "l": 9, "c": 11},
            ]}})

    with pytest.raises(ValueError, match="CHUNK_COVERAGE_INCOMPLETE"):
        WatchtowerTerminalAthFinalizer(str(database), binding=Binding()).finalize(
            "coverage-mint", interval="15m", watchtower_price_fact_contract=False
        )
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT final_proven_ath_mc FROM operation_monitor_facts").fetchone()[0] is None
