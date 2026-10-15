"""Offline component qualification: existing evidence read-only, no runtime."""
import ast
import hashlib
import importlib
import json
from pathlib import Path
import socket
import sqlite3
import sys
import subprocess
from types import SimpleNamespace
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
        data=subprocess.check_output(['git','show','5c81ed8f:'+path], cwd=ROOT)
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
    for marker in ['LIVE TOKENS','HISTORY — LAST 20 TOKENS','RUNNING MULTIPLE','TIME TO PEAK','MONITORING CADENCE']:
        assert marker in text
    for obsolete in ['LIVE WATCHTOWER','ENDED WATCHTOWER','LIVE BYZANTINE','HISTORICAL BYZANTINE']:
        assert obsolete not in text
    source=(ROOT/'src/ops/operator_routes.py').read_text()
    assert 'current_operation_names = ("byzantine", "watchtower", "nexus")' in source

def test_queue_read_no_longer_imports_acquisition_worker():
    tree=ast.parse((ROOT/'src/ops/operator_routes.py').read_text())
    projection=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_monitor_live_projection')
    assert not any(isinstance(n,ast.ImportFrom) and n.module=='src.ops.operation_monitor_worker' for n in ast.walk(projection))
    assert not (ROOT/'src/ops/operation_monitor_worker.py').exists()

def test_complete_retained_api_without_application_startup(monkeypatch):
    from src.ops import operator_routes as routes
    from src.core import db
    monkeypatch.setattr(db, 'OPS_DB_PATH', str(OPS))
    monkeypatch.setattr(db, 'DB_PATH', '/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db')
    monkeypatch.setenv('MISSION_CONTROL_MONITOR_UI_DB_PATH', str(MONITOR))
    monkeypatch.setenv('WATCHTOWER_MONITOR_QUEUE_PATH', str(MONITOR.parent/'queue'))
    monkeypatch.delenv('OPERATION_MONITOR_QUEUE_PATH', raising=False)
    monkeypatch.setenv('OPERATIONS_MODE', 'OFF')
    monkeypatch.setenv('MONITOR_INFRASTRUCTURE_ENABLED', 'false')
    monkeypatch.setattr(routes, '_store', None)
    app=Flask(__name__)
    app.register_blueprint(routes.operator_bp)
    started=time.monotonic()
    with app.test_client() as client:
        response=client.get('/api/operations/live-monitor')
    assert response.status_code==200, response.get_json()
    payload=response.get_json()
    assert time.monotonic()-started < 3
    assert payload['rows']
    assert 0 < len(payload['recent_operation_tokens']) <= 20
    assert any(row['entry_mc_usd'] is not None for row in payload['recent_operation_tokens'])
    assert any(row['peak_mc_usd'] is not None for row in payload['recent_operation_tokens'])
    assert {row['display_name'].lower() for row in payload['operation_summaries']} <= {'watchtower','byzantine','nexus'}
    assert 'src.ops.operation_monitor_worker' not in sys.modules
    assert sum(row['operation_id']=='watchtower' for row in payload['rows'])==8
    assert sum(row['operation_id']=='byzantine' for row in payload['rows'])==12
    assert sum(bool(row.get('byzantine_actual_entry_enrichment')) for row in payload['rows'])==9
    # Current membership selection is not the historical 17-mint enrichment
    # fixture. Never widen eligibility just to make missing prices appear.
    assert all(row.get('entry_mc_usd') is None for row in payload['rows'] if row['operation_id']=='watchtower')
    print('RETAINED_API', json.dumps({'ms':round((time.monotonic()-started)*1000,2),
          'rows':len(payload['rows']), 'history':len(payload['recent_operation_tokens']),
          'priced_history':sum(row['entry_mc_usd'] is not None for row in payload['recent_operation_tokens']),
          'universal_status':payload['universal_monitor']['status']}))

def test_unavailable_universal_evidence_preserves_retained_rows(monkeypatch):
    from src.ops import operator_routes as routes
    payload={'now':1792100000,'rows':[dict(mint='unavailable',operation_id='watchtower',assignment_timestamp=1)]}
    monkeypatch.setattr(routes,'_monitor_live_projection',lambda:dict(payload))
    def denied():
        raise routes.MissionControlMonitorAuthorityError('evidence unavailable')
    monkeypatch.setattr(routes,'_mission_control_monitor_read_connection',denied)
    app=Flask(__name__)
    app.register_blueprint(routes.operator_bp)
    with app.test_client() as client:
        response=client.get('/api/operations/live-monitor')
    assert response.status_code==200
    result=response.get_json()
    assert result['rows']==payload['rows']
    assert result['universal_monitor']==dict(status='UNAVAILABLE', reason='evidence unavailable',work=None,price_facts=None,budget=None)
    assert result['recent_operation_tokens'][0]['entry_mc_usd'] is None

