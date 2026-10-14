from pathlib import Path
import sqlite3
from src.ops.universal_monitor_runtime import ensure_runtime_schema, enable_forward_monitoring

ROOT = Path(__file__).resolve().parents[1]

def test_startup_schema_and_policy_are_confined_to_disposable_fixture(tmp_path):
    path = tmp_path / 'monitor.db'
    with sqlite3.connect(path) as connection:
        ensure_runtime_schema(connection)
        enable_forward_monitoring(connection, now=123)
        assert connection.execute('SELECT enabled_at FROM universal_monitor_forward_state').fetchone() == (123,)
        # Existing policy activation is retained; runtime network/thread guards,
        # not this stored policy flag, prevent provider transport in DEV.
        assert connection.execute('SELECT dispatch_enabled FROM universal_monitor_budget_state').fetchone() == (1,)
    assert path.stat().st_size < 1_000_000

def test_registry_uses_existing_rows_not_absent_universal_fields():
    html = (ROOT / 'templates/operators_index.html').read_text()
    assert 'LIVE WATCHTOWER' in html and 'LIVE BYZANTINE' in html
    assert 'HISTORICAL BYZANTINE' in html and '2H PEAK' in html
    assert 'd.universal_monitor' not in html
    assert 'Registry evidence unavailable' in html
    assert "observed(row.launches_last_1d)" in html

def test_requests_have_deadline_generation_guard_and_unavailable_state():
    for name in ['operators_index.html', 'operations_live_monitor.html']:
        html = (ROOT / 'templates' / name).read_text()
        assert 'controller.abort(), 3000' in html
        assert 'Superseded response' in html
        assert 'await response.text()' in html
        assert 'UNAVAILABLE' in html

def test_guards_preserved_and_database_override_does_not_bypass_them():
    text = (ROOT / 'src/dev_runtime.py').read_text()
    assert 'mode=ro' in text and 'PRAGMA query_only=ON' in text
    assert 'socket.socket.connect = _blocked_connect' in text
    assert '"FLEX_ENABLE_FLASK_BACKGROUND_WORKERS": "0"' in text
    assert '"DB_PATH": os.environ.get("DB_PATH"' in text
