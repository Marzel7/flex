import sqlite3
import pytest
from unittest.mock import patch

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

@pytest.mark.parametrize('case',('success','after_membership','append_failure','after_outbox'))
def test_monitor_live_admission_executable_failure_matrix(case):
    conn=db(); ensure_admission(conn); conn.commit()
    try:
        conn.execute('BEGIN')
        if case=='after_membership':
            conn.execute("INSERT INTO operator_launch_membership VALUES('m','watchtower','s',1,'e')")
            raise RuntimeError('INJECTED_AFTER_MEMBERSHIP')
        if case=='append_failure':
            with patch('src.ops.canonical_membership_outbox.append_transition',side_effect=RuntimeError('INJECTED_APPEND_FAILURE')):
                commit_membership_and_outbox(conn,operation_id='watchtower',mint='m',source_population_id='s',membership_id='e',committed_at=1)
        else:
            commit_membership_and_outbox(conn,operation_id='watchtower',mint='m',source_population_id='s',membership_id='e',committed_at=1)
            if case=='after_outbox': raise RuntimeError('INJECTED_AFTER_OUTBOX')
        conn.commit()
    except RuntimeError: conn.rollback()
    count=conn.execute("SELECT count(*) FROM operator_launch_membership").fetchone()[0]
    outbox=conn.execute("SELECT count(*) FROM canonical_membership_outbox").fetchone()[0]
    legacy=conn.execute("SELECT count(*) FROM monitor_admission_outbox").fetchone()[0]
    assert (count,outbox,legacy)==((1,1,1) if case=='success' else (0,0,0))
