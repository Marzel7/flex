"""Provider-free DEV049 scheduling and unchanged evidence qualification contracts."""
import json
import sqlite3
import time
from unittest.mock import patch
import pytest
from src.ops import anchor_reconciliation as a, create_event_ledger as ledger


def fixture(path=":memory:", count=10, same_time=False):
    c = sqlite3.connect(str(path)); c.row_factory = sqlite3.Row
    c.executescript("""CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY,creator TEXT,status TEXT,path_state TEXT,create_anchor_signature TEXT,create_anchor_audit_state TEXT,attempts INTEGER DEFAULT 0,enqueued_at INTEGER NOT NULL,next_retry_at INTEGER,anchor_lookup_attempts INTEGER DEFAULT 0,last_anchor_lookup_at INTEGER,anchor_lookup_state TEXT,create_anchor_source TEXT,anchor_recovered_at INTEGER,anchor_recovery_source TEXT,updated_at INTEGER);
    CREATE INDEX ix_wbq_status ON wt_walkback_queue(status,enqueued_at);
    CREATE TABLE wt_scheduler_state(key TEXT PRIMARY KEY,value_json TEXT,updated_at INTEGER);
    CREATE TABLE wt_anchor_reconciliation_log(id INTEGER PRIMARY KEY,mint TEXT,creator TEXT,recovered_signature TEXT,original_state TEXT,recovery_source TEXT,recovery_timestamp INTEGER);
    """)
    c.executemany("INSERT INTO wt_walkback_queue(mint,creator,status,path_state,create_anchor_audit_state,enqueued_at,next_retry_at) VALUES(?,?,'waiting',?,'MISSING_OR_MALFORMED',?,?)", ((str(i),'creator',a.WAITING_PATH_STATE,1 if same_time else i,9999999999 if i%2 else None) for i in range(count)))
    c.commit();return c


def turn(c):
    with patch.object(a,'resolve_anchor_with_priority',return_value={'confidence':'NONE','source':None}),patch.object(a,'classify_stuck_row',return_value={'classification':'MINT_NOT_FOUND','signature':None,'source':None}):
        return a.reconcile_waiting_create_anchors(c,None,ensure_schema_first=False,limit=1)


def test_original_starvation():
    c=fixture();sql="SELECT mint FROM wt_walkback_queue WHERE status='waiting' AND path_state='WAITING_FOR_CREATE_ANCHOR' AND (create_anchor_signature IS NULL OR create_anchor_audit_state='MISSING_OR_MALFORMED') ORDER BY enqueued_at LIMIT 1"
    assert [c.execute(sql).fetchone()[0] for _ in range(100)] == ['0']*100


def test_realistic_population_and_restart(tmp_path):
    path=tmp_path/'waiting.db';c=fixture(path,50000)
    c.execute("UPDATE wt_walkback_queue SET path_state='OTHER' WHERE CAST(mint AS INTEGER)%10<>0");c.commit()
    seen=set();maximum=0;start=time.monotonic()
    for i in range(6000):
        t=time.monotonic();r=turn(c);maximum=max(maximum,time.monotonic()-t)
        assert r['examined']<=1
        seen.update(x['mint'] for x in r['skipped'])
        if i==2500:c.close();c=sqlite3.connect(path);c.row_factory=sqlite3.Row
        if len(seen)==5000:break
    assert len(seen)==5000 and i+1<=50001
    assert maximum<1.0
    assert c.execute('SELECT SUM(anchor_lookup_attempts),SUM(attempts) FROM wt_walkback_queue').fetchone()[:] == (5000,0)
    assert c.execute("SELECT COUNT(*) FROM wt_walkback_queue WHERE status<>'waiting'").fetchone()[0]==0
    assert len(c.execute('SELECT value_json FROM wt_scheduler_state').fetchone()[0])<256
    assert path.stat().st_size<8000000
    print('DEV049_METRICS',json.dumps({'waiting_rows':50000,'eligible_rows':5000,'turns':i+1,'max_turn_ms':maximum*1000,'total_seconds':time.monotonic()-start,'fixture_bytes':path.stat().st_size}))


