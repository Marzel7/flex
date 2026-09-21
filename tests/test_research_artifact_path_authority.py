"""Offline cross-root path contracts for lazy operator research modules."""

import ast
import os
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _constants(relative_file: str, overrides: dict[str, str]) -> dict:
    source = ROOT / relative_file
    names = {"ROOT", "DB", "REGISTRY_PATH", "HISTORICAL", "BYZANTINE_LIFECYCLE_ARTIFACT"}
    nodes = [
        node for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id in names for target in node.targets)
    ]
    namespace = {"Path": Path, "os": os, "__file__": str(source)}
    with patch.dict(os.environ, overrides):
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


def test_all_lazy_research_modules_use_explicit_existing_root(tmp_path):
    research_root = tmp_path / "existing-research"
    ops_db = tmp_path / "existing-ops.db"
    overrides = {"OPERATION_RESEARCH_ROOT": str(research_root), "WT_OPS_DB_PATH": str(ops_db)}
    playbook = _constants("src/ops/operation_playbook_registry.py", overrides)
    operator = _constants("src/ops/operator_research_module.py", overrides)
    watchtower = _constants("src/ops/watchtower_research_cohorts.py", overrides)
    assert playbook["ROOT"] == operator["ROOT"] == watchtower["ROOT"] == research_root
    assert playbook["REGISTRY_PATH"] == research_root / "docs/operations/playbooks/operation_playbook_registry.v1.json"
    assert watchtower["HISTORICAL"] == research_root / "docs/audits/watchtower_51_generic_price_fact_replay.v1.json"
    assert watchtower["DB"] == ops_db