def test_research_hashes_unchanged():
    manifest=json.loads((ROOT/'docs/audits/dev037_rich_source_composition.v1.json').read_text())
    paths={
      'dev019':'/private/tmp/dev019-watchtower-opening-mc/journal.db',
      'cohort539':'/private/tmp/dev020-watchtower-prepeak/journal.db',
      'dev028':'/private/tmp/dev028-watchtower-sharp-exit/corrected-continuation-journal.json',
      'dev031':'/private/tmp/dev031-crash-level/journal.json',
      'dev032':'/private/tmp/flex-dev032-crash-mc-relationship/docs/audits/dev032_watchtower_crash_mc_relationship.md',
      'dev033_candles2860':'/private/tmp/dev033-chronological-pilot/journal.json',
      'backtest':'/private/tmp/dev033-backtest-summary.json',
    }
    for key,path in paths.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==manifest['research_hashes'][key]

def test_retained_byzantine_actual_entry_and_ath():
    connection=sqlite3.connect(MONITOR)
    try:
        rows=connection.execute('SELECT entry_mc_usd,ath_mc_usd FROM byzantine_actual_entry_24h_ath_facts').fetchall()
        assert len(rows)==9
        assert all(entry is not None and ath is not None for entry,ath in rows)
    finally:
        connection.close()

@pytest.mark.parametrize('enabled', [False, True])
def test_original_and_adapter_queue_projection_equivalence(monkeypatch, enabled):
    from src.ops import operator_routes as routes
    from src.core import db
    monkeypatch.setattr(db, 'OPS_DB_PATH', str(OPS))
    monkeypatch.setattr(db, 'DB_PATH', '/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db')
    root=MONITOR.parent/'queue'
    monkeypatch.setenv('MISSION_CONTROL_MONITOR_UI_DB_PATH', str(MONITOR))
    monkeypatch.setenv('WATCHTOWER_MONITOR_QUEUE_PATH', str(root))
    monkeypatch.setenv('OPERATION_MONITOR_QUEUE_PATH', str(root))
    monkeypatch.setenv('OPERATIONS_MODE', 'MONITOR' if enabled else 'OFF')
    monkeypatch.setenv('MONITOR_INFRASTRUCTURE_ENABLED', 'false')
    monkeypatch.setattr(routes, '_store', None)
    monkeypatch.setattr(time, 'time', lambda: 1792100000)
    text=subprocess.check_output(['git','show','5c81ed8f:src/ops/operator_routes.py'],cwd=ROOT,text=True)
    original=next(node for node in ast.parse(text).body if isinstance(node,ast.FunctionDef) and node.name=='_monitor_live_projection')
    # Supply only the old queue read interface, never import/construct a worker.
    original.body=[node for node in original.body if not (isinstance(node,ast.ImportFrom) and node.module=='src.ops.operation_monitor_worker')]
    queue=SimpleNamespace(root=root, enabled=enabled,
        depth=lambda: {state:sum(1 for _ in (root/state).glob('*.json')) if enabled else 0
                       for state in routes._MONITOR_QUEUE_STATES})
    context=dict(vars(routes), production_queue=lambda: SimpleNamespace(queue=queue))
    exec(compile(ast.Module(body=[original],type_ignores=[]),'<preserved-read-contract>','exec'),context)
    before=context['_monitor_live_projection']()
    after=routes._monitor_live_projection()
    assert before==after

def test_missing_queue_authority_is_unavailable_not_empty(monkeypatch):
    from src.ops import operator_routes as routes
    monkeypatch.delenv('WATCHTOWER_MONITOR_QUEUE_PATH', raising=False)
    with pytest.raises(routes.MissionControlMonitorAuthorityError, match='PATH_REQUIRED'):
        routes._monitor_queue_root()

