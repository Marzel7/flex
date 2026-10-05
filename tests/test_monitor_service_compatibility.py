"""Provider-free compatibility coverage for the Monitor service scan sequence."""
from __future__ import annotations

import sqlite3
import json
import os

import pytest

from src.ops import operation_monitor_service as service
from src.ops.operation_monitor_worker import (
    MonitorQueue,
    MonitorWorker,
    reconcile_qualified_monitor_fact_queue_projection,
)


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


def _active_fact(database, mint, *, terminal=False):
    state = "PRICE_MONITOR_COMPLETE_COLLAPSED" if terminal else "MONITORING_ACTIVE"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO operation_monitor_facts(
                 operation_id,mint,cohort_class,assignment_timestamp,assignment_provenance,
                 entry_method,entry_timestamp,entry_mc_usd,entry_status,entry_exactness,
                 monitor_state,monitor_started_at,last_observation_at,next_observation_at,
                 monitor_completed_at,evidence_status,provenance_digest,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("watchtower", mint, "PROSPECTIVE_MONITOR_COHORT", 1, "assignment",
             "FIRST_AVAILABLE", 100, 10.0, "QUALIFIED", "EXACT", state, 100,
             900, None if terminal else 960, 1000 if terminal else None,
             "QUALIFIED", "provenance", 1, 1),
        )


def _retry_envelope(mint, deadline):
    return {
        "operation_id": "watchtower", "mint": mint,
        "cohort": "PROSPECTIVE_MONITOR_COHORT", "entry_method": "FIRST_AVAILABLE",
        "entry_timestamp": 100, "entry_mc_usd": 10.0, "entry_exactness": "EXACT",
        "entry_provenance": "provenance", "entry_reference_state": "ENTRY_REFERENCE_QUALIFIED",
        # This is the historical overwrite shape: retry metadata is present,
        # but the projection has replaced the recoverable monitor state.
        "monitor_state": "ENTRY_REFERENCE_QUALIFIED", "candle_resolution": "15m",
        "next_eligible_dispatch_at": deadline, "empty_ohlcv_retry_count": 1,
    }


def _seed_retry(queue, envelope, *, message_id, error):
    queue.queue.enqueue(envelope, message_id=message_id)
    pending = queue.queue.root / "pending" / f"{message_id}.json"
    payload = json.loads(pending.read_text())
    payload.update({"last_error": error, "last_attempt_at": 1})
    queue.queue._replace_payload(pending, payload)
    os.replace(pending, queue.queue.root / "retry" / pending.name)


@pytest.mark.parametrize("mint,message_id", [("EFGK-test", "efgk"), ("J4t6-test", "j4t6")])
def test_elapsed_completed_boundary_retry_survives_projection_and_recovers_once(tmp_path, mint, message_id):
    database = tmp_path / "monitor.db"
    with sqlite3.connect(database) as connection:
        _ensure_schema(connection)
    _active_fact(database, mint)
    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    _seed_retry(queue, _retry_envelope(mint, 900), message_id=message_id,
                error="NO_COMPLETED_15M_BOUNDARY")

    reconcile_qualified_monitor_fact_queue_projection(str(database), queue, now=1000)
    retry_path = queue.queue.root / "retry" / f"{message_id}.json"
    assert json.loads(retry_path.read_text())["envelope"]["monitor_state"] == "NO_USABLE_CANDLE"
    assert queue.recover_due(now=1000) == 1
    assert not retry_path.exists()
    assert (queue.queue.root / "pending" / f"{message_id}.json").exists()

    # Repeated projection/recovery does not create another current identity.
    reconcile_qualified_monitor_fact_queue_projection(str(database), queue, now=1001)
    assert queue.recover_due(now=1001) == 0
    assert len(queue.current_fact_identities(operation_id="watchtower", mint=mint)) == 1


def test_future_retry_remains_deferred_after_projection(tmp_path):
    database = tmp_path / "monitor.db"
    with sqlite3.connect(database) as connection:
        _ensure_schema(connection)
    mint = "future-test"
    _active_fact(database, mint)
    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    _seed_retry(queue, _retry_envelope(mint, 1001), message_id="future",
                error="NO_COMPLETED_15M_BOUNDARY")

    reconcile_qualified_monitor_fact_queue_projection(str(database), queue, now=1000)
    assert queue.recover_due(now=1000) == 0
    assert (queue.queue.root / "retry" / "future.json").exists()
    assert not (queue.queue.root / "pending" / "future.json").exists()


def test_four_provider_free_service_scans_recover_elapsed_retry_once(tmp_path, monkeypatch):
    """The real scan ordering projects, recovers, then leaves one pending job."""
    database = tmp_path / "monitor.db"
    with sqlite3.connect(database) as connection:
        _ensure_schema(connection)
    mint = "service-retry-test"
    _active_fact(database, mint)
    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    _seed_retry(queue, _retry_envelope(mint, 1), message_id="service-retry",
                error="NO_COMPLETED_15M_BOUNDARY")
    # The recovery timer may requeue empty-OHLCV work while a shared provider
    # gate remains closed; this keeps all four scans transport-free.
    (queue.queue.root / "provider_backoff.json").write_text(
        json.dumps({"next_eligible_at": 9_999_999_999}), encoding="utf-8"
    )
    for name in (
        "reconcile_byzantine_assignment_admissions",
        "reconcile_watchtower_assignment_admissions",
        "reconcile_watchtower_deep_assignment_admissions",
    ):
        monkeypatch.setattr(service, name, lambda *_args, **_kwargs: {})
    worker = MonitorWorker(
        queue, db_path=str(database), persist=_persist(database),
        transport=lambda _request: (_ for _ in ()).throw(AssertionError("provider transport must not run")),
        opening_jobs_path=tmp_path / "opening.db", provider_work_path=tmp_path / "provider.db",
    )

    for _ in range(4):
        service.run_once(worker=worker, queue=queue, db_path=str(database))

    assert not (queue.queue.root / "retry" / "service-retry.json").exists()
    assert (queue.queue.root / "pending" / "service-retry.json").exists()
    assert len(queue.current_fact_identities(operation_id="watchtower", mint=mint)) == 1


def test_terminal_fact_retry_is_not_recovered_into_lifecycle_dispatch(tmp_path):
    database = tmp_path / "monitor.db"
    with sqlite3.connect(database) as connection:
        _ensure_schema(connection)
    mint = "terminal-test"
    _active_fact(database, mint, terminal=True)
    queue = MonitorQueue(tmp_path / "queue", enabled=True)
    _seed_retry(queue, _retry_envelope(mint, 900), message_id="terminal",
                error="NO_COMPLETED_15M_BOUNDARY")
    transport_calls = []
    worker = MonitorWorker(
        queue, db_path=str(database), persist=_persist(database),
        transport=lambda request: transport_calls.append(request),
        opening_jobs_path=tmp_path / "opening.db", provider_work_path=tmp_path / "provider.db",
    )

    reconcile_qualified_monitor_fact_queue_projection(str(database), queue, now=1000)
    # Terminal facts are outside the active projection, so their stale retry
    # never becomes pending lifecycle work in the first place.
    assert queue.recover_due(now=1000) == 0
    assert worker.process_once() == 0
    assert transport_calls == []
    assert len(queue.current_fact_identities(operation_id="watchtower", mint=mint)) == 1
