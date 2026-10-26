import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import socket
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
P=load('create_audit_prototype',ROOT/'docs/dev/v2_create_audit_authority/prototype.py')
ACCEPTED=load('accepted_create_delivery_audit_fixture',ROOT/'runtime/listener/src/ops/walkback_create_handoff.py')

def b58(data):
    abc='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';n=int.from_bytes(data,'big');s=''
    while n:n,r=divmod(n,58);s=abc[r]+s
    return '1'*(len(data)-len(data.lstrip(b'\0')))+s

def proof(i=0,source='PUMPPORTAL'):
    mint=b58(hashlib.sha256(('mint'+str(i)).encode()).digest());creator=b58(hashlib.sha256(('creator'+str(i)).encode()).digest());sig=b58(hashlib.sha512(('create'+str(i)).encode()).digest())
    return ACCEPTED.Proof(mint,creator,sig,source,'PUMPPORTAL_NEW_TOKEN' if source=='PUMPPORTAL' else 'DEV074_STRICT_CREATE',sig,100)

@pytest.fixture
def stores(tmp_path,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('NETWORK_FORBIDDEN')
    monkeypatch.setattr(socket.socket,'connect',forbidden);monkeypatch.setattr(socket.socket,'bind',forbidden);monkeypatch.setattr(socket,'create_connection',forbidden)
    old=resource.getrlimit(resource.RLIMIT_FSIZE);resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,old[1]))
    core=tmp_path/'core.sqlite';ops=tmp_path/'ops.sqlite'
    c=sqlite3.connect(core);c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE token_analysis(mint TEXT UNIQUE PRIMARY KEY,migrated_at INTEGER,migration_tx TEXT)');c.commit();P.install_index(c)
    o=sqlite3.connect(ops);o.execute('PRAGMA journal_mode=WAL');o.execute('CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY,creator TEXT,status TEXT,path_state TEXT,create_anchor_signature TEXT,enqueued_at INTEGER,create_anchor_slot INTEGER,create_anchor_block_time INTEGER,create_anchor_source TEXT,create_anchor_audit_state TEXT)');o.commit();P.initialize_ops(o,'fixture-v2')
    yield c,o,core,ops
    c.close();o.close();assert all(x.stat().st_size<500000000 for x in tmp_path.iterdir() if x.is_file());resource.setrlimit(resource.RLIMIT_FSIZE,old)

def queue(conn,p):
    conn.execute("INSERT INTO wt_walkback_queue(mint,creator,status,path_state,enqueued_at) VALUES(?,?,'waiting','WAITING_FOR_CREATE_ANCHOR',100)",(p.mint,p.creator))

def handoff(b,p):return b.record('HANDOFF',p.mint,'migration-'+str(p.mint),{'migrated_at':100})
def count(o,t):return o.execute('SELECT count(*) FROM '+t).fetchone()[0]

def test_all_core_index_not_operational_proxy(stores):
    c,o,_,_=stores;data=[(proof(i).mint,1,None) for i in range(1000)]+[(proof(2000+i).mint,100+i,None) for i in range(20)];c.executemany('INSERT INTO token_analysis VALUES(?,?,?)',data);c.commit();P.install_index(c)
    got=P.capture_core(c,100,120);assert got['complete'] and len(got['rows'])==20;assert set(got['rows'])=={(r[0],r[1]) for r in data if 100<=r[1]<120};assert all('SCAN token_analysis' not in p[3] for p in got['plan']);assert any('COVERING INDEX' in p[3] for p in got['plan'])

def test_bad_index_fails_closed(stores):
    c,*_=stores;c.execute('DROP INDEX '+P.INDEX);c.execute('CREATE INDEX '+P.INDEX+' ON token_analysis(mint)')
    with pytest.raises(P.Hold,match='INCOMPATIBLE_INDEX'):P.install_index(c)
    with pytest.raises(P.Hold,match='INDEX_REQUIRED'):P.capture_core(c,0,200)

