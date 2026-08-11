from pathlib import Path


LISTENER = Path("src/core/pumpfun_curve_listener.py")


def _enqueue_source() -> str:
    source = LISTENER.read_text()
    start = source.index("        def _enqueue_sync():")
    end = source.index("        async with self.db_lock:", start)
    return source[start:end]


def test_enqueue_priority_connection_closes_on_error_path() -> None:
    source = _enqueue_source()
    assert "_check = None" in source
    assert "finally:\n                # The cache/priority query" in source
    assert "if _check is not None:\n                    _check.close()" in source


def test_enqueue_secondary_connection_closes_on_error_path() -> None:
    source = _enqueue_source()
    assert "conn2 = None" in source
    assert "if conn2 is not None:\n                        conn2.close()" in source
