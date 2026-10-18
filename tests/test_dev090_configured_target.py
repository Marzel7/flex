"""Configured stopped-target overlay qualification; no installed runtime access."""
import ast
import hashlib
from pathlib import Path
import sqlite3
import pytest
import importlib.util
import sys
_spec=importlib.util.spec_from_file_location('_dev090_target_fixture',Path(__file__).with_name('test_dev090_handoff.py'))
_fixture_module=importlib.util.module_from_spec(_spec)
sys.modules[_spec.name]=_fixture_module
_spec.loader.exec_module(_fixture_module)
Fixture=_fixture_module.Fixture

ROOT=Path(__file__).resolve().parents[1]
MINT='68yywen51ppvR2zWptcpvmc5H5a5SC6tsz8evAP2pump'


def test_configured_worker_exact_reversal():
    source=(ROOT/'src/core/walkback_worker.py').read_text()
    hook='                from src.ops.walkback_create_handoff import maintenance_tick\n                maintenance_tick(LIVE_DB_PATH, OPS_DB_PATH)\n\n'
    assert source.count(hook)==1
    source=source.replace(hook,'',1)
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='_get_tx')
    lines=source.splitlines(True)
    lines[node.lineno-1:node.end_lineno]=(ROOT/'docs/audits/dev082/get-tx-preimage.py.txt').read_text().splitlines(True)
    assert hashlib.sha256(''.join(lines).encode()).hexdigest()=='a45ccc0db0a34269b8811975fef1b9297a27acad745074a3674a11f8f771e5b9'

@pytest.mark.parametrize('order',['birth-first','late-create'])
def test_dev075_exact_mint_prospective_fixture_only(tmp_path,order):
    # Exact retained mint identity; other proof fields are synthetic fixture
    # authorities, not a claim of recovered original creator/signature.
    f=Fixture(tmp_path)
    if order=='late-create':f.add_waiting(mint=MINT)
    f.publish(f.proof(mint=MINT))
    if order=='birth-first':
        assert f.dispatch()[0][1]=='OPS_ROW_MISSING'
        f.add_waiting(mint=MINT)
    f.dispatch(now=500)
    assert f.row(f.ops,'SELECT status FROM wt_walkback_queue')==('pending',)
    assert f.row(f.core,'SELECT state FROM wb_create_delivery')==('ACK',)
    assert f.execute_claim_model()==1
    f.dispatch(now=1000)
    assert f.execute_claim_model()==1
