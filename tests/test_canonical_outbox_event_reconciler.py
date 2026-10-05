import sqlite3

import pytest

from src.ops.canonical_membership_outbox import read_after
from src.ops.canonical_outbox_event_reconciler import reconcile_event
from src.ops.universal_adapter_registry_executor import AdapterRegistryExecutor, AdapterUnavailable


TARGET = '72741232-dcbe-5871-8ebe-ce9d294aeb50'


def authority():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE operator_launch_membership(mint TEXT PRIMARY KEY,operator_id TEXT NOT NULL,assigned_at INTEGER NOT NULL,event_id TEXT)')
    db.execute('INSERT INTO operator_launch_membership VALUES(?,?,?,?)', ('mint', 'byzantine', 7, TARGET))
    return db


def test_exact_event_projects_once_and_is_consumer_visible():
    db = authority()
    assert reconcile_event(db, event_id=TARGET, created_at=9)['status'] == 'PROJECTED'
    assert reconcile_event(db, event_id=TARGET, created_at=10)['status'] == 'ALREADY_PRESENT'
    rows = read_after(db, 0)
    assert len(rows) == 1 and rows[0][4] == TARGET and rows[0][2] == 'mint'


def test_unknown_event_fails_closed_without_scan_or_membership_change():
    db = authority()
    with pytest.raises(ValueError, match='NOT_FOUND'):
        reconcile_event(db, event_id='unknown', created_at=9)
    assert db.execute('SELECT count(*) FROM operator_launch_membership').fetchone()[0] == 1


def test_registry_executor_has_no_operation_fallback():
    executor = AdapterRegistryExecutor({'watchtower': ('FIRST_AVAILABLE', 'v1')}, {'FIRST_AVAILABLE': lambda _: {'qualified': True}})
    assert executor('ignored', {'operation_id': 'watchtower'}) == {'qualified': True}
    with pytest.raises(AdapterUnavailable):
        executor('ignored', {'operation_id': 'nexus'})
    with pytest.raises(AdapterUnavailable):
        AdapterRegistryExecutor({'byzantine': ('BYZ', 'v1')}, {})('ignored', {'operation_id': 'byzantine'})