def test_capture_no_partial_publication_over_bound(stores):
    c,*_=stores;c.executemany('INSERT INTO token_analysis VALUES(?,?,?)',[(proof(i).mint,100,None) for i in range(4)]);c.commit()
    with pytest.raises(P.Hold,match='CAPTURE_CAPACITY'):P.capture_core(c,0,200,max_rows=3)
    assert not c.in_transaction

def test_concurrent_migration_snapshot_and_restart(stores):
    c,o,path,_=stores;data=[(proof(i).mint,100,None) for i in range(129)];c.executemany('INSERT INTO token_analysis VALUES(?,?,?)',data);c.commit();writer=sqlite3.connect(path);called=[]
    def concurrent():
        if not called:
            writer.execute('INSERT INTO token_analysis VALUES(?,?,?)',(proof(500).mint,101,None));writer.commit();called.append(True)
    first=P.capture_core(c,0,200,between_batches=concurrent);assert len(first['rows'])==129
    replacement=sqlite3.connect(path);assert len(P.capture_core(replacement,0,200)['rows'])==130;replacement.close();writer.close()

def test_interrupted_capture_restarts_whole_window(stores):
    c,*_=stores;c.executemany('INSERT INTO token_analysis VALUES(?,?,?)',[(proof(i).mint,100,None) for i in range(129)]);c.commit()
    def crash():raise RuntimeError('capture-crash')
    with pytest.raises(RuntimeError,match='capture-crash'):P.capture_core(c,0,200,between_batches=crash)
    assert not c.in_transaction;assert len(P.capture_core(c,0,200)['rows'])==129

@pytest.mark.parametrize('when',['before','at','after'])
def test_exact_handoff_commit_order(stores,when):
    _,o,_,_=stores;p=proof();queue(o,p);o.commit()
    if when=='before':
        with P.dev_transaction(o,'fixture-v2') as b:b.activate(p,0,ACCEPTED._activate)
        with P.dev_transaction(o,'fixture-v2') as b:handoff(b,p)
    elif when=='at':
        with P.dev_transaction(o,'fixture-v2') as b:handoff(b,p);b.activate(p,0,ACCEPTED._activate)
    else:
        with P.dev_transaction(o,'fixture-v2') as b:handoff(b,p)
        with P.dev_transaction(o,'fixture-v2') as b:b.activate(p,0,ACCEPTED._activate)
    assert P.availability(o,'fixture-v2',p.mint,'migration-'+p.mint,p.signature)=={'before':'BEFORE_HANDOFF','at':'AT_HANDOFF','after':'AFTER_HANDOFF'}[when]
    assert not o.execute('PRAGMA foreign_key_check').fetchall()

def test_legacy_already_committed_never_backfilled(stores):
    _,o,_,_=stores;p=proof();queue(o,p);ACCEPTED._activate(o,p,0);o.commit();before=o.execute('SELECT * FROM wt_walkback_queue').fetchall()
    with P.dev_transaction(o,'fixture-v2') as b:assert b.activate(p,0,ACCEPTED._activate)=='LEGACY_UNTIMED'
    assert count(o,'create_audit_events_v1')==0;assert count(o,'create_audit_commits_v1')==0;assert o.execute('SELECT * FROM wt_walkback_queue').fetchall()==before

def test_already_committed_preserves_actual_first_source(stores):
    _,o,_,_=stores;rpc=proof(source='STRICT_RPC');queue(o,rpc);o.commit()
    with P.dev_transaction(o,'fixture-v2') as b:b.activate(rpc,0,ACCEPTED._activate)
    payload=o.execute('SELECT payload FROM create_audit_events_v1').fetchone()[0]
    for _ in range(3):
        with P.dev_transaction(o,'fixture-v2') as b:assert b.activate(proof(),0,ACCEPTED._activate)=='ALREADY_COMMITTED'
    assert count(o,'create_audit_events_v1')==1;assert o.execute('SELECT payload FROM create_audit_events_v1').fetchone()[0]==payload;assert json.loads(payload)['proof']['source']=='STRICT_RPC'

