"""Focused lifecycle tests for explicit DB connection-reaper ownership."""

import threading

from src.utils import db_locking


def _stop_reaper():
    assert db_locking.stop_connection_reaper(timeout=1.0)


def test_reaper_start_is_idempotent_and_shutdown_is_bounded():
    _stop_reaper()
    assert db_locking.start_connection_reaper() is True
    assert db_locking.start_connection_reaper() is False
    names = {thread.name for thread in threading.enumerate()}
    assert {"db-conn-reaper", "db-wal-watchdog"} <= names
    assert db_locking.stop_connection_reaper(timeout=1.0) is True
    names = {thread.name for thread in threading.enumerate()}
    assert "db-conn-reaper" not in names
    assert "db-wal-watchdog" not in names


def test_reaper_shutdown_is_idempotent():
    _stop_reaper()
    assert db_locking.stop_connection_reaper(timeout=1.0) is True
