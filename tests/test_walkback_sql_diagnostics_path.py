"""Path-only contract for the opt-in Walkback SQL diagnostics destination."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "logs" / "diagnostics" / "walkback_sql_lifecycle.jsonl"


def _resolved_path(value: str | None) -> str:
    env = os.environ.copy()
    if value is None:
        env.pop("DB_WALKBACK_SQL_DIAGNOSTICS_PATH", None)
    else:
        env["DB_WALKBACK_SQL_DIAGNOSTICS_PATH"] = value
    return subprocess.check_output(
        [sys.executable, "-c", "from src.utils.db_locking import _WALKBACK_SQL_DIAGNOSTICS_PATH; print(_WALKBACK_SQL_DIAGNOSTICS_PATH)"],
        cwd=ROOT, env=env, text=True,
    ).strip()


def test_walkback_sql_diagnostics_default_path_is_unchanged():
    assert _resolved_path(None) == str(DEFAULT)


def test_walkback_sql_diagnostics_empty_override_uses_default():
    assert _resolved_path("") == str(DEFAULT)


def test_walkback_sql_diagnostics_explicit_override_is_exact():
    assert _resolved_path("/tmp/test-walkback-sql-lifecycle.jsonl") == "/tmp/test-walkback-sql-lifecycle.jsonl"
