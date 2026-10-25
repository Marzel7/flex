"""Provider-free qualified-birth migration intent and receipt-boundary controls."""
import ast
import asyncio
import sqlite3
import time
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import pytest
from src.ops import walkback_create_handoff as h
from tests.test_dev090_prospective_enqueue import f, M, C, S

ROOT = Path(__file__).resolve().parents[1]
COPIES = ['src/core/pumpfun_curve_listener.py', 'runtime/listener/src/core/pumpfun_curve_listener.py']


def functions(path):
    tree = ast.parse(path.read_text())
    ns = {'time': time}
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'enqueue_prospective_migration')
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
    return tree, ns['enqueue_prospective_migration']


@pytest.mark.parametrize('copy', COPIES)
def test_qualified_intent_atomic_duplicate_and_rollback(f, copy):
    _, enqueue = functions(ROOT / copy)
    f.token(C, S)
    original = f.c.execute('select * from token_analysis').fetchall()
    f.c.execute('begin')
    assert enqueue(f.c, M, core_path=f.core, ops_path=f.ops, accepted_birth=(C, S)) == 'QUALIFIED_PUMPPORTAL'
    assert f.c.execute('select count(*) from wb_create_delivery').fetchone()[0] == 1
    f.c.rollback()  # A crash before the caller's commit cannot leave a partial intent.
    assert f.c.execute('select count(*) from wb_create_delivery').fetchone()[0] == 0
    for _ in range(2):
        f.c.execute('begin')
        assert enqueue(f.c, M, core_path=f.core, ops_path=f.ops, accepted_birth=(C, S)) == 'QUALIFIED_PUMPPORTAL'
        f.c.commit()
    assert f.c.execute('select pending_count,admitted from wb_create_control').fetchone()[:] == (1, 1)
    assert f.c.execute('select source,qualification,slot,block_time from wb_create_delivery').fetchone()[:] == ('PUMPPORTAL', 'PUMPPORTAL_NEW_TOKEN', None, None)
    assert not f.jobs()
    assert f.c.execute('select * from token_analysis').fetchall() == original


@pytest.mark.parametrize('copy', COPIES)
def test_staging_capacity_failure_cannot_return_qualified(f, copy):
    _, enqueue = functions(ROOT / copy)
    f.token(C, S)
    f.c.execute('update wb_create_control set pending_count=?', (h.MAX_PENDING,)); f.c.commit()
    f.c.execute('begin')
    with pytest.raises(h.Unavailable, match='INTENT_CAPACITY'):
        enqueue(f.c, M, core_path=f.core, ops_path=f.ops, accepted_birth=(C, S))
    f.c.rollback()
    assert f.c.execute('select count(*) from wb_create_delivery').fetchone()[0] == 0
    assert not f.jobs()


@pytest.mark.parametrize('copy', COPIES)
@pytest.mark.parametrize('receipt', ['committed', 'unconfirmed', 'missing', 'conflicting'])
def test_actual_migration_qualifies_receipt_even_with_matching_memory_cache(f, copy, receipt):
    tree, enqueue = functions(ROOT / copy)
    f.token(C, S)
    calls = []
    @contextmanager
    def managed(path, read_only=False, **kwargs):
        conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
        if read_only: conn.execute('pragma query_only=on')
        try: yield conn
        except BaseException: conn.rollback(); raise
        finally: conn.close()
    class Audit:
        def accepted_birth(self, mint, creator, signature):
            calls.append((mint, creator, signature))
            return (C, S) if receipt == 'committed' else None
    actor = types.SimpleNamespace(_portal_vsol={M: {'birth_creator': C, 'birth_signature': S if receipt != 'conflicting' else 'conflicting'}}, _eb_birth_audit=Audit())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == '_mark_token_migrated_in_db')
    ns = {'enqueue_prospective_migration': enqueue, 'TOKEN_PREDICTION_RUNTIME_ENABLED': False,
          'time': time, 'asyncio': asyncio, 'Optional': Optional, 'DB_PATH': str(f.core),
          'managed_db_connect': managed, 'PRIORITY_P0_CRITICAL_INGESTION': 0,
          '_listener_ops_db_path': lambda: str(f.ops), 'log_print': lambda *a, **k: None}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(ROOT / copy), 'exec'), ns)
    asyncio.run(ns['_mark_token_migrated_in_db'](actor, M, migrated_at=f.start + 10, migration_tx=S))
    assert calls and all(call == (M, C, S) for call in calls)
    assert len(calls) <= 2
    intents = f.c.execute('select count(*) from wb_create_delivery').fetchone()[0]
    assert intents == (1 if receipt == 'committed' else 0)
    assert len(f.jobs()) == (1 if receipt == 'conflicting' else 0)


