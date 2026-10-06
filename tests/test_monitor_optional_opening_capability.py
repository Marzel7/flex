import builtins
import json
import sqlite3
import sys
import types

import pytest

from src.ops import operation_monitor_worker as monitor_worker
from src.ops.operation_monitor_worker import MonitorQueue, MonitorWorker, StrictEntryNormalizationError


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


def test_missing_optional_opening_wrapper_is_durable_and_visible(tmp_path):
    worker = MonitorWorker(
        type('Queue', (), {})(), lambda _: None,
        opening_jobs_path=tmp_path / 'opening.db',
        provider_work_path=tmp_path / 'provider.db',
    )
    result = worker.process_entry_reference_opening_once(now=8)
    assert result == {
        'state': 'STRICT_OPENING_OPTIONAL_CAPABILITY_UNAVAILABLE',
        'reason': 'BIRTH_ANCHORED_OPENING_ACQUISITION_UNAVAILABLE',
        'module': 'src.ops.birth_anchored_opening_acquisition',
        'recorded_at': 8,
    }
    assert json.loads((tmp_path / 'opening_capability_unavailable.json').read_text()) == result


def test_unrelated_optional_opening_import_errors_are_not_swallowed(tmp_path, monkeypatch):
    worker = MonitorWorker(
        type('Queue', (), {})(), lambda _: None,
        opening_jobs_path=tmp_path / 'opening.db',
        provider_work_path=tmp_path / 'provider.db',
    )
    original = builtins.__import__

    def fail_unrelated(name, *args, **kwargs):
        fromlist = kwargs.get('fromlist') or (args[2] if len(args) > 2 else ())
        if name == 'src.ops' and 'live_opening_action_job' in fromlist:
            raise ImportError("cannot import name 'unrelated_symbol' from 'src.ops'", name='src.ops')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', fail_unrelated)
    with pytest.raises(ImportError):
        worker.process_entry_reference_opening_once(now=9)


def test_inner_dependency_and_generic_import_errors_are_not_swallowed(tmp_path, monkeypatch):
    worker = MonitorWorker(
        type('Queue', (), {})(), lambda _: None,
        opening_jobs_path=tmp_path / 'opening.db',
        provider_work_path=tmp_path / 'provider.db',
    )
    original = builtins.__import__

    def fail_inner(name, *args, **kwargs):
        fromlist = kwargs.get('fromlist') or (args[2] if len(args) > 2 else ())
        if name == 'src.ops' and 'live_opening_action_job' in fromlist:
            raise ImportError("cannot import name 'birth_dependency' from 'src.ops'", name='src.ops')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', fail_inner)
    with pytest.raises(ImportError):
        worker.process_entry_reference_opening_once(now=10)
    monkeypatch.setattr(builtins, '__import__', lambda *args, **kwargs: (_ for _ in ()).throw(ImportError('generic import failure')))
    with pytest.raises(ImportError):
        worker.process_entry_reference_opening_once(now=11)


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
    worker.process_once()
    assert calls == ['agency']
    assert queue.current_fact_identities(operation_id='watchtower', mint='agency')


