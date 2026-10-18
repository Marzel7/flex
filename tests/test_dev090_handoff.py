"""Compact provider-free actual handoff code tests; no operational worker loop."""
import ast
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import pytest
from src.ops import walkback_create_handoff as h

def encoded(byte,size=32):
    alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    value=int.from_bytes(bytes([byte])*size,'big');out=''
    while value:value,index=divmod(value,58);out=alphabet[index]+out
    return out
M=encoded(109)
C=encoded(99)
S=encoded(115,64)

class Crash(BaseException):pass

class Fixture:
    def __init__(self,path,create=True):
        self.root=Path(path);self.root.mkdir(exist_ok=True,parents=True)
        self.core=self.root/'core.db';self.ops=self.root/'ops.db';self.events=[];self.lane=threading.RLock()
        if create:
            c=sqlite3.connect(self.core)
            c.executescript('CREATE TABLE token_analysis(mint TEXT PRIMARY KEY,earliest_tx_creator TEXT,pf_ws_creator TEXT,create_tx_signature TEXT,migrated_at INTEGER);CREATE TABLE birth_persist_queue(mint TEXT PRIMARY KEY);')
            c.execute('BEGIN IMMEDIATE')
            h.initialize(c,epoch='fixture-only',activated_at=100,core_base=0,ops_base=0)
            c.execute('UPDATE wb_create_control SET enabled=1');c.commit();c.close()
            o=sqlite3.connect(self.ops);o.executescript('''CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY,creator TEXT,status TEXT,path_state TEXT,create_anchor_signature TEXT,enqueued_at INTEGER,create_anchor_slot INTEGER,create_anchor_block_time INTEGER,create_anchor_source TEXT,create_anchor_audit_state TEXT,attempts INTEGER DEFAULT 0,executions INTEGER DEFAULT 0);
CREATE INDEX ix_wbq_status ON wt_walkback_queue(status,enqueued_at);''');o.close()
    @contextmanager
    def read(self,path):
        c=sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True);c.execute('PRAGMA query_only=ON')
        try:yield c
        finally:c.close()
    def write(self,path,fn,before_commit=None):
        with self.lane:
            self.events.append('lane')
            c=sqlite3.connect(path);c.execute('PRAGMA max_page_count=1024');c.execute('PRAGMA synchronous=FULL')
            try:
                self.events.append('sqlite_begin');c.execute('BEGIN IMMEDIATE');result=fn(c)
                if before_commit:before_commit()
                c.commit();self.events.append('commit');return result
            except BaseException:c.rollback();raise
            finally:c.close()
    def add_waiting(self,mint=M,creator=None,enqueued=101):
        self.write(self.ops,lambda c:c.execute("INSERT INTO wt_walkback_queue(mint,creator,status,path_state,enqueued_at) VALUES(?,?,'waiting','WAITING_FOR_CREATE_ANCHOR',?)",(mint,creator,enqueued)))
    def proof(self,mint=M,creator=C,source='STRICT_RPC',migrated_at=101):
        return h.Proof(mint,creator,S,source,'DEV074_STRICT_CREATE' if source=='STRICT_RPC' else 'PUMPPORTAL_NEW_TOKEN',S,migrated_at)
    def publish(self,proof=None,stop=None):
        p=proof or self.proof()
        def action(c):
            c.execute('INSERT OR REPLACE INTO token_analysis VALUES(?,?,?,?,?)',(p.mint,p.creator,p.creator,p.signature,p.migrated_at))
            result=h.stage(c,p,core_path=self.core,ops_path=self.ops,now=200)
            if stop:stop()
            return result
        return self.write(self.core,action)
    def dispatch(self,now=200,fault=lambda x:None,ops_before_commit=None,core_before_commit=None):
        return h.dispatch(read_core=lambda:self.read(self.core),write_core=lambda fn:self.write(self.core,fn,core_before_commit),write_ops=lambda fn:self.write(self.ops,fn,ops_before_commit),core_path=self.core,ops_path=self.ops,now=now,fault=fault)
    def row(self,path,sql):
        with self.read(path) as c:return c.execute(sql).fetchone()
    def execute_claim_model(self):
        # Only models committed state CAS, NOT real funding traversal/execution.
        self.write(self.ops,lambda c:c.execute("UPDATE wt_walkback_queue SET status='complete',executions=executions+1,attempts=attempts+1 WHERE status='pending'"))
        return self.row(self.ops,'SELECT SUM(executions) FROM wt_walkback_queue')[0]

