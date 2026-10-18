"""Qualified prospective CREATE delivery; no providers, implicit DDL or private writer.

Producer stage() participates in its caller's authoritative Core transaction.
Dispatcher callbacks use existing shared lanes and acknowledge commits before returning.
Disk/WAL checks are stop conditions, not VFS hard quotas.
"""
from dataclasses import dataclass, asdict
from hashlib import sha256
import json
from pathlib import Path
import shutil
import time

PUMP_PROGRAM='6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
BASE58=frozenset('123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz')
BATCH=16
MAX_PENDING=10000
MAX_ACK=70000
MAX_TRIES=5
MIN_FREE=2_000_000_000
WAL_STOP=48*1024*1024
MAX_PROOF_BYTES=1024
MAX_ADMITTED=100000
EXPIRY=30*86400
RETENTION=7*86400
MAIN_GROWTH=64*1024*1024
SCHEMA='''CREATE TABLE wb_create_control(id INTEGER PRIMARY KEY CHECK(id=1),epoch TEXT NOT NULL CHECK(length(epoch)<=64),activated_at INTEGER NOT NULL,enabled INTEGER NOT NULL CHECK(enabled IN(0,1)),pending_count INTEGER NOT NULL DEFAULT 0 CHECK(pending_count BETWEEN 0 AND 10000),ack_count INTEGER NOT NULL DEFAULT 0 CHECK(ack_count BETWEEN 0 AND 70000),admitted INTEGER NOT NULL DEFAULT 0 CHECK(admitted BETWEEN 0 AND 100000),core_base INTEGER NOT NULL,ops_base INTEGER NOT NULL,birth_retry_admitted INTEGER NOT NULL DEFAULT 0 CHECK(birth_retry_admitted BETWEEN 0 AND 100000));
CREATE TABLE wb_create_delivery(seq INTEGER PRIMARY KEY AUTOINCREMENT,epoch TEXT NOT NULL CHECK(length(epoch)<=64),mint TEXT NOT NULL CHECK(length(mint)<=44),creator TEXT NOT NULL CHECK(length(creator)<=44),signature TEXT NOT NULL CHECK(length(signature)<=88),digest TEXT NOT NULL CHECK(length(digest)=64),source TEXT NOT NULL CHECK(source IN('STRICT_RPC','PUMPPORTAL')),qualification TEXT NOT NULL CHECK(length(qualification)<=64),migrated_at INTEGER NOT NULL,slot INTEGER,block_time INTEGER,state TEXT NOT NULL CHECK(state IN('PENDING','ACK','BLOCKED')),due INTEGER NOT NULL,tries INTEGER NOT NULL CHECK(tries BETWEEN 0 AND 5),last_error_code TEXT CHECK(length(last_error_code)<=64),acked_at INTEGER,UNIQUE(epoch,mint));
CREATE INDEX wb_create_due ON wb_create_delivery(state,due,seq);
CREATE INDEX wb_create_ack ON wb_create_delivery(state,acked_at,seq);'''

class Unavailable(RuntimeError):
    pass


def identity(value,lengths,size):
    if not isinstance(value,str) or len(value) not in lengths or not set(value)<=BASE58:return False
    alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    number=0
    for character in value:number=number*58+alphabet.index(character)
    decoded=(number.bit_length()+7)//8 + len(value)-len(value.lstrip('1'))
    return decoded==size


@dataclass(frozen=True)
class Proof:
    mint:str
    creator:str
    signature:str
    source:str
    qualification:str
    before_signature:str
    migrated_at:int
    program:str=PUMP_PROGRAM
    slot:int|None=None
    block_time:int|None=None

    def encode(self):
        if not identity(self.mint,range(32,45),32) or not identity(self.creator,range(32,45),32):
            raise Unavailable('INVALID_MINT_OR_CREATOR')
        if not identity(self.signature,range(64,89),64) or self.before_signature!=self.signature:
            raise Unavailable('INVALID_CREATE_BOUNDARY')
        if self.program!=PUMP_PROGRAM or (self.source,self.qualification) not in (('STRICT_RPC','DEV074_STRICT_CREATE'),('PUMPPORTAL','PUMPPORTAL_NEW_TOKEN')):
            raise Unavailable('UNQUALIFIED_PROGRAM_OR_SOURCE')
        for value in (self.migrated_at,self.slot,self.block_time):
            if value is not None and (type(value) is not int or value<0):raise Unavailable('INVALID_TIME_OR_SLOT')
        if type(self.migrated_at) is not int:raise Unavailable('MIGRATION_REQUIRED')
        text=json.dumps(asdict(self),sort_keys=True,separators=(',',':'))
        if len(text.encode())>MAX_PROOF_BYTES:raise Unavailable('PROOF_SIZE')
        return text,sha256(text.encode()).hexdigest()


