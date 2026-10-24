"""Bounded version-2 extension of the existing migration fallback JSONL journal.

No provider transport, background service, database or cutover.
A stable path must be explicitly bound before opt-in use. Missing/ambiguous files
fail closed; initialization is a separate explicit DEV/deployment step.
"""
from __future__ import annotations
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path

MAX_RECORD_BYTES = 4096
MAX_JOURNAL_BYTES = 2_000_000
MAX_RECORDS = 512
MAX_BATCH = 20
MAX_ATTEMPTS = 3
DISK_FLOOR = 2_000_000_000
STATES = {'PENDING', 'RESOLVED', 'ACKED', 'QUARANTINED', 'RETRY_EXHAUSTED'}

class JournalHold(RuntimeError):
    pass

class CompletionLookupDeferred(JournalHold):
    """Admission is durable; no dispatch until optional lookup qualifies."""
    admission_durable = True

def event_id(signature):
    if not isinstance(signature, str) or not 1 <= len(signature) <= 128:
        raise JournalHold('INVALID_SIGNATURE')
    return hashlib.sha256(('MIGRATION:' + signature).encode()).hexdigest()

def _text(value, cap, *, optional=False):
    if optional and value is None:
        return value
    if not isinstance(value, str) or not value or len(value.encode()) > cap:
        raise JournalHold('INVALID_FIELD')
    return value

def _paired_ack_generation():
    configured = os.environ.get("DEV004_PAIRED_BINDING_REQUIRED", "0") == "1" or os.environ.get("FLEX_DB_PAIR_MANIFEST") or os.environ.get("FLEX_DB_PAIR_AUTHORITY")
    paths = [os.environ.get("DB_PATH"), os.environ.get("WT_OPS_DB_PATH")]
    if configured or any(p and os.path.lexists(os.path.realpath(p) + ".pair-bound") for p in paths):
        from src.core.paired_binding_guard import validate_pair
        return validate_pair(purpose="active")
    return None