@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    def deny(*a,**k):raise AssertionError('PROVIDER FORBIDDEN')
    monkeypatch.setattr(socket.socket,'connect',deny)

@pytest.fixture
def f(tmp_path):return Fixture(tmp_path)

@pytest.mark.parametrize('source',['STRICT_RPC','PUMPPORTAL'])
@pytest.mark.parametrize('order',['migration-first','create-first'])
def test_two_sources_both_orders(f,source,order):
    if order=='migration-first':f.add_waiting()
    f.publish(f.proof(source=source))
    if order=='create-first':
        assert f.dispatch()[0][1]=='OPS_ROW_MISSING'
        f.add_waiting()
    f.dispatch(now=500)
    assert f.row(f.ops,'SELECT status,creator,create_anchor_signature,attempts FROM wt_walkback_queue')==('pending',C,S,0)
    assert f.row(f.core,'SELECT state FROM wb_create_delivery')==('ACK',)
    assert f.execute_claim_model()==1
    f.dispatch(now=1000);assert f.execute_claim_model()==1


def test_atomic_authority_rollback_before_commit(f):
    with pytest.raises(Crash):f.publish(stop=lambda:(_ for _ in ()).throw(Crash()))
    assert f.row(f.core,'SELECT COUNT(*) FROM token_analysis')==(0,)
    assert f.row(f.core,'SELECT COUNT(*) FROM wb_create_delivery')==(0,)

@pytest.mark.parametrize('point',['before_ops','during_ops','after_ops_commit','before_ack','after_ack'])
def test_interrupt_and_restart(f,point):
    f.add_waiting();f.publish()
    def crash(stage):
        if stage==point:raise Crash()
    with pytest.raises(Crash):f.dispatch(fault=crash,ops_before_commit=(lambda:crash('during_ops')))
    restarted=Fixture(f.root,create=False);restarted.dispatch(now=500)
    assert restarted.row(restarted.core,'SELECT state FROM wb_create_delivery')==('ACK',)
    assert restarted.execute_claim_model()==1
    restarted.dispatch(now=1000);assert restarted.execute_claim_model()==1


def test_duplicate_proof_and_concurrent_dispatch(f):
    f.add_waiting();assert f.publish()=='PENDING';assert f.publish()=='DUPLICATE'
    threads=[threading.Thread(target=f.dispatch) for _ in range(2)]
    for t in threads:t.start()
    for t in threads:t.join()
    assert f.execute_claim_model()==1
    assert f.row(f.core,'SELECT pending_count,ack_count,admitted FROM wb_create_control')==(0,1,1)

@pytest.mark.parametrize('change',[dict(creator=encoded(100)),dict(signature=encoded(116,64),before_signature=encoded(116,64))])
def test_conflicting_proof_rolls_back(f,change):
    f.publish()
    with pytest.raises(h.Unavailable,match='CONFLICTING_PROOF'):f.publish(replace(f.proof(),**change))
    assert f.row(f.core,'SELECT earliest_tx_creator,create_tx_signature FROM token_analysis')==(C,S)

@pytest.mark.parametrize('data',[dict(creator=C,is_pumpfun_create=False,creator_source='signer_inference',create_tx_signature=S),dict(creator=C,is_pumpfun_create=True,creator_source='rpc_pump_create',validation_status='inferred_create',create_tx_signature=S)])
def test_signer_never_qualifies(data):
    with pytest.raises(h.Unavailable):h.rpc_proof(M,data,101)

@pytest.mark.parametrize('change',[dict(program='wrong'),dict(mint='invalid!'),dict(signature=''),dict(before_signature='x'),dict(slot='fabricated')])
def test_bad_evidence_rejected(f,change):
    with pytest.raises(h.Unavailable):f.publish(replace(f.proof(),**change))
    assert f.row(f.core,'SELECT COUNT(*) FROM wb_create_delivery')==(0,)


def test_core_creator_conflict(f):
    def action(c):
        c.execute('INSERT INTO token_analysis VALUES(?,?,?,?,?)',(M,encoded(100),C,S,101))
        h.stage(c,f.proof(),core_path=f.core,ops_path=f.ops,now=200)
    with pytest.raises(h.Unavailable,match='CORE_AUTHORITY_CONFLICT'):f.write(f.core,action)


