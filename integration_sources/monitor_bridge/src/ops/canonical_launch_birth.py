"""Read-only, fail-closed projection of retained launch-birth evidence."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


def _identity(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def project(*, db_path: str, mint: str, operation_id: str, assignment: dict) -> dict:
    """Return one canonical birth or an explicit insufficient/conflict state.

    The immutable create ledger is preferred.  The retained launch authority is
    the compatibility source for older rows; neither path infers a creator from
    any funding/topology role and both are read-only.
    """
    with sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True) as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = []
        source = None
        if 'wt_create_event_ledger' in tables:
            rows = con.execute("SELECT creator,signature,slot,block_time,source,parser_path FROM wt_create_event_ledger WHERE mint=?", (mint,)).fetchall()
            source = 'WT_CREATE_EVENT_LEDGER'
        if not rows and 'wt_watchtower_launches' in tables:
            rows = con.execute("SELECT creator_wallet,create_signature,create_slot,create_time,creator_extraction_method,confidence FROM wt_watchtower_launches WHERE mint=?", (mint,)).fetchall()
            source = 'WT_WATCHTOWER_LAUNCH_AUTHORITY'
    usable = [tuple(row) for row in rows if row[0] and row[1]]
    pairs = {(str(row[0]), str(row[1]), row[2], row[3]) for row in usable}
    if not pairs:
        return {'state': 'INSUFFICIENT_CANONICAL_BIRTH', 'mint': mint, 'reason': 'CREATOR_OR_CREATE_SIGNATURE_ABSENT'}
    if len(pairs) != 1:
        return {'state': 'CONFLICTING_CANONICAL_BIRTH', 'mint': mint, 'reason': 'MULTIPLE_RETAINED_CREATOR_OR_SIGNATURE_VALUES'}
    creator, signature, slot, create_time = next(iter(pairs))
    evidence = {'mint': mint, 'creator': creator, 'create_signature': signature,
                'create_slot': slot, 'create_time': create_time, 'source': source,
                'operation_id': operation_id, 'assignment_event_id': assignment.get('event_id')}
    return {**evidence, 'state': 'CANONICAL_BIRTH_PROJECTED',
            'birth_evidence_id': _identity(evidence),
            'creator_evidence_id': _identity({'mint': mint, 'creator': creator, 'source': source}),
            'create_signature_evidence_id': _identity({'mint': mint, 'create_signature': signature, 'source': source})}
