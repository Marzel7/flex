import json
import sqlite3

import pytest

from src.ops.universal_live_history_projection import (ensure_live_history_schema, projection_state,
    reconcile_live_to_history, record_nonlive_opening)


def ready(operation='watchtower', entry_at=100, work_state='PENDING', financial=True):
    db=sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE universal_monitor_adoptions(adoption_id TEXT,operation_id TEXT,mint TEXT,snapshot_json TEXT);
    CREATE TABLE universal_monitor_global_work(adoption_id TEXT,work_type TEXT,state TEXT,due_at INTEGER);
    CREATE TABLE universal_monitor_price_facts(operation_id TEXT,mint TEXT,checkpoint_boundary INTEGER,running_peak_mc_usd REAL,running_peak_timestamp INTEGER,max_multiple REAL,time_to_peak_seconds INTEGER,current_mc_usd REAL);''')
    snapshot={'entry':{'qualified':True,'timestamp':entry_at,'mc_usd':10,'provenance':'FIXTURE'},'terminal':False}
    db.execute('INSERT INTO universal_monitor_adoptions VALUES(?,?,?,?)',('a',operation,'m',json.dumps(snapshot)))
    db.execute('INSERT INTO universal_monitor_global_work VALUES(?,?,?,?)',('a','ACQUIRE_PRICE_WINDOW',work_state,999999))
    if financial: db.execute('INSERT INTO universal_monitor_price_facts VALUES(?,?,?,?,?,?,?,?)',(operation,'m',900,99,480,9.9,380,20))
    db.commit(); return db


@pytest.mark.parametrize('operation',['watchtower','byzantine','future_operation'])
def test_terminal_history_is_identity_invariant(operation):
    db=ready(operation,work_state='TERMINAL'); out=reconcile_live_to_history(db,now=1000)
    assert len(out)==1 and out[0]['state']=='TERMINAL' and projection_state(db,operation_id=operation,mint='m')=='HISTORY'
    row=db.execute('SELECT opening_mc_usd,peak_mc_usd,max_multiple,time_to_peak_seconds FROM universal_monitor_lifecycle_completion').fetchone()
    assert row==(10,99,9.9,380)
    assert db.execute("SELECT state FROM universal_monitor_global_work").fetchone()[0]=='TERMINAL'


@pytest.mark.parametrize('operation',['watchtower','byzantine','future_operation'])
def test_horizon_history_is_identity_invariant(operation):
    db=ready(operation); assert reconcile_live_to_history(db,now=100+86400)[0]['state']=='24H_COMPLETE'
    assert projection_state(db,operation_id=operation,mint='m')=='HISTORY'


def test_fast_terminal_keeps_null_financials_and_is_idempotent():
    db=ready(work_state='TERMINAL',financial=False)
    first=reconcile_live_to_history(db,now=101); second=reconcile_live_to_history(db,now=102)
    assert len(first)==1 and second==[]
    assert db.execute('SELECT peak_mc_usd,max_multiple FROM universal_monitor_lifecycle_completion').fetchone()==(None,None)


def test_failed_opening_is_durable_nonlive_not_live():
    db=sqlite3.connect(':memory:'); record_nonlive_opening(db,operation_id='watchtower',mint='missing',assignment_identity='assignment',state='OPENING_PENDING',now=1,detail='Entry unavailable')
    assert projection_state(db,operation_id='watchtower',mint='missing')=='OPENING_PENDING'


def test_restart_reconcile_is_idempotent(tmp_path):
    path=tmp_path/'history.db'; db=ready(work_state='TERMINAL'); disk=sqlite3.connect(path); db.backup(disk); disk.close(); db.close()
    first=sqlite3.connect(path); reconcile_live_to_history(first,now=1000); first.close()
    second=sqlite3.connect(path); assert reconcile_live_to_history(second,now=1001)==[]
    assert second.execute('SELECT count(*) FROM universal_monitor_lifecycle_completion').fetchone()[0]==1