@pytest.mark.parametrize('status', ['complete', 'skipped', 'failed', 'ignored'])
def test_protected_ops_prevents_new_intent(f, status):
    f.token(C, S)
    assert f.run(accepted_birth=(C, S), ops_row=(status, f.start + 10)) == 'OPS_PROTECTED'
    assert f.c.execute('select count(*) from wb_create_delivery').fetchone()[0] == 0


def test_core_conflict_does_not_stage_birth(f):
    f.token(C, S, other='conflicting')
    assert f.run(accepted_birth=(C, S)) == 'CREATOR_CONFLICT'
    assert f.c.execute('select count(*) from wb_create_delivery').fetchone()[0] == 0


def test_observed_but_unconfirmed_birth_held_without_acquisition(f):
    f.token(C, S)
    assert f.run(observed_birth=(C, S)) == 'BIRTH_RECEIPT_PENDING'
    assert not f.jobs()
    assert f.c.execute('select count(*) from wb_create_delivery').fetchone()[0] == 0


@pytest.mark.parametrize('copy', COPIES)
@pytest.mark.parametrize('ordering', ['birth-confirmation-first', 'migration-first'])
def test_birth_confirmation_and_migration_race_closes_both_orders(f, copy, ordering):
    tree, enqueue = functions(ROOT / copy)
    f.token(C, S)
    f.c.execute('update token_analysis set migrated_at=NULL'); f.c.commit()
    class Audit:
        confirmed = False
        def record_outcome(self, *a, **kw):
            self.confirmed = True
            return True
        def accepted_birth(self, mint, creator, signature):
            return (C, S) if self.confirmed and (mint, creator, signature) == (M, C, S) else None
    audit = Audit()
    @contextmanager
    def managed(path, read_only=False, **kwargs):
        conn = sqlite3.connect(path)
        if read_only: conn.execute('pragma query_only=on')
        try: yield conn
        except BaseException: conn.rollback(); raise
        finally: conn.close()
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == '_record_pp_birth_outcome')
    ns = {'asyncio': asyncio, 'time': time, 'DB_PATH': str(f.core), 'managed_db_connect': managed,
          'PRIORITY_P0_CRITICAL_INGESTION': 0, '_listener_ops_db_path': lambda: str(f.ops),
          '_validate_active_pair': lambda: 'fixture', 'log_print': lambda *a, **k: None}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(ROOT / copy), 'exec'), ns)
    actor = types.SimpleNamespace(_eb_birth_audit=audit)
    def confirm(): asyncio.run(ns['_record_pp_birth_outcome'](actor, 'fixture-receipt', M, True))
    def migrate():
        f.c.execute('update token_analysis set migrated_at=?', (f.start + 10,)); f.c.commit()
        f.c.execute('begin')
        result = enqueue(f.c, M, core_path=f.core, ops_path=f.ops, observed_birth=(C, S),
                         birth_receipt_lookup=audit.accepted_birth)
        f.c.commit()
        return result
    if ordering == 'birth-confirmation-first':
        confirm()
        assert f.c.execute('select count(*) from wb_create_delivery').fetchone()[0] == 0
        assert migrate() == 'QUALIFIED_PUMPPORTAL'
    else:
        assert migrate() == 'BIRTH_RECEIPT_PENDING'
        confirm()
    confirm()  # Duplicate acknowledgement cannot duplicate the intent or admission counters.
    assert f.c.execute('select count(*) from wb_create_delivery').fetchone()[0] == 1
    assert f.c.execute('select pending_count,admitted from wb_create_control').fetchone()[:] == (1, 1)
    assert not f.jobs()


@pytest.mark.parametrize('copy', COPIES)
def test_transaction_receipt_recheck_accepts_confirmation_after_earlier_read(f, copy):
    _, enqueue = functions(ROOT / copy)
    f.token(C, S)
    f.c.execute('begin')
    assert enqueue(f.c, M, core_path=f.core, ops_path=f.ops, accepted_birth=None,
                   observed_birth=(C, S), birth_receipt_lookup=lambda *a: (C, S)) == 'QUALIFIED_PUMPPORTAL'
    f.c.commit()
    assert f.c.execute('select count(*) from wb_create_delivery').fetchone()[0] == 1
    assert not f.jobs()
