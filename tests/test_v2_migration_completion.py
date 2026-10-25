"""Actual journal and AST-extracted installed listener interfaces; no runtime startup."""
import ast
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import types
import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'runtime/listener/src/core/pumpfun_curve_listener.py'

@pytest.fixture
def env(tmp_path, monkeypatch):
    import socket
    def forbidden(*a, **kw): raise AssertionError('NETWORK_FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    spec = importlib.util.spec_from_file_location('completion_fixture', ROOT / 'runtime/listener/src/core/migration_signature_journal.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_paired_ack_generation', lambda: 'fixture')
    journal = module.MigrationJournal(tmp_path / 'migration.jsonl'); journal.initialize()
    db = tmp_path / 'core.sqlite'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE token_analysis(mint TEXT PRIMARY KEY,migration_tx TEXT,lifecycle_stage TEXT)')
        c.execute('CREATE TABLE migration_walkback_handoff(mint TEXT PRIMARY KEY,state TEXT,migration_tx TEXT)')
    with sqlite3.connect(tmp_path/'ops.sqlite') as c:
        c.execute('CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY)')
    clock = types.SimpleNamespace(time=lambda: 1000)
    ns = dict(_validate_active_pair=lambda: None, DB_PATH=str(db), os=os, time=clock,
              asyncio=asyncio, _listener_ops_db_path=lambda: str(tmp_path/'ops.sqlite'),
              _signature_migration_journal=lambda: journal,
              _migration_journal_readback=lambda row, **kw: row['signature'] == 'verified' and bool(row.get('mint')))
    tree=ast.parse(SOURCE.read_text())
    names={'_migration_notification_disposition','_schedule_journal_migration','_run_journal_migration','_replay_signature_migration_batch','_migration_journal_readback'}
    nodes=[n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),ns)
    worker=types.SimpleNamespace(processing_migrations=set(), completed_migrations=set(), tx_cache_pending_retries={})
    for name in names-{'_migration_notification_disposition','_migration_journal_readback'}: setattr(worker,name,types.MethodType(ns[name],worker))
    async def handle(*a,**kw): return 'DEFERRED'
    worker.handle_migration=handle
    return types.SimpleNamespace(j=journal,m=module,db=db,ns=ns,w=worker,clock=clock,tmp=tmp_path)

COHORT_PATH=Path('/private/tmp/flex-v2-migration-exhaustion-audit/RECORD_CLASSIFICATIONS.json')
COHORT=json.loads(COHORT_PATH.read_text()) if COHORT_PATH.exists() else []
KNOWN=[r for r in COHORT if r.get('mint')]
@pytest.mark.parametrize('case',range(446))
def test_known_mint_signature_mismatch_protected_without_attempt(env,case):
    mint=KNOWN[case]['mint'] if KNOWN else f'mint-{case}'
    sig=KNOWN[case]['signature'] if KNOWN else f'notification-{case}'
    with sqlite3.connect(env.db) as c: c.execute('INSERT INTO token_analysis VALUES(?,?,?)',(mint,'canonical','migrated'))
    env.j.admit(sig,'PUMPSWAP',100,mint)
    assert env.w._schedule_journal_migration(sig,[]) is None
    row=env.j.snapshot()[0]
    assert (row['state'],row['attempts'],row['error_code']) == ('QUARANTINED',0,'CANONICAL_SIGNATURE_CONFLICT')
    assert row['ack'] is None
    with sqlite3.connect(env.db) as c: assert c.execute('SELECT migration_tx FROM token_analysis').fetchone()[0]=='canonical'

@pytest.mark.parametrize('source',['PUMPPORTAL_MIGRATION','PUMPSWAP'])
def test_atomic_defer_restart_and_ceiling(env,source):
    env.j.admit('s',source,100)
    assert env.j.begin_attempt('s',now=1000,defer_until=1045)
    restarted=env.m.MigrationJournal(env.j.path)
    for t in (1000,1010,1020,1044): assert not restarted.begin_attempt('s',now=t,defer_until=t+45)
    assert restarted.snapshot()[0]['attempts']==1
    assert restarted.begin_attempt('s',now=1045,defer_until=1090)
    assert restarted.begin_attempt('s',now=1090,defer_until=1135)
    assert not restarted.begin_attempt('s',now=1135)
    assert restarted.snapshot()[0]['state']=='RETRY_EXHAUSTED'