def test_historical_and_oldest_exclusion(f):
    f.add_waiting(mint='a'*32,enqueued=1)
    f.publish(f.proof(mint='b'*32,migrated_at=1))
    assert f.row(f.core,'SELECT COUNT(*) FROM wb_create_delivery')==(0,)
    for char in ['m','n','p']:
        f.add_waiting(mint=encoded(ord(char)));f.publish(f.proof(mint=encoded(ord(char))))
    f.dispatch()
    assert f.row(f.ops,"SELECT status,attempts FROM wt_walkback_queue WHERE mint='"+'a'*32+"'")==('waiting',0)
    assert f.row(f.ops,"SELECT COUNT(*) FROM wt_walkback_queue WHERE status='pending'")==(3,)

@pytest.mark.parametrize('which',['headroom','wal','main'])
def test_disk_pressure_stops_without_ack(f,monkeypatch,which):
    f.add_waiting();f.publish()
    if which=='headroom':monkeypatch.setattr(h.shutil,'disk_usage',lambda p:shutil._ntuple_diskusage(10,9,1))
    if which=='wal':monkeypatch.setattr(h,'WAL_STOP',0);Path(str(f.core)+'-wal').write_bytes(b'')
    if which=='main':monkeypatch.setattr(h,'MAIN_GROWTH',0)
    with pytest.raises(h.Unavailable):f.dispatch()
    assert f.row(f.core,'SELECT state FROM wb_create_delivery')==('PENDING',)
    assert f.row(f.ops,'SELECT status FROM wt_walkback_queue')==('waiting',)

@pytest.mark.parametrize('boundary',['ops','ack'])
def test_injected_commit_full_rollback_then_restart(f,boundary):
    f.add_waiting();f.publish()
    def full():raise sqlite3.OperationalError('injected SQLITE_FULL at commit boundary')
    with pytest.raises(sqlite3.OperationalError) if boundary=='ack' else __import__('contextlib').nullcontext():
        f.dispatch(ops_before_commit=full if boundary=='ops' else None,core_before_commit=full if boundary=='ack' else None)
    restarted=Fixture(f.root,create=False);restarted.dispatch(now=1000)
    assert restarted.execute_claim_model()==1
    assert restarted.row(restarted.core,'SELECT state FROM wb_create_delivery')==('ACK',)


def test_retry_budget_retains_blocked_and_capacity(f,monkeypatch):
    f.publish()
    for now in [200,1000,2000,3000,4000]:f.dispatch(now=now)
    assert f.row(f.core,'SELECT state,tries FROM wb_create_delivery')==('BLOCKED',5)
    assert f.row(f.core,'SELECT pending_count FROM wb_create_control')==(1,)
    monkeypatch.setattr(h,'MAX_PENDING',1)
    with pytest.raises(h.Unavailable,match='CAPACITY'):f.publish(f.proof(mint=encoded(110)))
    assert f.row(f.core,'SELECT COUNT(*) FROM token_analysis')==(1,)


def test_ack_retention_and_page_reuse(f):
    f.add_waiting();f.publish();f.dispatch();f.execute_claim_model()
    kwargs=dict(read_core=lambda:f.read(f.core),read_ops=lambda:f.read(f.ops),write_core=lambda action:f.write(f.core,action),core_path=f.core,ops_path=f.ops)
    assert h.prune(**kwargs,now=201)==0
    assert h.prune(**kwargs,now=200+h.RETENTION+1)==1
    f.publish();f.dispatch(now=1000);assert f.execute_claim_model()==1


def test_birth_retry_source_is_bounded_and_not_invented(f):
    f.write(f.core,lambda c:c.execute('INSERT INTO birth_persist_queue(mint) VALUES(?)',(M,)))
    f.write(f.core,lambda c:h.set_birth_retry_source(c,M,None))
    assert f.row(f.core,'SELECT wb_birth_source FROM birth_persist_queue')==(None,)
    f.write(f.core,lambda c:h.set_birth_retry_source(c,M,'PUMPPORTAL_NEW_TOKEN'))
    assert f.row(f.core,'SELECT wb_birth_source FROM birth_persist_queue')==('PUMPPORTAL_NEW_TOKEN',)
    assert f.row(f.core,'SELECT birth_retry_admitted FROM wb_create_control')==(1,)


