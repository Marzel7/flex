"""Static guards for the narrowly scoped API import diagnostic boundary."""

import ast
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/core/main.py"


def test_db_fd_watchdog_diagnostic_suppression_is_explicit_and_default_off():
    tree = ast.parse(MAIN.read_text())
    helper = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_db_fd_watchdog_diagnostic_suppressed"
    )
    source = ast.get_source_segment(MAIN.read_text(), helper)
    assert "FLEX_DIAGNOSTIC_SUPPRESS_DB_FD_WATCHDOG" in source
    assert '"0"' in source


def test_db_fd_watchdog_remains_default_startup_and_only_explicit_flag_suppresses():
    source = MAIN.read_text()
    guarded = source.index("if _db_fd_watchdog_diagnostic_suppressed():")
    start = source.index("_start_db_fd_watchdog()", guarded)
    assert source.index("else:", guarded, start) < start
    assert source.count("_start_db_fd_watchdog()") == 2  # definition plus guarded default call


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