@pytest.mark.parametrize('point',['before_commit','after_commit_before_observation'])
def test_crash_before_ack_preserves_atomicity(stores,point):
    _,o,_,path=stores;p=proof();queue(o,p);o.commit()
    def crash(phase):
        if phase==point:raise RuntimeError('simulated-crash')
    with pytest.raises(RuntimeError,match='simulated-crash'):
        with P.dev_transaction(o,'fixture-v2',fault=crash) as b:b.activate(p,0,ACCEPTED._activate)
    r=sqlite3.connect(path)
    if point=='before_commit':
        assert r.execute('SELECT create_anchor_signature FROM wt_walkback_queue').fetchone()[0] is None;assert count(r,'create_audit_events_v1')==0
    else:
        assert r.execute('SELECT create_anchor_signature FROM wt_walkback_queue').fetchone()[0]==p.signature;assert count(r,'create_audit_events_v1')==1;assert P.utc_bounds(r,b.order)['upper_observed_ns'] is None
        with P.dev_transaction(r,'fixture-v2') as retry:assert retry.activate(p,0,ACCEPTED._activate)=='ALREADY_COMMITTED'
        P.observe_commit(r,b.order,recovered=True);bounds=P.utc_bounds(r,b.order);assert bounds['observation']=='RECOVERED_READBACK';assert bounds['upper_observed_ns']>=bounds['lower_staged_ns'];assert count(r,'create_audit_events_v1')==1
    r.close()

def test_no_uncommitted_anchor_or_receipt_visible(stores):
    _,o,_,path=stores;p=proof();queue(o,p);o.commit();reader=sqlite3.connect(path)
    with P.dev_transaction(o,'fixture-v2') as b:
        b.activate(p,0,ACCEPTED._activate);assert reader.execute('SELECT create_anchor_signature FROM wt_walkback_queue').fetchone()[0] is None;assert count(reader,'create_audit_events_v1')==0
        with pytest.raises(P.Hold,match='OBSERVATION_BEFORE_COMMIT'):P.observe_commit(o,b.order)
    assert count(reader,'create_audit_events_v1')==1;reader.close()

def test_concurrent_ops_writers_total_commit_order(stores):
    _,o,_,path=stores
    for i in (1,2):queue(o,proof(i))
    o.commit();gate=threading.Barrier(2)
    def write(i):
        c=sqlite3.connect(path,timeout=2);c.execute('PRAGMA foreign_keys=ON');gate.wait()
        with P.dev_transaction(c,'fixture-v2') as b:handoff(b,proof(i));b.activate(proof(i),0,ACCEPTED._activate)
        c.close()
    with ThreadPoolExecutor(2) as ex:list(ex.map(write,(1,2)))
    assert count(o,'create_audit_events_v1')==4;assert count(o,'create_audit_commits_v1')==2
    for i in (1,2):assert P.availability(o,'fixture-v2',proof(i).mint,'migration-'+proof(i).mint,proof(i).signature)=='AT_HANDOFF'

def test_generation_schema_and_conflict_rejection(stores):
    _,o,_,_=stores;p=proof()
    with pytest.raises(P.Hold,match='GENERATION_MISMATCH'):P.initialize_ops(o,'wrong')
    P.initialize_ops(o,'fixture-v2')
    with P.dev_transaction(o,'fixture-v2') as b:handoff(b,p)
    with pytest.raises(P.Hold,match='CONFLICTING_AUDIT_EVIDENCE'):
        with P.dev_transaction(o,'fixture-v2') as b:b.record('HANDOFF',p.mint,'migration-'+p.mint,{'migrated_at':101})
    assert count(o,'create_audit_events_v1')==1

def test_audit_quota_rolls_back_canonical_change(stores):
    _,o,_,_=stores;p=proof();queue(o,p);o.execute('UPDATE create_audit_owner_v1 SET events=4096');o.commit()
    with pytest.raises(sqlite3.IntegrityError):
        with P.dev_transaction(o,'fixture-v2') as b:b.activate(p,0,ACCEPTED._activate)
    assert o.execute('SELECT create_anchor_signature FROM wt_walkback_queue').fetchone()[0] is None;assert count(o,'create_audit_events_v1')==0