def test_notification_subject_retained_not_canonical(env):
    row=env.j.admit('s','PUMPPORTAL_MIGRATION',100)
    row=env.j.retain_subject('s','subject')
    assert row['mint'] is None and row['notification_mint']=='subject'
    assert not env.j.acknowledge('s','fixture',lambda r:True)
    env.j.retain_subject('s','different')
    assert env.j.snapshot()[0]['state']=='QUARANTINED'

def test_missing_handoff_no_acquisition(env):
    with sqlite3.connect(env.db) as c: c.execute('INSERT INTO token_analysis VALUES(?,?,?)',('mint','s','migrated'))
    env.j.admit('s','PUMPSWAP',100,'mint')
    assert env.w._schedule_journal_migration('s',[]) is None
    assert env.j.snapshot()[0]['attempts']==0
    assert env.j.snapshot()[0]['ack'] is None

def test_completed_verified_durable_ack(env):
    with sqlite3.connect(env.db) as c: c.execute('INSERT INTO token_analysis VALUES(?,?,?)',('mint','verified','migrated'))
    with sqlite3.connect(env.db) as c:c.execute('INSERT INTO migration_walkback_handoff VALUES(?,?,?)',('mint','COMPLETE','verified'))
    with sqlite3.connect(env.tmp/'ops.sqlite') as c:c.execute('INSERT INTO wt_walkback_queue VALUES(?)',('mint',))
    env.j.admit('verified','PUMPSWAP',100,'mint')
    env.w._schedule_journal_migration('verified',[])
    assert env.m.MigrationJournal(env.j.path).snapshot()[0]['state']=='ACKED'

@pytest.mark.parametrize('kind',['processing_migrations','tx_cache_pending_retries'])
def test_inflight_no_attempt(env,kind):
    state=getattr(env.w,kind)
    if isinstance(state,set): state.add('s')
    else: state['s']=object()
    env.w._schedule_journal_migration('s',[])
    assert env.j.snapshot()[0]['attempts']==0

def test_schedule_restart_preserves_subject_and_deferral(env):
    async def run():
        task=env.w._schedule_journal_migration('s',[],notification_source='PUMPPORTAL_MIGRATION',notification_mint='subject')
        assert await task == 'DEFERRED'
        env.clock.time=lambda:1010
        assert env.w._schedule_journal_migration('s',[]) is None
    asyncio.run(run())
    row=env.m.MigrationJournal(env.j.path).snapshot()[0]
    assert row['attempts']==1 and row['retry_not_before']==1045
    assert row['notification_mint']=='subject' and row['mint'] is None

def test_exhausted_population_never_reactivated(env):
    rows=[]
    for i in range(512):
        row=env.j._new(f's-{i}','PUMPSWAP',100,None)
        row.update(state='RETRY_EXHAUSTED',attempts=3,error_code='RETRY_CEILING'); rows.append(row)
    with env.j._lock(): env.j._publish(rows)
    before=env.j.path.read_bytes()
    for row in rows: env.w._schedule_journal_migration(row['signature'],[])
    assert env.j.path.read_bytes()==before

def test_frozen_cohort_classification_contract():
    # Private fixed evidence remains local; no production identifiers committed.
    p=Path('/private/tmp/flex-v2-migration-exhaustion-audit/RECORD_CLASSIFICATIONS.json')
    if not p.exists(): pytest.skip('private fixed cohort not supplied')
    rows=json.loads(p.read_text())
    assert len(rows)==512
    resolved=[r for r in rows if r.get('mint')]
    assert len(resolved)==446
    assert all(r['canonical_signature_relation']=='DIFFERENT_SIGNATURE' for r in resolved)

def test_verified_resolution_cannot_change_notification_subject(env):
    env.j.admit('s','PUMPPORTAL_MIGRATION',100)
    env.j.retain_subject('s','subject')
    row=env.j.resolve('s','other')
    assert row['state']=='QUARANTINED' and row['mint'] is None

@pytest.mark.parametrize('kind',['INVALID_PAIR','MISSING_SCHEMA'])
def test_authority_failure_no_dispatch_or_ack(env,kind):
    if kind=='INVALID_PAIR':
        def invalid(): raise RuntimeError('PAIR_GENERATION_MISMATCH')
        env.ns['_validate_active_pair']=invalid
    else:
        with sqlite3.connect(env.db) as c:c.execute('DROP TABLE token_analysis')
    with pytest.raises((RuntimeError,sqlite3.OperationalError)):
        env.w._schedule_journal_migration('s',[],notification_mint='subject')
    row=env.j.snapshot()[0]
    assert row['attempts']==0 and row['ack'] is None

