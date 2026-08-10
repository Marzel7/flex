class _FailingCursor:
    def execute(self, sql, params=()):
        raise RuntimeError("simulated schema failure after write-lane acquisition")


class _FailingConnection:
    def __init__(self):
        self.closed = False

    def cursor(self):
        return _FailingCursor()

    def close(self):
        self.closed = True


def test_price_service_schema_failure_releases_connection():
    """Lazy API initialization must release its lease when schema setup fails."""
    from src.core.price_service import TokenPriceService

    service = object.__new__(TokenPriceService)
    conn = _FailingConnection()
    service._get_conn = lambda: conn

    try:
        service._ensure_tables()
    except RuntimeError as exc:
        assert "simulated schema failure" in str(exc)
    else:
        raise AssertionError("expected schema setup to fail")

    assert conn.closed
