"""Compact provider-free safety fixtures; no canonical databases or HTTP server."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import socket
import sqlite3
import sys
import threading
import time
import pytest
from flask import Flask, jsonify
from src.ops import detail_resource_safety as safety

IDENTITY = next(iter(safety.DETAIL_IDS))

@pytest.fixture(autouse=True)
def boundary(monkeypatch):
    monkeypatch.setenv('FLEX_DEV_READ_ONLY_QUALIFICATION', '1')
    monkeypatch.setattr(safety, 'DEADLINE_SECONDS', .06)
    monkeypatch.setattr(safety, '_CAPACITY', threading.BoundedSemaphore(1))
    monkeypatch.setattr(safety, '_unavailable', lambda reason: (jsonify(ok=False, status='UNAVAILABLE', error=reason), 503))
    def denied(*args, **kwargs):
        raise AssertionError('Provider transport forbidden')
    monkeypatch.setattr(socket.socket, 'connect', denied)


def app_for(function):
    app = Flask(__name__)
    app.add_url_rule('/api/detail/<operator_id>', endpoint='detail', view_func=safety.bounded_detail(function))
    app.add_url_rule('/health', endpoint='health', view_func=lambda: 'responsive')
    return app


def slow_sql():
    c = sqlite3.connect(':memory:')
    c.execute('WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT sum(x) FROM n').fetchone()


def test_fast_success_and_other_identity_unchanged():
    def complete(operator_id):
        c = sqlite3.connect(':memory:')
        assert c.execute('SELECT 7').fetchone()[0] == 7
        return jsonify(full=['historical', 'evidence'], identity=operator_id)
    app = app_for(complete)
    with app.test_client() as client:
        assert client.get('/api/detail/'+IDENTITY).json['full'] == ['historical', 'evidence']
        assert client.get('/api/detail/watchtower').status_code == 200
    assert safety._ACTIVE.get() is None


@pytest.mark.parametrize('identity', sorted(safety.DETAIL_IDS))
def test_slow_sql_no_partial_fallback_and_closed_connections(identity):
    owned = []
    def detail(operator_id):
        c = sqlite3.connect(':memory:'); owned.append(c)
        try:
            c.execute('WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT sum(x) FROM n').fetchone()
        except sqlite3.Error:
            return jsonify(partial=True)  # Must never escape as successful detail.
        return 'unexpected'
    app = app_for(detail); started = time.monotonic()
    with app.test_client() as client:
        response = client.get('/api/detail/'+identity)
    assert response.status_code == 503 and response.json['status'] == 'UNAVAILABLE'
    assert 'partial' not in response.json
    assert time.monotonic()-started < .5
    for c in owned:
        with pytest.raises(sqlite3.ProgrammingError): c.execute('SELECT 1')
    assert safety._ACTIVE.get() is None


def test_python_processing_budget():
    # Execute loop in project source so the request trace observes projection work.
    namespace = {}
    exec(compile('def burn():\n while True:\n  value=1+1\n', str(Path(safety.__file__).with_name('fixture_projection.py')), 'exec'), namespace)
    app = app_for(lambda operator_id: namespace['burn']())
    with app.test_client() as client:
        assert client.get('/api/detail/'+IDENTITY).status_code == 503


def test_capacity_no_waiters_and_unrelated_health_responsive():
    entered = threading.Event(); release = threading.Event()
    def detail(operator_id):
        entered.set(); release.wait(.15)
        safety._ACTIVE.get().check()
        return 'complete'
    app = app_for(detail)
    def first():
        with app.test_client() as c: return c.get('/api/detail/'+IDENTITY).status_code
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first); assert entered.wait(.2)
        with app.test_client() as c:
            t = time.monotonic(); r = c.get('/api/detail/'+IDENTITY)
            assert r.status_code == 503 and 'capacity' in r.json['error'].lower()
            assert time.monotonic()-t < .05
            assert c.get('/health').data == b'responsive'
        release.set(); assert future.result(timeout=.5) in (200, 503)


def test_nexus_lock_wait_times_out_without_releasing_other_owner():
    lock = threading.Lock(); lock.acquire()
    def detail(operator_id):
        with safety.request_lock(lock): return 'unexpected'
    with app_for(detail).test_client() as c:
        assert c.get('/api/detail/'+IDENTITY).status_code == 503
    assert lock.locked(); lock.release()
    with safety.request_lock(lock): assert lock.locked()
    assert not lock.locked()


def test_lock_and_connections_release_after_timeout_then_success():
    lock = threading.Lock(); mode = ['slow']
    def detail(operator_id):
        with safety.request_lock(lock):
            if mode[0] == 'slow': slow_sql()
            return 'full evidence'
    with app_for(detail).test_client() as c:
        for _ in range(6): assert c.get('/api/detail/'+IDENTITY).status_code == 503
        assert not lock.locked(); mode[0] = 'fast'
        assert c.get('/api/detail/'+IDENTITY).data == b'full evidence'
    assert safety._ACTIVE.get() is None


def test_connection_limit_and_unrelated_connection_not_interrupted():
    unrelated = sqlite3.connect(':memory:')
    def detail(operator_id):
        for _ in range(safety.MAX_CONNECTIONS+1): sqlite3.connect(':memory:')
        return 'unexpected'
    with app_for(detail).test_client() as c:
        assert c.get('/api/detail/'+IDENTITY).status_code == 503
    assert unrelated.execute('SELECT 9').fetchone()[0] == 9; unrelated.close()


def test_disconnect_cleanup_without_retries_or_new_threads():
    # WSGI client stops consumption; SQL budget is independent of disconnect detection.
    before = {t.ident for t in threading.enumerate()}
    calls = []
    def detail(operator_id): calls.append(1); slow_sql(); return 'unexpected'
    app = app_for(detail)
    for _ in range(4):
        with app.test_client() as c:
            r = c.get('/api/detail/'+IDENTITY, buffered=False); r.close()
            assert r.status_code == 503
    assert len(calls) == 4
    assert {t.ident for t in threading.enumerate()} == before


def test_normal_runtime_passthrough(monkeypatch):
    monkeypatch.delenv('FLEX_DEV_READ_ONLY_QUALIFICATION')
    with app_for(lambda operator_id: 'original full detail').test_client() as c:
        assert c.get('/api/detail/'+IDENTITY).data == b'original full detail'


def test_busy_wait_is_limited_and_foreign_lock_preserved(tmp_path):
    database = tmp_path/'compact.db'
    blocker = sqlite3.connect(database)
    blocker.execute('CREATE TABLE evidence(value INTEGER)'); blocker.commit()
    blocker.execute('BEGIN EXCLUSIVE')
    def detail(operator_id):
        c = sqlite3.connect(database, timeout=10)
        return jsonify(rows=c.execute('SELECT * FROM evidence').fetchall())
    started = time.monotonic()
    with app_for(detail).test_client() as c:
        assert c.get('/api/detail/'+IDENTITY).status_code == 503
        assert time.monotonic()-started < .5
        assert blocker.in_transaction
        blocker.rollback()
        assert c.get('/api/detail/'+IDENTITY).status_code == 200
    blocker.close()


def test_real_sql_error_is_not_hidden_as_timeout():
    def detail(operator_id):
        c = sqlite3.connect(':memory:')
        c.execute('SELECT missing FROM missing_table')
        return 'unexpected'
    app = app_for(detail); app.testing = True
    with app.test_client() as c:
        with pytest.raises(sqlite3.OperationalError): c.get('/api/detail/'+IDENTITY)
    assert safety._ACTIVE.get() is None