@pytest.mark.parametrize(('mint', 'candle_count'), [('5TY-provider-free', 112), ('8N1-provider-free', 112), ('sixty-candle-control', 60)])
def test_valid_lifecycle_candles_are_not_rejected_by_normalized_response_size(tmp_path, mint, candle_count):
    """Valid normalized lifecycle evidence is governed by durable storage, not a local 8 KiB JSON gate."""
    db = tmp_path / 'monitor.db'
    connection = sqlite3.connect(db); _ensure_schema(connection); connection.commit(); connection.close()
    queue = MonitorQueue(tmp_path / 'queue', enabled=True)
    queue.queue.enqueue({
        'operation_id': 'watchtower', 'mint': mint, 'cohort': 'PROSPECTIVE_MONITOR_COHORT',
        'entry_method': 'frozen', 'entry_timestamp': 1, 'entry_mc_usd': 10.0,
        'entry_reference_state': 'ENTRY_REFERENCE_QUALIFIED', 'monitor_state': 'ENTRY_REFERENCE_QUALIFIED',
        'candle_resolution': '15m', 'assignment': {'assigned_at': 1},
    })
    candles = [
        {'timestamp': 900 * (index + 1), 'mc': 10.0, 'open': 10.0, 'high': 12.0, 'low': 9.0}
        for index in range(candle_count)
    ]
    assert (len(json.dumps(candles)) > 8192) is (candle_count > 60)
    worker = MonitorWorker(
        queue, transport=lambda _envelope: {'candles': candles, 'resolution': '15m', 'request': {}},
        persist=_committing_writer(db), db_path=str(db),
        opening_jobs_path=tmp_path / 'opening.db', provider_work_path=tmp_path / 'provider.db',
    )
    assert worker.process_once() == 1
    with sqlite3.connect(db) as connection:
        fact = connection.execute(
            'SELECT provider_call_count, candles_retained FROM operation_monitor_facts WHERE operation_id=? AND mint=?',
            ('watchtower', mint),
        ).fetchone()
        observations = connection.execute(
            'SELECT COUNT(*) FROM operation_monitor_observations WHERE operation_id=? AND mint=?',
            ('watchtower', mint),
        ).fetchone()[0]
    assert fact == (1, candle_count)
    assert observations == candle_count


def test_terminal_transition_semantics_remain_entry_and_peak_based(tmp_path):
    db = tmp_path / 'monitor.db'
    connection = sqlite3.connect(db); _ensure_schema(connection); connection.commit(); connection.close()
    queue = MonitorQueue(tmp_path / 'queue', enabled=True)
    queue.queue.enqueue({
        'operation_id': 'watchtower', 'mint': 'terminal-control', 'cohort': 'PROSPECTIVE_MONITOR_COHORT',
        'entry_method': 'frozen', 'entry_timestamp': 1, 'entry_mc_usd': 10.0,
        'entry_reference_state': 'ENTRY_REFERENCE_QUALIFIED', 'monitor_state': 'ENTRY_REFERENCE_QUALIFIED',
        'candle_resolution': '15m', 'assignment': {'assigned_at': 1},
    })
    worker = MonitorWorker(
        queue,
        transport=lambda _envelope: {'candles': [
            {'timestamp': 900, 'mc': 100.0, 'open': 10.0, 'high': 100.0, 'low': 10.0},
            {'timestamp': 1800, 'mc': 10.0, 'open': 100.0, 'high': 100.0, 'low': 10.0},
        ], 'resolution': '15m', 'request': {}},
        persist=_committing_writer(db), db_path=str(db),
        opening_jobs_path=tmp_path / 'opening.db', provider_work_path=tmp_path / 'provider.db',
    )
    assert worker.process_once() == 1
    with sqlite3.connect(db) as connection:
        fact = connection.execute(
            'SELECT monitor_state, running_peak_mc_usd, latest_mc_usd FROM operation_monitor_facts WHERE mint=?',
            ('terminal-control',),
        ).fetchone()
    assert fact == ('PRICE_MONITOR_COMPLETE_COLLAPSED', 100.0, 10.0)


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
    worker.process_once()
    assert calls == []


