"""Preserve the demonstrated protected-terminal capacity stop; no unsafe reclaim."""
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def case(tmp_path, monkeypatch):
    def forbidden(*a, **k): raise AssertionError('PROVIDER_FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    source = ROOT / 'runtime/listener/src/core/migration_signature_journal.py'
    spec = importlib.util.spec_from_file_location('protected_capacity_journal_fixture', source)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    # Test-local independent paired authority; never load a production manifest.
    monkeypatch.setattr(m, '_paired_ack_generation', lambda: 'disposable-generation')
    path = tmp_path / 'migration.jsonl'
    j = m.MigrationJournal(path, verify_completed=lambda r: False)
    j.initialize()
    def populate(acked=True, legacy=False):
        rows = []
        for n in range(512):
            r = j._new('synthetic-signature-' + str(n), 'PUMPSWAP', 1000 + n, None)
            r.update(state='RETRY_EXHAUSTED', attempts=3, error_code='RETRY_CEILING')
            rows.append(r)
        if acked:
            rows[-1].update(mint='synthetic-mint', state='ACKED', error_code=None,
                            ack={'identity': rows[-1]['id'], 'binding': 'fixture-only'})
            if legacy:
                rows[-1]['legacy'] = {'version': 1, 'mint': 'synthetic-mint',
                                      'signature': rows[-1]['signature'], 'received_at': 1511, 'source': 'PUMPSWAP'}
        with j._lock(): j._publish(rows)  # Actual owner validation/publication.
        return rows
    yield types.SimpleNamespace(module=m, journal=j, path=path, populate=populate, tmp=tmp_path)
    assert all(p.stat().st_size < 2_000_000 for p in tmp_path.iterdir() if p.is_file())


@pytest.mark.parametrize('mode', ['511-exhausted-unverified-ACK', '511-exhausted-protected-legacy-ACK', '512-exhausted'])
def test_event_513_refused_without_independent_terminal_authority(case, mode):
    rows = case.populate(acked=mode != '512-exhausted', legacy=mode == '511-exhausted-protected-legacy-ACK')
    if mode != '511-exhausted-unverified-ACK': case.journal.verify_completed = lambda r: True
    before = case.path.read_bytes()
    with pytest.raises(case.module.JournalHold, match='JOURNAL_CAPACITY'):
        case.journal.admit('event-513', 'PUMPSWAP', 2000)
    assert case.path.read_bytes() == before
    assert case.journal.snapshot() == rows
    assert not case.journal.tmp.exists()


def test_prior_capacity_reuse_covers_verified_ACK_not_protected_exhaustion(case):
    rows = case.populate()
    case.journal.verify_completed = lambda r: True
    admitted = case.journal.admit('event-513', 'PUMPSWAP', 2000)
    retained = case.journal.snapshot()
    assert admitted['signature'] == 'event-513' and admitted['state'] == 'PENDING'
    assert len(retained) == 512
    assert all(r in retained for r in rows[:-1])  # All511 exhausted records preserved.


def test_duplicate_delivery_does_not_reset_exhausted_budget(case):
    rows = case.populate(acked=False)
    row = case.journal.admit(rows[0]['signature'], 'PUMPSWAP', 9000)
    assert row == rows[0]
    assert case.journal.begin_attempt(rows[0]['signature']) is False
    assert case.journal.snapshot()[0] == rows[0]


def test_repeated_restart_retains_all_protected_signatures(case):
    original = case.populate(acked=False)
    for _ in range(3):
        j = case.module.MigrationJournal(case.path, verify_completed=lambda r: True)
        assert j.pending() == []  # Excluded from automatic replay, not deleted.
        assert j.snapshot() == original
        with pytest.raises(case.module.JournalHold, match='JOURNAL_CAPACITY'):
            j.admit('event-513', 'PUMPSWAP', 2000)


def test_terminal_authority_cannot_be_forged_by_completion_verifier(case):
    rows = case.populate(acked=False)
    assert case.journal.reclaim_completed(verify=lambda row: True) == []
    assert case.journal.snapshot() == rows


def test_disk_floor_refusal_preserves_original(case, monkeypatch):
    case.populate(acked=False)
    before = hashlib.sha256(case.path.read_bytes()).hexdigest()
    monkeypatch.setattr(case.module.os, 'statvfs', lambda p: types.SimpleNamespace(f_bavail=0, f_frsize=4096))
    with pytest.raises(case.module.JournalHold, match='DISK_FLOOR'):
        case.journal.admit('synthetic-signature-0', 'PUMPSWAP', 9999)
    assert hashlib.sha256(case.path.read_bytes()).hexdigest() == before


def test_missing_or_truncated_journal_never_creates_fresh_allowance(case):
    case.populate(acked=False)
    original = case.path.read_bytes();case.path.write_bytes(original[:-1])
    with pytest.raises(case.module.JournalHold, match='TRUNCATED_OR_OVERSIZE'):
        case.journal.admit('event-513', 'PUMPSWAP', 2000)
    assert case.path.read_bytes() == original[:-1]
