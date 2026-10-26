"""Isolated DEV audit contract. Never imported by an application entrypoint.

OPS receipt visibility is commit authority; UTC stamps are bounds, not commit
instants. Existing shared writer would own the transaction in any later design.
"""
import contextlib
import hashlib
import json
import os
import math
from pathlib import Path
import sqlite3
import time
import uuid

INDEX = 'dev_create_migration_time_v1'
INDEX_SQL = f'CREATE INDEX {INDEX} ON token_analysis(migrated_at,mint) WHERE migrated_at IS NOT NULL'
MAX_ROWS = 4096
MAX_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 4096
MAX_TX_EVENTS = 128
MAX_PAYLOAD = 1024
SCHEMA = (
 'CREATE TABLE create_audit_owner_v1(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL CHECK(version=1),generation TEXT NOT NULL,events INTEGER NOT NULL CHECK(events BETWEEN 0 AND 4096),bytes INTEGER NOT NULL CHECK(bytes BETWEEN 0 AND 4194304))',
 'CREATE TABLE create_audit_commits_v1(commit_order INTEGER PRIMARY KEY AUTOINCREMENT,token TEXT NOT NULL UNIQUE,staged_utc_ns INTEGER NOT NULL)',
 'CREATE TABLE create_audit_events_v1(event_key TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN (\'HANDOFF\',\'ANCHOR\')),commit_order INTEGER NOT NULL REFERENCES create_audit_commits_v1(commit_order),payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB))<=1024))',
 'CREATE TABLE create_audit_observations_v1(commit_order INTEGER PRIMARY KEY REFERENCES create_audit_commits_v1(commit_order),observed_utc_ns INTEGER NOT NULL,observation_kind TEXT NOT NULL CHECK(observation_kind IN (\'COMMIT_RETURN\',\'RECOVERED_READBACK\')))',
)

class Hold(RuntimeError):
    pass


def dev_store(conn):
    path = Path(conn.execute('PRAGMA database_list').fetchone()[2])
    if not path.is_absolute() or path.is_symlink() or not any(path.resolve().is_relative_to(Path(r)) for r in ('/private/tmp','/tmp')):
        raise Hold('DISPOSABLE_PATH_REQUIRED')
    space=os.statvfs(path.parent)
    if space.f_bavail*space.f_frsize<2_000_000_000+64*1024*1024:
        raise Hold('DISK_FLOOR')
    if path.stat().st_size >= 499000000:
        raise Hold('FILE_CAP')
    wal = Path(str(path)+'-wal')
    if wal.exists() and wal.stat().st_size >= 48*1024*1024:
        raise Hold('WAL_PRESSURE')
    return path


def norm(sql):
    return ' '.join(sql.lower().split())


def install_index(conn):
    dev_store(conn)
    sql = conn.execute('SELECT sql FROM sqlite_master WHERE name=?',(INDEX,)).fetchone()
    if sql and norm(sql[0]) != norm(INDEX_SQL):
        raise Hold('INCOMPATIBLE_INDEX')
    if not sql:
        conn.execute(INDEX_SQL)


