from pathlib import Path


def test_enrich_read_is_owner_scoped_and_read_only():
    source = Path("src/core/pumpfun_curve_listener.py").read_text()
    start = source.index("def _enrich_read(_m=mint):")
    end = source.index("_row = await asyncio.to_thread(_enrich_read)", start)
    body = source[start:end]
    assert "with managed_db_connect(" in body
    assert "read_only=True" in body
    assert "db_connect(DB_PATH" not in body


def test_enrich_read_has_no_success_only_close():
    source = Path("src/core/pumpfun_curve_listener.py").read_text()
    start = source.index("def _enrich_read(_m=mint):")
    end = source.index("_row = await asyncio.to_thread(_enrich_read)", start)
    body = source[start:end]
    assert "_conn.close()" not in body
