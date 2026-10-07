"""Focused lifecycle tests for explicit DB connection-reaper ownership."""

import threading
import subprocess
import sys
from pathlib import Path

from src.utils import db_locking


ROOT = Path(__file__).resolve().parents[1]


def test_db_locking_import_has_no_reaper_thread_side_effect():
    result = subprocess.run(
        [sys.executable, "-c", "import threading; import src.utils.db_locking; print(','.join(t.name for t in threading.enumerate()))"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "db-conn-reaper" not in result.stdout
    assert "db-wal-watchdog" not in result.stdout


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
