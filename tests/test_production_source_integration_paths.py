"""Offline path parity for the clean API/path integration candidate."""

import ast
import importlib.util
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


def _load_safe_path_module(relative_file: str, name: str, overrides: dict[str, str]):
    source = ROOT / relative_file
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, overrides):
        spec.loader.exec_module(module)
    return module


def test_api_database_and_armed_paths_bind_to_explicit_authorities(tmp_path):
    ops_db = str(tmp_path / "existing-ops.db")
    alerts_db = str(tmp_path / "existing-alerts.db")
    armed = str(tmp_path / "existing-armed.txt")
    overrides = {
        "WT_OPS_DB_PATH": ops_db,
        "OPS_V2_DB_PATH": ops_db,
        "ALERTS_DB_PATH": alerts_db,
        "WS_ARMED_STATE_PATH": armed,
    }
    assert _assignment("src/core/operation_dashboard_routes.py", "OPS_DB_PATH", overrides) == ops_db
    assert _assignment("src/core/operation_dashboard_routes.py", "ALERTS_DB_PATH", overrides) == alerts_db
    assert _assignment("src/core/operation_dashboard_routes.py", "_ARMED_STATE_FILE", overrides) == armed
    assert _assignment("src/core/operation_armed.py", "OPS_DB_PATH", overrides) == ops_db
    assert not any(tmp_path.iterdir())


def test_api_manual_workflow_and_research_paths_bind_without_io(tmp_path):
    workflow = str(tmp_path / "existing-workflow.db")
    research = str(tmp_path / "existing-research")
    ops_db = str(tmp_path / "existing-ops.db")
    overrides = {
        "MANUAL_ATTRIBUTION_WORKFLOW_DB_PATH": workflow,
        "OPERATION_RESEARCH_ROOT": research,
        "WT_OPS_DB_PATH": ops_db,
    }
    workflow_module = _load_safe_path_module(
        "src/ops/manual_attribution_workflow_store.py", "offline_workflow_path", overrides
    )
    with patch.dict(os.environ, overrides):
        assert workflow_module.workflow_db_path() == workflow
    playbook = _load_safe_path_module(
        "src/ops/operation_playbook_registry.py", "offline_playbook_path", overrides
    )
    cohorts = _load_safe_path_module(
        "src/ops/watchtower_research_cohorts.py", "offline_cohort_path", overrides
    )
    research_module = _load_safe_path_module(
        "src/ops/operator_research_module.py", "offline_research_path", overrides
    )
    assert playbook.ROOT == cohorts.ROOT == research_module.ROOT == Path(research).resolve()
    assert cohorts.DB == Path(ops_db).resolve()
    assert playbook.REGISTRY_PATH == Path(research) / "docs/operations/playbooks/operation_playbook_registry.v1.json"
    assert not any(tmp_path.iterdir())