def test_bounded_plans_and_lane_order(f):
    with f.read(f.core) as c:
        plan=c.execute("EXPLAIN QUERY PLAN SELECT seq FROM wb_create_delivery WHERE state='PENDING' AND due<=1 ORDER BY due,seq LIMIT 16").fetchall()
    assert 'wb_create_due' in str(plan) and 'SCAN' not in str(plan) and 'TEMP' not in str(plan)
    f.add_waiting();f.publish();f.dispatch()
    assert all(f.events[i-1]=='lane' for i,x in enumerate(f.events) if x=='sqlite_begin')
    assert all(p.stat().st_size<8*1024*1024 for p in f.root.iterdir() if p.is_file())


def test_actual_sqlite_full_has_no_partial_authority(f):
    c=sqlite3.connect(f.core);c.execute('PRAGMA max_page_count=16');c.execute('CREATE TABLE pressure(p BLOB)');c.commit()
    with pytest.raises(sqlite3.OperationalError) as info:c.execute('INSERT INTO pressure VALUES(?)',(b'x'*200000,))
    assert info.value.sqlite_errorcode==sqlite3.SQLITE_FULL;c.rollback();assert c.execute('SELECT COUNT(*) FROM pressure').fetchone()==(0,);c.close()

# Real child process exits, isolated to fixture files. No operational process signals.


def test_actual_rpc_producer_transaction_and_real_worker_execution(tmp_path,monkeypatch):
    import time
    import importlib.util
    spec=importlib.util.spec_from_file_location("_dev090_original_creator_controls",Path(__file__).with_name("test_dev074_creator_authority.py"))
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    # Reuse the original creator authority fixture and assertions; resolver is
    # a qualified retained-proof fixture, transport remains forbidden.
    q,db,boundary,get,crash,events=old.queue.__wrapped__(tmp_path,monkeypatch)
    f=Fixture(tmp_path/'handoff');f.core=Path(db)
    now=int(time.time());start=now-10
    with boundary(db) as c:
        c.execute('BEGIN IMMEDIATE')
        h.initialize(c,epoch='producer-fixture',activated_at=start,core_base=Path(db).stat().st_size,ops_base=f.ops.stat().st_size)
        c.execute('UPDATE wb_create_control SET enabled=1')
        c.execute('UPDATE token_analysis SET migrated_at=?',(now,));c.commit()
    f.add_waiting(mint=old.MINT,enqueued=now)
    monkeypatch.setattr(q,'_OPS_DB_PATH',str(f.ops))
    monkeypatch.setattr(q,'_resolve_creator_rpc',lambda *a,**k:old.qualified())
    assert q.process_queue(db,schema_ready=True)['resolved']==1
    assert f.row(f.core,'SELECT state FROM wb_create_delivery')==('PENDING',)
    f.dispatch(now=now+1)
    assert f.row(f.ops,'SELECT status FROM wt_walkback_queue')==('pending',)
    # Use the actual installed worker functions, not execute_claim_model.
    from src.core import walkback_worker as w
    from src.ops import watchtower_candidates
    ops=sqlite3.connect(f.ops);ops.row_factory=sqlite3.Row
    from src.core import walkback_queue
    ops.execute("ALTER TABLE wt_walkback_queue RENAME TO small_fixture_queue")
    walkback_queue.ensure_schema(ops)
    cols="mint,creator,status,path_state,create_anchor_signature,enqueued_at,create_anchor_slot,create_anchor_block_time,create_anchor_source,create_anchor_audit_state,attempts"
    ops.execute("INSERT INTO wt_walkback_queue("+cols+") SELECT "+cols+" FROM small_fixture_queue")
    ops.commit()
    attribution_source=(Path(__file__).parents[1]/'src/core/watchtower_attribution.py').read_text()
    ddl=next(n.value for n in ast.walk(ast.parse(attribution_source)) if isinstance(n,ast.Constant) and isinstance(n.value,str) and 'CREATE TABLE IF NOT EXISTS watchtower_token_attribution' in n.value)
    ops.executescript(ddl)
    monkeypatch.setattr(w,'LIVE_DB_PATH',db)
    monkeypatch.setattr(w,'OPS_DB_PATH',str(f.ops))
    monkeypatch.setattr(w,'_get_sigs',lambda *a,**k:[])
    monkeypatch.setattr(w,'_notify_living_after_walkback_commit',lambda *a:None)
    # This derived candidate notification is outside execution and admission;
    # prevent it from starting unrelated background work in the fixture.
    monkeypatch.setattr(watchtower_candidates,'sync_walkback_result',lambda *a,**k:None)
    out=w.drain_batch(ops,batch_size=1)
    assert out['processed']==1
    row=ops.execute('SELECT status,intelligence_outcome,attempts FROM wt_walkback_queue WHERE mint=?',(old.MINT,)).fetchone()
    assert tuple(row)==('complete','NO_ATTRIBUTION_FOUND',1)
    assert w.drain_batch(ops,batch_size=1)['processed']==0
    assert ops.execute('SELECT COUNT(*) FROM watchtower_token_attribution').fetchone()[0]==0
    ops.close()


