"""Opt-in DEV004 replay of committed WATCHTOWER membership, never acquisition.

A single fixed cursor row pages existing outcome-index entries. An absent or
invalid canonical outcome is not a projection intent. Membership PK and the
existing membership/outbox transaction are the durable completion identity;
cursor acknowledgement is a separate commit and can safely be repeated.
"""
import json
import os
import time
from pathlib import Path

FLAG = 'DEV004_WALKBACK_PROJECTION_RECOVERY_ENABLED'
BATCH = 32
MAX_DB_BYTES = 64 * 1024 * 1024
MAX_WAL_BYTES = 16 * 1024 * 1024
MAX_EVIDENCE_BYTES = 8192
MAX_VM_STEPS = 100000
MAX_SQL_SECONDS = .25
CURSOR_DDL = '''CREATE TABLE IF NOT EXISTS dev004_walkback_projection_cursor (
 id INTEGER PRIMARY KEY CHECK(id=1), last_rowid INTEGER NOT NULL CHECK(last_rowid>=0)
)'''
SELECTION = ("SELECT rowid,mint FROM wt_walkback_queue INDEXED BY ix_wbq_outcome "
             "WHERE intelligence_outcome='WATCHTOWER_CONFIRMED' AND rowid>? "
             "ORDER BY rowid LIMIT 32")


