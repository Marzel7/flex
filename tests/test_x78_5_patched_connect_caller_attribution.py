"""X78.5 Phase 2: regression test for db_connect()'s caller attribution
fix. Every NestedDatabaseWriteError raised against a connection opened
via the global sqlite3.connect() monkeypatch (_patched_connect) was
previously tagged "db_locking.py:718 in _patched_connect" -- identifying
the interception point, not the real caller, making every such error
unactionable. db_connect() now walks one frame further when it detects
it was entered via _patched_connect, so the tag identifies the actual
code that called bare sqlite3.connect().
"""
from __future__ import annotations

import sqlite3

import pytest

from src.utils import db_locking


def test_patched_connect_attributes_to_real_caller(tmp_path, monkeypatch):
    db_path = str(tmp_path / "x.db")
    monkeypatch.setattr(db_locking, "_FLEX_DB_ABS", db_path)

    def caller_of_interest():
        return sqlite3.connect(db_path, timeout=5)

    conn = caller_of_interest()
    try:
        assert "in caller_of_interest" in conn._db_caller
        assert "in _patched_connect" not in conn._db_caller, (
            "the caller tag must identify the real caller, not the "
            "monkeypatch interception point -- this was the exact gap "
            "that made every live db_locking.py:718 in _patched_connect "
            "signature unactionable during the X78.5 investigation"
        )
    finally:
        conn.close()


def test_direct_db_connect_call_still_attributes_correctly(tmp_path, monkeypatch):
    """Non-regression: calling db_connect() directly (not via the
    monkeypatch) must still correctly identify ITS caller, unaffected by
    the _patched_connect-specific frame-walking added above."""
    db_path = str(tmp_path / "x.db")

    def direct_caller():
        return db_locking.db_connect(db_path, timeout=5)

    conn = direct_caller()
    try:
        assert "direct_caller" in conn._db_caller
    finally:
        conn.close()
