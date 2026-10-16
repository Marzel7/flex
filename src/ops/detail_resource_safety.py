"""DEV043 request-owned detail budgets; no query or evidence projection changes."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time

DETAIL_IDS = frozenset({'d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334', 'bd7d7479-1454-5d41-9f68-115550348f3e'})
DEADLINE_SECONDS = 3.0
MAX_CONNECTIONS = 4
_ACTIVE = ContextVar('dev043_detail_budget', default=None)
_CAPACITY = threading.BoundedSemaphore(1)
_CONNECT_INSTALLED = False
_PROJECT = str(Path(__file__).resolve().parents[2] / 'src') + os.sep

class DetailUnavailable(BaseException):
    # Bypass legacy broad Exception/SQLite catches that return partial projections.
    pass

class Budget:
    def __init__(self):
        self.end = time.monotonic() + DEADLINE_SECONDS
        self.connections = []
        self.expired = False

    def check(self):
        if self.expired or time.monotonic() >= self.end:
            self.expired = True
            raise DetailUnavailable('Request exceeded 3 seconds')

    def progress(self):
        self.expired = self.expired or time.monotonic() >= self.end
        return int(self.expired)

    def trace(self, frame, event, arg):
        if event == 'line' and (frame.f_code.co_filename.startswith(_PROJECT) or frame.f_code.co_filename == '<template>') and frame.f_code.co_filename != __file__:
            self.check()
        return self.trace

    def close(self):
        for connection in self.connections:
            # Connections can already be closed by their owning helper.
            connection.close()
        self.connections.clear()


def _install_connection_budget():
    global _CONNECT_INSTALLED
    if _CONNECT_INSTALLED:
        return
    original = sqlite3.connect  # Preserve the existing isolation guard underneath.
    @wraps(original)
    def connect(database, *args, **kwargs):
        budget = _ACTIVE.get()
        if budget is None:
            return original(database, *args, **kwargs)
        budget.check()
        if len(budget.connections) >= MAX_CONNECTIONS:
            raise DetailUnavailable('Request connection capacity exhausted')
        remaining = max(0.0, budget.end - time.monotonic())
        if args:
            args = (min(float(args[0]), remaining), *args[1:])
        else:
            kwargs['timeout'] = min(float(kwargs.get('timeout', 5.0)), remaining)
        connection = original(database, *args, **kwargs)
        budget.connections.append(connection)
        connection.set_progress_handler(budget.progress, 1000)
        budget.check()
        return connection
    sqlite3.connect = connect
    _CONNECT_INSTALLED = True


@contextmanager
def request_lock(lock):
    """Keep existing lock behavior except for an active detail deadline."""
    budget = _ACTIVE.get()
    if budget is None:
        with lock:
            yield
        return
    budget.check()
    acquired = lock.acquire(timeout=max(0.0, budget.end - time.monotonic()))
    if not acquired:
        raise DetailUnavailable('Request exceeded 3 seconds waiting for activity evidence')
    try:
        budget.check()
        yield
    finally:
        lock.release()


def _unavailable(reason):
    from flask import jsonify, render_template, request
    if request.path.startswith('/api/'):
        return jsonify(ok=False, status='UNAVAILABLE', error=reason), 503
    return render_template('operation_detail_unavailable.html', reason=reason, active_page='operators'), 503


def bounded_detail(function):
    @wraps(function)
    def wrapped(operator_id, *args, **kwargs):
        if operator_id not in DETAIL_IDS or os.environ.get('FLEX_DEV_READ_ONLY_QUALIFICATION') != '1':
            return function(operator_id, *args, **kwargs)
        if not _CAPACITY.acquire(blocking=False):
            return _unavailable('Detail capacity exhausted; another detail request is active')
        budget = Budget()
        token = _ACTIVE.set(budget)
        prior_trace = sys.gettrace()
        try:
            _install_connection_budget()
            sys.settrace(budget.trace)
            try:
                response = function(operator_id, *args, **kwargs)
                budget.check()  # Reject legacy fallback after an interrupted SQLite call.
            except sqlite3.Error:
                sys.settrace(prior_trace)
                if budget.expired or time.monotonic() >= budget.end:
                    return _unavailable('Request exceeded 3 seconds')
                raise
            except DetailUnavailable as exc:
                sys.settrace(prior_trace)
                return _unavailable(str(exc))
            return response
        finally:
            sys.settrace(prior_trace)
            try:
                budget.close()
            finally:
                _ACTIVE.reset(token)
                _CAPACITY.release()
    return wrapped
