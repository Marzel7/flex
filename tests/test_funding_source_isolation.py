"""Offline source-identity check for the Funding classifier import."""

import json
import os
from pathlib import Path
import subprocess
import sys


def test_classifier_keeps_imports_in_pinned_checkout(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    code = """
import json
import sys
import src
from src.analysis import automatic_cex_detection as classifier
from src.utils import db_locking
print(json.dumps({
    'src': src.__file__,
    'classifier': classifier.__file__,
    'db_locking': db_locking.__file__,
    'sys_path': sys.path,
    'classifier_db_path': classifier.DB_PATH,
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env,
        check=True, capture_output=True, text=True, timeout=15,
    )
    observed = json.loads(result.stdout)
    for key in ("src", "classifier", "db_locking"):
        assert observed[key].startswith(str(repo) + os.sep)
    assert "/Users/kevinkeaveney/Dev/claude/flex" not in observed["sys_path"]
    assert observed["classifier_db_path"] == "flex_complete_database.db"
