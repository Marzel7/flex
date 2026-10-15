"""Offline component qualification: existing evidence read-only, no runtime."""
import ast
import hashlib
import importlib
import json
from pathlib import Path
import socket
import sqlite3
import sys
import time

import pytest
from flask import Flask, render_template

ROOT = Path(__file__).resolve().parents[1]
MONITOR = Path('/Users/kevinkeaveney/Dev/claude/flex/.dev_runtime/monitor/dev_005a/wt_ops_v2.dev.db')
OPS = Path('/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db')
EXPECTED = {
 'templates/operators_index.html': '439fc19f25fd8e3347474008bcc9d6df59755cdd',
 'src/ops/operator_routes.py': '802976ef21a824a7bb980afff2968739185eee64',
 'src/ops/watchtower_dev013_ui_projection.py': 'f3bef3609cdef68272b7231855fd45cb648046dc',
}

@pytest.fixture(autouse=True)
def read_only_guards(monkeypatch):
    original = sqlite3.connect
    def guarded(database, *args, **kwargs):
        text = str(database)
        path = Path(text.split('?',1)[0][5:] if text.startswith('file:') else text).resolve(strict=True)
        kwargs['uri'] = True
        connection = original(path.as_uri()+'?mode=ro', *args, **kwargs)
        connection.execute('PRAGMA query_only=ON')
        deadline = time.monotonic()+1
        connection.set_progress_handler(lambda: int(time.monotonic()>deadline), 1000)
        return connection
    def denied(*args, **kwargs):
        raise AssertionError('No network transport permitted in qualification')
    monkeypatch.setattr(sqlite3, 'connect', guarded)
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)

def retained():
    connection=sqlite3.connect(MONITOR)
    connection.row_factory=sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM operation_monitor_facts WHERE operation_id IN ('watchtower','byzantine')")]
    finally:
        connection.close()

def test_exact_source_preservation():
    for path, expected in EXPECTED.items():
        data=(ROOT/path).read_bytes()
        assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==expected

def test_top_level_api_and_enrichment_import_without_worker():
    importlib.import_module('src.ops.operator_routes')
    importlib.import_module('src.ops.watchtower_dev013_ui_projection')
    assert 'src.ops.operation_monitor_worker' not in sys.modules

def test_actual_retained_history_order_fields_and_prices():
    from src.ops.operator_routes import _recent_operation_tokens
    rows=retained()
    result=_recent_operation_tokens(rows, exclude_live=True, now=int(time.time()))
    assert len(result)==20
    keys=[(r['assignment_timestamp'],r['mint']) for r in result]
    assert keys==sorted(keys,reverse=True)
    assert len({r['mint'] for r in result})==20
    assert any(r['entry_mc_usd'] is not None for r in result)
    assert any(r['peak_mc_usd'] is not None for r in result)
    assert any(r['max_multiple'] is not None for r in result)
    assert any(r['time_to_peak_seconds'] is not None for r in result)
    for row in result:
        assert {'current_mc_usd','peak_scope','evidence','next_check_at','authoritative_lifecycle_state'} <= row.keys()

def test_missing_evidence_is_not_calculated_or_filled():
    from src.ops.operator_routes import _recent_operation_tokens
    result=_recent_operation_tokens([dict(mint='missing',operation_id='watchtower',assignment_timestamp=1)])
    assert result[0]['entry_mc_usd'] is None
    assert result[0]['current_mc_usd'] is None
    assert result[0]['peak_mc_usd'] is None
    assert result[0]['max_multiple'] is None

def test_actual_canonical_enrichment_reads_without_mutation():
    from src.ops.watchtower_dev013_ui_projection import dev013_watchtower_ui_facts
    mints=[r['mint'] for r in retained() if r['operation_id']=='watchtower'][:20]
    connection=sqlite3.connect(OPS)
    connection.row_factory=sqlite3.Row
    try:
        result=dev013_watchtower_ui_facts(connection,mints)
        assert set(result)==set(mints)
        assert connection.execute('PRAGMA query_only').fetchone()[0]==1
    finally:
        connection.close()

def test_rich_renderer_and_identity_scope_are_preserved():
    app=Flask(__name__,template_folder=str(ROOT/'templates'),static_folder=str(ROOT/'static'))
    with app.test_request_context('/'):
        text=render_template('operators_index.html',active_page='operators')
    for marker in ['LIVE TOKENS','HISTORY — LAST 20 TOKENS','RUNNING MULTIPLE','TIME TO PEAK','MONITORING CADENCE','LIVE WATCHTOWER','LIVE BYZANTINE','HISTORICAL BYZANTINE']:
        assert marker in text
    source=(ROOT/'src/ops/operator_routes.py').read_text()
    assert 'current_operation_names = ("byzantine", "watchtower", "nexus")' in source

def test_explicit_remaining_runtime_gap_is_not_masked():
    tree=ast.parse((ROOT/'src/ops/operator_routes.py').read_text())
    projection=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_monitor_live_projection')
    assert any(isinstance(n,ast.ImportFrom) and n.module=='src.ops.operation_monitor_worker' for n in ast.walk(projection))
    assert not (ROOT/'src/ops/operation_monitor_worker.py').exists()
