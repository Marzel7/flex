"""STORAGE-LIFECYCLE-P4 Part 26: legacy TransferIndexCleanup fail-closed
guard tests. Never touches a real database -- uses isolated tmp_path
fixtures. No provider calls.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.storage_cleanup import (  # noqa: E402
    LEGACY_CLEANUP_OVERRIDE_ENV_VAR,
    LEGACY_CLEANUP_OVERRIDE_VALUE,
    LegacyCleanupDisabledError,
    TransferIndexCleanup,
)


@pytest.fixture
def fixture_db(tmp_path):
    path = str(tmp_path / "fixture.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE transfer_index (id INTEGER PRIMARY KEY, block_time INTEGER)"
    )
    for i in range(10):
        conn.execute("INSERT INTO transfer_index (block_time) VALUES (?)", (1_000_000 + i,))
    conn.commit()
    conn.close()
    return path


def _ensure_env_clean(monkeypatch):
    monkeypatch.delenv(LEGACY_CLEANUP_OVERRIDE_ENV_VAR, raising=False)


def test_fails_closed_by_default(fixture_db, monkeypatch):
    _ensure_env_clean(monkeypatch)
    cleanup = TransferIndexCleanup(fixture_db, retention_days=90)
    with pytest.raises(LegacyCleanupDisabledError):
        cleanup.cleanup_old_transfers(dry_run=True)


def test_fails_closed_even_for_dry_run(fixture_db, monkeypatch):
    """Dry-run must ALSO be blocked -- a caller invoking this deprecated
    class at all, even in dry-run mode, indicates a wiring mistake that
    should surface loudly, not silently 'work' in dry-run only."""
    _ensure_env_clean(monkeypatch)
    cleanup = TransferIndexCleanup(fixture_db, retention_days=90)
    with pytest.raises(LegacyCleanupDisabledError):
        cleanup.cleanup_old_transfers(dry_run=True)


def test_truthy_but_wrong_value_still_blocked(fixture_db, monkeypatch):
    """A generic truthy value like 'true' or '1' must NOT satisfy the
    guard -- only the exact sentinel string does, to prevent accidental
    activation via some unrelated config convention."""
    for value in ("true", "1", "yes", "True", "TRUE", "enabled"):
        monkeypatch.setenv(LEGACY_CLEANUP_OVERRIDE_ENV_VAR, value)
        cleanup = TransferIndexCleanup(fixture_db, retention_days=90)
        with pytest.raises(LegacyCleanupDisabledError):
            cleanup.cleanup_old_transfers(dry_run=True)


def test_exact_override_value_allows_execution(fixture_db, monkeypatch):
    monkeypatch.setenv(LEGACY_CLEANUP_OVERRIDE_ENV_VAR, LEGACY_CLEANUP_OVERRIDE_VALUE)
    cleanup = TransferIndexCleanup(fixture_db, retention_days=90)
    # dry_run=True + fresh rows (all newer than any realistic cutoff) means
    # _verify_cleanup_safe will likely report "no rows old enough" and
    # return a 'skipped' status -- that's fine, the point of this test is
    # only that the guard itself does NOT raise once correctly overridden.
    result = cleanup.cleanup_old_transfers(dry_run=True)
    assert isinstance(result, dict)
    assert result["status"] in ("dry_run", "skipped")


def test_guard_checked_before_any_database_connection_opened(fixture_db, monkeypatch):
    """Structural + behavioral guard: the environment check must happen
    BEFORE _get_conn() is ever called, so a blocked call makes zero
    database contact at all (not even a read)."""
    _ensure_env_clean(monkeypatch)
    cleanup = TransferIndexCleanup("/nonexistent/path/that/would/fail/to/open.db", retention_days=90)
    # if the guard ran AFTER attempting to open the (nonexistent) DB, this
    # would raise sqlite3.OperationalError instead of LegacyCleanupDisabledError
    with pytest.raises(LegacyCleanupDisabledError):
        cleanup.cleanup_old_transfers(dry_run=True)


def test_guard_uses_exact_string_match_not_substring():
    """The override value must be an EXACT match, not merely contained in
    a longer string (defense against e.g. 'development-test-only-ish' or
    whitespace padding accidentally 'working')."""
    import os as os_module
    os_module.environ[LEGACY_CLEANUP_OVERRIDE_ENV_VAR] = LEGACY_CLEANUP_OVERRIDE_VALUE + "-extra"
    try:
        cleanup = TransferIndexCleanup(":memory:", retention_days=90)
        with pytest.raises(LegacyCleanupDisabledError):
            cleanup.cleanup_old_transfers(dry_run=True)
    finally:
        del os_module.environ[LEGACY_CLEANUP_OVERRIDE_ENV_VAR]


def test_module_docstring_marks_deprecated():
    src = (ROOT / "src/core/storage_cleanup.py").read_text()
    assert "DEPRECATED" in src
    assert "STORAGE-LIFECYCLE-P4" in src


def test_no_import_of_this_module_anywhere_in_active_cron_path():
    """Structural guard: storage_lifecycle_runner.py (the P2.2 cron
    target) must never import TransferIndexCleanup."""
    runner_src = (ROOT / "src/ops/storage_lifecycle_runner.py").read_text()
    assert "TransferIndexCleanup" not in runner_src
    assert "storage_cleanup" not in runner_src


def test_companion_cron_script_not_in_active_crontab_pattern():
    """cleanup_transfers.py exists but references a hardcoded absolute
    path and is NOT the P2.2-installed cron entry -- structural sanity
    check that our real cron installer script never references it."""
    runner_src = (ROOT / "src/ops/storage_lifecycle_runner.py").read_text()
    assert "cleanup_transfers" not in runner_src
