"""Provider-free compatibility coverage for the Monitor service scan sequence."""
from __future__ import annotations

import sqlite3

from src.ops import operation_monitor_service as service
from src.ops.operation_monitor_worker import MonitorQueue, MonitorWorker


def _ensure_schema(connection):
    connection.executescript("""
    CREATE TABLE operation_monitor_facts (
      operation_id TEXT NOT NULL, mint TEXT NOT NULL, cohort_class TEXT,
      assignment_timestamp INTEGER, assignment_provenance TEXT, entry_method TEXT,
      entry_timestamp INTEGER, entry_mc_usd REAL, entry_native_mc_sol TEXT,
      entry_status TEXT, entry_exactness TEXT, latest_mc_usd REAL,
      latest_mc_timestamp INTEGER, current_multiple REAL, running_peak_mc_usd REAL,
      running_peak_timestamp INTEGER, running_peak_multiple REAL, drawdown_percent REAL,
      reached_2x INTEGER, reached_5x INTEGER, reached_10x INTEGER,
      first_2x_timestamp INTEGER, first_5x_timestamp INTEGER, first_10x_timestamp INTEGER,
      drawdown_25_timestamp INTEGER, drawdown_50_timestamp INTEGER,
      drawdown_75_timestamp INTEGER, drawdown_85_timestamp INTEGER,
      monitor_state TEXT, monitor_started_at INTEGER, last_observation_at INTEGER,
      next_observation_at INTEGER, monitor_completed_at INTEGER,
      provider_call_count INTEGER DEFAULT 0, candles_retained INTEGER DEFAULT 0,
      candle_resolution TEXT, evidence_status TEXT, provenance_digest TEXT,
      created_at INTEGER, updated_at INTEGER, retained_monitor_peak_mc_usd REAL,
      retained_monitor_peak_evidence TEXT, final_proven_ath_mc REAL,
      final_ath_multiple REAL, final_ath_evidence TEXT, final_ath_resolution TEXT,
      final_ath_bucket_start INTEGER, final_ath_bucket_end INTEGER,
      ath_finalized_at INTEGER, ath_finalization_request_id TEXT,
      ath_finalization_provenance_digest TEXT,
      PRIMARY KEY(operation_id, mint)
    );
    CREATE TABLE operation_monitor_observations (
      operation_id TEXT NOT NULL, mint TEXT NOT NULL, observation_timestamp INTEGER NOT NULL,
      mc_usd REAL, resolution TEXT, source TEXT, request_identity TEXT,
      provenance_digest TEXT, created_at INTEGER, open_mc_usd REAL, high_mc_usd REAL,
      low_mc_usd REAL, close_mc_usd REAL,
      PRIMARY KEY(operation_id, mint, observation_timestamp, resolution)
    );
    """)


def _persist(path):
    def commit(item):
        with sqlite3.connect(path) as connection:
            for statement, values in item.statements:
                connection.execute(statement, values)
        return True
    return commit


def test_four_provider_free_service_scans_reach_existing_dispatch_path(tmp_path, monkeypatch):
    """The service calls the recovered compatibility method before dispatch."""
    database = tmp_path / "monitor.db"
    with sqlite3.connect(database) as connection:
        _ensure_schema(connection)

    # The admission reconcilers belong to source-authority integration.  Empty
    # provider-free scans exercise the same service ordering without requiring
    # their source tables or allowing a real transport.
    for name in (
        "reconcile_byzantine_assignment_admissions",
        "reconcile_watchtower_assignment_admissions",
        "reconcile_watchtower_deep_assignment_admissions",
        "reconcile_qualified_monitor_fact_queue_projection",
    ):
        monkeypatch.setattr(service, name, lambda *_args, **_kwargs: {})

    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    worker = MonitorWorker(
        queue,
        transport=lambda _request: (_ for _ in ()).throw(AssertionError("provider transport must not run")),
        persist=_persist(database),
        db_path=str(database),
        opening_jobs_path=tmp_path / "opening.db",
        provider_work_path=tmp_path / "provider.db",
    )
    calls = {"stale": 0, "retained": 0, "terminal": 0, "opening": 0, "dispatch": 0}
    for method, key in (
        ("reconcile_stale_watchtower_pending_openings", "stale"),
        ("reconcile_retained_watchtower_facts", "retained"),
        ("reconcile_terminal_ath_jobs", "terminal"),
        ("process_entry_reference_opening_once", "opening"),
        ("process_once", "dispatch"),
    ):
        original = getattr(worker, method)

        def wrapped(*args, _original=original, _key=key, **kwargs):
            calls[_key] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(worker, method, wrapped)

    for _ in range(4):
        service.run_once(worker=worker, queue=queue, db_path=str(database))

    assert calls == {key: 4 for key in calls}
