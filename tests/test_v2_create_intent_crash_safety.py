"""Actual durable owners and listener completion methods; disposable stores only."""
import ast
import asyncio
from contextlib import contextmanager
import hashlib
import importlib.util
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import types

import pytest
from tests.test_dev090_handoff import Fixture, M, C, S, Crash
from tests.test_v2_birth_availability import functions

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'runtime/listener/src/core/pumpfun_curve_listener.py'


@pytest.fixture
def f(tmp_path, monkeypatch):
    def blocked(*a, **kw):
        raise AssertionError('PROVIDER_NETWORK_FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    pair = types.ModuleType('src.core.paired_binding_guard')
    pair.validate_pair = lambda **kw: 'disposable-generation'
    pair.PairedBindingError = type('PairedBindingError', (RuntimeError,), {})
    monkeypatch.setitem(sys.modules, pair.__name__, pair)
    def load(name, relative):
        spec = importlib.util.spec_from_file_location(name, ROOT / relative)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module
    j = load('src.core.migration_signature_journal', 'runtime/listener/src/core/migration_signature_journal.py')
    b = load('src.core.birth_admission_journal', 'runtime/listener/src/core/birth_admission_journal.py')
    a = load('src.core.pumpportal_birth_audit', 'runtime/listener/src/core/pumpportal_birth_audit.py')
    x = Fixture(tmp_path)
    now = int(time.time())
    x.write(x.core, lambda c: c.execute('UPDATE wb_create_control SET activated_at=?', (now - 10,)))
    x.write(x.core, lambda c: c.execute('CREATE TABLE creator_resolution_queue(mint TEXT PRIMARY KEY,status TEXT,resolved_creator TEXT,create_tx_signature TEXT)'))
    x.write(x.core, lambda c: c.execute('INSERT INTO token_analysis VALUES(?,?,?,?,?)', (M, C, C, S, now)))
    x.add_waiting(enqueued=now)
    original = x.row(x.core, 'SELECT * FROM token_analysis')
    root = tmp_path / 'receipts'; root.mkdir()
    audit = a.PumpPortalBirthAudit(enabled=False, path=str(tmp_path / 'raw-forbidden'), compact_path=str(root))
    payload = dict(_birth_retry_schema=1, mint=M, signature=S, traderPublicKey=C,
                   bondingCurveKey=None, symbol=None, name=None, receive_utc_ns=time.time_ns(),
                   provenance='PUMPPORTAL_NEW_TOKEN')
    journal = b.BirthJournal(tmp_path / 'birth.jsonl'); journal.initialize()
    row = journal.admit_birth(payload)
    @contextmanager
    def managed(path, read_only=False, **kw):
        c = sqlite3.connect(path)
        c.execute('PRAGMA synchronous=FULL')
        c.execute('PRAGMA max_page_count=1024')
        if read_only: c.execute('PRAGMA query_only=ON')
        try: yield c
        except BaseException: c.rollback(); raise
        finally: c.close()
    def readback(row):
        r = x.row(x.core, 'SELECT earliest_tx_creator,pf_ws_creator,create_tx_signature FROM token_analysis')
        return bool(r and r == (row['payload']['traderPublicKey'], row['payload']['traderPublicKey'], row['signature']))
    ns = dict(asyncio=asyncio, os=os, DB_PATH=str(x.core), managed_db_connect=managed,
              PRIORITY_P0_CRITICAL_INGESTION=0, _listener_ops_db_path=lambda: str(x.ops),
              _validate_active_pair=lambda: pair.validate_pair(purpose='active'),
              _birth_admission_journal=lambda: b.BirthJournal(tmp_path / 'birth.jsonl'),
              _birth_journal_readback=readback, log_print=lambda *a, **kw: None)
    names = {'_record_pp_birth_outcome', '_complete_durable_birth', '_persist_durable_birth', '_drain_durable_birth_batch'}
    nodes = [n for n in ast.walk(ast.parse(SOURCE.read_text())) if isinstance(n, ast.AsyncFunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)
    def actor():
        obj = types.SimpleNamespace(_eb_birth_audit=audit)
        for name in names: setattr(obj, name, types.MethodType(ns[name], obj))
        async def no_reinsert(*args, **kwargs):
            raise AssertionError('COMMITTED_BIRTH_MUST_NOT_BE_REACQUIRED_OR_REINSERTED')
        obj._insert_bonding_curve_token = no_reinsert
        return obj
    def run(): return asyncio.run(actor()._persist_durable_birth(journal.snapshot()[0]))
    def restart(): return asyncio.run(actor()._drain_durable_birth_batch())
    x.audit, x.journal, x.rowdata, x.j, x.b, x.pair = audit, journal, row, j, b, pair
    x.ns, x.actor, x.run, x.restart, x.now, x.original = ns, actor, run, restart, now, original
    yield x
    assert x.row(x.core, 'SELECT * FROM token_analysis') == x.original
    assert not (tmp_path / 'raw-forbidden').exists()
    assert all(p.stat().st_size < 8_388_608 for p in tmp_path.rglob('*') if p.is_file())


def counts(f):
    return f.row(f.core, 'SELECT count(*) FROM wb_create_delivery')[0]


def test_original_candidate_crash_window_is_preserved(f):
    old = subprocess.check_output(['git', 'show', 'ad2dfa7a:runtime/listener/src/core/pumpfun_curve_listener.py'], cwd=ROOT, text=True)
    method = next(n for n in ast.walk(ast.parse(old)) if isinstance(n, ast.AsyncFunctionDef) and n.name == '_record_pp_birth_outcome')
    ns = dict(f.ns); exec(compile(ast.Module(body=[method], type_ignores=[]), 'ad2dfa7a', 'exec'), ns)
    rid = f.audit._compact_record(M, C, S, f.rowdata['received_at'])
    binding = hashlib.sha256(os.path.realpath(f.core).encode()).hexdigest()
    assert f.journal.acknowledge(rid, binding, f.ns['_birth_journal_readback'])
    original = f.audit.record_outcome
    def stop(*a, **kw): original(*a, **kw); raise Crash()
    f.audit.record_outcome = stop
    with pytest.raises(Crash): asyncio.run(ns['_record_pp_birth_outcome'](f.actor(), rid, M, True))
    assert f.audit.accepted_birth(M, C, S) == (C, S)
    assert not f.journal.pending() and counts(f) == 0
    assert f.dispatch(now=int(time.time()) + 2) == []
    assert f.row(f.ops, 'SELECT create_anchor_signature FROM wt_walkback_queue') == (None,)


@pytest.mark.parametrize('boundary', ['before_receipt', 'after_receipt', 'after_intent', 'after_ack', 'after_ops'])
def test_every_crash_boundary_recovers_without_reacquisition(f, monkeypatch, boundary):
    receipt = f.audit.record_outcome
    def outcome(*a, **kw):
        if boundary == 'before_receipt': raise Crash()
        result = receipt(*a, **kw)
        if boundary == 'after_receipt': raise Crash()
        return result
    monkeypatch.setattr(f.audit, 'record_outcome', outcome)
    original_ack = f.b.BirthJournal.acknowledge
    def ack(self, *a, **kw):
        assert f.audit.accepted_birth(M, C, S) == (C, S)
        assert counts(f) == 1  # No terminal journal ACK without durable intent.
        if boundary == 'after_intent': raise Crash()
        result = original_ack(self, *a, **kw)
        if boundary == 'after_ack': raise Crash()
        return result
    monkeypatch.setattr(f.b.BirthJournal, 'acknowledge', ack)
    if boundary != 'after_ops':
        with pytest.raises(Crash): f.run()
    else:
        assert f.run()
        def fault(where):
            if where == 'after_ops_commit': raise Crash()
        with pytest.raises(Crash): f.dispatch(now=int(time.time()) + 2, fault=fault)
    if boundary in {'before_receipt', 'after_receipt', 'after_intent'}:
        assert len(f.journal.pending()) == 1
    if boundary in {'after_intent', 'after_ack', 'after_ops'}: assert counts(f) == 1
    monkeypatch.setattr(f.audit, 'record_outcome', receipt)
    monkeypatch.setattr(f.b.BirthJournal, 'acknowledge', original_ack)
    for _ in range(3): f.restart()
    assert not f.journal.pending() and counts(f) == 1
    for _ in range(3): f.dispatch(now=int(time.time()) + 2)
    assert f.row(f.ops, 'SELECT creator,create_anchor_signature,create_anchor_audit_state FROM wt_walkback_queue') == (C, S, 'VALID')
    assert f.row(f.core, 'SELECT state FROM wb_create_delivery') == ('ACK',)


def test_birth_before_migration_ack_receipt_then_migration_stages(f):
    f.write(f.core, lambda c: c.execute('UPDATE token_analysis SET migrated_at=NULL'))
    assert f.run() and not f.journal.pending() and counts(f) == 0
    assert f.audit.accepted_birth(M, C, S) == (C, S)
    _, enqueue = functions(SOURCE)
    def migrate(c):
        c.execute('UPDATE token_analysis SET migrated_at=?', (f.now,))
        return enqueue(c, M, core_path=f.core, ops_path=f.ops, observed_birth=(C, S), birth_receipt_lookup=f.audit.accepted_birth)
    assert f.write(f.core, migrate) == 'QUALIFIED_PUMPPORTAL'
    assert counts(f) == 1


def test_migration_before_receipt_completion_does_not_strand_intent(f):
    _, enqueue = functions(SOURCE)
    assert f.write(f.core, lambda c: enqueue(c, M, core_path=f.core, ops_path=f.ops, observed_birth=(C, S), birth_receipt_lookup=f.audit.accepted_birth)) == 'BIRTH_RECEIPT_PENDING'
    assert f.run() and counts(f) == 1 and not f.journal.pending()
    assert f.row(f.core, 'SELECT count(*) FROM creator_resolution_queue') == (0,)


def test_duplicate_birth_keeps_receipt_timestamp_and_one_intent(f):
    assert f.run()
    first = list(f.audit.compact_path.rglob('*.json'))
    before = [(p, p.read_bytes()) for p in first]
    assert f.journal.admit_birth(f.rowdata['payload'])['id'] == f.rowdata['id']
    for _ in range(3): assert f.run()
    assert counts(f) == 1
    assert all(p.read_bytes() == data for p, data in before)


@pytest.mark.parametrize('kind', ['creator_conflict', 'signature_conflict', 'missing_signature', 'missing_creator'])
def test_conflicts_and_missing_fields_never_ack(f, kind):
    sql = {'creator_conflict': "UPDATE token_analysis SET earliest_tx_creator='other'",
           'signature_conflict': "UPDATE token_analysis SET create_tx_signature='other'",
           'missing_signature': 'UPDATE token_analysis SET create_tx_signature=NULL',
           'missing_creator': 'UPDATE token_analysis SET earliest_tx_creator=NULL,pf_ws_creator=NULL'}[kind]
    f.write(f.core, lambda c: c.execute(sql))
    f.original = f.row(f.core, 'SELECT * FROM token_analysis')
    binding = hashlib.sha256(os.path.realpath(f.core).encode()).hexdigest()
    assert not asyncio.run(f.actor()._complete_durable_birth(f.rowdata, f.journal, binding))
    assert f.journal.snapshot()[0]['state'] != 'ACKED' and counts(f) == 0


@pytest.mark.parametrize('pressure', ['intent_capacity', 'intent_disk', 'journal_disk', 'receipt_capacity'])
def test_storage_refusal_cannot_ack_and_restart_reuses_pending(f, monkeypatch, pressure):
    from src.ops import walkback_create_handoff as h
    with monkeypatch.context() as m:
        if pressure == 'intent_capacity':
            f.write(f.core, lambda c: c.execute('UPDATE wb_create_control SET pending_count=?', (h.MAX_PENDING,)))
        elif pressure == 'intent_disk':
            m.setattr(h, 'storage_check', lambda *a, **kw: (_ for _ in ()).throw(h.Unavailable('DISK_HEADROOM')))
        elif pressure == 'journal_disk':
            m.setattr(f.j.MigrationJournal, '_publish', lambda *a: (_ for _ in ()).throw(f.j.JournalHold('DISK_HEADROOM')))
        else:
            m.setattr(f.audit, '_compact_record', lambda *a: None)
        if pressure == 'journal_disk':
            with pytest.raises(f.j.JournalHold, match='DISK_HEADROOM'): f.run()
        else:
            assert not f.run()
        assert f.journal.snapshot()[0]['state'] != 'ACKED'
    if pressure == 'intent_capacity': f.write(f.core, lambda c: c.execute('UPDATE wb_create_control SET pending_count=0'))
    f.restart()
    assert not f.journal.pending() and counts(f) == 1


def test_journal_admission_capacity_refuses_before_ack(f, monkeypatch):
    monkeypatch.setattr(f.j, 'MAX_RECORDS', 1)
    other = dict(f.rowdata['payload'], signature='other-signature')
    with pytest.raises(f.j.JournalHold): f.journal.admit_birth(other)
    assert len(f.journal.pending()) == 1 and counts(f) == 0


def test_mixed_generation_fails_before_receipt_or_ack(f, monkeypatch):
    def invalid(**kw): raise f.pair.PairedBindingError('PAIR_GENERATION_MISMATCH')
    monkeypatch.setattr(f.pair, 'validate_pair', invalid)
    with pytest.raises(f.pair.PairedBindingError): f.run()
    assert f.journal.snapshot()[0]['state'] != 'ACKED' and counts(f) == 0
    assert not list(f.audit.compact_path.rglob('*.json'))


def test_early_crash_after_unconfirmed_receipt_recovers_exact_identity(f, monkeypatch):
    record = f.audit._compact_record
    def crash(*a):
        assert record(*a) == f.rowdata['id']
        raise Crash()
    with monkeypatch.context() as m:
        m.setattr(f.audit, '_compact_record', crash)
        with pytest.raises(Crash): f.run()
    assert len(f.journal.pending()) == 1 and counts(f) == 0
    f.restart()
    assert not f.journal.pending() and counts(f) == 1
    assert len(list(f.audit.compact_path.rglob('*.json'))) == 1


def test_new_canonical_commit_flows_through_completion_before_ack(f, monkeypatch):
    f.write(f.core, lambda c: c.execute('DELETE FROM token_analysis'))
    f.ns['_birth_journal_canonical'] = lambda row: None
    owner = types.ModuleType('src.core.database_write_service')
    owner.assert_database_writable = lambda path: None
    owner.DatabaseRetiredError = type('DatabaseRetiredError', (RuntimeError,), {})
    monkeypatch.setitem(sys.modules, owner.__name__, owner)
    actor = f.actor()
    async def insert(*a, **kw):
        f.write(f.core, lambda c: c.execute('INSERT INTO token_analysis VALUES(?,?,?,?,?)', f.original))
        return True
    actor._insert_bonding_curve_token = insert
    assert asyncio.run(actor._persist_durable_birth(f.rowdata))
    assert counts(f) == 1 and not f.journal.pending()
    assert f.journal.snapshot()[0]['attempts'] == 1


def test_existing_journal_byte_limit_refuses_completion_without_ack(f, monkeypatch):
    # Start with a valid receipt: the failure is the existing journal byte ceiling.
    assert f.audit._compact_record(M, C, S, f.rowdata['received_at'])
    with monkeypatch.context() as m:
        m.setattr(f.j, 'MAX_JOURNAL_BYTES', 1)
        # The retained journal itself becomes incompatible with the test limit;
        # no read, dispatch or terminal ACK is accepted under that limit.
        with pytest.raises(f.j.JournalHold): f.run()
    assert len(f.journal.pending()) == 1
    f.restart()
    assert not f.journal.pending() and counts(f) == 1


def test_duplicate_legacy_ack_requires_delivery_before_returning_success(f):
    binding = hashlib.sha256(os.path.realpath(f.core).encode()).hexdigest()
    assert f.journal.acknowledge(f.rowdata['id'], binding, f.ns['_birth_journal_readback'])
    assert counts(f) == 0
    # Exact duplicate admission, not a scan/replay of historical ACKED records.
    assert f.run() and counts(f) == 1
    f.dispatch(now=int(time.time()) + 2)
    assert f.row(f.ops, 'SELECT create_anchor_signature FROM wt_walkback_queue') == (S,)