def test_restart_failure_and_dryrun(tmp_path):
    path=tmp_path/'restart.db';c=fixture(path);assert turn(c)['skipped'][0]['mint']=='0';c.close();c=sqlite3.connect(path);c.row_factory=sqlite3.Row
    assert turn(c)['skipped'][0]['mint']=='1'
    before=c.execute('SELECT value_json FROM wt_scheduler_state').fetchone()[0]
    with patch.object(a,'resolve_anchor_with_priority',side_effect=RuntimeError('injected')):
        with pytest.raises(RuntimeError):a.reconcile_waiting_create_anchors(c,None,ensure_schema_first=False,limit=1)
    assert not c.in_transaction and c.execute('SELECT value_json FROM wt_scheduler_state').fetchone()[0]==before
    assert turn(c)['skipped'][0]['mint']=='2'
    before=c.execute('SELECT value_json FROM wt_scheduler_state').fetchone()[0]
    with patch.object(a,'resolve_anchor_with_priority',return_value={'confidence':'NONE'}),patch.object(a,'classify_stuck_row',return_value={'classification':'MINT_NOT_FOUND'}):
        a.reconcile_waiting_create_anchors(c,None,ensure_schema_first=False,limit=1,dry_run=True)
    assert c.execute('SELECT value_json FROM wt_scheduler_state').fetchone()[0]==before
    assert turn(c)['skipped'][0]['mint']=='3'


def test_serialization_and_no_duplicates(tmp_path):
    path=tmp_path/'locked.db';c=fixture(path);d=sqlite3.connect(path,timeout=0);d.row_factory=sqlite3.Row
    c.execute('BEGIN IMMEDIATE')
    with pytest.raises(sqlite3.OperationalError):turn(d)
    c.rollback();assert turn(d)['skipped'][0]['mint']=='0';assert turn(c)['skipped'][0]['mint']=='1'
    assert c.execute('SELECT SUM(anchor_lookup_attempts) FROM wt_walkback_queue').fetchone()[0]==2


def test_retry_backoff_terminal_equivalence():
    c=fixture(count=4)
    c.execute("UPDATE wt_walkback_queue SET attempts=99,next_retry_at=9999999999 WHERE mint='0'")
    c.execute("UPDATE wt_walkback_queue SET status='failed' WHERE mint='1'")
    c.execute("UPDATE wt_walkback_queue SET status='complete' WHERE mint='2'");c.commit()
    assert turn(c)['skipped'][0]['mint']=='0';assert turn(c)['skipped'][0]['mint']=='3'
    assert c.execute("SELECT attempts,next_retry_at FROM wt_walkback_queue WHERE mint='0'").fetchone()[:]==(99,9999999999)


def test_snapshot_bounds_arrivals_and_wrap():
    c=fixture(count=3);assert turn(c)['skipped'][0]['mint']=='0'
    for i in range(3,12):
        c.execute("INSERT INTO wt_walkback_queue(mint,status,path_state,create_anchor_audit_state,enqueued_at) VALUES(?,'waiting',?,'MISSING_OR_MALFORMED',?)",(str(i),a.WAITING_PATH_STATE,i));c.commit()
        r=turn(c)
        if i==3:assert r['skipped'][0]['mint']=='1'
        if i==4:assert r['skipped'][0]['mint']=='2'
        if i==5:assert r['skipped'][0]['mint']=='0'


def test_missing_schema_and_malformed_cursor_fail_closed():
    c=fixture();c.execute("INSERT INTO wt_scheduler_state VALUES(?, '{}', 0)",(a._CURSOR_KEY,));c.commit()
    with pytest.raises(ValueError):turn(c)
    assert c.execute('SELECT SUM(anchor_lookup_attempts) FROM wt_walkback_queue').fetchone()[0]==0
    c.execute('DROP TABLE wt_scheduler_state');c.commit()
    with pytest.raises(sqlite3.OperationalError):turn(c)


def test_valid_and_conflicting_evidence():
    c=fixture(count=2);live=sqlite3.connect(':memory:');live.row_factory=sqlite3.Row
    live.executescript('CREATE TABLE creator_funding_queue(mint TEXT,create_tx_signature TEXT,updated_at INTEGER);CREATE TABLE token_analysis(mint TEXT,create_tx_signature TEXT);')
    ledger.ensure_schema(c)
    signature='2'*88
    ledger.record_create_event(c,signature=signature,mint='0',creator='creator',slot=1,block_time=1,source='fixture',parser_path='fixture')
    result=a.reconcile_waiting_create_anchors(c,live,ensure_schema_first=False,limit=1)
    assert len(result['recovered'])==1
    assert c.execute("SELECT status,path_state,create_anchor_signature,attempts FROM wt_walkback_queue WHERE mint='0'").fetchone()[:]==('pending','CREATE_ANCHORED',signature,0)
    result=a.reconcile_waiting_create_anchors(c,live,ensure_schema_first=False,limit=1)
    assert not result['recovered'] and c.execute('SELECT COUNT(*) FROM wt_anchor_reconciliation_log').fetchone()[0]==1