def rpc_proof(mint,data,migrated_at):
    if not (data.get('is_pumpfun_create') is True and data.get('creator_source')=='rpc_pump_create' and data.get('validation_status')!='inferred_create'):
        raise Unavailable('SIGNER_OR_UNQUALIFIED_CREATE')
    return Proof(mint,data['creator'],data['create_tx_signature'],'STRICT_RPC','DEV074_STRICT_CREATE',data['create_tx_signature'],migrated_at)


def storage_check(core_path,ops_path,*,core_base=None,ops_base=None):
    for path,base in ((Path(core_path),core_base),(Path(ops_path),ops_base)):
        if shutil.disk_usage(path.parent).free<MIN_FREE:raise Unavailable('DISK_HEADROOM')
        wal=Path(str(path)+'-wal');journal=Path(str(path)+'-journal')
        if wal.exists() and wal.stat().st_size>=WAL_STOP:raise Unavailable('WAL_PRESSURE')
        if journal.exists() and journal.stat().st_size>=16*1024*1024:raise Unavailable('JOURNAL_PRESSURE')
        if base is not None and path.stat().st_size>base+MAIN_GROWTH:raise Unavailable('MAIN_GROWTH')


def initialize(conn,*,epoch,activated_at,core_base,ops_base):
    """Explicit migration only. Caller must own the shared writer transaction."""
    if not conn.in_transaction:raise ValueError('EXPLICIT_MIGRATION_TRANSACTION_REQUIRED')
    if not isinstance(epoch,str) or not 1<=len(epoch)<=64 or type(activated_at) is not int:
        raise ValueError('explicit epoch and activation required')
    for statement in SCHEMA.split(';'):
        if statement.strip():conn.execute(statement)
    conn.execute('INSERT INTO wb_create_control(id,epoch,activated_at,enabled,core_base,ops_base) VALUES(1,?,?,0,?,?)',(epoch,activated_at,core_base,ops_base))
    if conn.execute("SELECT 1 FROM sqlite_schema WHERE name='birth_persist_queue'").fetchone():
        conn.execute("ALTER TABLE birth_persist_queue ADD COLUMN wb_birth_source TEXT")


def stage(conn,proof,*,core_path,ops_path,now=None):
    """Never commit here; propagate errors so authority and intent roll back together."""
    if not conn.in_transaction:raise Unavailable('AUTHORITY_TRANSACTION_REQUIRED')
    control=conn.execute('SELECT epoch,activated_at,enabled,pending_count,ack_count,admitted,core_base,ops_base FROM wb_create_control WHERE id=1').fetchone()
    if not control:raise Unavailable('ACTIVATION_MISSING')
    epoch,start,enabled,pending,ack,admitted,core_base,ops_base=control
    now=int(time.time() if now is None else now)
    if not enabled or proof.migrated_at<start:return 'OUTSIDE_ACTIVATION'
    if now>=start+EXPIRY:raise Unavailable('ACTIVATION_EXPIRED')
    storage_check(core_path,ops_path,core_base=core_base,ops_base=ops_base)
    text,digest=proof.encode()
    token=conn.execute('SELECT earliest_tx_creator,pf_ws_creator,create_tx_signature,migrated_at FROM token_analysis WHERE mint=?',(proof.mint,)).fetchone()
    if token is None or any(v and v!=proof.creator for v in token[:2]) or proof.creator not in token[:2] or token[2]!=proof.signature or token[3]!=proof.migrated_at:
        raise Unavailable('CORE_AUTHORITY_CONFLICT')
    old=conn.execute('SELECT digest,creator,signature FROM wb_create_delivery WHERE epoch=? AND mint=?',(epoch,proof.mint)).fetchone()
    if old:
        if old[1:]!=(proof.creator,proof.signature):raise Unavailable('CONFLICTING_PROOF')
        return 'DUPLICATE'
    if pending>=MAX_PENDING or ack>=MAX_ACK or admitted>=MAX_ADMITTED:raise Unavailable('INTENT_CAPACITY')
    conn.execute("INSERT INTO wb_create_delivery(epoch,mint,creator,signature,digest,source,qualification,migrated_at,slot,block_time,state,due,tries) VALUES(?,?,?,?,?,?,?,?,?,?,'PENDING',?,0)",(epoch,proof.mint,proof.creator,proof.signature,digest,proof.source,proof.qualification,proof.migrated_at,proof.slot,proof.block_time,now))
    conn.execute('UPDATE wb_create_control SET pending_count=pending_count+1,admitted=admitted+1 WHERE id=1')
    return 'PENDING'


