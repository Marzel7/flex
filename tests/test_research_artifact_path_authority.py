"""Offline cross-root path contracts for lazy operator research modules."""

import ast
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
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


PINNED_ARTIFACTS = {
    "docs/operations/playbooks/operation_playbook_registry.v1.json": "d62563f96585b7cce9eec042fa5743249c225b698cec01798705efdd5667a31b",
    "docs/audits/watchtower_51_generic_price_fact_replay.v1.json": "09071c830e6a61792b3f2f098e22528cbbe4382062e1394e7d58aa370c0d12bf",
    "docs/audits/operation_all_birth_research/byzantine_428_v1/byzantine_64_full_lifecycle_peak_reevaluation.v1.json": "fbc166438fc5d435241225a14fced364e70145f069134169c39e4738ff65b895",
    "docs/audits/operation_all_birth_research/byzantine_428_v1/byzantine_64_playbook_read_model.v1.json": "147a1435a5777767a436959e23aee965fce9f3a87e2c103a61b954bc294a5924",
    "docs/audits/operation_all_birth_research/byzantine_428_v1/byzantine_64_playbook_read_model.v3.json": "a6126469ff2c931c0abccf72e8aa90b3c0c86298500dfce68cae5c29bf2ed2e1",
    "docs/audits/operation_all_birth_research/byzantine_428_v1/byzantine_64_playbook_read_model.v4.json": "8c30cfbec01b2f6cadb9451e1d8cbc7989510de17514deefb3a07a926fe224a0",
    "docs/audits/operation_all_birth_research/byzantine_428_v1/byzantine_64_position_peak_trading_opportunity.v2.json": "5a45024e025ee0f1693eda3f3a234b5c1d188a20854f6c96765dd0c0ca731c39",
    "docs/audits/operation_all_birth_research/byzantine_428_v1/byzantine_64_post_entry_rug_timing.v1.json": "50b21dbd3ce22a3f1a50668098a87b7ec7f90da7454357a3c3e912444c46a064",
    "docs/audits/operation_all_birth_research/byzantine_428_v1/byzantine_64_strategy_evidence_cases.v1.json": "9e40cd933736bb80b38b45082d5200154a3691aca02c6fa87ba600824232b3d2",
    "docs/audits/byzantine_canonical_120_creator_recurrence.v1.json": "a02bab8f677d095fe1b8a296445d76fda813cdf0dcc3476912ab69e2bf9a0fb2",
    "docs/audits/byzc_byzantine_119_179_population_comparison.v1.json": "d91755ccb45a56c69b7cf5e920db8443ff623f08ca30311992a68ff269072c10",
    "docs/audits/byzc_noncanonical_179_taxonomy.v1.json": "b1ac6dd7e8b5fb0cfac5e0b58dcb1d5fa0e0eee9608a9c0071c9705742840f74",
}


def test_candidate_research_artifacts_are_complete_and_byte_pinned():
    for relative_path, expected_hash in PINNED_ARTIFACTS.items():
        data = (ROOT / relative_path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected_hash
        assert isinstance(json.loads(data), dict)
    registry = json.loads((ROOT / "docs/operations/playbooks/operation_playbook_registry.v1.json").read_text())
    for spec in registry["artifact_playbooks"]:
        for artifact in spec["artifacts"].values():
            assert artifact in PINNED_ARTIFACTS


def test_pinned_watchtower_playbook_reads_from_candidate_tree_and_scratch_db(tmp_path):
    db = tmp_path / "ops.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE operation_monitor_facts (operation_id TEXT, cohort_class TEXT, "
            "assignment_timestamp INTEGER, mint TEXT)"
        )
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT),
        "OPERATION_RESEARCH_ROOT": str(ROOT),
        "WT_OPS_DB_PATH": str(db),
    })
    result = subprocess.run(
        [sys.executable, "-c", "from src.ops.operation_playbook_registry import artifact_playbooks; "
         "assert [row['operation_id'] for row in artifact_playbooks()] == ['watchtower']"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
