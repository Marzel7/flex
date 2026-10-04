import sqlite3
import pytest

from src.ops.canonical_membership_outbox import append_transition, ensure_schema, read_after
from src.ops.monitor_live_admission import commit_membership_and_outbox, ensure_schema as ensure_admission

def db():
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE operator_launch_membership(mint TEXT PRIMARY KEY,operator_id TEXT,source_population_id TEXT,assigned_at INTEGER,event_id TEXT)')
    ensure_schema(conn)
    return conn

def test_monotonic_compact_immutable_and_forward_only_reader():
    conn=db()
    one=append_transition(conn,event_type='MEMBERSHIP_ASSIGNED',mint='a',operator_id='watchtower',canonical_event_id=None,assigned_at=1,previous_operator_id=None,writer_identity='fixture',created_at=1)
    two=append_transition(conn,event_type='MEMBERSHIP_REASSIGNED',mint='a',operator_id='byzantine',canonical_event_id='event',assigned_at=2,previous_operator_id='watchtower',writer_identity='fixture',created_at=2)
    assert (one,two)==(1,2)
    assert [r[0] for r in read_after(conn,0)]==[1,2]
    with pytest.raises(sqlite3.IntegrityError): conn.execute('UPDATE canonical_membership_outbox SET mint="x" WHERE outbox_id=1')
    with pytest.raises(sqlite3.IntegrityError): conn.execute('DELETE FROM canonical_membership_outbox WHERE outbox_id=1')

def test_monitor_admission_legacy_and_canonical_outboxes_share_rollback_and_commit():
    conn=db(); ensure_admission(conn); conn.commit()
    conn.execute('BEGIN')
    commit_membership_and_outbox(conn,operation_id='watchtower',mint='m',source_population_id='s',membership_id='e',committed_at=10)
    conn.rollback()
    assert conn.execute('SELECT count(*) FROM operator_launch_membership').fetchone()[0]==0
    assert conn.execute('SELECT count(*) FROM canonical_membership_outbox').fetchone()[0]==0
    assert conn.execute('SELECT count(*) FROM monitor_admission_outbox').fetchone()[0]==0
    conn.execute('BEGIN')
    commit_membership_and_outbox(conn,operation_id='watchtower',mint='m',source_population_id='s',membership_id='e',committed_at=10)
    conn.commit()
    assert conn.execute('SELECT count(*) FROM operator_launch_membership').fetchone()[0]==1
    assert conn.execute('SELECT count(*) FROM canonical_membership_outbox').fetchone()[0]==1
    assert conn.execute('SELECT count(*) FROM monitor_admission_outbox').fetchone()[0]==1