def _activate(ops,proof,start):
    row=ops.execute('SELECT creator,status,path_state,create_anchor_signature,enqueued_at FROM wt_walkback_queue WHERE mint=?',(proof.mint,)).fetchone()
    if row is None:raise Unavailable('OPS_ROW_MISSING')
    creator,status,path,sig,enqueued=row
    if enqueued is None or enqueued<start:raise Unavailable('HISTORICAL_ROW')
    if creator and creator!=proof.creator:raise Unavailable('OPS_CREATOR_CONFLICT')
    if sig and sig!=proof.signature:raise Unavailable('OPS_SIGNATURE_CONFLICT')
    if status in ('pending','running','complete') and creator==proof.creator and sig==proof.signature:return 'ALREADY_COMMITTED'
    if status!='waiting' or path!='WAITING_FOR_CREATE_ANCHOR':raise Unavailable('OPS_STATE_INELIGIBLE')
    changed=ops.execute("UPDATE wt_walkback_queue SET creator=?,create_anchor_signature=?,create_anchor_slot=?,create_anchor_block_time=?,create_anchor_source=?,create_anchor_audit_state='VALID',path_state='CREATE_ANCHORED',status='pending' WHERE mint=? AND status='waiting' AND path_state='WAITING_FOR_CREATE_ANCHOR'",(proof.creator,proof.signature,proof.slot,proof.block_time,'CORE_QUALIFIED_CREATE',proof.mint))
    if changed.rowcount!=1:raise Unavailable('OPS_CONCURRENT_CHANGE')
    return 'ACTIVATED'


def dispatch(*,read_core,write_core,write_ops,core_path,ops_path,now=None,fault=lambda stage:None):
    now=int(time.time() if now is None else now)
    with read_core() as core:
        control=core.execute('SELECT epoch,activated_at,core_base,ops_base FROM wb_create_control WHERE id=1').fetchone()
        if not control:raise Unavailable('ACTIVATION_MISSING')
        epoch,start,core_base,ops_base=control
        rows=core.execute("SELECT seq,mint,digest,tries,epoch,creator,signature,source,qualification,migrated_at,slot,block_time FROM wb_create_delivery WHERE state='PENDING' AND due<=? ORDER BY due,seq LIMIT ?",(now,BATCH)).fetchall()
    def check():storage_check(core_path,ops_path,core_base=core_base,ops_base=ops_base)
    check();outcomes=[]
    for seq,mint,digest,tries,row_epoch,row_creator,row_signature,source,qualification,migrated_at,slot,block_time in rows:
        try:
            proof=Proof(mint,row_creator,row_signature,source,qualification,row_signature,migrated_at,slot=slot,block_time=block_time);canonical,checksum=proof.encode()
            if checksum!=digest or row_epoch!=epoch or proof.mint!=mint or proof.creator!=row_creator or proof.signature!=row_signature or proof.migrated_at<start:raise Unavailable('CORRUPT_PROOF')
            with read_core() as current:
                token=current.execute('SELECT earliest_tx_creator,pf_ws_creator,create_tx_signature,migrated_at FROM token_analysis WHERE mint=?',(proof.mint,)).fetchone()
            if token is None or any(v and v!=proof.creator for v in token[:2]) or proof.creator not in token[:2] or token[2]!=proof.signature or token[3]!=proof.migrated_at:raise Unavailable('CORE_AUTHORITY_CONFLICT')
            fault('before_ops');check()
            result=write_ops(lambda conn:_activate(conn,proof,start));fault('after_ops_commit')
            def ack(conn):
                if conn.execute('SELECT ack_count FROM wb_create_control WHERE id=1').fetchone()[0]>=MAX_ACK:raise Unavailable('ACK_CAPACITY')
                changed=conn.execute("UPDATE wb_create_delivery SET state='ACK',acked_at=?,last_error_code=NULL WHERE seq=? AND epoch=? AND digest=? AND state='PENDING'",(now,seq,epoch,digest))
                if changed.rowcount:conn.execute('UPDATE wb_create_control SET pending_count=pending_count-1,ack_count=ack_count+1 WHERE id=1')
            fault('before_ack');check();write_core(ack);fault('after_ack');outcomes.append((mint,result))
        except Exception as exc:
            reason=str(exc.args[0]) if isinstance(exc,Unavailable) and exc.args else getattr(exc,'sqlite_errorname',type(exc).__name__)
            reason=reason[:64]
            def retry(conn):
                conn.execute("UPDATE wb_create_delivery SET tries=MIN(tries+1,?),state=CASE WHEN tries+1>=? THEN 'BLOCKED' ELSE 'PENDING' END,due=?,last_error_code=? WHERE seq=? AND digest=? AND state='PENDING'",(MAX_TRIES,MAX_TRIES,now+min(300,15*(2**min(tries,4))),reason,seq,digest))
            check();write_core(retry)
            outcomes.append((mint,reason))
    return outcomes