def test_ui_deadline_executes_without_server():
    source=(ROOT/'templates/operators_index.html').read_text()
    helper=source.split('<script>',1)[1].split('</script>',1)[0]
    program="""
const vm=require('node:vm'),assert=require('node:assert/strict');
let pending=[], timers=[], listeners={};
const context={AbortController, Response, Map, Error, Promise,
 setTimeout:(fn,ms)=>{assert.equal(ms,3000);timers.push(fn);return fn},clearTimeout:()=>{},
 fetch:(url,options)=>new Promise(resolve=>pending.push({resolve,options})),
 window:{addEventListener:(event,fn)=>listeners[event]=fn}};
vm.runInNewContext(HELPER,context);
(async()=>{
 const request=context.window.flexRegistryRequest;
 const hung=request('/hung').catch(e=>e.message);timers.at(-1)();
 assert.match(await hung,/UNAVAILABLE/);assert(pending.at(-1).options.signal.aborted);
 const old=request('/same').catch(e=>e.message);const prior=pending.at(-1);
 const fresh=request('/same');pending.at(-1).resolve(new Response('{"new":true}'));
 assert.equal((await (await fresh).json()).new,true);
 prior.resolve(new Response('{"old":true}'));assert.match(await old,/superseded/);
 const body=request('/body').catch(e=>e.message);
 pending.at(-1).resolve({text:()=>new Promise(()=>{})});
 timers.at(-1)();assert.match(await body,/UNAVAILABLE/);
 const closing=request('/closing').catch(e=>e.message);listeners.pagehide();
 assert.match(await closing,/superseded/);
})().catch(e=>{console.error(e);process.exitCode=1});
""".replace('HELPER',json.dumps(helper))
    subprocess.run(['node','-e',program],check=True,timeout=5)
    scripts=source.split('<script>')[1:]
    subprocess.run(['node','-e',"const vm=require('node:vm');for(const s of JSON.parse(process.argv[1]))new vm.Script(s)",
                    json.dumps([script.split('</script>',1)[0] for script in scripts])],check=True,timeout=5)
    assert 'setTimeout(render,500)' not in source
    assert 'retainedViews' not in source
    assert 'flex-monitor-ready' in source

def test_safety_health_and_shell_bytes_preserved():
    for path in ['src/dev_work_registry.py','templates/system_health_dashboard.html',
                 'templates/partials/sidebar.html','templates/ops_shell_index.html']:
        assert (ROOT/path).read_bytes()==subprocess.check_output(['git','show','d10a690e:'+path],cwd=ROOT)

@pytest.mark.parametrize('qualification', [False, True])
@pytest.mark.parametrize('explicit', [None, '/incorrect/queue'])
def test_wrapper_queue_binding_and_adapter_validation(monkeypatch, qualification, explicit):
    from src.ops import operator_routes as routes
    root=MONITOR.parent/'queue'
    monkeypatch.setenv('WATCHTOWER_MONITOR_QUEUE_PATH',str(root))
    if explicit:
        monkeypatch.setenv('OPERATION_MONITOR_QUEUE_PATH',explicit)
    else:
        monkeypatch.delenv('OPERATION_MONITOR_QUEUE_PATH',raising=False)
    tree=ast.parse((ROOT/'src/dev_runtime.py').read_text())
    update=next(n.value for n in tree.body if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call)
                and isinstance(n.value.func,ast.Attribute) and n.value.func.attr=='update')
    expression=next(value for key,value in zip(update.args[0].keys,update.args[0].values)
                    if isinstance(key,ast.Constant) and key.value=='OPERATION_MONITOR_QUEUE_PATH')
    import os
    effective=eval(compile(ast.Expression(expression),'<wrapper-binding>','eval'),
                   dict(os=os,READ_ONLY_QUALIFICATION=qualification,DEV_ROOT=Path('/disposable')))
    expected=(explicit or str(root)) if qualification else '/disposable/monitor/queue'
    assert effective==expected
    monkeypatch.setenv('OPERATION_MONITOR_QUEUE_PATH',effective)
    if qualification and explicit is None:
        assert routes._monitor_queue_root()==root
    else:
        with pytest.raises(routes.MissionControlMonitorAuthorityError,match='MISMATCH'):
            routes._monitor_queue_root()

def test_only_wrapper_queue_configuration_changed():
    before=ast.parse(subprocess.check_output(['git','show','d22ac629:src/dev_runtime.py'],cwd=ROOT,text=True))
    after=ast.parse((ROOT/'src/dev_runtime.py').read_text())
    def normalize(tree):
        update=next(n.value for n in tree.body if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call)
                    and isinstance(n.value.func,ast.Attribute) and n.value.func.attr=='update')
        index=next(i for i,key in enumerate(update.args[0].keys) if key.value=='OPERATION_MONITOR_QUEUE_PATH')
        update.args[0].values[index]=ast.Constant('QUALIFIED_QUEUE_BINDING')
        return ast.dump(tree)
    assert normalize(before)==normalize(after)
