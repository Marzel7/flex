"""Execution-success boundary using actual analyzer and retained CREATE evidence."""
import copy
import json
from pathlib import Path
import pytest
import importlib.util
import sys
_spec = importlib.util.spec_from_file_location("dev090_execution_parent", Path(__file__).with_name("test_dev051_create_compatibility.py"))
_parent = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _parent
_spec.loader.exec_module(_parent)
module = _parent.module
analyzer = _parent.analyzer
retained_shape = _parent.retained_shape

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(params=["legacy", "create_v2"])
def create_case(request, module):
    a = analyzer(module)
    if request.param == "legacy":
        path = ROOT / "docs/audits/pumpfun_creation_anchor_poc/3dgGSHC49cbcmdjuZD6y1WDAtsP91v7j8Z1hTey9pump.creation.raw.v1.json"
        tx = json.loads(path.read_text())["response"]
        tx = tx.get("result", tx)
        a.token_mint = "3dgGSHC49cbcmdjuZD6y1WDAtsP91v7j8Z1hTey9pump"
    else:
        tx = retained_shape()
    assert a._validate_pumpfun_create_tx(tx)["is_pumpfun_create"]
    return a, tx


def test_success_preserved(create_case):
    a, tx = create_case
    assert tx["meta"]["err"] is None
    assert a._validate_pumpfun_create_tx(tx)["is_pumpfun_create"]

@pytest.mark.parametrize("status", ["missing_meta", "null_meta", "empty_meta", "non_dict_meta", "failed", "false_error", "zero_error", "empty_error"])
def test_non_explicit_success_rejected(create_case, status):
    a, original = create_case
    tx = copy.deepcopy(original)
    if status == "missing_meta": tx.pop("meta")
    elif status == "null_meta": tx["meta"] = None
    elif status == "empty_meta": tx["meta"] = {}
    elif status == "non_dict_meta": tx["meta"] = []
    else: tx["meta"]["err"] = {"failed": {"InstructionError": [0, "Custom"]}, "false_error": False, "zero_error": 0, "empty_error": ""}[status]
    before = copy.deepcopy(tx)
    result = a._validate_pumpfun_create_tx(tx)
    assert not result["is_pumpfun_create"]
    assert result["bonding_curve"] is None
    assert "transaction execution success is not established" in result["validation_notes"]
    assert tx == before