def maintenance_tick(core_path,ops_path):
    """Adapter for the installed worker loop; never creates schema or a worker."""
    from contextlib import contextmanager
    from src.utils.db_locking import managed_db_connect
    from src.core.database_write_service import database_write_service
    @contextmanager
    def reader():
        with managed_db_connect(core_path,read_only=True) as conn:yield conn
    def writer(path,label):
        selector='walkback-create:'+str(Path(path).resolve())
        database_write_service.register_database(selector,path)
        return lambda action:database_write_service.submit(selector,label,action)
    with reader() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_schema WHERE name='wb_create_control'").fetchone():return 'DISABLED'
    result=dispatch(read_core=reader,write_core=writer(core_path,'create-proof-ack'),write_ops=writer(ops_path,'create-proof-delivery'),core_path=core_path,ops_path=ops_path)
    @contextmanager
    def ops_reader():
        with managed_db_connect(ops_path,read_only=True) as conn:yield conn
    removed=prune(read_core=reader,read_ops=ops_reader,write_core=writer(core_path,'create-proof-retention'),core_path=core_path,ops_path=ops_path)
    return {'deliveries':result,'pruned':removed}


def activated(conn):
    exists=conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='wb_create_control'").fetchone()
    if not exists:return False
    row=conn.execute('SELECT enabled FROM wb_create_control WHERE id=1').fetchone()
    return bool(row and row[0])


def stage_rpc_completion(conn,mint,data,*,core_path,ops_path):
    if not activated(conn):return 'DISABLED'
    token=conn.execute('SELECT migrated_at FROM token_analysis WHERE mint=?',(mint,)).fetchone()
    if not token or token[0] is None:return 'NOT_MIGRATED'
    return stage(conn,rpc_proof(mint,data,token[0]),core_path=core_path,ops_path=ops_path)


def stage_birth_completion(conn,mint,creator,signature,*,core_path,ops_path,birth_evidence_source=None):
    if not activated(conn):return 'DISABLED'
    token=conn.execute('SELECT migrated_at FROM token_analysis WHERE mint=?',(mint,)).fetchone()
    if not token or token[0] is None:return 'NOT_MIGRATED'
    if birth_evidence_source!='PUMPPORTAL_NEW_TOKEN':return 'UNQUALIFIED_BIRTH_SOURCE'
    return stage(conn,Proof(mint,creator,signature,'PUMPPORTAL','PUMPPORTAL_NEW_TOKEN',signature,token[0]),core_path=core_path,ops_path=ops_path)


def prune(*,read_core,read_ops,write_core,core_path,ops_path,now=None):
    """New ACKs only; retain pending/BLOCKED. Never mutate OPS or VACUUM."""
    now=int(time.time() if now is None else now)
    storage_check(core_path,ops_path)
    with read_core() as c:
        rows=c.execute("SELECT seq,mint,creator,signature,digest FROM wb_create_delivery WHERE state='ACK' AND acked_at<=? ORDER BY acked_at,seq LIMIT ?",(now-RETENTION,BATCH)).fetchall()
    eligible=[]
    with read_ops() as o:
        for seq,mint,creator,sig,digest in rows:
            row=o.execute('SELECT creator,create_anchor_signature,status FROM wt_walkback_queue WHERE mint=?',(mint,)).fetchone()
            if row is not None and tuple(row)==(creator,sig,'complete'):eligible.append((seq,digest))
    def remove(c):
        removed=0
        for seq,digest in eligible:
            removed+=c.execute("DELETE FROM wb_create_delivery WHERE seq=? AND digest=? AND state='ACK' AND acked_at<=?",(seq,digest,now-RETENTION)).rowcount
        if removed:c.execute('UPDATE wb_create_control SET ack_count=ack_count-? WHERE id=1',(removed,))
        return removed
    return write_core(remove) if eligible else 0


def set_birth_retry_source(conn,mint,source):
    if source!='PUMPPORTAL_NEW_TOKEN' or not activated(conn):return
    row=conn.execute('SELECT wb_birth_source FROM birth_persist_queue WHERE mint=?',(mint,)).fetchone()
    if row is None:raise Unavailable('BIRTH_RETRY_ROW_MISSING')
    if row[0] and row[0]!=source:raise Unavailable('BIRTH_SOURCE_CONFLICT')
    if not row[0]:
        changed=conn.execute('UPDATE wb_create_control SET birth_retry_admitted=birth_retry_admitted+1 WHERE id=1 AND birth_retry_admitted<100000')
        if changed.rowcount!=1:raise Unavailable('BIRTH_PROVENANCE_CAPACITY')
        conn.execute('UPDATE birth_persist_queue SET wb_birth_source=? WHERE mint=?',(source,mint))