def capture_core(conn,start,end,*,max_rows=MAX_ROWS,between_batches=lambda:None):
    """One bounded read snapshot; interrupted runs restart, never merge cursors.

    This selects ALL canonical migrated_at values in the interval, including
    missing migration signatures or handoffs. No operational-owner proxy.
    """
    dev_store(conn)
    if conn.in_transaction or any(type(x) not in (int,float) or not -2**62<=x<=2**62 or not math.isfinite(x) for x in (start,end)) or start>=end or not 1<=max_rows<=MAX_ROWS:
        raise Hold('INVALID_CAPTURE')
    original_window=(start,end)
    # Canonical migration times are integer UTC seconds. Ceil preserves exact
    # membership in the caller's fractional half-open audit window.
    start,end=math.ceil(start),math.ceil(end)
    sql=conn.execute('SELECT sql FROM sqlite_master WHERE name=?',(INDEX,)).fetchone()
    if not sql or norm(sql[0])!=norm(INDEX_SQL):raise Hold('INDEX_REQUIRED')
    query=f'SELECT mint,migrated_at FROM token_analysis INDEXED BY {INDEX} WHERE migrated_at>=? AND migrated_at<? ORDER BY migrated_at,mint LIMIT ?'
    plan=conn.execute('EXPLAIN QUERY PLAN '+query,(start,end,max_rows+1)).fetchall()
    if not any('SEARCH' in p[3] and INDEX in p[3] for p in plan):raise Hold('BOUNDED_PLAN_REQUIRED')
    result=[];size=0;began=time.monotonic()
    conn.execute('BEGIN')
    try:
        cursor=conn.execute(query,(start,end,max_rows+1))
        while True:
            rows=cursor.fetchmany(128)
            if not rows:break
            for mint,stamp in rows:
                if not isinstance(mint,str) or not 1<=len(mint)<=44 or type(stamp) is not int:raise Hold('CANONICAL_IDENTITY')
                result.append((mint,stamp));size+=len(mint.encode())+24
                if len(result)>max_rows or size>MAX_BYTES:raise Hold('CAPTURE_CAPACITY')
            dev_store(conn)
            if time.monotonic()-began>2:raise Hold('CAPTURE_TIME_BUDGET')
            between_batches()
        return {'rows':result,'plan':plan,'snapshot':'single SQLite read transaction','complete':True,'bytes':size,'requested_window':original_window,'integer_bounds':(start,end)}
    finally:
        conn.rollback()


def initialize_ops(conn,generation):
    dev_store(conn)
    if conn.in_transaction or not isinstance(generation,str) or not 1<=len(generation)<=64:raise Hold('AUTHORITY_REQUIRED')
    conn.execute('PRAGMA foreign_keys=ON')
    with conn:
        for sql in SCHEMA:
            name=sql.split('(')[0].split()[-1]
            old=conn.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone()
            if old and norm(old[0])!=norm(sql):raise Hold('INCOMPATIBLE_AUDIT_SCHEMA')
            if not old:conn.execute(sql)
        conn.execute('INSERT OR IGNORE INTO create_audit_owner_v1 VALUES(1,1,?,0,0)',(generation,))
        if conn.execute('SELECT generation FROM create_audit_owner_v1 WHERE id=1').fetchone()[0]!=generation:raise Hold('GENERATION_MISMATCH')