def test_clock_reversal_never_claims_commit_instant(stores):
    _,o,_,_=stores;p=proof();queue(o,p);o.commit();clock=iter([100,50])
    with P.dev_transaction(o,'fixture-v2',clock=lambda:next(clock)) as b:b.activate(p,0,ACCEPTED._activate)
    assert P.utc_bounds(o,b.order)['quality']=='CLOCK_UNCERTAIN'

def test_bounded_storage_and_repeat_safe_handoff(stores):
    _,o,_,path=stores
    for start in range(0,1000,128):
        with P.dev_transaction(o,'fixture-v2') as b:
            for i in range(start,min(1000,start+128)):handoff(b,proof(i))
    bytes_used=o.execute('SELECT bytes FROM create_audit_owner_v1').fetchone()[0]
    with P.dev_transaction(o,'fixture-v2') as b:
        for i in range(1000):handoff(b,proof(i))
    assert count(o,'create_audit_events_v1')==1000;assert count(o,'create_audit_commits_v1')==8;assert o.execute('SELECT bytes FROM create_audit_owner_v1').fetchone()[0]==bytes_used;assert path.stat().st_size<4*1024*1024


def test_transaction_capacity_rollback(stores):
    _,o,_,_=stores
    with pytest.raises(P.Hold,match='TRANSACTION_CAPACITY'):
        with P.dev_transaction(o,'fixture-v2') as b:
            for i in range(129):handoff(b,proof(i))
    assert count(o,'create_audit_events_v1')==0


def test_unqualified_activation_record_rejected(stores):
    _,o,_,_=stores;p=proof()
    with pytest.raises(P.Hold,match='ACTIVATION_BOUNDARY_REQUIRED'):
        with P.dev_transaction(o,'fixture-v2') as b:b.record('ANCHOR',p.mint,p.signature,{'fabricated':True})
    assert count(o,'create_audit_events_v1')==0


def test_disk_and_wal_pressure_fail_closed(stores,monkeypatch):
    c,*_=stores
    class Space:f_bavail=1;f_frsize=4096
    monkeypatch.setattr(P.os,'statvfs',lambda *_:Space())
    with pytest.raises(P.Hold,match='DISK_FLOOR'):P.capture_core(c,0,200)


def test_populated_index_build_and_schema_repeat(stores):
    c,o,path,_=stores;c.execute('DROP INDEX '+P.INDEX);data=[(proof(i).mint,i,None) for i in range(2000)];c.executemany('INSERT INTO token_analysis VALUES(?,?,?)',data);c.commit();before=c.execute('PRAGMA page_count').fetchone()[0];P.install_index(c);c.commit();after=c.execute('PRAGMA page_count').fetchone()[0]
    assert after>before;assert path.stat().st_size<4*1024*1024;assert len(P.capture_core(c,1950,2000)['rows'])==50
    P.initialize_ops(o,'fixture-v2');P.initialize_ops(o,'fixture-v2');assert count(o,'create_audit_owner_v1')==1


def test_wal_pressure_refusal(stores,monkeypatch):
    c,_,path,_=stores;original=Path.stat
    def stat(p,*args,**kwargs):
        result=original(p,*args,**kwargs)
        if str(p)==str(path)+'-wal':
            import types
            return types.SimpleNamespace(st_size=48*1024*1024,st_mode=result.st_mode)
        return result
    monkeypatch.setattr(Path,'stat',stat)
    with pytest.raises(P.Hold,match='WAL_PRESSURE'):P.capture_core(c,0,200)


def test_payload_capacity_does_not_create_receipt(stores):
    _,o,_,_=stores;p=proof()
    with pytest.raises(P.Hold,match='PAYLOAD_CAPACITY'):
        with P.dev_transaction(o,'fixture-v2') as b:b.record('HANDOFF',p.mint,'migration',{'data':'x'*1024})
    assert count(o,'create_audit_events_v1')==0


