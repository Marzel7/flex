"""Provider-free compatibility checks for the frozen Watchtower lifecycle."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from src.ops.operator_lifecycle_projection import ensure_schema
from src.ops.operation_monitor_worker import MonitorQueue, _h
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


def test_monitor_projection_prefers_current_durable_queue_deadline(monkeypatch, tmp_path):
    database = tmp_path / "monitor.sqlite"
    _monitor_store(database)
    monkeypatch.setenv("WATCHTOWER_MONITOR_UI_DB_PATH", str(database))
    import src.ops.operation_monitor_worker as worker
    import src.ops.operator_routes as routes
    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    mint = next(iter(TARGETS))
    queue.queue.enqueue({
        "operation_id": "watchtower", "mint": mint,
        "monitor_state": "ENTRY_REFERENCE_QUALIFIED",
        "next_eligible_dispatch_at": 2_000_000_000,
    }, message_id="cattok-policy-c")
    monkeypatch.setattr(worker, "production_queue", lambda: queue)
    row = {item["mint"]: item for item in routes._monitor_live_projection()["rows"]}[mint]
    assert row["next_check_at"] == 2_000_000_000
    assert row["next_check_state"] == "SCHEDULED"


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


def _terminal_fact_with_assignment(assignment):
    return {
        "operation_id": "watchtower", "mint": "terminal-mint",
        "cohort_class": "PROSPECTIVE_MONITOR_COHORT",
        "monitor_state": "PRICE_MONITOR_COMPLETE_COLLAPSED",
        "next_observation_at": None, "final_proven_ath_mc": None,
        "entry_method": "FIRST_FULL_POST_MIGRATION_SECOND_MC",
        "entry_timestamp": 1791446400, "entry_mc_usd": 10.0,
        "monitor_completed_at": 1791447300,
        "assignment_timestamp": assignment["assigned_at"],
        "assignment_provenance": _h(assignment),
    }


def test_terminal_envelope_preserves_verified_post_watermark_assignment(tmp_path):
    selection = tmp_path / "selection.json"
    selection.write_text('{"mode":"DEV_005_ISOLATED_SOAK","allowlist":[],"minimum_assignment_timestamp":1791446333}')
    queue = MonitorQueue(tmp_path / "queue", enabled=True, soak_selection_path=selection)
    assignment = {"event_id": "membership-event", "assigned_at": 1791446334}
    fact = _terminal_fact_with_assignment(assignment)
    first = queue.enqueue_terminal_ath_finalization(fact, assignment=assignment)
    second = queue.enqueue_terminal_ath_finalization(fact, assignment=assignment)
    assert first["status"] == second["status"] == "ENQUEUED_TERMINAL_ATH"
    assert queue.queue.depth()["pending"] == 1
    payload = next((tmp_path / "queue" / "pending").glob("*.json")).read_text()
    assert '"event_id":"membership-event"' in payload
    assert queue.soak_allows({"assignment": assignment, "mint": "terminal-mint"}) is True


@pytest.mark.parametrize("assignment", [None, {"event_id": "", "assigned_at": 1791446334}, {"event_id": "event", "assigned_at": 1791446332}])
def test_terminal_envelope_missing_or_mismatched_assignment_fails_closed(tmp_path, assignment):
    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    fact_assignment = {"event_id": "event", "assigned_at": 1791446334}
    result = queue.enqueue_terminal_ath_finalization(
        _terminal_fact_with_assignment(fact_assignment), assignment=assignment
    )
    assert result == {"status": "DEFER_ASSIGNMENT_PROVENANCE_UNQUALIFIED"}
    assert queue.queue.depth()["pending"] == 0


def test_pre_watermark_terminal_envelope_remains_selection_excluded(tmp_path):
    selection = tmp_path / "selection.json"
    selection.write_text('{"mode":"DEV_005_ISOLATED_SOAK","allowlist":[],"minimum_assignment_timestamp":1791446333}')
    queue = MonitorQueue(tmp_path / "queue", enabled=True, soak_selection_path=selection)
    assignment = {"event_id": "historical-membership", "assigned_at": 1791446332}
    assert queue.enqueue_terminal_ath_finalization(
        _terminal_fact_with_assignment(assignment), assignment=assignment
    )["status"] == "ENQUEUED_TERMINAL_ATH"
    assert queue.soak_allows({"assignment": assignment, "mint": "terminal-mint"}) is False
