"""Offline path parity for the clean API/path integration candidate."""

import ast
import os
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _assignment(relative_file: str, name: str, overrides: dict[str, str]) -> str:
    source = ROOT / relative_file
    module = ast.parse(source.read_text())
    node = next(
        entry for entry in module.body
        if isinstance(entry, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in entry.targets)
    )
    namespace = {"os": os, "_os": os, "__file__": str(source), "_REPO_ROOT": str(ROOT)}
    with patch.dict(os.environ, overrides):
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
    return namespace[name]


def test_api_and_cascade_use_same_explicit_armed_state():
    authority = "/existing/state/armed_mode.txt"
    overrides = {"WS_ARMED_STATE_PATH": authority}
    assert _assignment("src/core/operation_dashboard_routes.py", "_ARMED_STATE_FILE", overrides) == authority
    assert _assignment("src/core/ws_cascade.py", "_ARMED_STATE_FILE", overrides) == authority


def test_operation_armed_uses_existing_ops_authority():
    authority = "/existing/database/wt_ops_v2.db"
    overrides = {"WT_OPS_DB_PATH": authority, "OPS_V2_DB_PATH": authority}
    assert _assignment("src/core/operation_armed.py", "OPS_DB_PATH", overrides) == authority