def _storage(conn):
    page_size = conn.execute('PRAGMA page_size').fetchone()[0]
    if page_size * conn.execute('PRAGMA page_count').fetchone()[0] > MAX_DB_BYTES:
        raise ValueError('RECOVERY_DATABASE_BUDGET')
    current = conn.execute('PRAGMA max_page_count').fetchone()[0]
    conn.execute('PRAGMA max_page_count=' + str(min(current, MAX_DB_BYTES // page_size)))
    for _, name, file in conn.execute('PRAGMA database_list'):
        if name == 'main' and file:
            wal = Path(file + '-wal')
            if wal.exists() and wal.stat().st_size >= MAX_WAL_BYTES:
                raise ValueError('RECOVERY_WAL_PRESSURE')


def _eligible(conn, mint):
    from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID
    q = conn.execute('SELECT status,intelligence_outcome,creator,treasury,subprov,'
                     'create_anchor_signature,create_anchor_audit_state,updated_at,completed_at '
                     'FROM wt_walkback_queue WHERE mint=?', (mint,)).fetchone()
    if not q or q['status'] != 'complete' or q['intelligence_outcome'] != 'WATCHTOWER_CONFIRMED':
        return 'NOT_COMPLETED_CONFIRMED'
    if (not isinstance(mint, str) or not 32 <= len(mint) <= 44 or
            any(not isinstance(q[k], str) or not 32 <= len(q[k]) <= 44
                for k in ('creator', 'treasury', 'subprov'))):
        return 'CANONICAL_IDENTITY_MALFORMED'
    a = conn.execute('SELECT outcome_type,operator_id,confidence,should_retry,'
                     'source_queue_updated_at,completed_at,terminal_entity,terminal_entity_type,substr(evidence_json,1,8193) evidence '
                     'FROM wt_attribution_outcomes WHERE mint=?', (mint,)).fetchone()
    if not a or (a['outcome_type'], a['operator_id'], a['confidence'], a['should_retry']) != (
            'CANONICAL_OPERATOR_REACHED', WATCHTOWER_OPERATOR_ID, 'HIGH', 0):
        return 'CANONICAL_AUTHORITY_MISSING_OR_CONFLICTING'
    if a['terminal_entity'] != WATCHTOWER_OPERATOR_ID or a['terminal_entity_type'] != 'CANONICAL_OPERATOR':
        return 'CANONICAL_TERMINAL_CONFLICT'
    if (not q['completed_at'] or not q['updated_at'] or
            a['source_queue_updated_at'] != q['updated_at'] or a['completed_at'] != q['completed_at']):
        return 'CANONICAL_QUEUE_VERSION_CONFLICT'
    from src.core.deep_walkback import valid_signature
    if not valid_signature(q['create_anchor_signature']) or q['create_anchor_audit_state'] != 'VALID':
        return 'CREATE_AUTHORITY_INCOMPLETE'
    text = a['evidence'] or ''
    if len(text.encode()) > MAX_EVIDENCE_BYTES:
        return 'CANONICAL_EVIDENCE_BUDGET'
    try:
        evidence = json.loads(text)
    except (ValueError, TypeError):
        return 'CANONICAL_EVIDENCE_MALFORMED'
    if (not isinstance(evidence, dict) or evidence.get('creator') != q['creator'] or
            evidence.get('treasuries') != [q['treasury']] or
            evidence.get('subprovisioners') != [q['subprov']]):
        return 'CANONICAL_ROUTE_CONFLICT'
    operator = conn.execute('SELECT display_name,status FROM operators WHERE operator_id=?',
                            (WATCHTOWER_OPERATOR_ID,)).fetchone()
    if not operator or operator['display_name'] != 'WATCHTOWER' or operator['status'] in ('MERGED','REJECTED','RETIRED'):
        return 'OPERATOR_AUTHORITY_CONFLICT'
    member = conn.execute('SELECT operator_id FROM operator_launch_membership WHERE mint=?', (mint,)).fetchone()
    if member:
        return 'ALREADY_PRESENT' if member[0] == WATCHTOWER_OPERATOR_ID else 'EXISTING_OTHER_OPERATOR'
    return None


def recover_batch(conn, *, core_db_path=None, fault=lambda point: None):
    if os.environ.get(FLAG) != '1':
        return {'status': 'DISABLED', 'examined': 0, 'results': []}
    if conn.in_transaction:
        return {'status': 'HOLD', 'reason': 'RECOVERY_REQUIRES_IDLE_CONNECTION', 'examined': 0}
    result = {'status': 'PASS', 'examined': 0, 'results': []}
    try:
        _storage(conn)
        conn.execute(CURSOR_DDL)
        conn.execute('INSERT OR IGNORE INTO dev004_walkback_projection_cursor VALUES(1,0)')
        conn.commit()
        cursor = conn.execute('SELECT last_rowid FROM dev004_walkback_projection_cursor WHERE id=1').fetchone()[0]
        plan = [r[3] for r in conn.execute('EXPLAIN QUERY PLAN ' + SELECTION, (cursor,))]
        if any('SCAN ' in p.upper() or 'TEMP B-TREE' in p.upper() for p in plan):
            raise ValueError('RECOVERY_SELECTION_NOT_INDEXED')
        rows = conn.execute(SELECTION, (cursor,)).fetchall()
        result['plan'] = plan
        if not rows:
            conn.execute('UPDATE dev004_walkback_projection_cursor SET last_rowid=0 WHERE id=1')
            conn.commit()
            result['wrapped'] = True
        for rowid, mint in rows:
            _storage(conn)
            steps = [0]
            deadline = time.monotonic() + MAX_SQL_SECONDS
            def stop():
                steps[0] += 100
                return int(steps[0] >= MAX_VM_STEPS or time.monotonic() > deadline)
            conn.set_progress_handler(stop, 100)
            try:
                conn.execute('BEGIN IMMEDIATE')
                reason = _eligible(conn, mint)
                if reason is None:
                    from src.core.watchtower_registry_promotion import project_watchtower_confirmed_membership
                    fault('before_membership')
                    projected = project_watchtower_confirmed_membership(
                        conn, mint, core_db_path=core_db_path, refresh_activity=False)
                    reason = projected['action']
                    if reason not in ('projected', 'already_present'):
                        conn.rollback()
                    else:
                        conn.commit()
                        fault('after_membership_commit')
                else:
                    conn.rollback()
            finally:
                conn.set_progress_handler(None, 0)
            # Ack after member/outbox commit. Crash here rechecks the same row;
            # its existing membership is skipped without re-enqueue or mutation.
            fault('before_ack')
            conn.execute('UPDATE dev004_walkback_projection_cursor SET last_rowid=? WHERE id=1', (rowid,))
            conn.commit()
            fault('after_ack')
            result['examined'] += 1
            result['results'].append({'mint': mint, 'action': reason})
    except Exception as exc:
        conn.set_progress_handler(None, 0)
        conn.rollback()
        result.update(status='HOLD', reason=type(exc).__name__ + ':' + str(exc)[:200])
    return result