@pytest.mark.parametrize('category', ['TARGET_SECOND_ABSENT', 'NO_PROVIDER_ITEMS'])
def test_legacy_watchtower_strict_miss_is_not_sealed_before_second_chance(tmp_path, category):
    queue = MonitorQueue(tmp_path / 'queue', enabled=True)
    message_id = f'legacy-{category.lower()}'
    queue.queue.enqueue({
        'operation_id': 'watchtower', 'mint': message_id,
        'monitor_state': 'WAITING_FOR_ENTRY_REFERENCE',
        'entry_reference_state': 'WAITING_FOR_ENTRY_REFERENCE',
        'strict_opening_failure_diagnostic': {'failure_category': category, 'http_status': 200},
        'opening_failure_reason': category,
        'next_entry_evaluation_at': 1,
    }, message_id=message_id)
    calls = []
    worker = MonitorWorker(queue, transport=lambda _: calls.append('provider'), db_path=str(tmp_path / 'unused.db'))

    assert worker.reconcile_exhausted_watchtower_strict_openings()['sealed'] == 0
    assert calls == []
    assert (tmp_path / 'queue' / 'pending' / f'{message_id}.json').exists()


def test_fresh_watchtower_strict_miss_gets_exactly_two_durable_attempts_then_stops(tmp_path, monkeypatch):
    db = tmp_path / 'monitor.db'
    connection = sqlite3.connect(db); _ensure_schema(connection); connection.commit(); connection.close()
    queue = MonitorQueue(tmp_path / 'queue', enabled=True)
    queue.admit_global_opening = lambda *_args, **_kwargs: {'admitted': True}
    queue.queue.enqueue({
        'operation_id': 'watchtower', 'mint': 'fresh-miss', 'cohort': 'PROSPECTIVE_MONITOR_COHORT',
        'entry_method': 'STRICT_MIGRATION_WINDOW', 'entry_reference_state': 'WAITING_FOR_ENTRY_REFERENCE',
        'monitor_state': 'WAITING_FOR_ENTRY_REFERENCE', 'candle_resolution': '15m',
        'assignment': {'assigned_at': 1}, 'history_start_timestamp': 1,
    }, message_id='fresh-miss')
    monkeypatch.setattr(monitor_worker, '_entry_inventory', lambda *_: {
        'result': 'ENTRY_EVIDENCE_ACQUISITION_DUE', 'entry_acquisition': {'migration_timestamp': 10},
    })
    calls = []
    def strict_miss(**kwargs):
        calls.append(kwargs['time_to_offset'])
        raise StrictEntryNormalizationError('TARGET_SECOND_ABSENT', {'failure_category': 'TARGET_SECOND_ABSENT', 'http_status': 200})
    monkeypatch.setattr(monitor_worker, 'dispatch_strict_migration_window', strict_miss)
    worker = MonitorWorker(queue, transport=lambda _: (_ for _ in ()).throw(AssertionError('NO_LIFECYCLE_PROVIDER_CALL')),
                           persist=_committing_writer(db), db_path=str(db))

    worker.process_once()
    assert calls == [2]
    pending = tmp_path / 'queue' / 'pending' / 'fresh-miss.json'
    payload = json.loads(pending.read_text())
    assert payload['envelope']['strict_opening_provider_attempt_count'] == 1
    assert payload['envelope']['strict_opening_retry_state'] == 'SECOND_CHANCE_PENDING'
    payload['envelope']['next_entry_evaluation_at'] = 0
    pending.write_text(json.dumps(payload))
    worker.process_once()
    assert calls == [2, 4]
    payload = json.loads((tmp_path / 'queue' / 'dead_letter' / 'fresh-miss.json').read_text())
    assert payload['envelope']['strict_opening_provider_attempt_count'] == 2
    worker.process_once()
    assert calls == [2, 4]


def test_fresh_watchtower_without_prior_miss_is_not_sealed(tmp_path):
    queue = MonitorQueue(tmp_path / 'queue', enabled=True)
    queue.queue.enqueue({
        'operation_id': 'watchtower', 'mint': 'fresh-opening',
        'monitor_state': 'WAITING_FOR_ENTRY_REFERENCE',
        'entry_reference_state': 'WAITING_FOR_ENTRY_REFERENCE',
    }, message_id='fresh-opening')
    worker = MonitorWorker(queue, transport=lambda _: None, db_path=str(tmp_path / 'unused.db'))
    assert worker.reconcile_exhausted_watchtower_strict_openings()['sealed'] == 0
    assert (tmp_path / 'queue' / 'pending' / 'fresh-opening.json').exists()