def test_producer_bindings_are_same_transaction_and_before_commit():
    root=Path(__file__).parents[1]
    text=(root/'src/core/creator_resolution_queue.py').read_text();n=next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name=='process_queue');body='\n'.join(text.splitlines()[n.lineno-1:n.end_lineno])
    assert body.index('stage_rpc_completion(conn, mint, data')<body.index('conn.commit()',body.index('stage_rpc_completion(conn, mint, data'))
    text=(root/'src/core/pumpfun_curve_listener.py').read_text();assert 'set_birth_retry_source(_qc, mint, birth_evidence_source)' in text
    assert 'set_birth_retry_source(conn, r.get("mint"), r.get("birth_evidence_source"))' in text
    assert 'birth_evidence_source=row["wb_birth_source"]' in text

@pytest.mark.parametrize('fail_commit',[False,True])
def test_actual_pumpportal_birth_core_transaction(f,fail_commit):
    import time
    now=int(time.time());f.write(f.core,lambda c:c.execute('UPDATE wb_create_control SET activated_at=?',(now-10,)))
    def prepare(c):
        for name,kind in [('created_at','TEXT'),('analyzed_at','REAL'),('bonding_curve_pda','TEXT'),('source_platform','TEXT'),('lifecycle_stage','TEXT'),('is_new','INTEGER'),('migration_signal_source','TEXT'),('migration_signal_updated_at','INTEGER'),('first_pre_migration_signal_at','INTEGER')]:c.execute('ALTER TABLE token_analysis ADD COLUMN '+name+' '+kind)
        c.execute('INSERT INTO token_analysis(mint,migrated_at,lifecycle_stage) VALUES(?,?,?)',(M,now,'migrated'))
    f.write(f.core,prepare);f.add_waiting(enqueued=now)
    path=Path(__file__).parents[1]/'src/core/pumpfun_curve_listener.py';tree=ast.parse(path.read_text());outer=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_insert_bonding_curve_token');node=next(n for n in outer.body if isinstance(n,ast.FunctionDef) and n.name=='_do_write')
    class Connection(sqlite3.Connection):
        def commit(self):
            if fail_commit:raise Crash()
            return super().commit()
    @contextmanager
    def writer(path,**kwargs):
        assert Path(path)==f.core
        with f.lane:
            c=sqlite3.connect(path,factory=Connection)
            try:yield c
            except BaseException:c.rollback();raise
            finally:c.close()
    ns=dict(managed_db_connect=writer,DB_PATH=str(f.core),PRIORITY_P0_CRITICAL_INGESTION=0,mint=M,creator=C,created_at=str(now),analyzed_at=now,birth_seen_at=now,bonding_curve_pda=None,create_tx_signature=S,symbol='FIXTURE',name='Fixture',birth_evidence_source='PUMPPORTAL_NEW_TOKEN',_listener_ops_db_path=lambda:str(f.ops))
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
    if fail_commit:
        with pytest.raises(Crash):ns['_do_write']()
        assert f.row(f.core,'SELECT create_tx_signature FROM token_analysis')==(None,)
        assert f.row(f.core,'SELECT COUNT(*) FROM wb_create_delivery')==(0,)
    else:
        ns['_do_write']();assert f.row(f.core,'SELECT state FROM wb_create_delivery')==('PENDING',)
        f.dispatch(now=now+1);assert f.row(f.ops,'SELECT status FROM wt_walkback_queue')==('pending',)

