from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import pathlib
import sqlite3
import threading
import pytest
from flask import Flask
from src.ops import inbox, inbox_routes as routes
from src.utils import db_locking as locking

ROOT = pathlib.Path(__file__).resolve().parents[1]


def item(key='fixture', operation='watchtower'):
    return inbox.InboxItem(operation_id=operation, subject_type=inbox.SUBJECT_LIFECYCLE,
        priority=inbox.HIGH, headline='Fixture', summary='Retained fixture',
        reason='Offline', recommended_action='Review', dedup_key=key)

@pytest.fixture
def authority(tmp_path, monkeypatch):
    import src.core.db as db
    path = tmp_path / 'inbox.db'
    writer = inbox.InboxStore(str(path))
    writer.upsert(item())
    monkeypatch.setattr(db, 'OPS_DB_PATH', path)
    monkeypatch.setattr(routes, '_store', None)
    monkeypatch.setattr(routes, '_read_store', None)
    app = Flask(__name__)
    app.register_blueprint(routes.inbox_bp)
    app.config['TESTING'] = True
    return path, writer, app


def forbid_writer(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('Read-only GET requested shared writer')
    monkeypatch.setattr(locking.TrackedConnection, '_acquire_write_lane', fail)

@pytest.mark.parametrize('url', ['/api/ops/inbox/summary', '/api/ops/inbox', '/api/ops/inbox/watchtower'])
def test_first_and_repeated_get_no_ddl_lock_or_byte_change(authority, monkeypatch, url):
    path, writer, app = authority
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    forbid_writer(monkeypatch)
    monkeypatch.setattr(inbox, 'ensure_inbox_schema', lambda *a: pytest.fail('GET submitted schema DDL'))
    trace = []
    connect = inbox.InboxStore._connect
    def inspect(self):
        conn = connect(self)
        if self._read_only:
            conn.set_trace_callback(trace.append)
        return conn
    monkeypatch.setattr(inbox.InboxStore, '_connect', inspect)
    with app.test_client() as client:
        for _ in range(3):
            response = client.get(url)
            assert response.status_code == 200
            assert response.get_json()['ok'] is True
    assert routes._read_store._read_only is True
    assert routes._store is None
    assert not any(s.lstrip().upper().startswith(('CREATE', 'ALTER', 'INSERT', 'UPDATE', 'DELETE')) for s in trace)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not list(path.parent.glob('*.write.lock*'))


def test_readonly_connection_and_summary_equivalence(authority, monkeypatch):
    path, writer, app = authority
    expected = writer.fetch_summary()
    forbid_writer(monkeypatch)
    reader = inbox.InboxStore(str(path), read_only=True)
    assert reader.fetch_summary() == expected
    conn = reader._connect()
    assert conn.execute('PRAGMA query_only').fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("delete from analyst_inbox")
    conn.close()
    with app.test_client() as client:
        actual = client.get('/api/ops/inbox/summary').get_json()
    assert {k: actual[k] for k in expected} == expected

@pytest.mark.parametrize('kind', ['missing_file', 'missing_schema', 'missing_columns'])
@pytest.mark.parametrize('url', ['/api/ops/inbox/summary', '/api/ops/inbox', '/api/ops/inbox/watchtower'])
def test_missing_evidence_is_503_without_fallback(tmp_path, monkeypatch, kind, url):
    import src.core.db as db
    path = tmp_path / 'unavailable.db'
    if kind != 'missing_file':
        conn = sqlite3.connect(path)
        if kind == 'missing_columns':
            conn.execute('create table analyst_inbox(item_id text)')
        conn.commit()
        conn.close()
    monkeypatch.setattr(db, 'OPS_DB_PATH', path)
    monkeypatch.setattr(routes, '_store', None)
    monkeypatch.setattr(routes, '_read_store', None)
    forbid_writer(monkeypatch)
    monkeypatch.setattr(inbox, 'ensure_inbox_schema', lambda *a: pytest.fail('read-only fallback to DDL'))
    app = Flask(__name__)
    app.register_blueprint(routes.inbox_bp)
    with app.test_client() as client:
        result = client.get(url)
    assert result.status_code == 503
    payload = result.get_json()
    assert payload['ok'] is False and 'unavailable' in payload['error']
    assert not any(k in payload for k in ['count', 'total_active', 'items'])
    assert routes._store is None and routes._read_store is None
    assert not list(tmp_path.glob('*.write.lock*'))
    if kind == 'missing_file':
        assert not path.exists()

@pytest.mark.parametrize('method', ['upsert', 'set_status', 'expire_resolved'])
def test_readonly_mutations_rejected_before_connection(authority, monkeypatch, method):
    path, _, _ = authority
    store = inbox.InboxStore(str(path), read_only=True)
    monkeypatch.setattr(store, '_connect', lambda: pytest.fail('read-only mutation opened connection'))
    with pytest.raises(PermissionError, match='read-only'):
        if method == 'upsert': store.upsert(item())
        elif method == 'set_status': store.set_status('x', inbox.ACKNOWLEDGED)
        else: store.expire_resolved()


def test_explicit_post_routes_keep_real_writer_and_reader_separate(authority, monkeypatch):
    path, writer, app = authority
    existing = writer.fetch_active()[0]
    seen = []
    def refresh(store):
        seen.append(store._read_only)
        store.upsert(item('second'))
        return 1
    monkeypatch.setattr(routes, 'refresh_inbox', refresh)
    with app.test_client() as client:
        assert client.get('/api/ops/inbox/summary').get_json()['total_active'] == 1
        assert routes._store is None
        response = client.post('/api/ops/inbox/' + existing['item_id'] + '/status', json={'status': inbox.ACKNOWLEDGED})
        assert response.get_json()['ok'] is True
        assert routes._store._read_only is False
        assert routes._read_store is not routes._store
        assert client.post('/api/ops/inbox/refresh').get_json()['items_written'] == 1
        assert client.get('/api/ops/inbox/summary').get_json()['total_active'] == 2
    assert seen == [False]
    assert writer.fetch_summary()['acknowledged'] == 1


def test_concurrent_first_get_has_no_writer_or_schema_side_effect(authority, monkeypatch):
    path, _, app = authority
    forbid_writer(monkeypatch)
    monkeypatch.setattr(inbox, 'ensure_inbox_schema', lambda *a: pytest.fail('GET DDL'))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    def request(_):
        with app.test_client() as client:
            response = client.get('/api/ops/inbox/summary')
            return response.status_code, response.get_json()['total_active']
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(request, range(16))) == [(200, 1)] * 16
    assert routes._store is None
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not list(path.parent.glob('*.write.lock*'))