def test_byzantine_provider_failure_is_durably_bounded_without_a_successor(tmp_path, monkeypatch):
    monkeypatch.delenv('DEV005_OPENING_ONLY_FIXTURE', raising=False)
    db = tmp_path / 'monitor.db'
    connection = sqlite3.connect(db)
    _ensure_schema(connection)
    connection.commit()
    connection.close()
    queue = MonitorQueue(tmp_path / 'queue', enabled=True)
    queue.soak_allows = lambda _envelope: True
    queue.queue.enqueue({
        'operation_id': 'byzantine', 'mint': 'byzantine-provider-failure',
        'cohort': 'PROSPECTIVE_MONITOR_COHORT', 'entry_method': 'SCENARIO_D',
        'entry_timestamp': 1, 'entry_native_mc_sol': '1.0',
        'entry_reference_state': 'NATIVE_QUALIFIED', 'monitor_state': 'NATIVE_QUALIFIED',
        'candle_resolution': '15m', 'assignment': {'assigned_at': 1},
    }, message_id='byzantine-provider-failure')
    calls = []

    def provider(_envelope):
        calls.append('provider')
        raise ConnectionError('HTTP_400:Compute units usage limit exceeded')

    worker = MonitorWorker(queue, transport=provider, persist=_committing_writer(db), db_path=str(db))
    assert worker.process_once() == 1
    assert calls == ['provider']
    payload = json.loads((tmp_path / 'queue' / 'dead_letter' / 'byzantine-provider-failure.json').read_text())
    envelope = payload['envelope']
    assert envelope['byzantine_provider_failure_attempt_count'] == 1
    assert envelope['byzantine_provider_failure_attempt_budget'] == 1
    assert envelope['byzantine_provider_retry_state'] == 'BUDGET_EXHAUSTED'
    assert envelope['next_eligible_dispatch_at'] is None

    restarted = MonitorWorker(queue, transport=provider, persist=_committing_writer(db), db_path=str(db))
    assert restarted.reconcile_exhausted_byzantine_provider_failures()['sealed'] == 0
    assert queue.queue.enqueue({'operation_id': 'byzantine', 'mint': 'byzantine-provider-failure'}, message_id='byzantine-provider-failure') == 'byzantine-provider-failure'
    restarted.process_once()
    assert calls == ['provider']


def test_byzantine_bounded_provider_failure_blocks_active_fact_reconstruction(tmp_path):
    db = tmp_path / 'monitor.db'
    connection = sqlite3.connect(db)
    _ensure_schema(connection)
    connection.execute(
        "INSERT INTO operation_monitor_facts(operation_id,mint,cohort_class,entry_method,entry_timestamp,entry_native_mc_sol,entry_status,entry_exactness,monitor_state,provenance_digest,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ('byzantine', 'bounded-reconstruction', 'PROSPECTIVE_MONITOR_COHORT', 'SCENARIO_D', 1, '1.0', 'QUALIFIED', 'FIXTURE', 'MONITORING_ACTIVE', 'fixture', 1, 1),
    )
    connection.commit()
    connection.close()
    queue = MonitorQueue(tmp_path / 'queue', enabled=True)
    queue.queue.enqueue({
        'operation_id': 'byzantine', 'mint': 'bounded-reconstruction',
        'byzantine_provider_failure_attempt_count': 1,
        'byzantine_provider_failure_attempt_budget': 1,
        'byzantine_provider_retry_state': 'BUDGET_EXHAUSTED',
    }, message_id='bounded-reconstruction')
    pending = tmp_path / 'queue' / 'pending' / 'bounded-reconstruction.json'
    dead = tmp_path / 'queue' / 'dead_letter' / pending.name
    pending.replace(dead)

    result = monitor_worker.reconcile_qualified_monitor_fact_queue_projection(str(db), queue, now=10)
    assert result['reconstructed'] == 0
    assert result['refused'] == 1
    assert dead.exists()
    assert not list((tmp_path / 'queue' / 'pending').glob('*.json'))