@pytest.mark.parametrize('where',['ops','ack'])
def test_real_sqlite_full_during_delivery_and_ack(f,where):
    f.add_waiting();f.publish()
    target=f.ops if where=='ops' else f.core
    c=sqlite3.connect(target);c.execute('CREATE TABLE full_pressure(p BLOB)')
    if where=='ops':c.execute('CREATE TRIGGER fixture_full BEFORE UPDATE ON wt_walkback_queue BEGIN INSERT INTO full_pressure VALUES(zeroblob(10000000)); END')
    else:c.execute("CREATE TRIGGER fixture_full BEFORE UPDATE OF state ON wb_create_delivery WHEN NEW.state='ACK' BEGIN INSERT INTO full_pressure VALUES(zeroblob(10000000)); END")
    c.commit();c.close()
    # Fixture's writer imposes max_page_count2048, so this produces real SQLITE_FULL
    # without filling the filesystem or exceeding an8MiB fixture limit.
    result=f.dispatch()
    assert result[0][1]=='SQLITE_FULL'
    assert f.row(f.core,'SELECT state FROM wb_create_delivery')==('PENDING',)
    assert f.row(f.ops,'SELECT status FROM wt_walkback_queue')==(('waiting',) if where=='ops' else ('pending',))
    c=sqlite3.connect(target);c.execute('DROP TRIGGER fixture_full');c.commit();c.close()
    f.dispatch(now=1000);assert f.row(f.core,'SELECT state FROM wb_create_delivery')==('ACK',)
    assert f.execute_claim_model()==1


def test_existing_shared_writer_commit_acknowledgement(f):
    from src.core.database_write_service import DatabaseWriteService
    service=DatabaseWriteService(_test_sqlite_timeout=1)
    service.register_database('dev090-fixture-core',str(f.core))
    service.register_database('dev090-fixture-ops',str(f.ops))
    events=[]
    def publish(c):
        events.append(('producer',bool(c.in_transaction),bool(c._holds_write_lock)))
        c.execute('INSERT INTO token_analysis VALUES(?,?,?,?,?)',(M,C,C,S,101))
        return h.stage(c,f.proof(),core_path=f.core,ops_path=f.ops,now=200)
    assert service.submit('dev090-fixture-core','fixture-proof',publish)=='PENDING'
    f.add_waiting()
    def writer(label):
        def call(action):
            def transaction(c):
                events.append((label,bool(c.in_transaction),bool(c._holds_write_lock)))
                return action(c)
            return service.submit('dev090-fixture-'+label,'fixture-'+label,transaction)
        return call
    h.dispatch(read_core=lambda:f.read(f.core),write_core=writer('core'),write_ops=writer('ops'),core_path=f.core,ops_path=f.ops,now=200)
    assert f.row(f.core,'SELECT state FROM wb_create_delivery')==('ACK',)
    assert [e[0] for e in events]==['producer','ops','core']
    assert all(e[1:] == (True,True) for e in events)
    assert all(t['status']=='COMMITTED' for t in service.telemetry())


def test_core_conflict_before_delivery_stays_waiting(f):
    f.add_waiting();f.publish()
    f.write(f.core,lambda c:c.execute('UPDATE token_analysis SET pf_ws_creator=?',(encoded(100),)))
    assert f.dispatch()[0][1]=='CORE_AUTHORITY_CONFLICT'
    assert f.row(f.ops,'SELECT status FROM wt_walkback_queue')==('waiting',)
    assert f.row(f.core,'SELECT state,last_error_code FROM wb_create_delivery')==('PENDING','CORE_AUTHORITY_CONFLICT')


def test_wrong_decoded_identity_width_and_corrupt_proof_fail_closed(f):
    with pytest.raises(h.Unavailable):f.publish(replace(f.proof(),mint='m'*32))
    f.add_waiting();f.publish()
    f.write(f.core,lambda c:c.execute("UPDATE wb_create_delivery SET qualification='tampered'"))
    f.dispatch()
    assert f.row(f.ops,'SELECT status FROM wt_walkback_queue')==('waiting',)


def test_external_crash_harness_preserves_points_and_frozen_guards():
    import importlib.util
    path=Path(__file__).with_name('dev090_process_crash_driver.py')
    spec=importlib.util.spec_from_file_location('_dev090_process_harness',path)
    driver=importlib.util.module_from_spec(spec);sys.modules[spec.name]=driver;spec.loader.exec_module(driver)
    assert driver.POINTS==('before_core_commit','after_core_commit','before_ops','during_ops','after_ops_commit','before_ack','after_ack')
    assert driver.MAX_CHILD_SECONDS==15
    assert driver.CHILD.index('isolation_bootstrap.py')<driver.CHILD.index('from test_dev090_handoff import Fixture')
    assert 'assert not value[\'isolation_events\']' in driver.CHILD