class MigrationJournal:
    _key = staticmethod(event_id)
    def __init__(self, path, *, verify_completed=None, resolve_completed=None):
        self.verify_completed = verify_completed
        self.resolve_completed = resolve_completed
        self.path = Path(path).absolute()
        self.lock_path = self.path.with_name(self.path.name + '.lock')
        self.tmp = self.path.with_name(self.path.name + '.tmp')
        if self.path.is_symlink() or self.lock_path.is_symlink() or self.tmp.is_symlink():
            raise JournalHold('SYMLINK_AUTHORITY')

    def _sync_dir(self):
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @contextlib.contextmanager
    def _lock(self):
        # Fixed sidecar is never renamed/unlinked; flock survives connection replacement.
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JournalHold('OWNER_BUSY') from None
            os.fsync(fd)
            self._sync_dir()
            yield
        finally:
            os.close(fd)

    def initialize(self):
        """Explicit empty-file initialization; never invoked by notification admission."""
        with self._lock():
            if self.path.exists() or self.tmp.exists():
                raise JournalHold('EXISTING_AUTHORITY')
            self._publish([])

    def _read(self):
        if self.tmp.exists():
            raise JournalHold('INTERRUPTED_PUBLICATION_REQUIRES_REVIEW')
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(MAX_JOURNAL_BYTES + 1)
        except FileNotFoundError:
            raise JournalHold('MISSING_JOURNAL_AUTHORITY') from None
        if len(raw) > MAX_JOURNAL_BYTES or (raw and not raw.endswith(b'\n')):
            raise JournalHold('TRUNCATED_OR_OVERSIZE_JOURNAL')
        records = []
        seen = set()
        for line in raw.splitlines():
            if len(line) > MAX_RECORD_BYTES:
                raise JournalHold('RECORD_CAPACITY')
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                raise JournalHold('MALFORMED_JOURNAL') from None
            if not isinstance(row, dict):
                raise JournalHold('MALFORMED_RECORD')
            if type(row.get('version')) is not int:
                raise JournalHold('UNSUPPORTED_VERSION')
            if row.get('version') == 1:
                # Original resolved records remain exact retained evidence.
                if set(row) - {'version','mint','signature','received_at','source','migration_slot','last_error'}:
                    raise JournalHold('UNEXPECTED_LEGACY_FIELDS')
                _text(row.get('mint'), 64)
                sig = _text(row.get('signature'), 128)
                row = self._new(sig, row.get('source') or 'legacy', row.get('received_at'), row['mint'], row.get('migration_slot')) | {'legacy': row}
            if row.get('version') != 2:
                raise JournalHold('UNSUPPORTED_VERSION')
            self._validate(row)
            if row['id'] in seen:
                # Legacy duplicates must not silently overwrite conflicting evidence.
                previous = next(x for x in records if x['id'] == row['id'])
                if previous != row:
                    if 'legacy' not in previous or 'legacy' not in row or previous['mint'] != row['mint'] or previous.get('migration_slot') != row.get('migration_slot'):
                        raise JournalHold('DUPLICATE_RECORD_CONFLICT')
                    alternatives = previous.setdefault('legacy_duplicates', [])
                    if row['legacy'] != previous['legacy'] and row['legacy'] not in alternatives:
                        if len(alternatives) >= 3:
                            raise JournalHold('LEGACY_DUPLICATE_CAPACITY')
                        alternatives.append(row['legacy'])
                        previous['received_at'] = min(previous['received_at'], row['received_at'])
                    if len(json.dumps(previous, sort_keys=True, separators=(',', ':')).encode()) + 1 > MAX_RECORD_BYTES:
                        raise JournalHold('RECORD_CAPACITY')
                continue
            seen.add(row['id'])
            records.append(row)
        if len(records) > MAX_RECORDS:
            raise JournalHold('JOURNAL_CAPACITY')
        return records

    def _new(self, signature, source, received_at, mint, migration_slot=None):
        _text(source, 80)
        if type(received_at) is not int or not 0 < received_at < 2**63:
            raise JournalHold('INVALID_RECEPTION_TIME')
        _text(mint, 64, optional=True)
        if migration_slot is not None and (type(migration_slot) is not int or not 0 <= migration_slot < 2**63):
            raise JournalHold('INVALID_SLOT')
        ident = event_id(signature)
        return {'version': 2, 'id': ident, 'signature': signature,
                'notification_identity': hashlib.sha256((source + ':' + signature).encode()).hexdigest(),
                'source': source, 'received_at': received_at, 'mint': mint,
                'state': 'RESOLVED' if mint else 'PENDING', 'attempts': 0,
                'error_code': None, 'ack': None, 'conflicting_mint': None,
                'migration_slot': migration_slot, 'conflicting_slot': None}

    def _validate(self, row):
        allowed = {'version','id','signature','notification_identity','source','received_at','mint','state','attempts','error_code','ack','legacy','legacy_duplicates','conflicting_mint','migration_slot','conflicting_slot'}
        if set(row) - allowed or row.get('id') != event_id(row.get('signature')):
            raise JournalHold('RECORD_IDENTITY')
        self._new(row['signature'], row.get('source'), row.get('received_at'), row.get('mint'), row.get('migration_slot'))
        if row.get('state') not in STATES or type(row.get('attempts')) is not int or not 0 <= row['attempts'] <= MAX_ATTEMPTS:
            raise JournalHold('INVALID_STATE')
        _text(row.get('error_code'), 80, optional=True)
        _text(row.get('conflicting_mint'), 64, optional=True)
        if row.get('conflicting_slot') is not None and (type(row['conflicting_slot']) is not int or not 0 <= row['conflicting_slot'] < 2**63):
            raise JournalHold('INVALID_SLOT')
        if row.get('notification_identity') != hashlib.sha256((row['source'] + ':' + row['signature']).encode()).hexdigest():
            raise JournalHold('NOTIFICATION_IDENTITY')
        if row['state'] == 'RESOLVED' and not row.get('mint'):
            raise JournalHold('INVALID_RESOLUTION')
        if row.get('ack') is not None:
            if not isinstance(row['ack'], dict) or set(row['ack']) != {'identity','binding'} or row['ack']['identity'] != row['id']:
                raise JournalHold('INVALID_ACK')
            _text(row['ack'].get('binding'), 256)
        if row['state'] == 'ACKED' and (not row.get('mint') or row.get('ack') is None):
            raise JournalHold('INVALID_ACK')
        duplicates = row.get('legacy_duplicates', [])
        if not isinstance(duplicates, list) or len(duplicates) > 3 or any(not isinstance(x, dict) for x in duplicates):
            raise JournalHold('INVALID_LEGACY_DUPLICATES')

    def _publish(self, records):
        if len(records) > MAX_RECORDS:
            raise JournalHold('JOURNAL_CAPACITY')
        encoded = []
        for row in records:
            self._validate(row)
            line = json.dumps(row, sort_keys=True, separators=(',', ':')).encode() + b'\n'
            if len(line) > MAX_RECORD_BYTES:
                raise JournalHold('RECORD_CAPACITY')
            encoded.append(line)
        payload = b''.join(encoded)
        if len(payload) > MAX_JOURNAL_BYTES:
            raise JournalHold('JOURNAL_CAPACITY')
        stat = os.statvfs(self.path.parent)
        if stat.f_bavail * stat.f_frsize < DISK_FLOOR + MAX_JOURNAL_BYTES:
            raise JournalHold('DISK_FLOOR')
        fd = os.open(self.tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            if os.write(fd, payload) != len(payload):
                raise JournalHold('SHORT_WRITE')
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(self.tmp, self.path)
        self._sync_dir()  # Dispatch is forbidden unless all publication steps succeed.

    def snapshot(self):
        if self.tmp.exists() and self.verify_completed is not None:
            self.recover_reclamation()
        with self._lock():
            return self._read()

    def _change(self, fn):
        if self.tmp.exists() and self.verify_completed is not None:
            self.recover_reclamation()
        with self._lock():
            records = self._read()
            result = fn(records)
            if len(records) > MAX_RECORDS or sum(len(json.dumps(r, sort_keys=True, separators=(',', ':')).encode()) + 1 for r in records) > MAX_JOURNAL_BYTES:
                if self.verify_completed is None:
                    raise JournalHold('JOURNAL_CAPACITY')
                self._reclaim(records, self.verify_completed, MAX_BATCH, keep=result.get('id') if isinstance(result, dict) else None)
            self._publish(records)
            return result

    @staticmethod
    def _reclaimable(row):
        # Legacy originals and every conflict remain retained evidence.
        return (row['state'] == 'ACKED' and row.get('ack') is not None
                and row.get('error_code') is None
                and not any(row.get(k) for k in ('legacy', 'legacy_birth', 'legacy_duplicates',
                                                'conflicting_mint', 'conflicting_slot', 'conflicting_payload')))

    def _reclaim(self, rows, verify, limit, keep=None):
        if verify is None:
            raise JournalHold('COMPLETION_VERIFIER_REQUIRED')
        generation = _paired_ack_generation()
        removed = []
        for row in rows:
            if len(removed) == limit:
                break
            if row['id'] != keep and self._reclaimable(row) and verify(dict(row)) is True:
                removed.append(row['id'])
        if _paired_ack_generation() != generation:
            raise JournalHold('PAIR_CHANGED_DURING_RECLAMATION')
        identities = set(removed)
        rows[:] = [r for r in rows if r['id'] not in identities]
        return removed

    def reclaim_completed(self, verify=None, *, limit=MAX_BATCH):
        if type(limit) is not int or not 0 < limit <= MAX_BATCH:
            raise JournalHold('BATCH_CAPACITY')
        with self._lock():
            rows = self._read()
            removed = self._reclaim(rows, verify or self.verify_completed, limit)
            self._publish(rows)
            return removed

    def recover_reclamation(self):
        """Explicit recovery of an interrupted capacity-only publication.

        The candidate can add unacknowledged admissions, but cannot change or
        omit any old record except independently reverified completed work.
        Never discard a temporary file or silently reset ambiguous state.
        """
        with self._lock():
            if not self.tmp.exists():
                return False
            with self.tmp.open('rb') as stream:
                raw = stream.read(MAX_JOURNAL_BYTES + 1)
            if len(raw) > MAX_JOURNAL_BYTES or (raw and not raw.endswith(b'\n')):
                raise JournalHold('RECLAMATION_RECOVERY_AMBIGUOUS')
            try:
                lines = raw.splitlines()
                if any(len(line) + 1 > MAX_RECORD_BYTES for line in lines):
                    raise ValueError()
                candidate = [json.loads(line) for line in lines]
                for row in candidate:
                    self._validate(row)
                if len(candidate) > MAX_RECORDS or len({r['id'] for r in candidate}) != len(candidate):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise JournalHold('RECLAMATION_RECOVERY_AMBIGUOUS') from None
            # Read original without bypassing its validation or renaming evidence.
            original = self.tmp
            self.tmp = self.path.with_name(self.path.name + '.recovery-unpublished')
            try:
                rows = self._read()
            finally:
                self.tmp = original
            byid = {r['id']: r for r in candidate}
            missing = []
            for row in rows:
                if row['id'] in byid:
                    if byid[row['id']] != row:
                        raise JournalHold('RECLAMATION_RECOVERY_AMBIGUOUS')
                else:
                    missing.append(row)
            if len(missing) > MAX_BATCH:
                raise JournalHold('RECLAMATION_RECOVERY_BATCH_CAPACITY')
            if any(not self._reclaimable(r) for r in missing):
                raise JournalHold('RECLAMATION_RECOVERY_PROTECTED')
            if missing:
                copy = list(missing)
                self._reclaim(copy, self.verify_completed, MAX_RECORDS)
                if copy:
                    raise JournalHold('RECLAMATION_RECOVERY_UNVERIFIED')
            stat = os.statvfs(self.path.parent)
            if stat.f_bavail * stat.f_frsize < DISK_FLOOR + MAX_JOURNAL_BYTES:
                raise JournalHold('DISK_FLOOR')
            fd = os.open(self.tmp, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(self.tmp, self.path)
            self._sync_dir()
            return True

    def admit(self, signature, source, received_at, mint=None, *, migration_slot=None):
        incoming = self._new(signature, source, received_at, mint, migration_slot)
        def change(rows):
            for row in rows:
                if row['id'] == incoming['id']:
                    if migration_slot is not None:
                        if row.get('migration_slot') is None:
                            row['migration_slot'] = migration_slot
                        elif row['migration_slot'] != migration_slot:
                            if row.get('conflicting_slot') not in (None, migration_slot):
                                raise JournalHold('CONFLICT_CAPACITY')
                            row['conflicting_slot'] = migration_slot
                            row['state'] = 'QUARANTINED'; row['error_code'] = 'SLOT_CONFLICT'
                    if row['mint'] and mint and row['mint'] != mint:
                        if row.get('conflicting_mint') not in (None, mint):
                            raise JournalHold('CONFLICT_CAPACITY')
                        row['conflicting_mint'] = mint
                        row['state'] = 'QUARANTINED'; row['error_code'] = 'MINT_CONFLICT'
                    elif mint and row['mint'] is None and row['state'] not in {'QUARANTINED','ACKED','RETRY_EXHAUSTED'}:
                        row['mint'] = mint; row['state'] = 'RESOLVED'
                    return dict(row)
            rows.append(incoming)
            return dict(incoming)
        # The independently durable input always precedes any Core/OPS read.
        row = self._change(change)
        if self.resolve_completed is not None and row['state'] not in {'ACKED','QUARANTINED','RETRY_EXHAUSTED'}:
            try:
                resolved = self.resolve_completed(dict(row))
                if resolved is not None:
                    _text(resolved, 64)
                    return self.resolve(signature, resolved)
            except Exception as error:
                if isinstance(error, RuntimeError) and str(error).startswith('PAIR_') and str(error) not in {'PAIR_TRANSFER_ONLY','PAIR_AUTHORITY_NOT_CURRENT','PAIR_DATABASE_RETIRED'}:
                    raise  # Invalid bindings still fail loudly, after admission.
                raise CompletionLookupDeferred('COMPLETION_LOOKUP_DEFERRED') from None
        return row

    def resolve(self, signature, mint):
        _text(mint, 64)
        def change(rows):
            row = next((r for r in rows if r['id'] == self._key(signature)), None)
            if row is None:
                return None
            if row['mint'] and row['mint'] != mint:
                if row.get('conflicting_mint') not in (None, mint):
                    raise JournalHold('CONFLICT_CAPACITY')
                row['conflicting_mint'] = mint
                row['state'] = 'QUARANTINED'; row['error_code'] = 'MINT_CONFLICT'
            elif row['state'] not in {'ACKED','QUARANTINED','RETRY_EXHAUSTED'}:
                row['mint'] = mint; row['state'] = 'RESOLVED'
            return dict(row)
        return self._change(change)

    def begin_attempt(self, signature):
        def change(rows):
            row = next(r for r in rows if r['id'] == self._key(signature))
            if row['state'] in {'ACKED','QUARANTINED','RETRY_EXHAUSTED'}:
                return False
            if row['attempts'] >= MAX_ATTEMPTS:
                row['state'] = 'RETRY_EXHAUSTED'; row['error_code'] = 'RETRY_CEILING'
                return False
            row['attempts'] += 1
            row['error_code'] = 'DISPATCH_RESERVED'
            return True
        return self._change(change)

    def acknowledge(self, signature, binding, verify):
        pair_generation = _paired_ack_generation()
        _text(binding, 256)
        def change(rows):
            row = next(r for r in rows if r['id'] == self._key(signature))
            if row['state'] == 'ACKED':
                if row['ack']['binding'] != binding:
                    raise JournalHold('ACK_BINDING_CONFLICT')
                return True
            if row['state'] in {'QUARANTINED','RETRY_EXHAUSTED'} or not row['mint']:
                return False
            if not verify(dict(row)):
                row['error_code'] = 'READBACK_UNVERIFIED'
                return False
            if _paired_ack_generation() != pair_generation:
                raise JournalHold('PAIR_CHANGED_DURING_ACK')
            row['state'] = 'ACKED'
            row['ack'] = {'binding': binding, 'identity': row['id']}
            row['error_code'] = None
            return True
        return self._change(change)

    def quarantine(self, signature, reason):
        _text(reason, 80)
        def change(rows):
            row = next(r for r in rows if r['id'] == self._key(signature))
            if row['state'] == 'ACKED':
                raise JournalHold('ACKED_REQUIRES_EXPLICIT_REVIEW')
            row['state'] = 'QUARANTINED'; row['error_code'] = reason
        self._change(change)

    def pending(self, limit=MAX_BATCH):
        if type(limit) is not int or not 0 < limit <= MAX_BATCH:
            raise JournalHold('BATCH_CAPACITY')
        return [r for r in self.snapshot() if r['state'] not in {'ACKED','QUARANTINED','RETRY_EXHAUSTED'}][:limit]