def test_fresh_qualified_strict_opening_still_activates_live_monitor(tmp_path, monkeypatch):
    db = tmp_path / 'monitor.db'
    connection = sqlite3.connect(db); _ensure_schema(connection); connection.commit(); connection.close()
    queue = MonitorQueue(tmp_path / 'queue', enabled=True)
    queue.queue.enqueue({
        'operation_id': 'watchtower', 'mint': 'fresh-qualified', 'cohort': 'PROSPECTIVE_MONITOR_COHORT',
        'entry_method': 'STRICT_MIGRATION_WINDOW', 'entry_reference_state': 'WAITING_FOR_ENTRY_REFERENCE',
        'monitor_state': 'WAITING_FOR_ENTRY_REFERENCE', 'candle_resolution': '15m',
        'assignment': {'assigned_at': 1}, 'history_start_timestamp': 1,
    }, message_id='fresh-qualified')
    monkeypatch.setattr(monitor_worker, '_entry_inventory', lambda *_: {
        'result': 'ENTRY_EVIDENCE_ACQUISITION_DUE', 'entry_acquisition': {'migration_timestamp': 10},
    })
    monkeypatch.setattr(monitor_worker, 'dispatch_strict_migration_window', lambda **_kwargs: {
        'entry': {'timestamp': 11, 'mc': 42.0},
        'manifest': {'request_parameters': {'type': '1s'}},
        'request': {'request_id': 'fresh-qualified-request'},
    })
    monkeypatch.setenv('MONITOR_RUNTIME', 'dev')
    monkeypatch.setenv('DEV005_OPENING_ONLY_FIXTURE', '1')
    worker = MonitorWorker(queue, transport=lambda _: (_ for _ in ()).throw(AssertionError('NO_LIFECYCLE_PROVIDER_CALL')),
                           persist=_committing_writer(db), db_path=str(db))

    worker.process_once()
    with sqlite3.connect(db) as connection:
        fact = connection.execute(
            'SELECT entry_status, entry_timestamp, entry_mc_usd, monitor_state FROM operation_monitor_facts WHERE mint=?',
            ('fresh-qualified',),
        ).fetchone()
    assert fact == ('QUALIFIED', 11, 42.0, 'MONITORING_ACTIVE')


def test_service_continues_to_lifecycle_dispatch_after_optional_opening_unavailable(monkeypatch):
    from src.ops import operation_monitor_service as service

    class Queue:
        def recover_due(self):
            return None

    class Worker:
        def __init__(self):
            self.dispatched = 0
        def reconcile_retained_watchtower_facts(self):
            return None
        def reconcile_terminal_ath_jobs(self):
            return None
        def reconcile_stale_watchtower_pending_openings(self):
            return None
        def reconcile_exhausted_watchtower_strict_openings(self):
            return None
        def reconcile_exhausted_byzantine_provider_failures(self):
            return None
        def process_entry_reference_opening_once(self):
            return {'state': 'STRICT_OPENING_OPTIONAL_CAPABILITY_UNAVAILABLE'}
        def process_once(self):
            self.dispatched += 1
            return 1

    for name in ('reconcile_byzantine_assignment_admissions', 'reconcile_watchtower_assignment_admissions',
                 'reconcile_watchtower_deep_assignment_admissions', 'reconcile_qualified_monitor_fact_queue_projection'):
        monkeypatch.setattr(service, name, lambda *_: None)
    worker = Worker()
    for _ in range(4):
        service.run_once(worker=worker, queue=Queue(), db_path='unused')
    assert worker.dispatched == 4
