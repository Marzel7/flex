from contextlib import contextmanager


class _Response:
    status_code = 200

    def json(self):
        return {"symbol": "TEST", "name": "Test Token"}


class _Connection:
    def __init__(self, *, fail_on_write=False):
        self.fail_on_write = fail_on_write
        self.closed = False

    def execute(self, sql, params=()):
        if sql.lstrip().upper().startswith("SELECT"):
            return self
        if self.fail_on_write:
            raise RuntimeError("simulated write failure after lease acquisition")
        return self

    def fetchone(self):
        return None

    def commit(self):
        return None

    def close(self):
        self.closed = True


def test_symbol_fetch_releases_connection_when_write_fails(monkeypatch):
    """A failed symbol write must not leak the listener's global write lease."""
    from src.core import pumpfun_curve_listener as listener

    opened = []

    @contextmanager
    def fake_managed_connect(path, timeout=15, read_only=False, **kwargs):
        conn = _Connection(fail_on_write=not read_only)
        opened.append((read_only, conn))
        try:
            yield conn
        finally:
            conn.close()

    monkeypatch.setattr(listener, "managed_db_connect", fake_managed_connect)
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: _Response())
    listener._symbol_fetch_seen.add("mint-under-test")

    listener._fetch_and_store_symbol("mint-under-test", "/tmp/not-opened.db")

    assert [read_only for read_only, _ in opened] == [True, False]
    assert all(conn.closed for _, conn in opened)
    assert "mint-under-test" not in listener._symbol_fetch_seen


def test_cached_symbol_backfill_releases_write_connection(monkeypatch):
    from src.core import pumpfun_curve_listener as listener

    opened = []

    class _CachedConnection(_Connection):
        def fetchone(self):
            return ("CACHED",)

    @contextmanager
    def fake_managed_connect(path, timeout=15, read_only=False, **kwargs):
        conn = _CachedConnection() if read_only else _Connection()
        opened.append((read_only, conn))
        try:
            yield conn
        finally:
            conn.close()

    monkeypatch.setattr(listener, "managed_db_connect", fake_managed_connect)
    listener._fetch_and_store_symbol("cached-mint", "/tmp/not-opened.db")

    assert [read_only for read_only, _ in opened] == [True, False]
    assert all(conn.closed for _, conn in opened)


def test_first_pumpportal_timeout_is_not_fatal():
    from src.core.pumpfun_curve_listener import _pumpportal_failure_is_fatal

    assert not _pumpportal_failure_is_fatal(1, 0.0, now=1_000.0)
    assert not _pumpportal_failure_is_fatal(9, 0.0, now=1_000.0)
    assert _pumpportal_failure_is_fatal(10, 0.0, now=1_000.0)


def test_connected_pumpportal_preserves_three_minute_recovery_policy():
    from src.core.pumpfun_curve_listener import _pumpportal_failure_is_fatal

    assert not _pumpportal_failure_is_fatal(1, 900.0, now=1_079.0)
    assert _pumpportal_failure_is_fatal(1, 900.0, now=1_081.0)


def test_old_healthy_connection_does_not_make_first_disconnect_fatal():
    from src.core.pumpfun_curve_listener import _pumpportal_failure_is_fatal

    outage_started = 10_000.0
    assert not _pumpportal_failure_is_fatal(1, outage_started, now=outage_started)
    assert not _pumpportal_failure_is_fatal(2, outage_started, now=outage_started + 179)
    assert _pumpportal_failure_is_fatal(3, outage_started, now=outage_started + 181)


def test_listener_startup_schema_failure_releases_connection(monkeypatch):
    from src.core import pumpfun_curve_listener as listener

    class _StartupConnection:
        def __init__(self):
            self.closed = False

        def execute(self, sql, params=()):
            return self

        def cursor(self):
            raise RuntimeError("simulated startup schema failure")

        def close(self):
            self.closed = True

    conn = _StartupConnection()
    monkeypatch.setattr(listener, "db_connect", lambda *args, **kwargs: conn)
    instance = object.__new__(listener.PumpFunCurveListener)

    try:
        instance._ensure_db_once()
    except RuntimeError as exc:
        assert "simulated startup schema failure" in str(exc)
    else:
        raise AssertionError("expected startup schema setup to fail")

    assert conn.closed
