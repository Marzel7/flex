"""The subtype reader must use the pinned operations DB, not its source root."""

import sqlite3

import pytest


def test_subtype_reader_uses_read_only_operations_authority(monkeypatch, tmp_path):
    from src.core import db
    from src.ops import operator_routes

    expected = tmp_path / "existing_ops.db"
    monkeypatch.setattr(db, "OPS_DB_PATH", str(expected))
    observed = {}

    class ReachedConnect(Exception):
        pass

    def capture(database, **kwargs):
        observed.update(database=database, kwargs=kwargs)
        raise ReachedConnect

    monkeypatch.setattr(operator_routes.sqlite3, "connect", capture)
    with pytest.raises(ReachedConnect):
        operator_routes.operator_subtype_page("parent", "subtype")

    assert observed == {
        "database": f"file:{expected}?mode=ro",
        "kwargs": {"uri": True},
    }
    assert not expected.exists()
