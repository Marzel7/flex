import builtins
import json
import sqlite3
import sys
import types

from src.ops.operation_monitor_worker import MonitorQueue, MonitorWorker


def _ensure_schema(connection):
    connection.executescript('''
    CREATE TABLE operation_monitor_facts (
      operation_id TEXT NOT NULL, mint TEXT NOT NULL, cohort_class TEXT NOT NULL,
      assignment_timestamp INTEGER, assignment_provenance TEXT, entry_method TEXT NOT NULL,
      entry_timestamp INTEGER, entry_mc_usd REAL, entry_native_mc_sol TEXT, entry_status TEXT,
      entry_exactness TEXT, latest_mc_usd REAL, latest_mc_timestamp INTEGER, current_multiple REAL,
      running_peak_mc_usd REAL, running_peak_timestamp INTEGER, running_peak_multiple REAL,
      drawdown_percent REAL, reached_2x INTEGER, reached_5x INTEGER, reached_10x INTEGER,
      first_2x_timestamp INTEGER, first_5x_timestamp INTEGER, first_10x_timestamp INTEGER,
      drawdown_25_timestamp INTEGER, drawdown_50_timestamp INTEGER, drawdown_75_timestamp INTEGER,
      drawdown_85_timestamp INTEGER, monitor_state TEXT NOT NULL, monitor_started_at INTEGER,
      last_observation_at INTEGER, next_observation_at INTEGER, monitor_completed_at INTEGER,
      provider_call_count INTEGER NOT NULL DEFAULT 0, candles_retained INTEGER NOT NULL DEFAULT 0,
      candle_resolution TEXT, evidence_status TEXT, provenance_digest TEXT NOT NULL,
      created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
      PRIMARY KEY(operation_id,mint));
    CREATE TABLE operation_monitor_observations (
      operation_id TEXT NOT NULL, mint TEXT NOT NULL, observation_timestamp INTEGER NOT NULL,
      mc_usd REAL NOT NULL, resolution TEXT NOT NULL, source TEXT NOT NULL, request_identity TEXT NOT NULL,
      provenance_digest TEXT NOT NULL, created_at INTEGER NOT NULL, open_mc_usd REAL, high_mc_usd REAL,
      low_mc_usd REAL, close_mc_usd REAL,
      PRIMARY KEY(operation_id,mint,observation_timestamp,resolution));
    ''')


def test_missing_birth_anchored_opening_capability_is_durable_and_visible(tmp_path, monkeypatch):
    worker = MonitorWorker(
        type('Queue', (), {})(), lambda _: None,
        opening_jobs_path=tmp_path / 'opening.db',
        provider_work_path=tmp_path / 'provider.db',
    )
    original = builtins.__import__
    monkeypatch.setitem(sys.modules, 'src.ops.generic_provider_work_scheduler', types.ModuleType('scheduler'))

    def blocked(name, *args, **kwargs):
        fromlist = kwargs.get('fromlist') or (args[2] if len(args) > 2 else ())
        if name == 'src.ops' and 'live_opening_action_job' in fromlist:
            raise ModuleNotFoundError(
                "No module named 'src.ops.birth_anchored_opening_acquisition'",
                name='src.ops.birth_anchored_opening_acquisition',
            )
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', blocked)
    result = worker.process_entry_reference_opening_once(now=7)

    assert result == {
        'state': 'STRICT_OPENING_OPTIONAL_CAPABILITY_UNAVAILABLE',
        'reason': 'BIRTH_ANCHORED_OPENING_ACQUISITION_UNAVAILABLE',
        'module': 'src.ops.birth_anchored_opening_acquisition',
        'recorded_at': 7,
    }
    assert json.loads((tmp_path / 'opening_capability_unavailable.json').read_text()) == result


def _committing_writer(path):
    def persist(item):
        connection = sqlite3.connect(path)
        try:
            for statement, parameters in item.statements:
                connection.execute(statement, parameters)
            connection.commit()
        finally:
            connection.close()
        return types.SimpleNamespace(committed=True, commit_timestamp=1, error=None)
    return persist


def test_qualified_live_dispatch_continues_after_optional_opening_failure(tmp_path, monkeypatch):
    db = tmp_path / 'monitor.db'
    connection = sqlite3.connect(db); _ensure_schema(connection); connection.commit(); connection.close()
    queue = MonitorQueue(tmp_path / 'queue', enabled=True)
    queue.queue.enqueue({
        'operation_id': 'watchtower', 'mint': 'agency', 'cohort': 'PROSPECTIVE_MONITOR_COHORT',
        'entry_method': 'frozen', 'entry_timestamp': 1, 'entry_mc_usd': 100.0,
        'entry_reference_state': 'ENTRY_REFERENCE_QUALIFIED', 'monitor_state': 'ENTRY_REFERENCE_QUALIFIED',
        'candle_resolution': '15m', 'assignment': {'assigned_at': 1},
    })
    calls = []
    worker = MonitorWorker(
        queue,
        transport=lambda envelope: calls.append(envelope['mint']) or {
            'candles': [{'timestamp': 900, 'mc': 90.0, 'open': 100.0, 'high': 120.0, 'low': 80.0}],
            'resolution': '15m', 'request': {},
        },
        persist=_committing_writer(db), db_path=str(db),
        opening_jobs_path=tmp_path / 'opening.db', provider_work_path=tmp_path / 'provider.db',
    )
    original = builtins.__import__
    monkeypatch.setitem(sys.modules, 'src.ops.generic_provider_work_scheduler', types.ModuleType('scheduler'))

    def blocked(name, *args, **kwargs):
        fromlist = kwargs.get('fromlist') or (args[2] if len(args) > 2 else ())
        if name == 'src.ops' and 'live_opening_action_job' in fromlist:
            raise ModuleNotFoundError('missing', name='src.ops.birth_anchored_opening_acquisition')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', blocked)
    assert worker.process_entry_reference_opening_once(now=7)['state'] == 'STRICT_OPENING_OPTIONAL_CAPABILITY_UNAVAILABLE'
    monkeypatch.setattr(builtins, '__import__', original)
    assert worker.process_once() == 1
    assert calls == ['agency']
    assert queue.current_fact_identities(operation_id='watchtower', mint='agency')


def test_non_live_entry_never_reaches_transport(tmp_path):
    queue = MonitorQueue(tmp_path / 'queue', enabled=True)
    queue.queue.enqueue({
        'operation_id': 'watchtower', 'mint': 'failed-opening', 'cohort': 'PROSPECTIVE_MONITOR_COHORT',
        'entry_method': 'frozen', 'entry_reference_state': 'WAITING_FOR_ENTRY_REFERENCE',
        'monitor_state': 'WAITING_FOR_ENTRY_REFERENCE', 'candle_resolution': '15m',
        'assignment': {'assigned_at': 1}, 'next_entry_evaluation_at': 9999999999,
    })
    calls = []
    worker = MonitorWorker(queue, transport=lambda _: calls.append('called'), db_path=str(tmp_path / 'unused.db'))
    assert worker.process_once() == 1
    assert calls == []
