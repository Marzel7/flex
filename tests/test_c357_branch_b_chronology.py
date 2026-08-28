"""Offline assertions for the retained C357 Branch B reconstruction."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "docs/audits/c357_branch_b_chronology.v1.json"
SCRIPT = ROOT / "scripts/reconstruct_c357_branch_b.py"


def module():
    spec = importlib.util.spec_from_file_location("branch_b", SCRIPT)
    value = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(value)
    return value


def artifact():
    return json.loads(ARTIFACT.read_text())


def test_initialization_is_retained_exactly():
    value = artifact()["initialization"]
    assert value["block_time"] == 1787578100
    assert value["wsol_transfer_lamports"] == 3_049_997_960_720
    assert value["provisioner"] == "33myosxzjbzfx2GcW71zmzvrzibQnnh6njW2vLKiMxr4"
    assert value["destination"] == "HXufNWTdtH1oq2SscHQsfGpXLv1P8Givsz7mBqqYrive"


def test_chronology_and_directed_hxuf_cztx_flows_are_preserved():
    value = artifact()
    flows = value["hxuf_cztx_direct_transfers"]
    assert flows["hxuf_to_cztx"]["count"] == 17
    assert flows["hxuf_to_cztx"]["lamports"] == 39_280_999_915_000
    assert flows["cztx_to_hxuf"]["count"] == 0
    assert flows["hxuf_to_cztx"]["first_block_time"] == 1787578101


def test_two_exact_launches_share_hxuf_but_not_creator():
    launches = artifact()["launches"]
    assert len(launches) == 2
    assert {row["parent"] for row in launches} == {"HXufNWTdtH1oq2SscHQsfGpXLv1P8Givsz7mBqqYrive"}
    assert len({row["creator"] for row in launches}) == 2
    assert all(row["amount_lamports"] == 99_999_985_000 for row in launches)
    assert all(row["atomic"]["instruction_order_json"] == '["createAccountWithSeed", "initializeAccount3", "transfer", "syncNative", "closeAccount"]' for row in launches)


def test_role_model_classifications_and_paused_safety_are_retained():
    value = artifact()
    assert value["classifications"]["hxuf_cztx_relationship"] == "ONE_WAY_PROVISIONING"
    assert value["classifications"]["c357_continuity"] == "YES_MODERATE"
    assert value["safety"] == {"source_db_writes": 0, "workflow_writes": 0, "provider_mutations": 0, "membership_changed": False, "fingerprint_changed": False, "detector_changed": False}


def test_c357_workflow_remains_paused():
    assert module().detail("database/wt_ops_v2.db", "p3r-v2-c357da9d0d4d560311e4")["workflow_status"] == "PAUSED"


def test_replay_is_offline_and_digest_equal():
    result = module().replay(artifact())
    assert result["replay"] == "C357_BRANCH_B_REPLAY_PASS"
    assert result["recorded_digest"] == result["replay_digest"]
    assert result["provider_calls_during_replay"] == 0