def test_query_plan_and_timestamp_ties():
    c=fixture(count=100000,same_time=True)
    plan=[r[3] for r in c.execute("EXPLAIN QUERY PLAN SELECT rowid,mint FROM wt_walkback_queue INDEXED BY ix_wbq_status WHERE status=? AND enqueued_at=? AND rowid>? AND rowid<=? ORDER BY rowid LIMIT 64",('waiting',1,99900,100000))]
    assert not any('TEMP' in x or 'SCAN ' in x for x in plan)
    c.execute('BEGIN IMMEDIATE');state={'version':1,'after':[1,99900],'ceiling':[1,100000]};c.execute('INSERT INTO wt_scheduler_state VALUES(?,?,0)',(a._CURSOR_KEY,json.dumps(state)));c.commit()
    steps=[0]
    def progress():steps[0]+=100;return int(steps[0]>20000)
    c.set_progress_handler(progress,100)
    r=turn(c);assert r['skipped'][0]['mint']=='99900';print('DEV049_TIE_PLAN',plan,'steps',steps[0])


def test_uncommitted_crash_rolls_back_cursor_and_attempts(tmp_path):
    path=tmp_path/'crash.db';c=fixture(path)
    c.execute('BEGIN IMMEDIATE');rows=a._fair_stuck_rows(c,limit=1)
    c.execute('UPDATE wt_walkback_queue SET anchor_lookup_attempts=1 WHERE mint=?',(rows[0]['mint'],))
    c.close() # Crash-equivalent SQLite rollback before acknowledgment.
    c=sqlite3.connect(path);c.row_factory=sqlite3.Row
    assert turn(c)['skipped'][0]['mint']=='0'
    assert c.execute('SELECT SUM(anchor_lookup_attempts) FROM wt_walkback_queue').fetchone()[0]==1


def test_validation_body_unchanged_and_provider_budget():
    import ast,subprocess
    from pathlib import Path
    original=(Path(__file__).parents[1]/'docs/audits/dev049/original_anchor_reconciliation.py.txt').read_text()
    current=Path(a.__file__).read_text()
    old={n.name:ast.dump(n,include_attributes=False) for n in ast.parse(original).body if isinstance(n,ast.FunctionDef)}
    new={n.name:ast.dump(n,include_attributes=False) for n in ast.parse(current).body if isinstance(n,ast.FunctionDef)}
    for name in ['classify_stuck_row','resolve_anchor_with_priority','find_stored_create_anchor','apply_rpc_recovered_anchor','recheck_single_row_anchor']:
        assert old[name]==new[name]
    # No scheduler network transport is added; zero-RPC resolver call per examined row.
    c=fixture();calls=[]
    def missing(*args,**kwargs):calls.append(args[2]);return {'confidence':'NONE'}
    with patch.object(a,'resolve_anchor_with_priority',side_effect=missing),patch.object(a,'classify_stuck_row',return_value={'classification':'MINT_NOT_FOUND'}):
        for _ in range(10):assert a.reconcile_waiting_create_anchors(c,None,limit=1,ensure_schema_first=False)['examined']==1
    assert len(calls)==10 and len(set(calls))==10


def test_worker_call_compatibility():
    import inspect
    from src.core import walkback_worker
    source=inspect.getsource(walkback_worker)
    assert 'reconcile_waiting_create_anchors(ops, _live, ensure_schema_first=False, limit=1)' in source
    assert inspect.signature(a.reconcile_waiting_create_anchors).parameters['limit'].default is None


def test_invalid_missing_and_conflict_stay_unresolved():
    c=fixture(count=3);live=sqlite3.connect(':memory:');live.row_factory=sqlite3.Row
    live.executescript('CREATE TABLE creator_funding_queue(mint TEXT,create_tx_signature TEXT,updated_at INTEGER);CREATE TABLE token_analysis(mint TEXT,create_tx_signature TEXT);')
    ledger.ensure_schema(c)
    live.execute("INSERT INTO creator_funding_queue VALUES('0','invalid',0)");live.commit()
    with patch.object(a,'resolve_anchor_with_priority',return_value={'confidence':'NONE'}):
        result=a.reconcile_waiting_create_anchors(c,live,ensure_schema_first=False,limit=1)
    assert result['skipped'][0]['classification']=='ANCHOR_PRESENT_INVALID'
    with patch.object(a,'resolve_anchor_with_priority',return_value={'confidence':'CONFLICT'}):
        result=a.reconcile_waiting_create_anchors(c,live,ensure_schema_first=False,limit=1)
    assert result['skipped'][0]['classification']=='AMBIGUOUS_MULTIPLE_ROWS'
    result=a.reconcile_waiting_create_anchors(c,live,ensure_schema_first=False,limit=1)
    assert not result['recovered']
    assert c.execute("SELECT COUNT(*) FROM wt_walkback_queue WHERE status<>'waiting' OR create_anchor_signature IS NOT NULL").fetchone()[0]==0
    assert c.execute('SELECT COUNT(*) FROM wt_anchor_reconciliation_log').fetchone()[0]==0
