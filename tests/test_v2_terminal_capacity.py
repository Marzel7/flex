"""Bounded OPS terminal transfer; no production database or provider access."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import socket
import sqlite3
import types
import pytest

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=Path('/private/tmp/flex-v2-terminal-capacity/protected-fixed-512.jsonl')

def load(name):
    p=ROOT/'runtime/listener/src/core'/f'{name}.py'
    spec=importlib.util.spec_from_file_location('test_'+name,p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

@pytest.fixture
def case(tmp_path,monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('NETWORK_FORBIDDEN')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(socket,'create_connection',forbidden)
    jm=load('migration_signature_journal');tm=load('migration_terminal_evidence')
    monkeypatch.setattr(jm,'_paired_ack_generation',lambda:'fixture-v2')
    old=resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE,(min(499000000,old[0]) if old[0]!=resource.RLIM_INFINITY else 499000000,old[1]))
    ops=tmp_path/'ops.sqlite'
    with sqlite3.connect(ops) as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE unrelated_evidence(id TEXT PRIMARY KEY)')
        c.execute("INSERT INTO unrelated_evidence VALUES('UNCHANGED')")
    j=jm.MigrationJournal(tmp_path/'migration.jsonl');j.initialize()
    owner=tm.TerminalEvidenceStore(ops,j.path,generation='fixture-v2',validate_record=j._validate,validate_pair=lambda *_:'fixture-v2')
    owner.initialize();j.terminal_owner=owner
    def rows(n=512):
        if n==512 and PRIVATE.exists():return [json.loads(l) for l in PRIVATE.read_bytes().splitlines()]
        result=[]
        for i in range(n):
            row=j._new(f'protected-{i}','PUMPSWAP',100+i,'mint-'+str(i))
            row.update(state='RETRY_EXHAUSTED',attempts=3,error_code='RETRY_CEILING');result.append(row)
        return result
    def seed(n=512):
        data=rows(n)
        with j._lock():j._publish(data)
        return data
    def restart():
        x=jm.MigrationJournal(j.path)
        x.terminal_owner=tm.TerminalEvidenceStore(ops,x.path,generation='fixture-v2',validate_record=x._validate,validate_pair=lambda *_:'fixture-v2')
        return x
    yield types.SimpleNamespace(j=j,owner=owner,jm=jm,tm=tm,ops=ops,root=tmp_path,seed=seed,restart=restart)
    assert all(p.stat().st_size<500000000 for p in tmp_path.rglob('*') if p.is_file())
    resource.setrlimit(resource.RLIMIT_FSIZE,old)


def test_owner_repeat_initialization_and_index(case):
    case.owner.initialize()
    with sqlite3.connect(case.ops) as c:
        plan=c.execute(f'EXPLAIN QUERY PLAN SELECT payload FROM {case.tm.TABLE} WHERE signature=?',('s',)).fetchall()
        assert all('SEARCH' in p[3] and 'INDEX' in p[3] for p in plan)
        assert c.execute('SELECT * FROM unrelated_evidence').fetchall()==[('UNCHANGED',)]


def test_fixed_512_exact_transfer_event_513_and_restart(case):
    rows=case.seed()
    new=case.j.admit('event-513','PUMPSWAP',2000)
    assert new['state']=='PENDING' and new['attempts']==0
    assert len(case.j.snapshot())==493
    while any(r['state']=='RETRY_EXHAUSTED' for r in case.j.snapshot()):case.j.reclaim_protected()
    assert case.j.snapshot()==[new]
    j=case.restart()
    for row in rows:
        assert j.terminal_owner.verified(row)
        assert j.admit(row['signature'],row['source'],3000)==row
        assert not j.begin_attempt(row['signature'])
    assert j.snapshot()==[new]
    with sqlite3.connect(case.ops) as c:
        assert c.execute(f'SELECT count(*) FROM {case.tm.TABLE}').fetchone()[0]==512
        assert c.execute(f"SELECT count(*) FROM {case.tm.TABLE} WHERE transfer_state!='VERIFIED'").fetchone()[0]==0
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_two_thousand_sequential_admissions_bounded(case):
    original=case.seed()
    while case.j.snapshot():case.j.reclaim_protected()
    peak=0
    def exhaust(rows):
        for row in rows:
            if row['state']=='PENDING':row.update(state='RETRY_EXHAUSTED',attempts=3,error_code='RETRY_CEILING')
    for batch in range(100):
        for i in range(20):
            row=case.j.admit(f'next-{batch*20+i}','PUMPSWAP',5000+batch*20+i)
            assert row['attempts']==0
        case.j._change(exhaust)  # Fixture simulates bounded worker exhaustion; no provider/replay.
        assert len(case.j.reclaim_protected())==20
        peak=max(peak,max(p.stat().st_size for p in case.root.iterdir() if p.is_file()))
        if batch%10==0:case.j=case.restart()
    with sqlite3.connect(case.ops) as c:
        count,used=c.execute(f'SELECT used_records,used_bytes FROM {case.tm.META}').fetchone()
        assert count==2512 and used<=case.tm.MAX_BYTES
        assert c.execute(f'SELECT count(DISTINCT identity),count(DISTINCT signature) FROM {case.tm.TABLE}').fetchone()==(2512,2512)
    assert case.j.snapshot()==[] and peak<case.tm.MAX_DB_BYTES
    (case.root/'storage-result.json').write_text(json.dumps({'sequential_new_admissions':2000,'protected_originals':512,'terminal_rows':2512,'largest_sampled_file':peak}))


@pytest.mark.parametrize('point',['before_commit','after_commit','after_verified','journal_replace','journal_after_commit'])
def test_crash_boundaries_restart_exact(case,monkeypatch,point):
    rows=case.seed(20);before=case.j.path.read_bytes()
    if point=='before_commit':
        original=case.owner._authority;calls=[0]
        def fail():
            calls[0]+=1
            if calls[0]==2:raise RuntimeError('CRASH')
            return original()
        monkeypatch.setattr(case.owner,'_authority',fail)
    elif point=='after_commit':
        monkeypatch.setattr(case.owner,'lookup',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('CRASH')))
    elif point=='after_verified':
        original=case.owner.preserve_batch
        def fail(rows):original(rows);raise RuntimeError('CRASH')
        monkeypatch.setattr(case.owner,'preserve_batch',fail)
    elif point=='journal_replace':
        original=case.jm.os.replace
        def fail(src,dst):
            if str(dst)==str(case.j.path):raise RuntimeError('CRASH')
            return original(src,dst)
        monkeypatch.setattr(case.jm.os,'replace',fail)
    else:
        monkeypatch.setattr(case.j,'_sync_dir',lambda:(_ for _ in ()).throw(RuntimeError('CRASH')))
    with pytest.raises(RuntimeError,match='CRASH'):case.j.reclaim_protected()
    if point!='journal_after_commit':assert case.j.path.read_bytes()==before
    monkeypatch.undo()
    j=case.restart()
    if j.tmp.exists():j.recover_reclamation()
    if j.snapshot():j.reclaim_protected()
    assert j.snapshot()==[]
    for row in rows:assert j.terminal_owner.lookup(row['signature'],verified_only=True)==row
    assert j.reclaim_protected()==[]


def test_conflicting_signature_refused_original_preserved(case):
    row=case.seed(1)[0];case.j.reclaim_protected()
    with pytest.raises(case.jm.JournalHold,match='TERMINAL_NOTIFICATION_CONFLICT'):
        case.j.admit(row['signature'],row['source'],2000,mint='different')
    assert case.owner.lookup(row['signature'])==row
    contradictory=dict(row,error_code='OTHER_REASON')
    with pytest.raises(case.tm.TerminalHold,match='TERMINAL_IDENTITY_CONFLICT'):
        case.owner.preserve_batch([contradictory])
    assert case.owner.lookup(row['signature'])==row


def test_quarantine_conflict_fields_and_pending_protected(case):
    rows=case.seed(3)
    rows[0].update(state='QUARANTINED',error_code='MINT_CONFLICT',conflicting_mint='alternative')
    rows[1].update(state='PENDING',attempts=1,error_code='DISPATCH_RESERVED',mint=None)
    rows[2].update(notification_mint='source-subject',retry_not_before=1234)
    with case.j._lock():case.j._publish(rows)
    assert len(case.j.reclaim_protected())==2
    assert case.j.snapshot()==[rows[1]]
    assert case.owner.lookup(rows[0]['signature'])==rows[0]
    assert case.owner.lookup(rows[2]['signature'])==rows[2]
    with pytest.raises(case.tm.TerminalHold,match='NOT_PROTECTED_TERMINAL'):case.owner.preserve_batch([rows[1]])


def test_corrupt_destination_blocks_reclaim(case):
    rows=case.seed(2);case.owner.preserve_batch(rows)
    with sqlite3.connect(case.ops) as c:
        c.execute('DROP TRIGGER migration_terminal_immutable_v1')
        c.execute(f"UPDATE {case.tm.TABLE} SET payload='{{}}' WHERE signature=?",(rows[0]['signature'],))
        c.execute(next(sql for sql in case.tm.DDL if sql.startswith('CREATE TRIGGER migration_terminal_immutable')))
    before=case.j.path.read_bytes()
    with pytest.raises(case.tm.TerminalHold,match='CORRUPT'):case.j.reclaim_protected()
    assert case.j.path.read_bytes()==before


@pytest.mark.parametrize('mode',['quota','disk','wal','kernel','generation','version'])
def test_capacity_and_authority_refusal_preserves_source(case,monkeypatch,mode):
    case.seed(2);before=case.j.path.read_bytes()
    if mode=='quota':monkeypatch.setattr(case.tm,'MAX_ROWS',1)
    elif mode=='disk':monkeypatch.setattr(case.tm.os,'statvfs',lambda p:types.SimpleNamespace(f_bavail=0,f_frsize=4096))
    elif mode=='wal':monkeypatch.setattr(case.tm,'WAL_ADMISSION_BYTES',0);Path(str(case.ops)+'-wal').write_bytes(b'X')
    elif mode=='kernel':monkeypatch.setattr(case.tm.resource,'getrlimit',lambda _: (resource.RLIM_INFINITY,resource.RLIM_INFINITY))
    elif mode=='generation':case.owner.validate_pair=lambda *_:'other-generation'
    else:
        with sqlite3.connect(case.ops) as c:c.execute(f"UPDATE {case.tm.META} SET contract='unsupported'")
    with pytest.raises((case.tm.TerminalHold,sqlite3.DatabaseError)):case.j.reclaim_protected()
    assert case.j.path.read_bytes()==before


def test_no_terminal_replay_or_retry_reset_after_restore(case):
    rows=case.seed(1);case.j.reclaim_protected()
    restored=case.root/'rollback-ops.sqlite'
    with sqlite3.connect(case.ops.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(restored) as dst:src.backup(dst,pages=20)
    owner=case.tm.TerminalEvidenceStore(restored,case.j.path,generation='rollback-generation',validate_record=case.j._validate,validate_pair=lambda *_:'rollback-generation')
    owner.initialize()
    assert owner.verified(rows[0]) and owner.lookup(rows[0]['signature'])['attempts']==3
    case.j.terminal_owner=owner
    assert case.j.admit(rows[0]['signature'],rows[0]['source'],9999)==rows[0]
    assert case.j.snapshot()==[]


def test_actual_fresh_compact_matched_pair_rollback(case,monkeypatch):
    g=load('paired_binding_guard')
    core=case.root/'core.sqlite'
    with sqlite3.connect(core) as c:c.execute('CREATE TABLE canonical_birth(mint TEXT PRIMARY KEY)');c.execute("INSERT INTO canonical_birth VALUES('preserved')")
    authority_path=case.root/'authority.json'
    authority={'id':'terminal-rollback-fixture','path':str(authority_path),'origin_generation':'fixture-v2'}
    source_paths={'core':core,'ops':case.ops}
    source={'version':1,'authority':authority,'generation':'fixture-v2','stores':{k:g.database_identity(str(p),'fixture-v2',k) for k,p in source_paths.items()},'predecessor':None}
    def publish(p,obj):
        with open(p,'wb') as f:f.write(json.dumps(obj,sort_keys=True,separators=(',',':')).encode());f.flush();os.fsync(f.fileno())
    def activate(manifest,name):
        path=case.root/(name+'.json');publish(path,manifest)
        state={'version':1,'authority_id':authority['id'],'origin_generation':authority['origin_generation'],'generation':manifest['generation'],'manifest_path':str(path),'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'phase':'ACTIVE','owner':'runtime','transfer_complete':True}
        publish(authority_path,state);g.seal_manifest(path)
        for k,v in dict(DB_PATH=manifest['stores']['core']['path'],WT_OPS_DB_PATH=manifest['stores']['ops']['path'],DEV004_PAIRED_BINDING_REQUIRED='1',FLEX_DB_PAIR_MANIFEST=str(path),FLEX_DB_PAIR_AUTHORITY=str(authority_path),FLEX_DB_PAIR_ROLE='runtime').items():monkeypatch.setenv(k,v)
        return lambda *paths:g.validate_pair(core_path=manifest['stores']['core']['path'],ops_path=str(paths[0]) if paths else manifest['stores']['ops']['path'],purpose='active')
    source_validator=activate(source,'source-pair')
    case.owner.validate_pair=source_validator
    rows=case.seed(20);case.j.reclaim_protected()
    admitted=case.j.admit('post-cutover-durable-admission','PUMPSWAP',9000)
    targets={k:case.root/f'fresh-rollback-{k}.sqlite' for k in ['core','ops']}
    for k in targets:
        src=sqlite3.connect(source_paths[k].as_uri()+'?mode=ro',uri=True);dst=sqlite3.connect(targets[k])
        try:src.backup(dst,pages=20)
        finally:src.close();dst.close()
        publish(Path(str(source_paths[k])+'.retired'),{'version':1,'path':str(source_paths[k]),'generation':'fixture-v2','retired':True})
    rollback={'version':1,'authority':authority,'generation':'fresh-rollback','stores':{k:g.database_identity(str(p),'fresh-rollback',k) for k,p in targets.items()},'predecessor':{'generation':'fixture-v2','stores':source['stores']}}
    validator=activate(rollback,'rollback-pair')
    assert validator()=='fresh-rollback'
    owner=case.tm.TerminalEvidenceStore(targets['ops'],case.j.path,generation='fresh-rollback',validate_record=case.j._validate,validate_pair=validator)
    owner.initialize();case.j.terminal_owner=owner
    for row in rows:
        assert owner.verified(row)
        assert case.j.admit(row['signature'],row['source'],9999)==row
    assert case.j.snapshot()==[admitted]
    with sqlite3.connect(targets['core']) as c:assert c.execute('SELECT * FROM canonical_birth').fetchall()==[('preserved',)]
    with pytest.raises(g.PairedBindingError):source_validator()
    with sqlite3.connect(targets['ops']) as c:
        assert c.execute(f'SELECT DISTINCT transfer_generation FROM {case.tm.TABLE}').fetchall()==[('fixture-v2',)]
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_incomplete_publication_cannot_hide_corrupt_terminal_copy(case,monkeypatch):
    rows=case.seed(2)
    original=case.jm.os.replace
    def fail(src,dst):
        if str(dst)==str(case.j.path):raise RuntimeError('CRASH')
        return original(src,dst)
    monkeypatch.setattr(case.jm.os,'replace',fail)
    with pytest.raises(RuntimeError):case.j.reclaim_protected()
    monkeypatch.undo()
    with sqlite3.connect(case.ops) as c:
        c.execute('DROP TRIGGER migration_terminal_immutable_v1')
        c.execute(f"UPDATE {case.tm.TABLE} SET evidence_hash='corrupt'")
        c.execute(next(sql for sql in case.tm.DDL if sql.startswith('CREATE TRIGGER migration_terminal_immutable')))
    j=case.restart();before=j.path.read_bytes()
    with pytest.raises(case.tm.TerminalHold):j.recover_reclamation()
    assert j.path.read_bytes()==before and j.tmp.exists()


def test_record_and_batch_quotas(case):
    rows=case.seed(20)
    with pytest.raises(case.tm.TerminalHold,match='BATCH_CAPACITY'):case.owner.preserve_batch(rows+[rows[0]])
    row=dict(rows[0],legacy={'fixture':'x'*4096})
    with pytest.raises(case.tm.TerminalHold,match='RECORD_CAPACITY'):case.owner.preserve_batch([row])


def test_event_513_interrupted_publication_recovers_both_new_and_protected(case,monkeypatch):
    original=case.seed()
    replace=case.jm.os.replace
    def fail(src,dst):
        if str(dst)==str(case.j.path):raise RuntimeError('CRASH')
        return replace(src,dst)
    monkeypatch.setattr(case.jm.os,'replace',fail)
    with pytest.raises(RuntimeError,match='CRASH'):case.j.admit('interrupted-513','PUMPSWAP',9999)
    assert len(case.j.path.read_bytes().splitlines())==512 and case.j.tmp.exists()
    monkeypatch.undo()
    j=case.restart();j.recover_reclamation()
    pending=j.admit('interrupted-513','PUMPSWAP',10000)
    assert pending['state']=='PENDING' and pending['attempts']==0
    active={r['id']:r for r in j.snapshot()}
    for row in original:
        assert active.get(row['id'])==row or j.terminal_owner.verified(row)
    assert sum(r['signature']=='interrupted-513' for r in active.values())==1


def test_database_size_guard_refuses_before_source_change(case,monkeypatch):
    case.seed(2);before=case.j.path.read_bytes()
    monkeypatch.setattr(case.tm,'MAX_DB_BYTES',1)
    with pytest.raises(case.tm.TerminalHold,match='STORAGE_PRESSURE'):case.j.reclaim_protected()
    assert case.j.path.read_bytes()==before


def test_listener_factory_binds_opt_in_owner_and_default_stays_unchanged(case,monkeypatch):
    import ast,sys
    for name in ['src','src.core']:
        package=types.ModuleType(name);package.__path__=[];monkeypatch.setitem(sys.modules,name,package)
    monkeypatch.setitem(sys.modules,'src.core.migration_signature_journal',case.jm)
    monkeypatch.setitem(sys.modules,'src.core.migration_terminal_evidence',case.tm)
    tree=ast.parse((ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_signature_migration_journal')
    ns={'os':os,'_migration_journal_readback':lambda r:False,'_migration_completed_mint':lambda r:None,'_listener_ops_db_path':lambda:str(case.ops),'_validate_active_pair':lambda:'fixture-v2'}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'listener_factory','exec'),ns)
    monkeypatch.setenv('DEV004_SIGNATURE_FIRST_JOURNAL_ENABLED','1')
    monkeypatch.setenv('MIGRATION_JOURNAL_PATH',str(case.j.path))
    monkeypatch.delenv('DEV004_MIGRATION_TERMINAL_EVIDENCE_ENABLED',raising=False)
    assert ns['_signature_migration_journal']().terminal_owner is None
    case.seed()
    monkeypatch.setenv('DEV004_MIGRATION_TERMINAL_EVIDENCE_ENABLED','1')
    journal=ns['_signature_migration_journal']()
    assert journal.admit('factory-event-513','PUMPSWAP',9999)['state']=='PENDING'
    assert len(journal.snapshot())==493
    journal.terminal_owner.ops_path=case.root/'wrong-ops.sqlite'
    with pytest.raises(case.jm.JournalHold,match='TERMINAL_OPS_BINDING_MISMATCH'):
        journal.terminal_owner.lookup('factory-event-513')
    with sqlite3.connect(case.ops) as c:assert c.execute(f'SELECT used_records FROM {case.tm.META}').fetchone()[0]==20