def packed(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'))


def key(kind,generation,mint,signature):
    return hashlib.sha256(packed([kind,generation,mint,signature]).encode()).hexdigest()


class Boundary:
    def __init__(self,conn,generation,clock=time.time_ns):
        self.conn=conn;self.generation=generation;self.clock=clock;self.order=None;self.inserted=0;self._activating=False
        dev_store(conn)
        if not conn.in_transaction:raise Hold('WRITER_TRANSACTION_REQUIRED')
        if conn.execute('SELECT generation FROM create_audit_owner_v1 WHERE id=1').fetchone()[0]!=generation:raise Hold('GENERATION_MISMATCH')

    def record(self,kind,mint,signature,detail):
        if kind=='ANCHOR' and not self._activating:raise Hold('ACTIVATION_BOUNDARY_REQUIRED')
        if kind not in ('HANDOFF','ANCHOR') or not isinstance(mint,str) or not 1<=len(mint)<=44 or not isinstance(signature,str) or not 1<=len(signature)<=88:raise Hold('EVENT_IDENTITY')
        payload=packed(dict(detail,mint=mint,signature=signature,generation=self.generation))
        if len(payload.encode())>MAX_PAYLOAD:raise Hold('PAYLOAD_CAPACITY')
        event=key(kind,self.generation,mint,signature)
        old=self.conn.execute('SELECT payload,commit_order FROM create_audit_events_v1 WHERE event_key=?',(event,)).fetchone()
        if old:
            if old[0]!=payload:raise Hold('CONFLICTING_AUDIT_EVIDENCE')
            return old[1]
        if self.inserted>=MAX_TX_EVENTS:raise Hold('TRANSACTION_CAPACITY')
        self.conn.execute('UPDATE create_audit_owner_v1 SET events=events+1,bytes=bytes+? WHERE id=1',(len(payload.encode()),))
        if self.order is None:
            self.order=self.conn.execute('INSERT INTO create_audit_commits_v1(token,staged_utc_ns) VALUES(?,?)',(uuid.uuid4().hex,self.clock())).lastrowid
        self.conn.execute('INSERT INTO create_audit_events_v1 VALUES(?,?,?,?)',(event,kind,self.order,payload))
        self.inserted+=1
        return self.order

    def activate(self,proof,start,accepted_activate):
        # Exactly the existing accepted mutation. An audit insert rolls back
        # with that mutation; it never changes source qualification or attribution.
        result=accepted_activate(self.conn,proof,start)
        event=key('ANCHOR',self.generation,proof.mint,proof.signature)
        if result=='ACTIVATED':
            text,digest=proof.encode()
            row=self.conn.execute('SELECT creator,create_anchor_signature,create_anchor_source,create_anchor_audit_state FROM wt_walkback_queue WHERE mint=?',(proof.mint,)).fetchone()
            if row is None or tuple(row)!=(proof.creator,proof.signature,'CORE_QUALIFIED_CREATE','VALID'):raise Hold('ACTIVATION_READBACK_MISMATCH')
            self._activating=True
            try:self.record('ANCHOR',proof.mint,proof.signature,{'proof':json.loads(text),'digest':digest})
            finally:self._activating=False
        elif result=='ALREADY_COMMITTED':
            # Do not turn a legacy anchor into new provenance/timing evidence.
            old=self.conn.execute('SELECT event_key FROM create_audit_events_v1 WHERE event_key=?',(event,)).fetchone()
            if not old:return 'LEGACY_UNTIMED'
        else:raise Hold('UNEXPECTED_ACTIVATION_RESULT')
        return result


def observe_commit(conn,order,*,recovered=False,clock=time.time_ns):
    if order is None:return
    dev_store(conn)
    if conn.in_transaction:raise Hold('OBSERVATION_BEFORE_COMMIT')
    if not conn.execute('SELECT 1 FROM create_audit_commits_v1 WHERE commit_order=?',(order,)).fetchone():raise Hold('COMMIT_NOT_VISIBLE')
    stamp=clock()  # Actual observation, never a manufactured historical time.
    with conn:
        conn.execute('INSERT OR IGNORE INTO create_audit_observations_v1 VALUES(?,?,?)',(order,stamp,'RECOVERED_READBACK' if recovered else 'COMMIT_RETURN'))


@contextlib.contextmanager
def dev_transaction(conn,generation,*,fault=lambda phase:None,clock=time.time_ns):
    """Fixture harness only. Production integration must use its shared writer."""
    if conn.in_transaction:raise Hold('NESTED_TRANSACTION')
    conn.execute('BEGIN IMMEDIATE')
    try:
        boundary=Boundary(conn,generation,clock)
        yield boundary
        fault('before_commit');conn.commit()
    except BaseException:
        conn.rollback();raise
    fault('after_commit_before_observation')
    observe_commit(conn,boundary.order,clock=clock)


def availability(conn,generation,mint,migration_signature,create_signature):
    if conn.execute('SELECT generation FROM create_audit_owner_v1 WHERE id=1').fetchone()[0]!=generation:raise Hold('GENERATION_MISMATCH')
    def receipt(kind,sig):
        return conn.execute('SELECT commit_order,payload FROM create_audit_events_v1 WHERE event_key=?',(key(kind,generation,mint,sig),)).fetchone()
    handoff=receipt('HANDOFF',migration_signature);anchor=receipt('ANCHOR',create_signature)
    if not handoff or not anchor:return 'UNVERIFIABLE'
    return 'AT_HANDOFF' if anchor[0]==handoff[0] else 'BEFORE_HANDOFF' if anchor[0]<handoff[0] else 'AFTER_HANDOFF'


def utc_bounds(conn,order):
    row=conn.execute('SELECT c.staged_utc_ns,o.observed_utc_ns,o.observation_kind FROM create_audit_commits_v1 c LEFT JOIN create_audit_observations_v1 o USING(commit_order) WHERE c.commit_order=?',(order,)).fetchone()
    if not row:return None
    return {'lower_staged_ns':row[0],'upper_observed_ns':row[1],'observation':row[2],
            'quality':'UPPER_UNAVAILABLE' if row[1] is None else 'CLOCK_UNCERTAIN' if row[1]<row[0] else 'UTC_BRACKET_NOT_COMMIT_INSTANT'}