@pytest.mark.parametrize('outcome',['DEFERRED','CONFLICTING','FAILED'])
def test_runner_never_acknowledges_unqualified_processing(env,outcome):
    async def handle(*a,**kw):return outcome
    env.w.handle_migration=handle
    async def run():
        task=env.w._schedule_journal_migration('s',[])
        assert await task==outcome
    asyncio.run(run())
    assert env.j.snapshot()[0]['ack'] is None

@pytest.mark.parametrize('case',range(66))
def test_unresolved_fixed_subject_retained_without_canonical_promotion(env,case):
    unresolved=[r for r in COHORT if not r.get('mint')]
    row=unresolved[case] if unresolved else {'signature':f'u-{case}','retained_notification_mints':['subject']}
    subjects=row['retained_notification_mints']
    subject=next(iter(subjects))
    env.j.admit(row['signature'],'PUMPPORTAL_MIGRATION',100)
    retained=env.j.retain_subject(row['signature'],subject)
    assert retained['mint'] is None and retained['notification_mint']==subject
    assert not env.j.acknowledge(row['signature'],'fixture',lambda r:False)


def test_actual_handler_delayed_resolution_and_ack_after_restart(env):
    tree=ast.parse(SOURCE.read_text())
    node=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='handle_migration')
    env.ns['log_print']=lambda *a,**kw:None
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(SOURCE),'exec'),env.ns)
    env.w.handle_migration=types.MethodType(env.ns['handle_migration'],env.w)
    calls=[]; release=asyncio.Event()
    real_asyncio=asyncio
    async def sleep(seconds):
        calls.append(('delay',seconds)); await release.wait()
    env.ns['asyncio']=types.SimpleNamespace(sleep=sleep,create_task=real_asyncio.create_task)
    async def cached(sig):calls.append(('cached',sig));return None
    async def extract(tx):return 'mint'
    async def rpc(*a,**kw):calls.append(('delayed_rpc',));return {'result':{'verified_fixture':True}}
    async def process(sig,logs,mint,tx,**kw):
        env.j.resolve(sig,mint)
        with sqlite3.connect(env.db) as c:
            c.execute('INSERT INTO token_analysis VALUES(?,?,?)',(mint,sig,'migrated'))
            c.execute('INSERT INTO migration_walkback_handoff VALUES(?,?,?)',(mint,'COMPLETE',sig))
        with sqlite3.connect(env.tmp/'ops.sqlite') as c:c.execute('INSERT INTO wt_walkback_queue VALUES(?)',(mint,))
    env.w._get_transaction_cached=cached
    env.w._extract_mint_from_tx=extract
    env.w._extract_mint_from_logs=lambda logs:None
    env.w.call_discovery_rpc=rpc
    env.w._process_migration_with_mint=process
    async def run():
        task=env.w._schedule_journal_migration('verified',[],notification_mint='mint')
        assert await task=='DEFERRED'
        await real_asyncio.sleep(0)
        assert ('delay',45) in calls
        assert env.w._schedule_journal_migration('verified',[]) is None
        assert env.j.snapshot()[0]['attempts']==1
        pending=env.w.tx_cache_pending_retries['verified']
        release.set(); await pending
        # Simulates loss of in-memory state after canonical commit, before ACK.
        env.w.completed_migrations.clear()
        env.w._schedule_journal_migration('verified',[])
    asyncio.run(run())
    assert env.j.snapshot()[0]['state']=='ACKED'
    assert len([c for c in calls if c[0]=='cached'])==1
    assert len([c for c in calls if c[0]=='delayed_rpc'])==1

def test_subject_durable_before_optional_lookup_failure(env):
    def fail(row):raise RuntimeError('UNAVAILABLE')
    env.j.resolve_completed=fail
    with pytest.raises(env.m.CompletionLookupDeferred):
        env.j.admit('s','PUMPPORTAL_MIGRATION',100,notification_mint='subject')
    row=env.m.MigrationJournal(env.j.path).snapshot()[0]
    assert row['notification_mint']=='subject' and row['mint'] is None
    assert row['attempts']==0 and row['ack'] is None