def test_readonly_request_does_not_start_background_thread(authority, monkeypatch):
    _, _, app = authority
    monkeypatch.setattr(threading.Thread, 'start', lambda *a: pytest.fail('GET started background worker'))
    forbid_writer(monkeypatch)
    with app.test_client() as client:
        assert client.get('/api/ops/inbox/summary').status_code == 200


def test_current_production_writer_initialization_and_commit(tmp_path, monkeypatch):
    path = tmp_path / 'writer.db'
    connect = inbox.InboxStore._connect
    def current(self):
        if self._read_only:
            return connect(self)
        return locking.db_connect(str(path), row_factory=sqlite3.Row, _caller='dev084.production-contract-fixture')
    monkeypatch.setattr(inbox.InboxStore, '_connect', current)
    writer = inbox.InboxStore(str(path))
    writer.upsert(item())
    assert writer.fetch_summary()['total_active'] == 1
    conn = connect(writer)
    assert conn.execute("select count(*) from sqlite_master where type='index' and name like 'idx_inbox_%'").fetchone()[0] == 4
    conn.close()
    assert path.stat().st_size < 8388608
    assert path.with_name(path.name + '.write.lock').exists()


def test_writer_dependencies_and_application_preimage_authority():
    proof = json.loads((ROOT / 'docs/audits/dev084/source-authority.json').read_text())
    for row in proof['files']:
        assert hashlib.sha256((ROOT / row['path']).read_bytes()).hexdigest() == row['candidate_sha256']
        if 'inbox' not in row['path']:
            assert row['candidate_sha256'] == row['preimage_sha256']


def test_readonly_context_closes_native_connection(authority):
    path, _, _ = authority
    reader = inbox.InboxStore(str(path), read_only=True)
    with reader._connect() as conn:
        assert conn.execute('select count(*) from analyst_inbox').fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        conn.execute('select 1')
