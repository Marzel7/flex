"""Versioned, quota-bounded OPS ownership of protected migration notifications.

No provider, replay, retirement, schema auto-creation, or background service.
Only the existing paired authority can authorize destination access.
notification_mint remains an unverified source subject; mint preserves the
original journal resolution, never canonical migration/attribution authority.
Missing legacy subject evidence remains unknown, not reconstructed here.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import resource
import sqlite3
import stat

MAX_ROWS = 8192
MAX_BYTES = 64_000_000
MAX_RECORD_BYTES = 4096
MAX_BATCH = 20
MAX_DB_BYTES = 480_000_000
MAX_FILE_BYTES = 499_000_000
WAL_ADMISSION_BYTES = 8_000_000
TRANSACTION_RESERVE = 2_000_000
DISK_FLOOR = 2_000_000_000
RIGHTS = 'PROTECTED_NO_AUTOMATIC_REPLAY'
TABLE = 'migration_terminal_evidence_v1'
META = 'migration_terminal_owner_v1'
DDL = (
    "CREATE TABLE migration_terminal_owner_v1 (singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL CHECK(version=1), contract TEXT NOT NULL, used_records INTEGER NOT NULL CHECK(used_records BETWEEN 0 AND 8192), used_bytes INTEGER NOT NULL CHECK(used_bytes BETWEEN 0 AND 64000000))",
    "CREATE TABLE migration_terminal_evidence_v1 (identity TEXT PRIMARY KEY, signature TEXT NOT NULL UNIQUE, journal_identity TEXT NOT NULL, journal_path TEXT NOT NULL, transfer_generation TEXT NOT NULL, payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB))<=4096), evidence_hash TEXT NOT NULL, allocation INTEGER NOT NULL CHECK(allocation BETWEEN 1 AND 8192), recovery_rights TEXT NOT NULL CHECK(recovery_rights='PROTECTED_NO_AUTOMATIC_REPLAY'), transfer_state TEXT NOT NULL CHECK(transfer_state IN ('PERSISTED','VERIFIED')))",
    "CREATE TRIGGER migration_terminal_budget_v1 BEFORE INSERT ON migration_terminal_evidence_v1 BEGIN SELECT CASE WHEN (SELECT used_records FROM migration_terminal_owner_v1 WHERE singleton=1)>=8192 OR (SELECT used_bytes FROM migration_terminal_owner_v1 WHERE singleton=1)+NEW.allocation>64000000 THEN RAISE(ABORT,'TERMINAL_CAPACITY') END; END",
    "CREATE TRIGGER migration_terminal_account_v1 AFTER INSERT ON migration_terminal_evidence_v1 BEGIN UPDATE migration_terminal_owner_v1 SET used_records=used_records+1,used_bytes=used_bytes+NEW.allocation WHERE singleton=1; END",
    "CREATE TRIGGER migration_terminal_preserve_v1 BEFORE DELETE ON migration_terminal_evidence_v1 BEGIN SELECT RAISE(ABORT,'PROTECTED_TERMINAL_EVIDENCE'); END",
    "CREATE TRIGGER migration_terminal_immutable_v1 BEFORE UPDATE ON migration_terminal_evidence_v1 WHEN OLD.identity IS NOT NEW.identity OR OLD.signature IS NOT NEW.signature OR OLD.journal_identity IS NOT NEW.journal_identity OR OLD.journal_path IS NOT NEW.journal_path OR OLD.transfer_generation IS NOT NEW.transfer_generation OR OLD.payload IS NOT NEW.payload OR OLD.evidence_hash IS NOT NEW.evidence_hash OR OLD.allocation IS NOT NEW.allocation OR OLD.recovery_rights IS NOT NEW.recovery_rights OR NOT (OLD.transfer_state=NEW.transfer_state OR (OLD.transfer_state='PERSISTED' AND NEW.transfer_state='VERIFIED')) BEGIN SELECT RAISE(ABORT,'IMMUTABLE_TERMINAL_EVIDENCE'); END",
)
CONTRACT = hashlib.sha256('\n'.join(DDL).encode()).hexdigest()
COLUMNS = 'identity,signature,journal_identity,journal_path,transfer_generation,payload,evidence_hash,allocation,recovery_rights,transfer_state'

class TerminalHold(RuntimeError):
    pass

def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)

class TerminalEvidenceStore:
    DDL=DDL
    TABLE=TABLE
    META=META
    CONTRACT=CONTRACT
    SCHEMA_PREFIX='migration_terminal_%'
    KEY_COLUMN='signature'
    record_key=staticmethod(lambda row:row['signature'])

    def __init__(self, ops_path, journal_path, *, generation, validate_record, validate_pair):
        self.ops_path = Path(ops_path).absolute()
        self.journal_path = str(Path(journal_path).absolute())
        if len(self.journal_path.encode()) > 1024 or not isinstance(generation,str) or not 1 <= len(generation.encode()) <= 128:
            raise TerminalHold('TERMINAL_AUTHORITY')
        self.generation = generation
        self.journal_identity = hashlib.sha256(self.journal_path.encode()).hexdigest()
        self.validate_record = validate_record
        self.validate_pair = validate_pair

    def _authority(self):
        if self.validate_pair(self.ops_path) != self.generation:
            raise TerminalHold('TERMINAL_PAIR_CHANGED')
        p=self.ops_path
        if str(p.resolve()) != str(p) or not stat.S_ISREG(p.lstat().st_mode):
            raise TerminalHold('TERMINAL_PHYSICAL_PATH')
        soft,_=resource.getrlimit(resource.RLIMIT_FSIZE)
        if soft == resource.RLIM_INFINITY or not 0 < soft <= MAX_FILE_BYTES:
            raise TerminalHold('KERNEL_FILE_LIMIT_REQUIRED')

    def _connect(self, *, write=False):
        self._authority()
        if write:
            free=os.statvfs(self.ops_path.parent)
            if free.f_bavail*free.f_frsize < DISK_FLOOR+TRANSACTION_RESERVE:
                raise TerminalHold('TERMINAL_DISK_FLOOR')
            for suffix,limit in [('',MAX_DB_BYTES),('-wal',WAL_ADMISSION_BYTES),('-journal',WAL_ADMISSION_BYTES)]:
                path=Path(str(self.ops_path)+suffix)
                if path.exists() and path.stat().st_size > limit:
                    raise TerminalHold('TERMINAL_STORAGE_PRESSURE')
        c=sqlite3.connect(self.ops_path.as_uri()+('?mode=rw' if write else '?mode=ro'),uri=True,timeout=5,isolation_level=None)
        try:
            if write:
                c.execute('PRAGMA synchronous=FULL')
                c.execute('PRAGMA foreign_keys=ON')
                c.execute('PRAGMA wal_autocheckpoint=32')
                page=c.execute('PRAGMA page_size').fetchone()[0]
                c.execute(f'PRAGMA max_page_count={MAX_DB_BYTES//page}')
        except BaseException:
            c.close()
            raise
        return c

    def _schema(self, c):
        expected={sql.split()[2]:sql for sql in self.DDL}
        rows=c.execute("SELECT name,sql FROM sqlite_master WHERE (name LIKE ? OR tbl_name IN (?,?)) AND type IN ('table','trigger')",(self.SCHEMA_PREFIX,self.META,self.TABLE)).fetchall()
        if dict(rows)!=expected:
            raise TerminalHold('TERMINAL_SCHEMA_AUTHORITY')
        meta=c.execute(f'SELECT version,contract,used_records,used_bytes FROM {self.META} WHERE singleton=1').fetchone()
        if not meta or meta[:2]!=(1,self.CONTRACT) or not 0<=meta[2]<=MAX_ROWS or not 0<=meta[3]<=MAX_BYTES:
            raise TerminalHold('TERMINAL_VERSION_OR_BUDGET')
        return meta

    def _sync_parent(self):
        fd=os.open(self.ops_path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)

    def initialize(self):
        """Explicit owner initialization against already-authorized OPS; no implicit call."""
        c=self._connect(write=True)
        try:
            c.execute('BEGIN IMMEDIATE')
            exists=c.execute("SELECT 1 FROM sqlite_master WHERE name=?",(self.META,)).fetchone()
            if not exists:
                for sql in self.DDL:c.execute(sql)
                c.execute(f'INSERT INTO {self.META} VALUES(1,1,?,0,0)',(self.CONTRACT,))
            self._schema(c)
            self._authority()
            c.execute('COMMIT')
        except BaseException:
            if c.in_transaction:c.execute('ROLLBACK')
            raise
        finally:c.close()
        # SQLite FULL commit plus explicit directory durability for new owner files.
        self._sync_parent()

    def _envelope(self,row):
        self.validate_record(row)
        if row['state'] not in {'RETRY_EXHAUSTED','QUARANTINED'}:
            raise TerminalHold('NOT_PROTECTED_TERMINAL')
        payload=_json(row)
        if len(payload.encode())+1>MAX_RECORD_BYTES:
            raise TerminalHold('TERMINAL_RECORD_CAPACITY')
        body=(row['id'],row['signature'],self.journal_identity,self.journal_path,self.generation,payload,RIGHTS)
        digest=hashlib.sha256(_json(body).encode()).hexdigest()
        allocation=len(_json(body).encode())+len(digest)+64
        if allocation>8192:raise TerminalHold('TERMINAL_ENVELOPE_CAPACITY')
        return body[:6]+(digest,allocation,RIGHTS,'PERSISTED')

    def _decode(self,record):
        if record is None:return None
        ident,sig,jid,jpath,gen,payload,digest,allocation,rights,state=record
        if jid!=self.journal_identity or jpath!=self.journal_path or rights!=RIGHTS or state not in {'PERSISTED','VERIFIED'} or not isinstance(gen,str) or not 1<=len(gen.encode())<=128:
            raise TerminalHold('TERMINAL_PROVENANCE')
        if not isinstance(payload,str) or len(payload.encode())+1>MAX_RECORD_BYTES:
            raise TerminalHold('TERMINAL_RECORD_CAPACITY')
        body=(ident,sig,jid,jpath,gen,payload,rights)
        if hashlib.sha256(_json(body).encode()).hexdigest()!=digest or allocation!=len(_json(body).encode())+len(digest)+64:
            raise TerminalHold('TERMINAL_EVIDENCE_CORRUPT')
        try:row=json.loads(payload)
        except (ValueError,TypeError):raise TerminalHold('TERMINAL_EVIDENCE_CORRUPT') from None
        self.validate_record(row)
        if row['id']!=ident or row['signature']!=sig or row['state'] not in {'RETRY_EXHAUSTED','QUARANTINED'} or _json(row)!=payload:
            raise TerminalHold('TERMINAL_IDENTITY_CORRUPT')
        return row

    def lookup(self,signature,*,verified_only=False):
        c=self._connect()
        try:
            self._schema(c)
            record=c.execute(f'SELECT {COLUMNS} FROM {self.TABLE} WHERE {self.KEY_COLUMN}=?',(signature,)).fetchone()
            row=self._decode(record)
            if record and verified_only and record[-1]!='VERIFIED':return None
            self._authority()
            return row
        finally:c.close()

    def verified(self,row):
        return self.lookup(self.record_key(row),verified_only=True)==row

    def preserve_batch(self,rows):
        if not 1<=len(rows)<=MAX_BATCH or len({r['id'] for r in rows})!=len(rows):
            raise TerminalHold('TERMINAL_BATCH_CAPACITY')
        envelopes=[self._envelope(r) for r in rows]
        c=self._connect(write=True)
        try:
            c.execute('BEGIN IMMEDIATE');meta=self._schema(c)
            additions=[]
            for row,entry in zip(rows,envelopes):
                old=c.execute(f'SELECT {COLUMNS} FROM {self.TABLE} WHERE {self.KEY_COLUMN}=?',(self.record_key(row),)).fetchone()
                if old:
                    if self._decode(old)!=row:raise TerminalHold('TERMINAL_IDENTITY_CONFLICT')
                else:additions.append(entry)
            if meta[2]+len(additions)>MAX_ROWS or meta[3]+sum(e[7] for e in additions)>MAX_BYTES:
                raise TerminalHold('TERMINAL_CAPACITY')
            c.executemany(f'INSERT INTO {self.TABLE} VALUES(?,?,?,?,?,?,?,?,?,?)',additions)
            self._authority();c.execute('COMMIT')
        except BaseException:
            if c.in_transaction:c.execute('ROLLBACK')
            raise
        finally:c.close()
        self._sync_parent()
        # Separate connection/read transaction; commit alone never permits removal.
        for row in rows:
            if self.lookup(self.record_key(row))!=row:raise TerminalHold('TERMINAL_READBACK_FAILED')
        c=self._connect(write=True)
        try:
            c.execute('BEGIN IMMEDIATE');self._schema(c)
            for row in rows:
                old=c.execute(f'SELECT {COLUMNS} FROM {self.TABLE} WHERE {self.KEY_COLUMN}=?',(self.record_key(row),)).fetchone()
                if self._decode(old)!=row:raise TerminalHold('TERMINAL_READBACK_FAILED')
                c.execute(f"UPDATE {self.TABLE} SET transfer_state='VERIFIED' WHERE {self.KEY_COLUMN}=?",(self.record_key(row),))
            self._authority();c.execute('COMMIT')
        except BaseException:
            if c.in_transaction:c.execute('ROLLBACK')
            raise
        finally:c.close()
        self._sync_parent()
        for row in rows:
            if not self.verified(row):raise TerminalHold('TERMINAL_COMPLETION_UNVERIFIED')
        return [r['id'] for r in rows]


class BirthTerminalEvidenceStore(TerminalEvidenceStore):
    """Typed reuse of the existing bounded protected-evidence owner in OPS.

    Identity is the original birth id, never a canonical mint/signature alias.
    This preserves conflicts only; it grants no CREATE or retry authority.
    """
    DDL=tuple(sql.replace('migration_terminal_', 'birth_terminal_').replace(
        'signature TEXT NOT NULL UNIQUE', 'signature TEXT NOT NULL') for sql in DDL)
    TABLE='birth_terminal_evidence_v1'
    META='birth_terminal_owner_v1'
    CONTRACT=hashlib.sha256('\n'.join(DDL).encode()).hexdigest()
    SCHEMA_PREFIX='birth_terminal_%'
    KEY_COLUMN='identity'
    record_key=staticmethod(lambda row:row['id'])

    def _envelope(self,row):
        if row.get('state')!='QUARANTINED' or row.get('error_code')!='CORE_AUTHORITY_CONFLICT':
            raise TerminalHold('BIRTH_PROTECTED_CONFLICT_REQUIRED')
        return super()._envelope(row)

    def _decode(self,record):
        row=super()._decode(record)
        if row is not None and (row.get('state')!='QUARANTINED' or row.get('error_code')!='CORE_AUTHORITY_CONFLICT'):
            raise TerminalHold('BIRTH_PROTECTED_CONFLICT_REQUIRED')
        return row
