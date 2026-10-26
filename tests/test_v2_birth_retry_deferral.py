"""Provider-free legacy retry deferral; no new terminal disposition."""
import ast
import asyncio
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import sys
import types
import pytest
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py'

def load():
    p=ROOT/'runtime/listener/src/core/birth_retry_quarantine.py'
    sp=importlib.util.spec_from_file_location('birth_retry_deferral_fixture',p)
    m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m

@pytest.fixture
def case(tmp_path,monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('PROVIDER_FORBIDDEN')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(socket,'create_connection',forbidden)
    m=load();monkeypatch.setitem(sys.modules,'src.core.birth_retry_quarantine',m)
    core=tmp_path/'core.sqlite';ops=tmp_path/'ops.sqlite'
    with sqlite3.connect(core) as c:
        c.execute('CREATE TABLE token_analysis(mint TEXT PRIMARY KEY,earliest_tx_creator TEXT,pf_ws_creator TEXT,create_tx_signature TEXT,migrated_at INTEGER)')
        c.execute('CREATE TABLE webhook_birth_queue(id INTEGER PRIMARY KEY,signature TEXT,payload TEXT,source TEXT,consumed INTEGER,quarantine_reason TEXT,retry_count INTEGER)')
    with sqlite3.connect(ops) as c:
        c.execute('CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY,status TEXT,creator TEXT,create_anchor_signature TEXT,create_anchor_audit_state TEXT)')
    return types.SimpleNamespace(m=m,core=core,ops=ops)

def payload(i=0):
    return dict(_birth_retry_schema=1,provenance='PUMPPORTAL_NEW_TOKEN',mint=f'mint-{i}',signature=f'notification-{i}',traderPublicKey='creator',receive_utc_ns=1000000000,bondingCurveKey=None,symbol=None,name=None)

@pytest.mark.parametrize('kind',['conflict','same','missing','missing_signature','missing_creator','bad_source'])
def test_exact_conflict_gate(case,kind):
    d=payload();sig='canonical' if kind!='same' else d['signature'];creator=None if kind=='missing_creator' else 'creator'
    if kind=='bad_source':d['provenance']='OTHER'
    if kind=='missing_signature':sig=None
    with sqlite3.connect(case.core) as c:
        if kind!='missing':c.execute('INSERT INTO token_analysis VALUES(?,?,?,?,?)',(d['mint'],creator,creator,sig,100))
        before=c.total_changes
        assert case.m.defer_canonical_conflict(c,d)==(kind=='conflict')
        assert c.total_changes==before

def run_poll(case,monkeypatch):
    tree=ast.parse(SOURCE.read_text());fn=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='drain_webhook_birth_queue')
    class EndPoll(Exception):pass
    async def sleep(*a):raise EndPoll()
    logs=[]
    ns={'DB_PATH':str(case.core),'_listener_ops_db_path':lambda:str(case.ops),'_ensure_webhook_birth_queue_schema':lambda *_:None,'log_print':lambda *a,**k:logs.append(a),'_PP_BIRTH_RETRY_MAX_BYTES':4096,'_compact_birth_retry_storage_check':lambda *_:None,'_decode_compact_pumpportal_birth_retry':lambda d,s:d,'asyncio':types.SimpleNamespace(to_thread=asyncio.to_thread,sleep=sleep)}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(SOURCE),'exec'),ns)
    async def forbidden(*a,**kw):raise AssertionError('CANONICAL_WRITE_FORBIDDEN')
    worker=types.SimpleNamespace(completed_launches=set(),_insert_bonding_curve_token=forbidden)
    with pytest.raises(EndPoll):asyncio.run(ns['drain_webhook_birth_queue'](worker))
    return logs

def test_18_conflicts_repeated_poll_and_restart_preserve_exact_evidence(case,monkeypatch):
    with sqlite3.connect(case.core) as c:
        for i in range(18):
            d=payload(i);c.execute('INSERT INTO token_analysis VALUES(?,?,?,?,?)',(d['mint'],'creator','creator','canonical-'+str(i),100))
            c.execute('INSERT INTO webhook_birth_queue VALUES(?,?,?,?,?,?,?)',(i,d['signature'],json.dumps(d),'pumpportal_retry',0,None,65000+i))
        before=c.execute('SELECT * FROM webhook_birth_queue ORDER BY id').fetchall();canonical=c.execute('SELECT * FROM token_analysis ORDER BY mint').fetchall()
    for _ in range(3):
        logs=run_poll(case,monkeypatch)
        assert not any('RETRY_PENDING' in str(x) for x in logs)
        with sqlite3.connect(case.core) as c:
            assert c.execute('SELECT * FROM webhook_birth_queue ORDER BY id').fetchall()==before
            assert c.execute('SELECT * FROM token_analysis ORDER BY mint').fetchall()==canonical
    assert all(p.stat().st_size<500000000 for p in case.core.parent.iterdir())

def test_authorized_canonical_change_removes_deferral(case):
    d=payload()
    with sqlite3.connect(case.core) as c:
        c.execute('INSERT INTO token_analysis VALUES(?,?,?,?,?)',(d['mint'],'creator','creator','canonical',100))
        assert case.m.defer_canonical_conflict(c,d)
        c.execute('UPDATE token_analysis SET create_tx_signature=? WHERE mint=?',(d['signature'],d['mint']))
        assert not case.m.defer_canonical_conflict(c,d)

def test_existing_valid_anchor_quarantine_contract_preserved(case):
    d=payload();raw=json.dumps(d)
    with sqlite3.connect(case.core) as c,sqlite3.connect(case.ops) as o:
        c.execute('INSERT INTO token_analysis VALUES(?,?,?,?,?)',(d['mint'],'creator','creator','canonical',100))
        c.execute('INSERT INTO webhook_birth_queue VALUES(?,?,?,?,?,?,?)',(1,d['signature'],raw,'pumpportal_retry',0,None,3))
        o.execute('INSERT INTO wt_walkback_queue VALUES(?,?,?,?,?)',(d['mint'],'complete','creator','canonical','VALID'))
        assert case.m.quarantine(c,o,1,d['signature'],raw,d)
        assert c.execute('SELECT consumed,quarantine_reason,retry_count FROM webhook_birth_queue').fetchone()==(0,'CORE_AUTHORITY_CONFLICT',3)