@pytest.mark.parametrize('source',['RPC','PUMPPORTAL'])
def test_create_committed_before_migration_uses_existing_enqueue_and_executes(f,monkeypatch,source):
    from src.core import walkback_queue,walkback_worker as w
    from src.ops import watchtower_candidates
    def birth(c):
        c.execute('INSERT INTO token_analysis VALUES(?,?,?,?,NULL)',(M,C,C,S))
        c.execute('CREATE TABLE creator_funding_queue(mint TEXT PRIMARY KEY,create_tx_signature TEXT,updated_at INTEGER)')
        if source=='RPC':
            result=h.stage_rpc_completion(c,M,dict(creator=C,create_tx_signature=S,is_pumpfun_create=True,creator_source='rpc_pump_create',validation_status='validated'),core_path=f.core,ops_path=f.ops)
        else:result=h.stage_birth_completion(c,M,C,S,core_path=f.core,ops_path=f.ops,birth_evidence_source='PUMPPORTAL_NEW_TOKEN')
        assert result=='NOT_MIGRATED'
    f.write(f.core,birth)
    assert f.row(f.core,'SELECT COUNT(*) FROM wb_create_delivery')==(0,)
    f.write(f.core,lambda c:c.execute('UPDATE token_analysis SET migrated_at=101'))
    # Classification of this compact unknown-creator fixture is supplied as FULL;
    # the production classifier/admission implementation remains unchanged.
    monkeypatch.setattr(walkback_queue,'classify_creator',lambda creator,ops,live:('FULL_WALKBACK',creator,None,None,'FIXTURE_UNKNOWN'))
    f.ops=f.root/'normal-ops.db';ops=sqlite3.connect(f.ops);ops.row_factory=sqlite3.Row
    walkback_queue.ensure_schema(ops)
    live=sqlite3.connect(f.core);live.row_factory=sqlite3.Row
    walkback_queue.enqueue_migration(ops,mint=M,creator=C,live_conn=live)
    assert tuple(ops.execute('SELECT status,path_state,create_anchor_signature FROM wt_walkback_queue').fetchone())==('pending','CREATE_ANCHORED',S)
    live.close()
    source_text=(Path(__file__).parents[1]/'src/core/watchtower_attribution.py').read_text()
    ddl=next(n.value for n in ast.walk(ast.parse(source_text)) if isinstance(n,ast.Constant) and isinstance(n.value,str) and 'CREATE TABLE IF NOT EXISTS watchtower_token_attribution' in n.value)
    ops.executescript(ddl)
    monkeypatch.setattr(w,'LIVE_DB_PATH',str(f.core));monkeypatch.setattr(w,'OPS_DB_PATH',str(f.ops))
    monkeypatch.setattr(w,'_get_sigs',lambda *a,**k:[])
    monkeypatch.setattr(w,'_notify_living_after_walkback_commit',lambda *a:None)
    monkeypatch.setattr(watchtower_candidates,'sync_walkback_result',lambda *a,**k:None)
    assert w.drain_batch(ops,batch_size=1)['processed']==1
    assert tuple(ops.execute('SELECT status,intelligence_outcome,attempts FROM wt_walkback_queue').fetchone())==('complete','NO_ATTRIBUTION_FOUND',1)
    assert w.drain_batch(ops,batch_size=1)['processed']==0
    assert ops.execute('SELECT COUNT(*) FROM watchtower_token_attribution').fetchone()[0]==0
    ops.close()


def test_producer_pressure_stops_before_claim_or_rpc(f,monkeypatch):
    from src.core import creator_resolution_queue as q
    monkeypatch.setattr(q,'_read_db',f.read)
    monkeypatch.setattr(q,'_OPS_DB_PATH',str(f.ops))
    def forbidden(*a,**k):raise AssertionError('claim/RPC forbidden under pressure')
    monkeypatch.setattr(q,'_db',forbidden)
    monkeypatch.setattr(q,'_resolve_creator_rpc',forbidden)
    f.write(f.core,lambda c:c.execute('UPDATE wb_create_control SET pending_count=?',(h.MAX_PENDING,)))
    monkeypatch.setattr(h.time,'time',lambda:200)
    with pytest.raises(h.Unavailable,match='INTENT_CAPACITY'):
        q.process_queue(str(f.core),schema_ready=True)
    assert f.row(f.core,'SELECT COUNT(*) FROM wb_create_delivery')==(0,)
