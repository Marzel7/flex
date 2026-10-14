import ast
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = 'cd459a99017f4495188c875af710bde115038137'

def symbols(text, names, context):
    tree = ast.parse(text)
    selected = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    for n in selected:
        n.decorator_list = []
    exec(compile(ast.Module(body=selected, type_ignores=[]), '<qualification>', 'exec'), context)
    return context

def test_preserved_selection_order_and_projection_are_exact():
    original = subprocess.check_output(['git', 'show', REFERENCE+':src/ops/operator_routes.py'], cwd=ROOT, text=True)
    current = (ROOT/'src/ops/operator_routes.py').read_text()
    for name in ['_monitor_live_projection', '_monitor_store_connection', '_monitor_queue_root']:
        before = next(n for n in ast.parse(original).body if isinstance(n, ast.FunctionDef) and n.name == name)
        after = next(n for n in ast.parse(current).body if isinstance(n, ast.FunctionDef) and n.name == name)
        assert ast.dump(before) == ast.dump(after)
    assert 'operation_monitor_worker' not in current

def test_missing_authority_is_explicit_unavailable(monkeypatch):
    monkeypatch.delenv('WATCHTOWER_MONITOR_UI_DB_PATH', raising=False)
    ctx = symbols((ROOT/'src/ops/operator_routes.py').read_text(),
                  {'MonitorStoreUnavailable', '_monitor_store_connection'},
                  dict(os=os, Path=Path, sqlite3=sqlite3))
    with pytest.raises(ctx['MonitorStoreUnavailable'], match='PATH_REQUIRED'):
        ctx['_monitor_store_connection']()

def test_read_projection_import_does_not_import_acquisition_binding():
    # Import only read-path dependency, not application or worker constructors.
    before = set(sys.modules)
    from src.ops.watchtower_current_mc_overlay import read_current_mc_quote_projection
    assert read_current_mc_quote_projection(None, now=1) == {}
    added = set(sys.modules)-before
    assert 'src.ops.operation_monitor_worker' not in added
    assert 'src.ops.token_data_provider_bindings' not in added

def test_readonly_qualification_keeps_every_connection_query_only():
    path = Path('/private/tmp/flex-dev037-qualification.odp7tk/core-fixture.db')
    if not path.exists(): pytest.skip('retained isolated fixture not present')
    ctx = symbols((ROOT/'src/dev_runtime.py').read_text(), {'_dev_guarded_connect'},
                  dict(sqlite3=sqlite3, DEV_ROOT=path.parent, READ_ONLY_QUALIFICATION=True,
                       _sqlite_path=lambda *a, **kw: path, _sqlite_connect=sqlite3.connect))
    with ctx['_dev_guarded_connect'](path) as connection:
        assert connection.execute('PRAGMA query_only').fetchone() == (1,)

def test_network_denial_prevents_transport():
    calls=[]
    ctx = symbols((ROOT/'src/dev_runtime.py').read_text(), {'_is_loopback','_blocked_create_connection'},
                  dict(_create_connection=lambda *a, **kw: calls.append(a)))
    with pytest.raises(OSError, match='blocks outbound'):
        ctx['_blocked_create_connection'](('198.51.100.1',443))
    assert calls == []

def test_terminal_display_uses_authoritative_retained_state_and_deadlines():
    text=(ROOT/'templates/operators_index.html').read_text()
    assert "row.monitor_state==='PRICE_MONITOR_COMPLETE_COLLAPSED'" in text
    assert 'controller.abort(), 3000' in text
    assert 'Superseded response' in text