@pytest.mark.parametrize('crash_point',['after_ops_commit','after_commit_before_observation'])
def test_accepted_dispatcher_commit_before_core_ack(stores,monkeypatch,crash_point):
    import contextlib
    _,o,_,ops_path=stores;p=proof();queue(o,p);o.commit()
    with P.dev_transaction(o,'fixture-v2') as b:handoff(b,p)
    core_path=ops_path.parent/'delivery-core.sqlite';c=sqlite3.connect(core_path)
    c.execute('CREATE TABLE token_analysis(mint TEXT PRIMARY KEY,earliest_tx_creator TEXT,pf_ws_creator TEXT,create_tx_signature TEXT,migrated_at INTEGER)');c.commit()
    c.execute('BEGIN');ACCEPTED.initialize(c,epoch='fixture-epoch',activated_at=1,core_base=core_path.stat().st_size,ops_base=ops_path.stat().st_size);c.execute('UPDATE wb_create_control SET enabled=1');c.execute('INSERT INTO token_analysis VALUES(?,?,?,?,?)',(p.mint,p.creator,p.creator,p.signature,100));ACCEPTED.stage(c,p,core_path=core_path,ops_path=ops_path,now=100);c.commit()
    @contextlib.contextmanager
    def read_core():
        r=sqlite3.connect(core_path.as_uri()+'?mode=ro',uri=True)
        try:yield r
        finally:r.close()
    def write_core(fn):
        with c:return fn(c)
    active={};accepted_activate=ACCEPTED._activate
    monkeypatch.setattr(ACCEPTED,'_activate',lambda conn,proof,start:active['boundary'].activate(proof,start,accepted_activate))
    class Crash(BaseException):pass
    crash_enabled=[True]
    def boundary_fault(phase):
        if crash_enabled[0] and crash_point==phase:raise Crash()
    def write_ops(fn):
        with P.dev_transaction(o,'fixture-v2',fault=boundary_fault) as b:
            active['boundary']=b
            result=fn(o)
        return result
    def dispatch_fault(phase):
        if crash_enabled[0] and crash_point==phase:raise Crash()
    args=dict(read_core=read_core,write_core=write_core,write_ops=write_ops,core_path=core_path,ops_path=ops_path,now=200,fault=dispatch_fault)
    with pytest.raises(Crash):ACCEPTED.dispatch(**args)
    assert c.execute('SELECT state FROM wb_create_delivery').fetchone()[0]=='PENDING'
    assert o.execute('SELECT create_anchor_signature FROM wt_walkback_queue').fetchone()[0]==p.signature
    original=o.execute("SELECT commit_order,payload FROM create_audit_events_v1 WHERE kind='ANCHOR'").fetchone();assert original
    crash_enabled[0]=False;assert ACCEPTED.dispatch(**args)==[(p.mint,'ALREADY_COMMITTED')]
    assert c.execute('SELECT state FROM wb_create_delivery').fetchone()[0]=='ACK'
    assert o.execute("SELECT commit_order,payload FROM create_audit_events_v1 WHERE kind='ANCHOR'").fetchone()==original
    assert json.loads(original[1])['proof']['source']=='PUMPPORTAL'
    assert P.availability(o,'fixture-v2',p.mint,'migration-'+p.mint,p.signature)=='AFTER_HANDOFF';c.close()


def test_fractional_utc_bounds_preserve_integer_canonical_membership(stores):
    c,*_=stores;c.executemany('INSERT INTO token_analysis VALUES(?,?,?)',[(proof(i).mint,stamp,None) for i,stamp in enumerate((100,101,102,103))]);c.commit()
    result=P.capture_core(c,100.25,102.75)
    assert [r[1] for r in result['rows']]==[101,102]
    assert result['integer_bounds']==(101,103)
    with pytest.raises(P.Hold,match='INVALID_CAPTURE'):P.capture_core(c,float('nan'),103)
