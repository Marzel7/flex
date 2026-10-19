"""Current execution contract plus preserved historical source-preimage contracts."""
import asyncio
import copy
import hashlib
import importlib.util
import sys
from pathlib import Path
import pytest

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj

compat = load("dev090_compat_parent", "test_dev051_create_compatibility.py")
correlation = load("dev090_correlation_parent", "test_dev051_signature_correlation.py")
module = correlation.module
# Preserve all functional baseline assertions; original exact-source assertions
# run below against their original preimage, while the current diff is pinned.
for parent in (compat, correlation):
    for name, function in vars(parent).items():
        if name.startswith("test_") and name not in {
            "test_only_five_literals_changed",
            "test_imports_selection_retries_and_qualification_unchanged",
        }:
            globals()[name] = function

GUARD = """        meta = tx.get("meta")
        if not isinstance(meta, dict) or "err" not in meta or meta["err"] is not None:
            result["validation_notes"].append("transaction execution success is not established")
            return result

"""
ENHANCED = """                    if "transactionError" not in tx or tx["transactionError"] is not None:
                        return None
                    if "meta" not in tx:
                        tx = dict(tx)
                        tx["meta"] = {"err": tx["transactionError"],
                                      "innerInstructions": tx.get("innerInstructions", [])}
"""

def test_exact_current_diff_and_original_source_identity_contracts(monkeypatch):
    source_path = compat.ROOT / "src/analysis/pump_fun_post_migration_analyzer.py"
    current = source_path.read_text()
    assert current.count(GUARD) == current.count(ENHANCED) == 1
    preimage = current.replace(GUARD, "", 1).replace(ENHANCED, "", 1)
    assert hashlib.sha256(preimage.encode()).hexdigest() == "b4f2064305faa285f13672217013243c7d5d6a001566b6891b663c79973101ac"
    # The historical contracts pin the old implementation, not arbitrary new
    # bytes. Execute their unchanged assertions on exactly that proved preimage.
    read_text = Path.read_text
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "read_text", lambda path, *a, **kw: preimage if path == source_path else read_text(path, *a, **kw))
        compat.test_only_five_literals_changed()
        correlation.test_imports_selection_retries_and_qualification_unchanged()

@pytest.mark.parametrize("case", ["success", "failure", "missing", "false", "zero", "empty", "meta_failure", "meta_missing_err", "meta_null"])
def test_enhanced_explicit_execution_authority(module, monkeypatch, case):
    tx = correlation.mutate(correlation.c.retained_shape(), "matching_v1", True)
    if case == "failure": tx["transactionError"] = {"InstructionError": [0, "Custom"]}
    elif case == "missing": tx.pop("transactionError")
    elif case == "false": tx["transactionError"] = False
    elif case == "zero": tx["transactionError"] = 0
    elif case == "empty": tx["transactionError"] = ""
    elif case == "meta_failure": tx["meta"] = {"err": {"InstructionError": [0, "Custom"]}}
    elif case == "meta_missing_err": tx["meta"] = {}
    elif case == "meta_null": tx["meta"] = None
    before = copy.deepcopy(tx)
    calls = []
    class Session:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def post(self, url, json):
            calls.append(json)
            return correlation.c.Response([tx])
    monkeypatch.setattr(module.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(module, "HELIUS_API_KEY", "offline-fixture-only")
    a = correlation.c.analyzer(module)
    result = asyncio.run(a.extract_bonding_curve_via_helius_parse(correlation.c.SIG))
    assert bool(result) == (case == "success")
    assert (a._create_tx_signature is not None) == (case == "success")
    assert len(calls) == 1
    assert tx == before
