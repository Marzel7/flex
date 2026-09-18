"""Structural safety gates for retiring legacy Farm Detector acquisition."""

from __future__ import annotations

import ast
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = tuple(REPO / part for part in ("src/core", "src/ops", "src/analysis"))
HISTORICAL_READERS = (
    REPO / "src/core/operation_dashboard_routes.py",
    REPO / "src/ops/launcher_observatory_routes.py",
    REPO / "src/ops/lifecycle_adapters.py",
    REPO / "src/ops/spam_classification.py",
)
ATTRIBUTION_PATHS = (
    REPO / "src/core/watchtower_attribution.py",
    REPO / "src/core/walkback_worker.py",
    REPO / "src/core/treasury_bank.py",
    REPO / "src/core/ws_cascade.py",
    REPO / "src/ops/attribution_outcome.py",
)
RETIRED_MARKER = "Farm Detector production acquisition was retired on 2026-09-18"


def _python_files():
    return sorted(path for root in SOURCE_ROOTS for path in root.rglob("*.py"))


def test_farm_detector_has_no_production_producer_module_or_callers():
    """No scheduler, provider, or mutation producer can invoke a retired scan."""
    assert not (REPO / "src/core/farm_detector.py").exists()
    imports = []
    calls = []
    for path in _python_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "src.core.farm_detector":
                imports.append((path, node.lineno))
            elif isinstance(node, ast.Import):
                imports.extend((path, node.lineno) for name in node.names
                               if name.name == "src.core.farm_detector")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "run_farm_scan":
                calls.append((path, node.lineno))
    assert imports == []
    assert calls == []


def test_no_production_farm_table_mutations_remain():
    """Historical Farm tables are retained but production has no writer path."""
    mutations = []
    pattern = re.compile(r"\b(?:INSERT|UPDATE|DELETE|REPLACE)\b[\s\S]{0,160}\bwt_farm", re.I)
    for path in _python_files():
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and pattern.search(node.value):
                mutations.append((path, node.lineno))
    assert mutations == []


def test_historical_readers_are_explicitly_marked_and_read_only():
    for path in HISTORICAL_READERS:
        text = path.read_text()
        assert RETIRED_MARKER in text
        assert "run_farm_scan" not in text


def test_farm_output_cannot_influence_attribution_or_watchtower_decisions():
    """Farm history remains presentation evidence, never operational input."""
    forbidden = ("wt_farm_", "farm_detector", "run_farm_scan")
    for path in ATTRIBUTION_PATHS:
        text = path.read_text().lower()
        assert all(token not in text for token in forbidden), path


def test_process_exit_rolls_back_uncommitted_sqlite_work_and_releases_flock(tmp_path):
    """The controlled-stop recovery prerequisite: kernel teardown releases both."""
    db_path = tmp_path / "recovery.db"
    lock_path = tmp_path / "recovery.write.lock"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE pending (value TEXT)")
    conn.commit()
    conn.close()
    child = textwrap.dedent(
        """
        import fcntl, os, sqlite3, sys
        db_path, lock_path = sys.argv[1:]
        lock = open(lock_path, 'w')
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO pending(value) VALUES ('uncommitted')")
        os._exit(0)
        """
    )
    subprocess.run([sys.executable, "-c", child, str(db_path), str(lock_path)], check=True)
    check = sqlite3.connect(db_path)
    assert check.execute("SELECT COUNT(*) FROM pending").fetchone()[0] == 0
    check.close()
    with open(lock_path, "w") as lock:
        import fcntl
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
