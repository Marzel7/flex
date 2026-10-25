"""Fail-closed, durably published DEV-only rolling provider dispatch budget.

Existing version-1 ledgers must be provisioned from independently verified
allowance authority. Admission never initializes, repairs or resets a ledger.
The existing lock file also records interrupted publication; no recovery service.
"""
from __future__ import annotations
import fcntl
import json
import os
import stat
import time
from pathlib import Path

GLOBAL_LIMIT = 20
TOKEN_LIMIT = 4
WINDOW = 60
MAX_STATE_BYTES = 65_536
MAX_FIELD_BYTES = 256
_READY = b'READY_V1\n'
_PENDING = b'PENDING_V1\n'


class BudgetDenied(RuntimeError):
    pass


def _deny(reason):
    raise BudgetDenied(reason)


def _integer(value):
    return type(value) is int and value >= 0


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _deny('PROVIDER_BUDGET_STATE_INVALID')
        result[key] = value
    return result


def _regular(fd, maximum):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
        _deny('PROVIDER_BUDGET_STATE_INVALID')


def _write_all(fd, encoded):
    offset = 0
    while offset < len(encoded):
        written = os.write(fd, encoded[offset:])
        if written <= 0:
            raise OSError('short write')
        offset += written


def _guard_state(fd, state):
    os.lseek(fd, 0, os.SEEK_SET)
    _write_all(fd, state)
    os.ftruncate(fd, len(state))
    os.fsync(fd)


def _sync_directory(root):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class DevProviderBudget:
    def __init__(self, root, now=time.time):
        self.path = Path(root) / 'provider_budget.json'
        self.lock = Path(root) / 'provider_budget.lock'
        self.now = now

    def _read(self):
        # O_NOFOLLOW and bounded reads: ambiguous input never implies zero use.
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            _regular(fd, MAX_STATE_BYTES)
            raw = os.read(fd, MAX_STATE_BYTES + 1)
        finally:
            os.close(fd)
        if len(raw) > MAX_STATE_BYTES:
            _deny('PROVIDER_BUDGET_STATE_INVALID')
        state = json.loads(raw, object_pairs_hook=_pairs)
        if (not isinstance(state, dict) or set(state) != {'version', 'updated_at', 'calls'}
                or type(state['version']) is not int or state['version'] != 1
                or not _integer(state['updated_at']) or not isinstance(state['calls'], list)):
            _deny('PROVIDER_BUDGET_STATE_INVALID')
        for call in state['calls']:
            if (not isinstance(call, dict) or set(call) != {'at', 'mint', 'class'}
                    or not _integer(call['at']) or call['at'] > state['updated_at']):
                _deny('PROVIDER_BUDGET_STATE_INVALID')
            for field in ('mint', 'class'):
                value = call[field]
                if not isinstance(value, str) or not value or len(value.encode()) > MAX_FIELD_BYTES:
                    _deny('PROVIDER_BUDGET_STATE_INVALID')
        return state

    def admit(self, mint, request_class, *, now=None, global_limit=GLOBAL_LIMIT, token_limit=TOKEN_LIMIT):
        """Reserve before dispatch; failure grants no permission to call a provider.

        Fixed ledger/lock/temp paths; every new file <=65536 bytes. An interrupted
        publication stays blocked for independently verified external recovery.
        No automatic cleanup, missing-ledger creation or inferred allowance.
        """
        stamp = int(self.now() if now is None else now)
        if not _integer(stamp) or not _integer(global_limit) or not _integer(token_limit):
            _deny('INVALID_PROVIDER_BUDGET_LIMIT')
        for value in (mint, request_class):
            if not isinstance(value, str) or not value or len(value.encode()) > MAX_FIELD_BYTES:
                _deny('INVALID_PROVIDER_BUDGET_IDENTITY')
        root = self.path.parent.absolute()
        if root != root.resolve():
            _deny('PROVIDER_BUDGET_AUTHORITY_REQUIRED')
        temp = self.path.with_name('.' + self.path.name + '.tmp')
        fd = None
        try:
            # Only this existing guard may be created. The authoritative ledger
            # must already exist, validate, and have no unresolved publication.
            fd = os.open(self.lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            _regular(fd, 64)
            guard = os.read(fd, 65)
            if guard not in (b'', _READY) or temp.exists() or temp.is_symlink():
                _deny('PROVIDER_BUDGET_RECOVERY_REQUIRED')
            state = self._read()
            if stamp < state['updated_at']:
                _deny('PROVIDER_BUDGET_CLOCK_REGRESSION')
            calls = [x for x in state['calls'] if x['at'] > stamp - WINDOW]
            token = [x for x in calls if x['mint'] == mint]
            if len(calls) >= global_limit:
                _deny('GLOBAL_PROVIDER_BUDGET_EXHAUSTED')
            if len(token) >= token_limit:
                _deny('TOKEN_PROVIDER_BUDGET_EXHAUSTED')
            calls.append({'at': stamp, 'mint': mint, 'class': request_class})
            encoded = (json.dumps({'calls': calls, 'updated_at': stamp, 'version': 1},
                                  separators=(',', ':'), allow_nan=False) + '\n').encode()
            if len(encoded) > MAX_STATE_BYTES:
                _deny('PROVIDER_BUDGET_STATE_CAPACITY')
            # Persist unresolved-publication authority before touching the ledger.
            _guard_state(fd, _PENDING)
            _sync_directory(root)
            temporary = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                _write_all(temporary, encoded)
                os.fsync(temporary)
            finally:
                os.close(temporary)
            os.replace(temp, self.path)
            _sync_directory(root)
            # The reservation is durably published before permission is returned.
            _guard_state(fd, _READY)
            return {'global_calls': len(calls), 'token_calls': len(token) + 1,
                    'remaining_global': global_limit - len(calls),
                    'remaining_token': token_limit - len(token) - 1}
        except BudgetDenied:
            raise
        except (OSError, ValueError, TypeError, OverflowError):
            # No paths, payloads, keys or provider data in errors. Any pending
            # guard/temp remains in place; admission never repairs ambiguity.
            _deny('PROVIDER_BUDGET_AUTHORITY_UNAVAILABLE')
        finally:
            if fd is not None:
                os.close(fd)  # releases the flock even on failure
